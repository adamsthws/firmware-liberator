"""Where a fault-suite batch has got to: from one or more results files - one
a machine, copied here or reached however suits - each scenario's latest
verdict counted, those not passed named, and how many of the batch's
scenarios are left, with about how long they take at the pace the results
show (`fault_suite.py status`, both families alike).

    fault_suite.py status [PATH ...] [--ids FILE]

PATH: a results.jsonl, or a folder holding one (a suite's folder, or a copy
of it); none, the one this computer's suite wrote last. FILE: the batch's
scenario ids, one a line (as `list` writes them); without, every scenario.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tools.repo import fault_verdicts, scenario_times

RESULTS = "results.jsonl"
# How far back from a results file's newest row its pace is measured
# (seconds): long enough for many scenarios to end, short enough to follow a
# machine slowed down, or sped up, part way.
RECENT = 3600


def results_file(path: Path) -> Path:
    """The results file `path` names: itself, or the one in the folder."""
    found = path / RESULTS if path.is_dir() else path
    if not found.is_file():
        raise ValueError(f"no {RESULTS} at {path}")
    return found


def newest(root: Path) -> list[Path]:
    """The results file a suite wrote last in its folders below `root`
    (`<root>/<suite>/results.jsonl`, as scenario_times reads them): none
    where there is none."""
    found = sorted(Path(root).glob(f"*/{RESULTS}"), key=lambda path: path.stat().st_mtime)
    return found[-1:]


def rows_of(path: Path) -> list[dict[str, Any]]:
    """A results file's rows in the order written, each with its verdict
    (fault_verdicts.rows) - those with a time."""
    return [row for row in fault_verdicts.rows(path) if scenario_times.when(row)]


def this_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows a results file's latest batch wrote: those after its last
    pause longer than a scenario takes (scenario_times.LONGEST), as
    scenario_times tells a pause between batches from a scenario. A batch
    started sooner after another on that machine is taken as one with it."""
    start = 0
    for at in range(1, len(rows)):
        if (scenario_times.when(rows[at]) - scenario_times.when(rows[at - 1])).total_seconds() > scenario_times.LONGEST:
            start = at
    return rows[start:]


def pace(rows: list[dict[str, Any]]) -> float | None:
    """Scenarios an hour one results file's machine has ended lately: those
    in its last RECENT seconds, between the first and the last of them. None
    for fewer than two."""
    times = sorted(scenario_times.when(row) for row in rows)
    recent = [at for at in times if (times[-1] - at).total_seconds() <= RECENT] if times else []
    span = (recent[-1] - recent[0]).total_seconds() if len(recent) > 1 else 0
    return (len(recent) - 1) * 3600 / span if span > 0 else None


def how_long(hours: float) -> str:
    """Hours said as a person reads them: minutes under an hour."""
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.1f} h"


def status(paths: Iterable[Path], wanted: list[str]) -> list[str]:
    """What to say of the results in `paths` for a batch of the scenarios
    `wanted`: each verdict's count, the scenarios not passed, and how many of
    the batch's are left - neither done (fault_verdicts.DONE) nor run in its
    latest batch on any machine - with about how long they take at the
    machines' recent pace together."""
    every = [rows_of(path) for path in paths]
    latest: dict[str, dict[str, Any]] = {}
    for row in sorted((row for rows in every for row in rows), key=scenario_times.when):
        latest[row["id"]] = row
    counts = Counter(row["verdict"] for row in latest.values())
    lines = [f"{len(latest)} scenarios in {len(every)} results file{'s' if len(every) != 1 else ''}"]
    lines += [f"  {verdict:16} {count:5}" for verdict, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    failed = sorted((row["verdict"], sid) for sid, row in latest.items() if row["verdict"] != "PASS")
    if failed:
        lines.append("Not passed:")
        lines += [f"  {verdict:16} {sid}" for verdict, sid in failed]
    ran = {row["id"] for rows in every for row in this_batch(rows)}
    left = [sid for sid in wanted if sid not in ran
            and latest.get(sid, {}).get("verdict") not in fault_verdicts.DONE]
    paces = [found for found in map(pace, (this_batch(rows) for rows in every if rows)) if found]
    hourly = sum(paces)
    when = (f", about {how_long(len(left) / hourly)} at the recent pace ({hourly:.0f} an hour)" if hourly and left
            else ", the pace not known yet" if left else "")
    lines.append(f"Left: {len(left)} of the batch's {len(wanted)}{when}")
    return lines


def main(argv: list[str], every: Iterable[dict[str, Any]], root: Path, prog: str) -> int:
    """`fault_suite.py status`, for a suite whose scenarios are `every` and
    whose folders are below `root`."""
    parser = argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="*", type=Path, metavar="PATH",
                        help="a results.jsonl, or a folder holding one: one a machine (default: the one "
                             "this computer's suite wrote last)")
    parser.add_argument("--ids", metavar="FILE", help="the batch's scenario ids, one a line (default: every scenario)")
    args = parser.parse_args(argv)
    try:
        paths = [results_file(path) for path in args.results] or newest(root)
    except ValueError as exc:
        parser.error(str(exc))
    if not paths:
        parser.error(f"no results here (below {root}): name a results.jsonl or its folder")
    wanted = scenario_times.read_ids(args.ids) if args.ids else [sc["id"] for sc in every]
    for line in status(paths, wanted):
        print(line)
    return 0

