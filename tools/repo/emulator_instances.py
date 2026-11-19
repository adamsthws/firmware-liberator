#!/usr/bin/env python3
"""Which emulator instances already run on this computer, so a suite keeps
clear of another's: each suite is given its numbers with --first-instance,
and one asked to start on a number in use refuses rather than restart it.
Starting a Beken instance afresh stops everything on that instance's network,
and an Espressif one already running refuses to start (emu.sh), so a batch
sharing another's numbers fails it or itself.

A suite also claims its numbers for as long as it runs (claim), as its
emulators are not running all that time: a suite that restarts its
emulator between scenarios leaves a gap Docker cannot show. Run as a
script, it lists the claims on this computer - each suite's number, its
process and its checkout - so a suite started detached is stopped by its
own process alone:

    python3 tools/repo/emulator_instances.py"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import IO

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


# Where suites claim their numbers: one lock file each, for every checkout of
# this user on this computer alike, as Docker's containers are.
LOCKS = Path.home() / ".local/share/autoflash-emu/instances"

# The checkout a claim is made from, written in it to tell whose it is.
CHECKOUT = Path(__file__).resolve().parents[2]

# The claims this process holds, by family and number: open, as closing one
# lets it go. The system lets them all go as the process ends, however.
HELD: dict[tuple[str, int], IO[str]] = {}


def lock_path(family: str, number: int) -> Path:
    return LOCKS / f"{family}-{number}.lock"


def claim(family: str, numbers: Iterable[int]) -> list[int]:
    """Claim `numbers` for this process, as long as it runs: [] once each is
    held, else those another process holds (and none is claimed). None is
    made where AUTOFLASH_EMU_NO_CLAIMS=1: the tests (tests/no_network.py),
    which run side by side and touch nothing computer-wide."""
    if os.environ.get("AUTOFLASH_EMU_NO_CLAIMS") == "1":
        return []
    LOCKS.mkdir(parents=True, exist_ok=True)
    taken, got = [], []
    for number in sorted(set(numbers)):
        if (family, number) in HELD:
            continue
        handle = open(lock_path(family, number), "a+", encoding="utf-8")  # noqa: SIM115 - held open
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            taken.append(number)
            continue
        got.append((number, handle))
    if taken:
        for _, handle in got:
            handle.close()
        return taken
    for number, handle in got:
        handle.truncate(0)
        handle.write(f"{os.getpid()} {CHECKOUT} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        handle.flush()
        HELD[(family, number)] = handle
    return []


def claims() -> list[str]:
    """Each claim held on this computer: family and number, the process
    holding it, its checkout, and since when."""
    found = []
    for path in sorted(LOCKS.glob("*.lock")) if LOCKS.is_dir() else []:
        with open(path, encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:   # held: what its holder wrote
                found.append(f"{path.stem}  {handle.read().strip()}")
                continue
            fcntl.flock(handle, fcntl.LOCK_UN)
    return found


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
    claimed = claim(family, wanted)
    if claimed:
        free = first_free(taken | claimed_elsewhere(family), len(wanted))
        instead = f"give --first-instance {free} to keep clear of it" if free else "run fewer at once"
        raise SystemExit(f"{family} emulator instance{'s' if len(claimed) > 1 else ''} "
                         f"{', '.join(map(str, claimed))} claimed by another suite here "
                         f"(python3 tools/repo/emulator_instances.py lists them): {instead}")


def add_first_instance(parser: argparse.ArgumentParser, default: int = 1, commands: str = "") -> None:
    """--first-instance N, as every suite takes it: the first emulator
    instance it uses. `commands`: those of the suite's that take it, where it
    has others."""
    parser.add_argument("--first-instance", type=int, default=default, metavar="N",
                        help=f"{commands + ': ' if commands else ''}the first emulator instance used, "
                             f"one more for each emulator (default {default})")


def take(family: str, first: int, count: int = 1) -> range:
    """The `count` instances of `family` from `first` on, claimed for this
    process: SystemExit where one is not free (refuse_taken)."""
    numbers = range(first, first + count)
    refuse_taken(family, numbers)
    return numbers


def claimed_elsewhere(family: str) -> set[int]:
    """The numbers of `family` another process holds."""
    return {int(line.split()[0].rsplit("-", 1)[1]) for line in claims()
            if line.split()[0].rsplit("-", 1)[0] == family}


if __name__ == "__main__":
    print("\n".join(claims()) or "No emulator instance is claimed on this computer.")
