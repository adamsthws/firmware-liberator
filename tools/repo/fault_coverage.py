"""The fault suites' situations and reasons for tools/repo/suite_coverage.py: a
situation is a kind of fault, or a way a readout ends, tried in a cell - a
conversion and a readout on each chip family's suite, as the attended
scenarios are. Their tables say which cells a situation cannot be tried in
(NOT_EVERYWHERE, NOT_IN) and which it is not yet (NOT_YET), with why.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from tools.repo.suite_coverage import Cell, Suite

TASMOTA = "only the S60 runs Tasmota"
INSTALLER = ("the S60's installer, which no S61 route has: an S61 is sent its firmware through eWeLink, and its "
             "own network is ESPHome's (fallback)")
FIRST_STAGE = "a readout of a device off Sonoff's firmware reads its first stage alone, sending it nothing"
NOT_EVERYWHERE: dict[str, tuple[tuple[str, ...], str]] = {
    **{f"none-{ending}": (("readout",), "a readout ending without the device read whole: a conversion has none")
       for ending in ("alike", "enough", "many", "mixed", "first-stage", "unattended")},
    "cancel-switch": (("conversion",), "the other firmware chosen after Ctrl-C twice: a readout chooses none"),
    "ctrlc-countdown": (("conversion",), "Ctrl-C at the countdown a Ctrl-C is asked about at: a readout asks at its "
                                         "next question, never at a stage's end, and an unattended one - the fault "
                                         "suites' - asks none; the attended suite tries a readout's "
                                         "(readout-stopped-at-question-ctrl-c)"),
    "legacy": (("conversion",), "an earlier release's unfinished run taken up: a readout is never offered back to "
                                "finish (readout.py)"),
    "blips": (("conversion", "espressif"), "a Berry phase riding out the network by itself: Berry is Tasmota's, "
                                           f"and {TASMOTA}; an S60's readout reads through the bridge, which runs "
                                           "none (readout_espressif)"),
    "lossy-phase": (("conversion", "espressif"), "as blips"),
    "badimage-restart": (("conversion", "beken"), "the run restarting the device to send a refused image afresh, "
                                                  "past its own sends (runner.GO_BACK_LIMIT): an S60's refused image "
                                                  "ends its run - the installer's Tasmota at D1 - and a readout's "
                                                  "refused reader ends it on either family, the device on Sonoff's "
                                                  "firmware; the next sends it again (badimage-once)"),
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
    "ownbuild-replaced": (("conversion",), "a build of its own, an ESPHome builder's, which the tool leaves be "
                                           f"unless told to replace it: {FIRST_STAGE}, whosever the build"),
}

# A cell a situation cannot be tried in though the others it is for can, and
# why: as NOT_EVERYWHERE, for one cell alone.
NOT_IN: dict[str, dict[Cell, str]] = {
    "fallback": {("readout", "beken"): "a device's own network, raised as it loses the home one: an S61 read out "
                                       "runs Sonoff's firmware or the reading OpenBeken, neither of which raises one "
                                       "(only ESPHome does, at K2)"},
}

# Each cell a situation could be tried in and is not yet. Filling one takes it
# off; finding it cannot be moves it to NOT_EVERYWHERE, with why.
NOT_YET: dict[str, tuple[Cell, ...]] = {
    "legacy": (("conversion", "beken"),),
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


def tried() -> dict[str, set[Cell]]:
    return covered(suites())


SUITE = Suite("fault suites", tried, NOT_EVERYWHERE, NOT_IN, NOT_YET)
