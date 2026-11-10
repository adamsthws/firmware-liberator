#!/usr/bin/env python3
"""The readouts people have sent, in the R2 bucket the API Worker keeps them in.

    python3 tools/repo/readouts.py [prefix]        list them (prefix: readouts/)
    python3 tools/repo/readouts.py notify [PRODUCT]  who asked to be told when a
                                                    device's status changes
    python3 tools/repo/readouts.py review [PRODUCT]  the readouts waiting for review
    python3 tools/repo/readouts.py approve ID        one taken as right
    python3 tools/repo/readouts.py reject ID         one taken as wrong: its place
                                                    goes to another sender's
    python3 tools/repo/readouts.py rotated TIME      the keys taken as right with
                                                    readouts from both sides of
                                                    SENDER_KEY's rotation at TIME
    python3 tools/repo/readouts.py changes [REF]     the devices whose status changed
                                                    since REF (the latest release's
                                                    tag), and whom to tell of each

Each line is a readout's key, size, when it came in and what the Worker noted
of it: the tool, the product, whether it holds a whole read and a contact.
Under readouts/ only the readouts are listed, not the marks of what is held
beside them; a prefix given lists everything under it.

`review` lists, by product, firmware model and version, each readout the
API has not taken as right: fewer than three of them, from three senders,
agree (.github/readouts.md). Each line is its reference, stage, when it came
in, its object and fingerprint; one whose fingerprint differs from the rest
of its key is marked ODD. `rotated` lists, the same way, each key taken as
right from readouts sent both before and after TIME (as `cloudflare_settings.py
rotate SENDER_KEY` printed it): two of its three could be one sender's, as a
new key makes every sender look new. The API's database is read with the maintainer's
token, as cloudflare_settings.py reads it (D1 is refused to CI's).

`notify` lists each address given for a product (eWeLink's product model),
with the firmware models it was given with and when: whom to write to when
that device's status changes (.github/readouts.md), and the link each email
to them carries for them to remove their address themselves. `changes` is the
same for each device whose status in devices/status.json differs from REF's:
run it when a release changes one. Never from CI, whose logs are public.
Fetch one with `npx wrangler r2 object get <bucket>/<key> --remote --file
readout.tar`, from website/. The token is CLOUDFLARE_API_TOKEN, as for
tools/repo/cloudflare_settings.py; reading the account's R2 is enough.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

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
    return "  ".join(shown(part) for part in [item["key"], f"{item.get('size', 0):>10}", item.get("last_modified", ""),
                     *(f"{key}={noted[key]}" for key in ("tool", "product", "read", "contact") if key in noted)])


def shown(value: Any) -> str:
    """`value`, from the store, as it is safe to print: anything but
    printable ASCII escaped, so nothing a sender wrote reaches the terminal
    as a control sequence."""
    return "".join(c if " " <= c <= "~" else f"\\x{ord(c):02x}" if ord(c) < 256 else f"\\u{ord(c):04x}"
                   for c in str(value))


def query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """The rows `sql` gives in the API's database."""
    zones = cs.api(f"/zones?name={cs.ZONE}")
    if not zones:
        raise cs.SettingsError(f"the token cannot see the zone {cs.ZONE}")
    path = f"/accounts/{zones[0]['account']['id']}/d1/database/{cs.bound_database_id()}/query"
    answer = cs.api(path, "POST", {"sql": sql, "params": params or []})
    return list((answer or [{}])[0].get("results") or [])


REVIEW_STATES = {"approve": "approved", "reject": "rejected"}
# The count of the bytes of readouts waiting for review (website/api/pending.js).
PENDING = "readouts:pending"


def review(product: str | None) -> int:
    """The readouts waiting for review, by key: each odd one marked."""
    rows = query("SELECT id, key, stage, fingerprint, object, received FROM readouts WHERE review = 'pending'"
                 + (" AND key LIKE ?1" if product else "") + " ORDER BY key, stage, received",
                 [f"{product.upper()}|%"] if product else [])
    keys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        keys.setdefault((row["key"], row["stage"]), []).append(row)
    for (key, stage), held in keys.items():
        print(f"{shown(key)} ({shown(stage)}): {len(held)} waiting")
        prints = [row["fingerprint"] for row in held]
        for row in held:
            odd = len(held) > 1 and prints.count(row["fingerprint"]) == 1 and len(set(prints)) > 1
            print(f"    {shown(row['id'])}  {shown(row['received'])}  {shown(row['object'])}  "
                  f"{shown(row['fingerprint'][:12])}" + ("  ODD" if odd else ""))
    print(f"{len(rows)} waiting for review")
    return 0


