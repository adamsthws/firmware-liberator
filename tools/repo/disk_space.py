"""Whether the disk the fault suites keep their results on has room for another
scenario. On 2026-11-19 another job's build filled a test machine's disk during
a batch: writes failed part way, workers died, and a results row was left half
written. So both fault suites wait, before starting a scenario, while the disk
is short; and stop the batch, saying so, if it stays short.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable

# Short of space under this many bytes free. A scenario writes, at most: its
# emulator's state made afresh (the emulated computer's copy of the checkout,
# 40-60 MB; the device's flash, 4-16 MB), the logs and evidence kept in its
# folder (up to 5 MB), and the containers' own writes. About 100 MB, then, for
# each emulator; a gigabyte is every emulator of an 8-core machine starting its
# next scenario, with room to spare.
FLOOR = 1 << 30
# The longest a scenario waits for room (seconds) before the batch stops: a disk
# filled by something else that does not go away will not empty itself.
LONGEST = 1800.0
POLL = 10.0


def free(path: Path) -> int:
    """Bytes free on the disk holding `path`, or the nearest folder above it
    that is there."""
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


def short(path: Path, floor: int = FLOOR) -> bool:
    return free(path) < floor


def wait_for_room(path: Path, say: Callable[[str], object] = print,
                  halt: threading.Event | None = None) -> bool:
    """Waits while the disk holding `path` is short, up to LONGEST, saying so
    once; whether there is room now (False: still short then, or `halt` set
    while waiting)."""
    if not short(path):
        return True
    started = time.monotonic()
    say(f"disk_space: under {FLOOR >> 20} MB free on the disk holding {path}; "
        "the next scenario waits for room")
    halt = halt or threading.Event()
    while short(path):
        if halt.is_set() or time.monotonic() - started >= LONGEST:
            return False
        halt.wait(POLL)
    say("disk_space: room again; scenarios go on")
    return True


def stop_reason(path: Path) -> str:
    """What a batch stopping for want of room says."""
    return (f"under {FLOOR >> 20} MB free on the disk holding {path} for {LONGEST / 60:.0f} minutes "
            f"({free(path) >> 20} MB now): free some space and run the batch again - it carries on "
            "from the scenarios not yet passed")
