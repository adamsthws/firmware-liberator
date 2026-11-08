"""How long each fault-suite scenario took when it last ran, so a batch can
start the longest first.

A batch ends when its last scenario does: one of half an hour started near the
end keeps a machine going alone while the others are done. Started first, the
long ones run beside the short ones, and the machines' end times are close
enough to share out again.

Taken from every results file a machine has kept, one to a suite's folder
(`<root>/<suite>/results.jsonl`; the Beken suite's, in beken/, are a level
further down, so each family reads its own): a row's own `seconds`, or for a
row without (the Espressif suite's before it recorded them) the time since the
row before it from the same emulator instance, whose worker runs its scenarios
one after another.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

# A gap between two rows of one instance longer than this is a pause between
# batches, not a scenario (seconds).
LONGEST = 2 * 3600


def when(row: dict[str, Any]) -> dt.datetime | None:
    stamp = row.get("finished") or row.get("at")
    try:
        return dt.datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


def times(root: Path) -> dict[str, float]:
    """Each scenario id in the suites' folders in `root`, and how long it took
    the last time it ran."""
    found: dict[str, tuple[dt.datetime, float]] = {}
    for path in sorted(Path(root).glob("*/results.jsonl")):
        previous: dict[Any, dt.datetime] = {}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or "id" not in row:
                continue
            finished, instance = when(row), row.get("instance")
            seconds = row.get("seconds")
            if seconds is None and finished and instance in previous:
                gap = (finished - previous[instance]).total_seconds()
                seconds = gap if 0 < gap < LONGEST else None
            if finished and instance is not None:
                previous[instance] = finished
            if isinstance(seconds, (int, float)) and seconds > 0 and finished \
                    and (row["id"] not in found or found[row["id"]][0] <= finished):
                found[row["id"]] = (finished, float(seconds))
    return {sid: seconds for sid, (_, seconds) in found.items()}


def fault_seconds(scenario: dict[str, Any]) -> float:
    """How long a scenario's fault lasts, where its length is its argument
    (an outage of 180 s): what one never run takes beyond the usual."""
    arg = scenario.get("arg")
    return float(arg) if isinstance(arg, (int, float)) else 0.0


def longest_first(scenarios: Iterable[dict[str, Any]], took: dict[str, float],
                  estimate: Callable[[dict[str, Any]], float] = fault_seconds) -> list[dict[str, Any]]:
    """`scenarios` in the order to start them: by how long each took last, the
    longest first; one never run, as the typical one and its fault's length.
    Those that took as long keep their order."""
    typical = statistics.median(took.values()) if took else 0.0
    return sorted(scenarios, key=lambda sc: -took.get(sc["id"], typical + estimate(sc)))
