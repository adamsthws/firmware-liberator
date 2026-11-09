#!/usr/bin/env python3
"""The readouts people have sent, in the R2 bucket the API Worker keeps them in.

    python3 tools/repo/readouts.py [prefix]        list them (prefix: readouts/)
    python3 tools/repo/readouts.py notify [PRODUCT]  who asked to be told when a
                                                    device's status changes
    python3 tools/repo/readouts.py review [PRODUCT]  the readouts waiting for review
    python3 tools/repo/readouts.py approve ID        one taken as right
    python3 tools/repo/readouts.py reject ID         one taken as wrong: its place
                                                    goes to another sender's

Each line is a readout's key, size, when it came in and what the Worker noted
of it: the tool, the product, whether it holds a whole read and a contact.
Under readouts/ only the readouts are listed, not the marks of what is held
beside them; a prefix given lists everything under it.

`review` lists, by product, firmware model and version, each readout the
API has not taken as right: fewer than three of them, from three senders,
agree (.github/readouts.md). Each line is its reference, stage, when it came
in, its object and fingerprint; one whose fingerprint differs from the rest
of its key is marked ODD. The API's database is read with the maintainer's
token, as cloudflare_settings.py reads it (D1 is refused to CI's).

`notify` lists each address given for a product (eWeLink's product model),
with the firmware models it was given with and when: whom to write to when
that device's status changes (.github/readouts.md). An address is removed by
deleting its object (notify/<PRODUCT>/<hash>.json).
Fetch one with `npx wrangler r2 object get <bucket>/<key> --remote --file
readout.tar`, from website/. The token is CLOUDFLARE_API_TOKEN, as for
tools/repo/cloudflare_settings.py; reading the account's R2 is enough.
"""
from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.repo import cloudflare_settings as cs  # noqa: E402


def objects(prefix: str) -> Iterator[dict[str, Any]]:
    """Every object under `prefix`, a page of 1000 at a time."""
    zones = cs.api(f"/zones?name={cs.ZONE}")
    if not zones:
        raise cs.SettingsError(f"the token cannot see the zone {cs.ZONE}")
    path = f"/accounts/{zones[0]['account']['id']}/r2/buckets/{cs.READOUTS_BUCKET}/objects"
    query = {"prefix": prefix, "per_page": "1000"}
    while True:
        answer = cs.api(f"{path}?{urllib.parse.urlencode(query)}", whole=True)
        yield from answer.get("result") or []
        info = answer.get("result_info") or {}
        if not info.get("is_truncated") or not info.get("cursor"):
            return
        query["cursor"] = info["cursor"]


def describe(item: dict[str, Any]) -> str:
    noted = item.get("custom_metadata") or {}
    return "  ".join([item["key"], f"{item.get('size', 0):>10}", str(item.get("last_modified", "")),
                      *(f"{key}={noted[key]}" for key in ("tool", "product", "read", "contact") if key in noted)])


def query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """The rows `sql` gives in the API's database."""
    zones = cs.api(f"/zones?name={cs.ZONE}")
    if not zones:
        raise cs.SettingsError(f"the token cannot see the zone {cs.ZONE}")
    path = f"/accounts/{zones[0]['account']['id']}/d1/database/{cs.bound_database_id()}/query"
    answer = cs.api(path, "POST", {"sql": sql, "params": params or []})
    return list((answer or [{}])[0].get("results") or [])


REVIEW_STATES = {"approve": "approved", "reject": "rejected"}


def review(product: str | None) -> int:
    """The readouts waiting for review, by key: each odd one marked."""
    rows = query("SELECT id, key, stage, fingerprint, object, received FROM readouts WHERE review = 'pending'"
                 + (" AND key LIKE ?1" if product else "") + " ORDER BY key, stage, received",
                 [f"{product.upper()}|%"] if product else [])
    keys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        keys.setdefault((row["key"], row["stage"]), []).append(row)
    for (key, stage), held in keys.items():
        print(f"{key} ({stage}): {len(held)} waiting")
        prints = [row["fingerprint"] for row in held]
        for row in held:
            odd = len(held) > 1 and prints.count(row["fingerprint"]) == 1 and len(set(prints)) > 1
            print(f"    {row['id']}  {row['received']}  {row['object']}  {row['fingerprint'][:12]}"
                  + ("  ODD" if odd else ""))
    print(f"{len(rows)} waiting for review")
    return 0


def settle(action: str, reference: str) -> int:
    """`approve` or `reject` one readout, by its reference."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{12}", reference):
        print(f"readouts: {reference} is not a readout's reference (2026-11-09-0123456789ab)", file=sys.stderr)
        return 2
    done = query("UPDATE readouts SET review = ?1 WHERE id = ?2 RETURNING id, key, review",
                 [REVIEW_STATES[action], reference])
    if not done:
        print(f"readouts: no readout {reference}", file=sys.stderr)
        return 1
    print(f"{done[0]['id']} ({done[0]['key']}): {done[0]['review']}")
    return 0


def subscriber(item: dict[str, Any]) -> str:
    noted = item.get("custom_metadata") or {}
    return "  ".join([noted.get("product", item["key"].split("/")[1]), noted.get("email", "?"),
                      noted.get("models", "") or "-", str(item.get("last_modified", ""))])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] in (["review"], ["approve"], ["reject"]):
        if (args[0] == "review" and len(args) > 2) or (args[0] != "review" and len(args) != 2):
            print(__doc__.strip(), file=sys.stderr)
            return 2
        try:
            return review(args[1] if len(args) == 2 else None) if args[0] == "review" else settle(*args)
        except cs.SettingsError as error:
            print(f"readouts: {error}", file=sys.stderr)
            return 1
    if args[:1] == ["notify"]:
        if len(args) > 2:
            print(__doc__.strip(), file=sys.stderr)
            return 2
        prefix, show, what = (f"notify/{args[1].upper()}/" if len(args) == 2 else "notify/"), subscriber, "addresses"
    elif len(args) > 1 or args[:1] in (["-h"], ["--help"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    else:
        prefix, show, what = (args[0] if args else "readouts/"), describe, "in " + cs.READOUTS_BUCKET
    count = 0
    try:
        for item in objects(prefix):
            if not args and not item["key"].endswith(".tar"):
                continue   # the marks of what is held
            print(show(item))
            count += 1
    except cs.SettingsError as error:
        print(f"readouts: {error}", file=sys.stderr)
        return 1
    print(f"{count} {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
