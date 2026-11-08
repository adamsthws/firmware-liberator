"""What both fault suites judge a run by beyond its steps being done: the
end-of-run checks the report of a converting run gives. A run whose steps all
passed can still end with a relay that did not switch, or a check that never
got to run, and say so only there (docs/testing.md#the-fault-suites)."""
from __future__ import annotations

import re

# As the run's log has them: the report's answer (tools/autoflash/report.py,
# checks_passed), and the line its check said of the relay.
CHECKS_PASSED = re.compile(r"^\[[\d:]+\] +Checks passed: +(.+?) *$", re.M)
RELAY = re.compile(r"^\[[\d:]+\] +Relay: +(.+?) *$", re.M)

# The answers a run ending on each firmware may give in the emulator. On
# Tasmota, ESPHome and OpenBeken the check needs only the firmware, which the
# emulator runs: it switches the relay, so anything but True is a failure -
# "Not run" there is a check never tried. Back on Sonoff's it switches the
# relay through eWeLink, the stub's or the real one, by the device id the plug's
# record keeps: "Not run" is a plug with none (one converted before it was
# kept), which the emulator cannot change. False is a failure everywhere: the
# relay did not switch, or could not be checked, as when eWeLink had not shown
# the plug online yet.
PASSING = {"tasmota": {"True"}, "esphome": {"True"}, "openbeken": {"True"}, "sonoff": {"True", "Not run"}}


def last_run(log: str) -> str:
    """The last run in a run log: one log holds every run carried on in it."""
    return log[log.rfind("=== run started"):] if "=== run started" in log else log


def checks_problem(log: str, firmware: str) -> str | None:
    """What the end-of-run checks of the last run in `log` said, when they did
    not pass for a plug on `firmware`: "Checks passed: False; Relay: Did not
    switch". None when they passed, or the run gave no report (a plug already
    on the firmware asked for has nothing to check)."""
    run = last_run(log)
    answers = CHECKS_PASSED.findall(run)
    if not answers or answers[-1] in PASSING.get(firmware, {"True", "Not run"}):
        return None
    relay = RELAY.findall(run)
    return f"Checks passed: {answers[-1]}; Relay: {relay[-1] if relay else 'not said'}"
