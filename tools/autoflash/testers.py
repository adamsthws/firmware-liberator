"""A kind whose status is "Testers wanted" (devices/status.json): converted in
the emulator, or added from its official file and a published pin map, but no
real one reported yet.  The go-ahead before a run's first
write says so, the same words for every such kind, and the run's report ends
by saying where its log is.  A proven kind's go-ahead says nothing of it.
"""

from __future__ import annotations

from tools.autoflash import kinds, output

# Where the log is, for a run with none open.
LOG_ELSEWHERE = "run.log, in this run's folder under captures/"


def wanted(kind: kinds.Kind) -> bool:
    """Whether a run on this kind asks for testers."""
    return kind.status is kinds.Status.TESTERS_WANTED


def notice(kind: kinds.Kind) -> list[str]:
    """What the go-ahead before the first write says of a kind wanting testers."""
    if not wanted(kind):
        return []
    return [
        "",
        "The conversion for this particular device has not been proven on a real",
        "one yet. This run's log:",
        str(output.log_path() or LOG_ELSEWHERE),
    ]


def show_log(kind: kinds.Kind) -> None:
    """With the run's report: where its log is, for a kind still wanting
    testers, whose testers may want to look."""
    if wanted(kind):
        output.tell("")
        output.tell("This run's log:")
        output.tell(f"    {output.log_path() or LOG_ELSEWHERE}")
