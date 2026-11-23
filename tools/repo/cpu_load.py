"""Whether this computer has the processor time to start another emulated
device. One started on a machine already overloaded has its own connections time
out with nothing wrong: on 2026-11-08 unit tests run beside a batch took the
laptop to a load of 28 on 6 cores, and fresh devices never reached eWeLink. So
both fault suites wait, before starting a scenario, while the machine is that
busy; what is running already carries on.

How many emulators a suite runs at once is counted in the computer's cores
they need (EMULATOR_CORES): emulators_at_once makes a suite's own default,
and cores_short warns where more are asked for.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable

# Overloaded above this many runnable tasks a core, over the last minute. A
# batch with an emulator on each core keeps the load near one a core: the line
# is well above that, and well under what starved the devices.
PER_CORE = 1.5
# The longest a scenario is held back (seconds): load from something else that
# does not go away must not stop the batch for good.
LONGEST = 1800.0
POLL = 10.0

# The computer's cores one emulator keeps busy, by its chip as kinds.Kind.chip
# names it; one for a chip not here (an ESP32-C3, a BK7238). QEMU runs a
# classic ESP32's two cores on a thread each (MTTCG, its default for Xtensa),
# so its emulator takes two. With one classic ESP32 a core, an emulated Tasmota
# answered commands seconds apart and missed CSE7761 reads it times across its
# two cores; with one for every two cores it did not
# (tools/espressif/emulator/README.md, "When Tasmota misses the CSE7761").
EMULATOR_CORES = {"esp32": 2}


def computer_cores() -> int:
    return os.cpu_count() or 1


def emulator_cores(chip: str) -> int:
    """The computer's cores an emulator of `chip` needs (EMULATOR_CORES)."""
    return EMULATOR_CORES.get(chip, 1)


def emulators_at_once(chip: str, most: int, cores: int | None = None) -> int:
    """How many emulators of `chip` the computer's cores (`cores`, else this
    computer's) hold at once: at most `most`, and at least one."""
    return max(1, min(most, (cores or computer_cores()) // emulator_cores(chip)))


def cores_short(chips: Iterable[str], cores: int | None = None) -> str | None:
    """A warning where emulators of `chips`, one each and all at once, need
    more cores than the computer has (`cores`, else this computer's); None
    where they fit. A count the person asked for is kept: this only says so."""
    chips = list(chips)
    need = sum(map(emulator_cores, chips))
    have = cores or computer_cores()
    if need <= have:
        return None
    each = "".join(f", {emulator_cores(chip)} for each {chip}" for chip in sorted(set(chips))
                   if emulator_cores(chip) > 1)
    return (f"{len(chips)} emulators at once need {need} cores{each}, and this computer has {have}: "
            "runs may fail with nothing wrong (docs/emulator.md#several-at-once)")


def overloaded(cores: int | None = None) -> bool:
    """Whether the last minute's load is above PER_CORE for each core."""
    try:
        load = os.getloadavg()[0]
    except OSError:   # not kept here (Windows)
        return False
    return load > PER_CORE * (cores or computer_cores())


def wait_for_room(say: Callable[[str], object] = print) -> float:
    """Waits while the machine is overloaded, up to LONGEST, saying so once;
    how long it waited (seconds)."""
    started = time.monotonic()
    if not overloaded():
        return 0.0
    say(f"cpu_load: the load is above {PER_CORE:g} a core; the next scenario waits for it to fall")
    while overloaded():
        if time.monotonic() - started >= LONGEST:
            say(f"cpu_load: still overloaded after {LONGEST / 60:.0f} minutes; starting the next scenario anyway")
            break
        time.sleep(POLL)
    return time.monotonic() - started
