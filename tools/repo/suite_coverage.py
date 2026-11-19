#!/usr/bin/env python3
"""The suites tried alike: each situation a suite tries is to be tried in
every cell - a conversion and a readout, on each chip family - so the
families, and conversions and readouts, cannot drift apart. Each suite gives
its own situations and reasons as a Suite: the attended scenarios
(attended_suite.SUITE) and the fault suites (fault_coverage.SUITE).

A suite's not_everywhere names a situation that cannot be in every cell,
with the cells it is for - "conversion", "readout", "espressif", "beken",
each narrowing - and why; its not_in one cell alone it cannot be tried in,
and why; its not_yet each cell a situation could be tried in and is not yet.
tests/repo/test_suite_coverage.py fails on a gap in none of them, and on one in
not_yet filled since, so a situation added in one cell asks for the others
and the list of what is left only shrinks.

    tools/repo/suite_coverage.py      each suite's cells each situation is tried in, and the gaps
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, NamedTuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

Cell = tuple[str, str]   # (what it runs: "conversion" or "readout", the chip family)
COMMANDS = ("conversion", "readout")
FAMILIES = ("espressif", "beken")


class Suite(NamedTuple):
    name: str
    tried: Callable[[], dict[str, set[Cell]]]   # the cells each situation is tried in
    not_everywhere: dict[str, tuple[tuple[str, ...], str]]
    not_in: dict[str, dict[Cell, str]] = {}
    not_yet: dict[str, tuple[Cell, ...]] = {}


def cells_for(within: tuple[str, ...] = ()) -> set[Cell]:
    """The cells a situation narrowed to `within` is for: every one for ()."""
    return {(command, family) for command in COMMANDS for family in FAMILIES
            if all(narrowing in (command, family) for narrowing in within)}


def gaps(suite: Suite, cells: dict[str, set[Cell]]) -> dict[str, set[Cell]]:
    """Each situation's cells it is for (not_everywhere, not_in) that try it not."""
    found = {}
    for name, tried in cells.items():
        missing = cells_for(suite.not_everywhere.get(name, ((), ""))[0]) - tried - set(suite.not_in.get(name, {}))
        if missing:
            found[name] = missing
    return found


def suites() -> list[Suite]:
    from tools.espressif.emulator import attended_suite
    from tools.repo import fault_coverage
    return [attended_suite.SUITE, fault_coverage.SUITE]


def listed(cells: set[Cell]) -> str:
    return ", ".join(f"{command} {family}" for command, family in sorted(cells))


def main() -> int:
    for suite in suites():
        cells = suite.tried()
        print(f"{suite.name}:")
        for name in sorted(cells):
            print(f"  {name:26} {listed(cells[name])}")
        left = gaps(suite, cells)
        print(f"\n  {sum(map(len, left.values()))} cells left to try (not_yet):")
        for name in sorted(left):
            print(f"  {name:26} {listed(left[name])}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
