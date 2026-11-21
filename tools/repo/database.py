#!/usr/bin/env python3
"""A question asked of the API's database, read-only.

    python3 tools/repo/database.py "SELECT ..."          the rows, as a table
    python3 tools/repo/database.py --json "SELECT ..."   the rows, a JSON object a line
    python3 tools/repo/database.py --tables              each table and its columns

One statement a run. It is read with the maintainer's CLOUDFLARE_DATABASE_TOKEN,
in the environment or tools/repo/.env.infra, which TOKENS in
tools/repo/cloudflare_settings.py allows D1 Read alone: Cloudflare refuses
it any write, so nothing asked here can change the database.
tools/repo/cloudflare_tokens.py database-read-only makes it, for the days
TOKENS gives it: run that again once it runs out. What the rows
hold came from people's computers: each value is shown with its control
characters escaped. Never from CI, whose logs are public.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.repo import cloudflare_settings as cs  # noqa: E402
from tools.repo import readouts  # noqa: E402

TABLES = ("SELECT m.name AS tables, group_concat(p.name, ', ') AS columns "
          "FROM sqlite_master m JOIN pragma_table_info(m.name) p "
          "WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite_%' AND m.name NOT LIKE '_cf_%' "
          "GROUP BY m.name ORDER BY m.name")


def table(rows: list[dict[str, Any]]) -> list[str]:
    """The rows as lines of columns lined up under their names."""
    if not rows:
        return ["(no rows)"]
    names = list(rows[0])
    cells = [names] + [["" if row.get(name) is None else readouts.shown(row.get(name)) for name in names]
                       for row in rows]
    widths = [max(len(line[i]) for line in cells) for i in range(len(names))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in cells]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cs.TOKEN_VARIABLE = cs.TOKENS["database-read-only"]["variable"]
    as_json = args[:1] == ["--json"]
    args = args[1:] if as_json else args
    if args == ["--tables"]:
        args = [TABLES]
    if len(args) != 1 or not args[0].strip():
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        rows = readouts.query(args[0])
    except cs.SettingsError as error:
        print(f"The database was not read: {error}", file=sys.stderr)
        return 1
    if as_json:
        for row in rows:
            print(json.dumps(row))   # its control characters escaped, as JSON's are
    else:
        print("\n".join(table(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
