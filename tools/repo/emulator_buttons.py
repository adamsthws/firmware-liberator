"""The buttons and wall switches on an emulated device's board, pressed and let
go while its firmware runs, the same way in both emulators.

An emulator's `buttons` file names the input pins held down now, one number a
line: the Espressif emulator's in its container's run folder
(/run/autoflash-emu), the Beken emulator's in the device's state folder. Each
family's board.py follows it, looking every POLL seconds: a pin it names is
driven low, as a button or a wall switch closed to ground drives it, and one it
no longer names is let go, back to its idle level. Driven from outside the
chip, as on the board, a change raises the pin's interrupt as the firmware set
it, edge or level. A pin still named when the device starts again is held from
its start, as a button held while the device is plugged in.

A check presses them through `Buttons` (each board.py's `buttons()` makes one
for its emulator); a board follows the file through `Follower`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable

FILE = "buttons"
# How often a board looks at the file (seconds): a press must be held longer.
POLL = 0.1
# How long a press is held (seconds): a person's quick press, well inside
# every firmware's long press (Tasmota's and OpenBeken's take a second or more).
PRESS = 0.4


def pins_of(text: str) -> frozenset[int]:
    """The pins a buttons file names; anything else in it is left out."""
    return frozenset(int(word) for word in text.split() if word.isdigit())


def text_of(pins: Iterable[int]) -> str:
    return "".join(f"{pin}\n" for pin in sorted(set(pins)))


class Buttons:
    """An emulator's buttons file, as a check presses them: `read` gives its
    text ("" while there is none), `write` replaces it whole."""

    def __init__(self, read: Callable[[], str], write: Callable[[str], None]) -> None:
        self.read, self.write = read, write

    def held(self) -> frozenset[int]:
        return pins_of(self.read())

    def hold(self, *pins: int) -> None:
        self.write(text_of(self.held() | set(pins)))

    def release(self, *pins: int) -> None:
        self.write(text_of(self.held() - set(pins)))

    def press(self, pin: int, seconds: float = PRESS) -> None:
        """`pin` held for `seconds`, at least long enough for the board to see it, then let go."""
        self.hold(pin)
        time.sleep(max(seconds, 2 * POLL))
        self.release(pin)


class Follower:
    """The buttons file at `path` as a board follows it: the pins pressed and
    let go since it last looked."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.held: frozenset[int] = frozenset()
        self.looked: float | None = None

    def read(self) -> frozenset[int]:
        try:
            return pins_of(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return frozenset()

    def changes(self, now: float) -> tuple[frozenset[int], frozenset[int]]:
        """The pins pressed and the pins let go since the last look, by `now`
        (time.monotonic()); none until POLL has passed since it."""
        if self.looked is not None and now - self.looked < POLL:
            return frozenset(), frozenset()
        self.looked = now
        held = self.read()
        pressed, released = held - self.held, self.held - held
        self.held = held
        return pressed, released

    def follow(self, now: float, hold: Callable[[int], None], release: Callable[[int], None]) -> None:
        """Each pin pressed since the last look held, then each let go released,
        through the board's own `hold` and `release`."""
        pressed, released = self.changes(now)
        for pin in sorted(pressed):
            hold(pin)
        for pin in sorted(released):
            release(pin)
