#!/usr/bin/env python3
"""The readouts people have sent, in the R2 bucket the API Worker keeps them in.

    python3 tools/repo/readouts.py [prefix]        list them (prefix: readouts/)
    python3 tools/repo/readouts.py notify [PRODUCT]  who asked to be told when a
                                                    device's status changes

Each line is a readout's key, size, when it came in and what the Worker noted
of it: the tool, the product, whether it holds a whole read and a contact.
Under readouts/ only the readouts are listed, not the marks of what is held
or the day's counts beside them; a prefix given lists everything under it.

`notify` lists each address given for a product (eWeLink's product model),
with the firmware models it was given with and when: whom to write to when
that device's status changes (.github/readouts.md). An address is removed by
deleting its object (notify/<PRODUCT>/<hash>.json).
Fetch one with `npx wrangler r2 object get <bucket>/<key> --remote --file
readout.tar`, from website/. The token is CLOUDFLARE_API_TOKEN, as for
tools/repo/cloudflare_settings.py; reading the account's R2 is enough.
"""
from __future__ import annotations

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


def subscriber(item: dict[str, Any]) -> str:
    noted = item.get("custom_metadata") or {}
    return "  ".join([noted.get("product", item["key"].split("/")[1]), noted.get("email", "?"),
                      noted.get("models", "") or "-", str(item.get("last_modified", ""))])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
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
                continue   # the marks of what is held, and the day's counts
            print(show(item))
            count += 1
    except cs.SettingsError as error:
        print(f"readouts: {error}", file=sys.stderr)
        return 1
    print(f"{count} {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
