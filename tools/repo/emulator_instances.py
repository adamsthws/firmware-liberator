"""Which emulator instances already run on this computer, so a suite keeps
clear of another's: each suite is given its numbers with --first-instance,
and one asked to start on a number in use refuses rather than restart it.
Starting a Beken instance afresh stops everything on that instance's network,
and an Espressif one already running refuses to start (emu.sh), so a batch
sharing another's numbers fails it or itself."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

# Each family's containers for instance N, as emu.sh and beken-emu.sh
# --instance N name them: the device's, and its API or computer beside it.
NAMES = {
    "espressif": re.compile(r"^autoflash-emu-(\d+)(?:-api)?$"),
    "beken": re.compile(r"^autoflash-beken-emu-(?:computer-)?(\d+)(?:-api)?$"),
}

# The highest instance emu.sh, beken-emu.sh and unattended-test.sh take
# (--instance 1-99): a suite given more has every run refused with usage,
# each scenario then NOT-FIRED in seconds.
LAST = 99


# What gave a suite its first instance before --first-instance: set, it would
# now be passed over, and the batch run on instances from 1, another's perhaps.
OLD_SETTINGS = ("AUTOFLASH_EMU_FIRST_INSTANCE", "AUTOFLASH_BEKEN_EMU_FIRST_INSTANCE", "AUTOFLASH_EMU_PREP_INSTANCE")


def running(family: str) -> set[int]:
    """The instances of `family` with a container running here; none where
    Docker does not answer."""
    try:
        names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True,
                               timeout=30, check=False).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {int(found[1]) for found in map(NAMES[family].match, names) if found}


def first_free(taken: set[int], count: int) -> int | None:
    """The first number from which `count` instances in a row are free, or
    None where no such run fits below LAST."""
    first = 1
    while any(n in taken for n in range(first, first + count)):
        first += 1
    return first if first + count - 1 <= LAST else None


def refuse_taken(family: str, numbers: Iterable[int]) -> None:
    """SystemExit, naming a free first instance, where any of `numbers` is
    already running or above LAST - or where an OLD_SETTINGS variable is set, which no
    longer chooses them."""
    old = [name for name in OLD_SETTINGS if name in os.environ]
    if old:
        raise SystemExit(f"{', '.join(old)} is no longer read: unset it and give --first-instance "
                         f"{os.environ[old[0]]} instead")
    wanted = sorted(set(numbers))
    outside = [n for n in wanted if not 1 <= n <= LAST]
    if outside:
        raise SystemExit(f"{family} emulator instance{'s' if len(outside) > 1 else ''} "
                         f"{', '.join(map(str, outside))} out of range: the emulator takes 1-{LAST}, "
                         f"so give a --first-instance at most {LAST - len(wanted) + 1} for {len(wanted)}")
    taken = running(family)
    clash = [n for n in wanted if n in taken]
    if clash:
        free = first_free(taken, len(wanted))
        instead = f"give --first-instance {free} to keep clear of it, or stop it" if free \
            else "stop it, or run fewer at once"
        raise SystemExit(f"{family} emulator instance{'s' if len(clash) > 1 else ''} "
                         f"{', '.join(map(str, clash))} already running here: {instead}")
