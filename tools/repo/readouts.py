#!/usr/bin/env python3
"""The readouts people have sent, in the R2 bucket the API Worker keeps them in.

    python3 tools/repo/readouts.py [prefix]    list them (prefix: readouts/)

Each line is a readout's key, size, when it came in and what the Worker noted
of it: the tool, the product, whether it holds a whole read and a contact.
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


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1 or args[:1] in (["-h"], ["--help"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    count = 0
    try:
        for item in objects(args[0] if args else "readouts/"):
            print(describe(item))
            count += 1
    except cs.SettingsError as error:
        print(f"readouts: {error}", file=sys.stderr)
        return 1
    print(f"{count} in {cs.READOUTS_BUCKET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
