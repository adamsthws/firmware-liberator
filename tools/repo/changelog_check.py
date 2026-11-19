#!/usr/bin/env python3
"""Warn when the tool changes and CHANGELOG.md does not: what a person sees
the tool do differently goes into the coming release's section. A warning,
never a stop - a refactor or a test's own fix changes the tool unseen - so a
commit that is one says so with a trailer, and is not named again:

    Changelog: none

    python3 tools/repo/changelog_check.py push        what a push sends (the pre-push hook, from its stdin)
    python3 tools/repo/changelog_check.py [A..B]      a range, origin/main..HEAD if none
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.repo import leak_scan  # noqa: E402

TOOL = "tools/autoflash/"   # what a person runs; its changes are theirs to read of
CHANGELOG = "CHANGELOG.md"
UNSEEN = re.compile(r"^Changelog:\s*none\s*$", re.MULTILINE | re.IGNORECASE)


def paths(commit: str) -> list[str]:
    return leak_scan.git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit).decode().split("\n")


def unlisted(commits: list[str]) -> list[str]:
    """Each commit changing the tool, no trailer saying it changes nothing
    seen, where none of the commits changes the changelog: [] for none."""
    changed = {commit: paths(commit) for commit in commits}
    if any(CHANGELOG in files for files in changed.values()):
        return []
    return [commit for commit, files in changed.items()
            if any(path.startswith(TOOL) for path in files)
            and not UNSEEN.search(leak_scan.git("show", "-s", "--format=%B", commit).decode())]


def warn(commits: list[str]) -> None:
    for commit in commits:
        print(f"  {leak_scan.git('show', '-s', '--format=%h %s', commit).decode().strip()}", file=sys.stderr)
    print(f"{len(commits)} commit(s) above change {TOOL} and {CHANGELOG} is not changed: add what a person sees "
          f"to its coming release's section, or 'Changelog: none' to a commit that changes nothing seen "
          f"(tools/repo/changelog_check.py).", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["push"]:
        commits = leak_scan.pushed(sys.stdin.read().splitlines())
    elif len(args) <= 1 and not (args and args[0].startswith("-")):
        commits = leak_scan.git("rev-list", *(args or ["origin/main..HEAD"])).decode().split()
    else:
        print(__doc__, file=sys.stderr)
        return 2
    found = unlisted(commits)
    if found:
        warn(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
