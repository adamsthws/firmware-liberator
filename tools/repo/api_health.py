#!/usr/bin/env python3
"""Whether the project's API is well, as the cloudflare-settings workflow checks it daily.

    python3 tools/repo/api_health.py snapshot    the public figures are fresh, and of today's schema
    python3 tools/repo/api_health.py usage       the account is well within what its plan includes

`snapshot` needs no token: it reads the public figures the API Worker writes
each night (website/api/nightly.js), and fails if they are missing, older than
SNAPSHOT_AGE, or written by a database short of the newest migration
(website/api/database.js) - which is how CI, whose token is refused D1,
notices a migration not yet applied.

`usage` reads the account's use from Cloudflare's analytics, with
CLOUDFLARE_API_TOKEN (CI's read token, which needs Account Analytics: Read):
this month's Workers requests, D1 rows and R2 operations to yesterday, and
what D1 and R2 hold, each against what the account's plan includes a month
(INCLUDED: Workers Paid since 2026-11-10, and R2's own free allowance). It
fails at WARN_AT of any, so the maintainer hears long before a bill: past
what is included, each is billed, not stopped. Any error Cloudflare's
analytics give is said in full.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.repo import cloudflare_settings as cs  # noqa: E402

SNAPSHOT_URL = f"https://{cs.STATS_HOST}/v1/stats.json"
# The night's job runs at 03:17 UTC, and the workflow checks at 06:00: a
# night missed is then nearly 27 hours old, so more than 25 is a night
# missed, while a check run by hand at any hour sees at most about 24.
SNAPSHOT_AGE = dt.timedelta(hours=25)
DATABASE_JS = ROOT / "website" / "api" / "database.js"
WARN_AT = 0.5
GRAPHQL = f"{cs.API}/graphql"

# What the account's plan includes, as Cloudflare's pricing pages give it
# (2026): Workers Paid's allowance a month for Workers and D1, and R2's own
# free allowance, which Workers Paid leaves as it is.
INCLUDED = {
    "Workers requests this month": 10_000_000,
    "D1 rows read this month": 25_000_000_000,
    "D1 rows written this month": 50_000_000,
    "D1 storage, bytes": 5 * 1000 ** 3,
    "R2 storage, bytes": 10 * 1000 ** 3,
    "R2 Class A operations this month": 1_000_000,
    "R2 Class B operations this month": 10_000_000,
}
# R2's operations, as its analytics name them, of Class B (reads); every
# other is of Class A, as R2's pricing counts any it does not list as free.
CLASS_B = {"GetObject", "HeadObject", "HeadBucket", "UsageSummary", "GetBucketEncryption", "GetBucketLocation",
           "GetBucketCors", "GetBucketLifecycleConfiguration"}
FREE = {"DeleteObject", "DeleteBucket", "AbortMultipartUpload"}


class HealthError(RuntimeError):
    pass


def latest_migration() -> str:
    found = re.search(r'^export const LATEST_MIGRATION = "([^"]+)";$', DATABASE_JS.read_text(encoding="utf-8"), re.M)
    if not found:
        raise HealthError(f"{DATABASE_JS.relative_to(ROOT)} names no LATEST_MIGRATION")
    return found.group(1)


def fetch_snapshot(url: str = SNAPSHOT_URL) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "firmware-liberator-health"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, ValueError) as error:
        raise HealthError(f"{url}: {error}") from None


def snapshot_problems(figures: dict[str, Any], now: dt.datetime) -> list[str]:
    """What is wrong with the public figures: [] when fresh and current."""
    problems = []
    if figures.get("format") != 1:
        problems.append(f"they are of format {figures.get('format')!r}, not 1")
    try:
        made = dt.datetime.strptime(str(figures.get("generated")), "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        problems.append(f"they say no time they were made ({figures.get('generated')!r})")
    else:
        if now - made > SNAPSHOT_AGE:
            problems.append(f"they were made {made:%Y-%m-%d %H:%M} UTC, over {SNAPSHOT_AGE} ago: "
                            "the nightly job has not run")
    if figures.get("schema") != latest_migration():
        problems.append(f"they were made by a database at {figures.get('schema')!r}, not {latest_migration()!r}: "
                        "run python3 tools/repo/cloudflare_settings.py apply")
    return problems


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        GRAPHQL, method="POST", data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {cs.token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        raise HealthError(f"Cloudflare's analytics answered {error.code}: "
                          f"{error.read().decode('utf-8', 'replace')[:500]}") from None
    except (OSError, ValueError) as error:
        raise HealthError(f"Cloudflare's analytics: {error}") from None
    if answer.get("errors"):
        raise HealthError("Cloudflare's analytics: " + "; ".join(str(e.get("message")) for e in answer["errors"]))
    return answer["data"]


USAGE_QUERY = """
query Usage($account: string!, $day: Date!, $firstDay: Date!, $from: Time!, $to: Time!, $month: Time!) {
  viewer { accounts(filter: {accountTag: $account}) {
    workers: workersInvocationsAdaptive(limit: 10000, filter: {date_geq: $firstDay, date_leq: $day}) {
      sum { requests } }
    d1: d1AnalyticsAdaptiveGroups(limit: 10000, filter: {date_geq: $firstDay, date_leq: $day}) {
      sum { rowsRead rowsWritten } }
    d1Storage: d1StorageAdaptiveGroups(limit: 10000, filter: {date: $day}) {
      max { databaseSizeBytes } dimensions { databaseId } }
    r2Storage: r2StorageAdaptiveGroups(limit: 10000, filter: {datetime_geq: $from, datetime_lt: $to}) {
      max { payloadSize metadataSize } dimensions { bucketName } }
    r2Operations: r2OperationsAdaptiveGroups(limit: 10000, filter: {datetime_geq: $month, datetime_lt: $to}) {
      sum { requests } dimensions { actionType } }
  } }
}"""


def held(rows: list[dict[str, Any]] | None, keys: tuple[str, ...], by: str) -> int:
    """What the databases or buckets hold together: each one's most in the
    period (`keys` added up), summed, as the plan's limit is the account's."""
    most: dict[str, int] = {}
    for row in rows or []:
        name = (row.get("dimensions") or {}).get(by, "")
        size = sum(int((row.get("max") or {}).get(key) or 0) for key in keys)
        most[name] = max(most.get(name, 0), size)
    return sum(most.values())


def used(data: dict[str, Any]) -> dict[str, int]:
    """Each of INCLUDED's measures, from the analytics' answer."""
    accounts = data["viewer"]["accounts"]
    if not accounts:
        raise HealthError("the token sees no account: give it this account's Account Analytics: Read")
    account = accounts[0]
    total = lambda rows, part, key: sum(int((row.get(part) or {}).get(key) or 0) for row in rows or [])
    operations = account.get("r2Operations") or []
    by_class = {"A": 0, "B": 0}
    for row in operations:
        action = (row.get("dimensions") or {}).get("actionType", "")
        if action not in FREE:
            by_class["B" if action in CLASS_B else "A"] += int((row.get("sum") or {}).get("requests") or 0)
    return {
        "Workers requests this month": total(account.get("workers"), "sum", "requests"),
        "D1 rows read this month": total(account.get("d1"), "sum", "rowsRead"),
        "D1 rows written this month": total(account.get("d1"), "sum", "rowsWritten"),
        "D1 storage, bytes": held(account.get("d1Storage"), ("databaseSizeBytes",), "databaseId"),
        "R2 storage, bytes": held(account.get("r2Storage"), ("payloadSize", "metadataSize"), "bucketName"),
        "R2 Class A operations this month": by_class["A"],
        "R2 Class B operations this month": by_class["B"],
    }


def usage(now: dt.datetime) -> dict[str, int]:
    zones = cs.api(f"/zones?name={cs.ZONE}")
    if not zones:
        raise HealthError(f"the token cannot see the zone {cs.ZONE}")
    yesterday = (now - dt.timedelta(days=1)).date()
    start = dt.datetime.combine(yesterday, dt.time(), dt.timezone.utc)
    # The month that yesterday is in: on the 1st, the whole of the month before.
    month = start.replace(day=1)
    stamp = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return used(graphql(USAGE_QUERY, {"account": zones[0]["account"]["id"], "day": yesterday.isoformat(),
                                      "firstDay": month.date().isoformat(),
                                      "from": stamp(start), "to": stamp(start + dt.timedelta(days=1)),
                                      "month": stamp(month)}))


def usage_problems(measures: dict[str, int]) -> list[str]:
    return [f"{what}: {measures[what]:,} of {limit:,} ({measures[what] / limit:.0%}), past {WARN_AT:.0%}"
            for what, limit in INCLUDED.items() if measures.get(what, 0) >= WARN_AT * limit]


def main(argv: list[str] | None = None, now: dt.datetime | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (["snapshot"], ["usage"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        if args == ["snapshot"]:
            problems = snapshot_problems(fetch_snapshot(), now)
            good = f"The public figures at {SNAPSHOT_URL} are fresh, of schema {latest_migration()}."
        else:
            measures = usage(now)
            for what, limit in INCLUDED.items():
                print(f"  {what}: {measures[what]:,} of {limit:,} ({measures[what] / limit:.1%})")
            problems = usage_problems(measures)
            good = f"Every measure is under {WARN_AT:.0%} of what the plan includes."
    except (HealthError, cs.SettingsError) as error:
        print(f"api_health: {error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"  {problem}")
    if problems:
        return 1
    print(good)
    return 0


if __name__ == "__main__":
    sys.exit(main())
