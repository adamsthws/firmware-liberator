"""A fault suite's batch of worker threads, an emulator each, stopped as one.
A worker that dies of an error no scenario caught, or a computer that cannot go
on (its disk full: disk_space), stops every worker before its next scenario,
and the batch ends saying why with a non-zero exit. Before, a worker killed by
a full disk on 2026-11-19 went unnoticed, and the batch carried on with fewer.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable


class Workers:
    """The batch's workers: add() each, start() them, join() them, then end()."""

    def __init__(self, say: Callable[[str], object] = print):
        self.say = say
        self.stopping = threading.Event()
        self.why: str | None = None
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def stop(self, why: str) -> None:
        """No worker takes another scenario: those under way finish. The
        first reason is said at once, and again as the batch ends."""
        with self._lock:
            first = self.why is None
            if first:
                self.why = why
        self.stopping.set()
        if first:
            self.say(f"batch: stopping - {why}")

    def going(self) -> bool:
        """Whether a worker may take another scenario."""
        return not self.stopping.is_set()

    def add(self, target: Callable[..., Any], *args: Any) -> threading.Thread:
        """A worker running target(*args); one that raises stops the batch."""
        def guarded() -> None:
            try:
                target(*args)
            except BaseException as exc:  # noqa: BLE001 - any death, said and stopping the rest
                self.stop(f"a worker died: {exc!r}\n{traceback.format_exc().rstrip()}")
        thread = threading.Thread(target=guarded)
        self.threads.append(thread)
        return thread

    def join(self) -> None:
        for thread in self.threads:
            thread.join()

    def end(self) -> None:
        """Once the batch has ended: SystemExit (exit 1) saying why, if it stopped early."""
        if self.why is not None:
            raise SystemExit(f"batch stopped before its end: {self.why}")