def rotated(when: str) -> int:
    """The keys taken as right from readouts on both sides of `when`."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", when):
        print(f"readouts: {when} is not a time as rotate prints it (2026-11-10T12:00:00Z)", file=sys.stderr)
        return 2
    rows = query("SELECT id, key, stage, fingerprint, object, received FROM readouts"
                 " WHERE review IN ('quorum', 'approved') ORDER BY key, stage, received", [])
    keys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        keys.setdefault((row["key"], row["stage"]), []).append(row)
    both = {found: kept for found, kept in keys.items()
            if min(row["received"] for row in kept) < when <= max(row["received"] for row in kept)}
    for (key, stage), kept in both.items():
        print(f"{shown(key)} ({shown(stage)}): {len(kept)} taken as right, sent both sides of {when}")
        for row in kept:
            side = "before" if row["received"] < when else "after"
            print(f"    {shown(row['id'])}  {shown(row['received'])}  {shown(row['object'])}  {side}")
    print(f"{len(both)} to look at again")
    return 0


def settle(action: str, reference: str) -> int:
    """`approve` or `reject` one readout, by its reference."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{12}", reference):
        print(f"readouts: {reference} is not a readout's reference (2026-11-09-0123456789ab)", file=sys.stderr)
        return 2
    before = query("SELECT review, bytes FROM readouts WHERE id = ?1", [reference])
    if not before:
        print(f"readouts: no readout {reference}", file=sys.stderr)
        return 1
    done = query("UPDATE readouts SET review = ?1 WHERE id = ?2 RETURNING id, key, review",
                 [REVIEW_STATES[action], reference])
    # Settled: no longer held to the room for readouts waiting for review.
    if before[0]["review"] == "pending":
        query("UPDATE counters SET count = max(count - 1, 0), bytes = max(bytes - ?1, 0) WHERE name = ?2",
              [before[0]["bytes"], PENDING])
    print(f"{shown(done[0]['id'])} ({shown(done[0]['key'])}): {shown(done[0]['review'])}")
    return 0


def subscriber(item: dict[str, Any]) -> str:
    noted = item.get("custom_metadata") or {}
    return "  ".join(shown(part) for part in [noted.get("product", item["key"].split("/")[1]),
                                              noted.get("email", "?"), noted.get("models", "") or "-",
                                              item.get("last_modified", ""),
                                              noted.get("forget", "(no removal link)")])


def subscribers(prefix: str = "notify/") -> Iterator[dict[str, Any]]:
    """Each address given, one a product: not the index each address has."""
    return (item for item in objects(prefix) if not item["key"].startswith("notify/_who/"))


def statuses(ref: str | None = None) -> dict[str, str]:
    """devices/status.json as it is, or as it was at `ref`."""
    if ref is None:
        return json.loads((ROOT / "devices/status.json").read_text(encoding="utf-8"))
    shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:devices/status.json"], capture_output=True,
                           text=True, check=False)
    return json.loads(shown.stdout) if shown.returncode == 0 else {}


def latest_release() -> str:
    found = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--abbrev=0", "--match", "v*"],
                           capture_output=True, text=True, check=False)
    if found.returncode != 0:
        raise cs.SettingsError("no release tag to compare with: give one")
    return found.stdout.strip()


def changes(ref: str | None) -> int:
    """The devices whose status changed since `ref`, each with whom to tell."""
    from tools.autoflash import kinds
    ref = ref or latest_release()
    before, now = statuses(ref), statuses()
    changed = [kind for kind in kinds.EVERY if before.get(kind.key) != now.get(kind.key)]
    if not changed:
        print(f"No device's status changed since {ref}.")
        return 0
    given = list(subscribers())
    told = 0
    for kind in changed:
        print(f"{kind.name}: {before.get(kind.key, 'not listed')} -> {now.get(kind.key)}")
        theirs = [item for item in given if kind.product_of(item["key"].split("/")[1])]
        for item in theirs:
            print("    " + subscriber(item))
        told += len(theirs)
        if not theirs:
            print("    nobody asked to be told")
    print(f"{len(changed)} changed since {ref}, {told} to tell")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] in (["review"], ["approve"], ["reject"], ["rotated"]):
        if (args[0] == "review" and len(args) > 2) or (args[0] != "review" and len(args) != 2):
            print(__doc__.strip(), file=sys.stderr)
            return 2
        try:
            if args[0] == "rotated":
                return rotated(args[1])
            return review(args[1] if len(args) == 2 else None) if args[0] == "review" else settle(*args)
        except cs.SettingsError as error:
            print(f"readouts: {error}", file=sys.stderr)
            return 1
    if args[:1] == ["changes"]:
        if len(args) > 2:
            print(__doc__.strip(), file=sys.stderr)
            return 2
        try:
            return changes(args[1] if len(args) == 2 else None)
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
            if item["key"].startswith("notify/_who/"):
                continue   # an address's index: its products and removal token
            print(show(item))
            count += 1
    except cs.SettingsError as error:
        print(f"readouts: {error}", file=sys.stderr)
        return 1
    print(f"{count} {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
