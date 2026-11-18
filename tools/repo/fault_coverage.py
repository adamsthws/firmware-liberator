#!/usr/bin/env python3
"""The fault suites tried alike: each situation one tries - a kind of fault, or
a way a readout ends - is to be tried in every cell, a conversion and a
readout on each chip family's suite, as the attended suite's scenarios are
(attended_suite.coverage_gaps). NOT_EVERYWHERE names a situation that cannot
be, with the cells it is for and why; NOT_YET, each cell a situation could be
tried in and is not yet. tests/repo/test_fault_coverage.py fails on a gap in
neither, and on one in NOT_YET filled since - so a fault added to one suite
asks for the other, and the list of what is left only shrinks.

    tools/repo/fault_coverage.py      the cells each situation is tried in, and the gaps
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

Cell = tuple[str, str]   # (what it runs: "conversion" or "readout", the chip family)
COMMANDS = ("conversion", "readout")
FAMILIES = ("espressif", "beken")

TASMOTA = "only the S60 runs Tasmota"
INSTALLER = ("the S60's installer, which no S61 route has: an S61 is sent its firmware through eWeLink, and its "
             "own network is ESPHome's (fallback)")
FIRST_STAGE = "a readout of a device off Sonoff's firmware reads its first stage alone, sending it nothing"
NOT_EVERYWHERE: dict[str, tuple[tuple[str, ...], str]] = {
    **{f"none-{ending}": (("readout",), "a readout ending without the device read whole: a conversion has none")
       for ending in ("alike", "enough", "many", "mixed", "first-stage", "unattended")},
    "cancel-switch": (("conversion",), "the other firmware chosen after Ctrl-C twice: a readout chooses none"),
    "legacy": (("conversion",), "an earlier release's unfinished run taken up: a readout is never offered back to "
                                "finish (readout.py)"),
    "blips": (("espressif",), "a Berry phase riding out the network by itself: Berry is Tasmota's, and " + TASMOTA),
    "lossy-phase": (("espressif",), "as blips"),
    "bootloop-fallback": (("conversion", "espressif"), f"Tasmota's boot-loop module: {TASMOTA}; {FIRST_STAGE}"),
    "lostapp-factory": (("conversion", "espressif"), f"Tasmota's own safeboot: {TASMOTA}; {FIRST_STAGE}"),
    "lostapp-switched": (("conversion", "espressif"), "as lostapp-factory"),
    "notemplate-generic": (("conversion", "espressif"), f"a Tasmota without its template: {TASMOTA}; {FIRST_STAGE}"),
    "nohook-cut": (("conversion", "espressif"), f"F4's Berry driver, Tasmota's: {TASMOTA}"),
    "nohook-run": (("conversion", "espressif"), "as nohook-cut"),
    "seven-times": (("conversion", "espressif"), f"seven power cuts erase a Tasmota's settings: {TASMOTA}"),
    "seven-advised": (("conversion", "espressif"), "as seven-times"),
    "ownnet-cut": (("conversion", "espressif"), INSTALLER),
    "ownnet-dropsetup": (("conversion", "espressif"), INSTALLER),
    "ownnet-dropupload": (("conversion", "espressif"), INSTALLER),
    "lostanswer-once": (("conversion", "espressif"), INSTALLER),
    "safemode-ten": (("conversion",), f"ESPHome's safe mode, on a converted device: {FIRST_STAGE}"),
}

# Each cell a situation could be tried in and is not yet. Filling one takes it
# off; finding it cannot be moves it to NOT_EVERYWHERE, with why.
NOT_YET: dict[str, tuple[Cell, ...]] = {
    "badimage-once": (("conversion", "espressif"), ("readout", "espressif"), ("readout", "beken")),
    "badimage-restart": (("conversion", "espressif"), ("readout", "espressif"), ("readout", "beken")),
    "blips": (("readout", "espressif"),),
    "cancel-continue": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "cancel-new": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "cancel-switch": (("conversion", "beken"),),
    "corrupt-once": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "ctrlc-apart": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "ctrlc-countdown": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "ctrlc-once": (("readout", "espressif"), ("readout", "beken")),
    "diskfull-once": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "double-deviceoff": (("readout", "espressif"), ("readout", "beken")),
    "double-outage": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "fallback": (("readout", "espressif"), ("readout", "beken")),
    "hangup-continue": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "legacy": (("conversion", "beken"),),
    "lossy-phase": (("readout", "espressif"),),
    "pcoff": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "readdress-new": (("conversion", "espressif"), ("readout", "espressif"), ("readout", "beken")),
    "safemode-ten": (("conversion", "beken"),),
    "second-run": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "sleep": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "token-login": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
    "weak": (("conversion", "beken"), ("readout", "espressif"), ("readout", "beken")),
}


def situation(scenario: dict[str, Any]) -> str:
    """What a scenario tries: its fault, and the kind of it its argument names
    where that is a word (cancel-new, none-mixed) - not a length, a release
    (legacy's), or a fault's own length (double's outage-30)."""
    arg = scenario["arg"]
    if not isinstance(arg, str) or arg.startswith("v"):
        return scenario["fault"]
    return f"{scenario['fault']}-{re.sub(r'-[0-9]+$', '', arg)}"


def cell(family: str, scenario: dict[str, Any]) -> Cell:
    return ("readout" if "readout" in scenario["route"].split(">", 1)[-1] else "conversion", family)


def suites() -> dict[str, Iterable[dict[str, Any]]]:
    """Every scenario of each family's suite, as its `list` gives them."""
    from tools.beken.emulator import fault_suite as beken
    from tools.espressif.emulator import fault_suite as espressif
    return {"espressif": espressif.scenarios(), "beken": beken.scenarios()}


def covered(found: dict[str, Iterable[dict[str, Any]]]) -> dict[str, set[Cell]]:
    """The cells each situation is tried in."""
    cells: dict[str, set[Cell]] = {}
    for family, scenarios in found.items():
        for scenario in scenarios:
            cells.setdefault(situation(scenario), set()).add(cell(family, scenario))
    return cells


def gaps(cells: dict[str, set[Cell]]) -> dict[str, set[Cell]]:
    """Each situation's cells it is for (NOT_EVERYWHERE) that try it not."""
    every = {(command, family) for command in COMMANDS for family in FAMILIES}
    found = {}
    for name, tried in cells.items():
        within = NOT_EVERYWHERE.get(name, ((), ""))[0]
        missing = {where for where in every if all(narrowing in where for narrowing in within)} - tried
        if missing:
            found[name] = missing
    return found


def main() -> int:
    cells = covered(suites())
    for name in sorted(cells):
        print(f"{name:20} {', '.join(f'{c} {f}' for c, f in sorted(cells[name]))}")
    left = gaps(cells)
    print(f"\n{sum(map(len, left.values()))} cells left to try (NOT_YET):")
    for name in sorted(left):
        print(f"{name:20} {', '.join(f'{c} {f}' for c, f in sorted(left[name]))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
