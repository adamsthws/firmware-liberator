"""The work the project's API asks of what the tool sends it, so a flood
costs its sender: "<time>:<nonce>" such that the SHA-256 of
"<time>:<subject>:<nonce>" starts with `bits` zero bits.

The subject says what the work is for, so work done for one thing is good
for nothing else: a readout's is its body's SHA-256 (body_subject), a
report's its random ID (report_subject), made before the report exists so
the work can be done while the run goes on. The Worker checks it with
website/api/proof-of-work.js.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable


def body_subject(data: bytes) -> str:
    """What a readout's work is for: its body, by SHA-256."""
    return hashlib.sha256(data).hexdigest()


def report_subject(report_id: str) -> str:
    """What a report's work is for: its ID, never a body's 64 hex digits."""
    return f"report-{report_id}"


def solve(subject: str, bits: int, now: float | None = None,
          tick: Callable[[], None] | None = None) -> str:
    """The work for `subject`, as "<time>:<nonce>". About 2**bits tries on
    average, but luck decides: one in twenty takes three times as long.
    `tick` is called every quarter of a million tries, to show it is still
    going."""
    stamp = int(time.time() if now is None else now)
    prefix = hashlib.sha256(f"{stamp}:{subject}:".encode())
    target = 1 << (256 - bits)
    nonce = 0
    while True:
        attempt = prefix.copy()
        attempt.update(str(nonce).encode())
        if int.from_bytes(attempt.digest(), "big") < target:
            return f"{stamp}:{nonce}"
        nonce += 1
        if tick is not None and not nonce & 0x3FFFF:
            tick()
