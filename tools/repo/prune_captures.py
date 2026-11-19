#!/usr/bin/env python3
"""What in captures/, in old checkouts and in Docker can be made again, and has
not been used for a while.

    python3 tools/repo/prune_captures.py [--days N]           what it would remove, and how much
    python3 tools/repo/prune_captures.py [--days N] --delete  remove it
        --trees FOLDER  and the old checkouts of this repository in FOLDER
        --images        and the emulator images no checkout here runs on

In captures/, only what is listed in PRUNABLE, every part of it made again by the
next run, build or prep: an emulator's device and its computer's copy of the
checkout, the suites' logs, the build caches and the earlier releases' trees.

An old checkout - a folder in FOLDER holding CHECKOUT_MARK, other than this one,
unchanged for TREE_DAYS - is cleared down to its backups, KEPT_IN_TREES and
KEPT_AT_THE_TOP. One an emulator runs from, a suite claims instances for
(tools/repo/emulator_instances.py) or git shows changes not committed in is
left alone. So is a git worktree (its .git a file, not a folder): clearing one
would leave git counting it, every file in it deleted, and its work may not be
done. One old, with nothing to commit, its commit on main and no backup in it
is named with the git worktree remove that takes it, folder and record, for
whoever owns it to run.

An emulator image (EMULATOR_IMAGES) goes when no container uses it and it was
built from neither family's current files (image.sh tag) in this checkout, in
one a suite claims instances for, or in a recent one in FOLDER.

A backup anywhere is kept, as a device's stock backup cannot be made again
(AGENTS.md); so are the starting devices prep saved, the public dumps and
everything not listed. An emulator running keeps its state.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.autoflash.records import BACKUP, CAPTURES  # noqa: E402
from tools.repo import emulator_instances  # noqa: E402

# What may go, by its path in captures/, and what it is.
PRUNABLE = {
    "emulation/state": "an Espressif emulator's device and its computer's copy of the checkout",
    "emulation/state-*": "an Espressif emulator's device and its computer's copy of the checkout",
    "emulation/beken/state": "a Beken emulator's device and its computer's copy of the checkout",
    "emulation/beken/state-*": "a Beken emulator's device and its computer's copy of the checkout",
    "emulation/legacy/*": "an earlier release's tree, for the legacy scenarios",
    "emulation/beken/legacy/*": "an earlier release's tree, for the Beken update scenarios",
    "emulation/*/logs": "a suite's logs",
    "emulation/beken/*/logs": "a Beken suite's logs",
    "emulation/public-devices": "the public devices' check's logs",
    "emulation/device-check": "the emulator check's logs",
    "esphome-build": "ESPHome's build cache",
    "firmware-build": "the firmware build cache",
    "openbeken-build": "OpenBeken's build cache",
    "unattended": "unattended runs' screens",
}

# Unused this many days, what in captures/ may go.
DAYS = 14

# Unchanged this many days, a checkout may be cleared: a test run's copy is
# done with within days, and holds gigabytes of emulator devices.
TREE_DAYS = 7

# A checkout of this repository holds this file.
CHECKOUT_MARK = Path("tools/autoflash/__init__.py")

# What a cleared checkout keeps besides its backups: what each suite's runs
# found, wherever it is (small); and, at these paths in it, its git repository,
# so no commit is lost, its .env, never changed (AGENTS.md), and its devices'
# records, never made again.
KEPT_IN_TREES = ("results.jsonl",)
KEPT_AT_THE_TOP = (".git", ".env", "captures/devices")

# The emulator images, by repository name: emu.sh's and beken-emu.sh's
# default, and the published ones (ghcr.io/adamsthws/...). One named after
# them (AUTOFLASH_EMU_IMAGE=autoflash-emu-stub, say) counts too; any other
# image, such as the Windows one, is never touched.
EMULATOR_IMAGES = ("autoflash-emu", "autoflash-beken-emu")

# Each family's image.sh, whose `tag` is the hash an image built from that
# checkout's files is labelled with (LABEL).
IMAGE_SH = ("tools/espressif/emulator/image.sh", "tools/beken/emulator/image.sh")
LABEL = "autoflash.built-from"

# Above this much to free in old checkouts, images and captures/, a suite's
# prep says how (suggest).
HINT_AT = 1 << 30


def protected(path: Path) -> bool:
    """A backup, wherever it is: never removed."""
    return any("backup" in part.lower() for part in path.parts)


def docker(*args: str) -> str | None:
    """Docker's answer; None where it is not there or does not answer."""
    try:
        done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None if done.returncode else done.stdout


def running_states() -> set[Path] | None:
    """The state of each emulator running now; None where Docker cannot say."""
    found = docker("ps", "--format", "{{.Names}}")
    if found is None:
        return None
    states = set()
    for name in found.split():
        if name == "autoflash-emu":
            states.add(CAPTURES / "emulation/state")
        elif name.startswith("autoflash-emu-") and name.removeprefix("autoflash-emu-").isdigit():
            states.add(CAPTURES / f"emulation/state-{name.removeprefix('autoflash-emu-')}")
        elif name == "autoflash-beken-emu":
            states.add(CAPTURES / "emulation/beken/state")
        elif name.startswith("autoflash-beken-emu-") and name.removeprefix("autoflash-beken-emu-").isdigit():
            states.add(CAPTURES / f"emulation/beken/state-{name.removeprefix('autoflash-beken-emu-')}")
    return states


def containers() -> list[dict] | None:
    """Every container here, running or not: its image's id, whether it runs,
    and the folders it mounts. None where Docker cannot say."""
    ids = docker("ps", "-aq")
    if ids is None:
        return None
    if not ids.split():
        return []
    found = docker("inspect", "--format", "{{json .Image}}\t{{json .State.Running}}\t{{json .Mounts}}", *ids.split())
    if found is None:
        return None
    listed = []
    for line in found.splitlines():
        image, running, mounts = (json.loads(part) for part in line.split("\t"))
        listed.append({"image": image, "running": running,
                       "mounts": [Path(m["Source"]) for m in mounts or [] if m.get("Source")]})
    return listed


def candidates(captures: Path) -> list[tuple[Path, str]]:
    """Each, once: a suite's logs inside an emulator's state go with the state."""
    found: dict[Path, str] = {}
    for pattern, what in PRUNABLE.items():
        for path in sorted(captures.glob(pattern)):
            if path.is_dir():
                found.setdefault(path, what)
    return [(path, what) for path, what in found.items()
            if not any(other != path and other in path.parents for other in found)]


def walk(path: Path) -> Iterator[tuple[Path, int, float]]:
    """Every file and link under `path`, with its size and age, not following
    links: a link to a folder is one, removed without what it points to."""
    for directory, dirs, files in os.walk(path):
        for name in files + [d for d in dirs if Path(directory, d).is_symlink()]:
            file = Path(directory, name)
            try:
                info = file.lstat()
            except OSError:
                continue
            yield file, info.st_size, info.st_mtime


def last_used(path: Path) -> float:
    return max((mtime for _file, _size, mtime in walk(path)), default=path.lstat().st_mtime)


def remove(path: Path, kept: Callable[[Path], bool]) -> tuple[int, int]:
    """Everything under `path` but what is `kept` (given its path relative to
    `path`); the bytes freed, and the files that would not go."""
    freed = refused = 0
    for file, size, _mtime in walk(path):
        if kept(file.relative_to(path)):
            continue
        try:
            os.chmod(file.parent, os.stat(file.parent).st_mode | stat.S_IWUSR)
            file.unlink()
            freed += size
        except OSError:
            refused += 1
    for directory, dirs, _files in os.walk(path, topdown=False):
        for name in dirs:
            try:
                Path(directory, name).rmdir()
            except OSError:
                pass   # something kept in it, or a file that would not go
    try:
        path.rmdir()
    except OSError:
        pass
    return freed, refused


def size(path: Path, kept: Callable[[Path], bool]) -> int:
    return sum(file_size for file, file_size, _mtime in walk(path) if not kept(file.relative_to(path)))


def human(count: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.1f} {unit}" if unit != "B" else f"{count} B"
        count /= 1024
    return f"{count} B"


@dataclass
class Item:
    """One thing that may go: what to print, how much it frees, and how to
    remove it - which gives the bytes freed and how many `parts` would not go."""
    name: str
    what: str
    bytes: int
    delete: Callable[[], tuple[int, int]]
    parts: str = "files"


def in_captures(days: float) -> tuple[list[Item], list[str]]:
    """What in this checkout's captures/ has gone unused for `days`."""
    notes = []
    running = running_states()
    if running is None:
        notes.append("Docker cannot say which emulators run: their states are left alone.")
    cutoff = time.time() - days * 86400
    items = []
    for path, what in candidates(CAPTURES):
        is_state = what.startswith(("an Espressif emulator", "a Beken emulator"))
        if is_state and (running is None or path in running):
            continue
        if last_used(path) > cutoff:
            continue

        def kept(relative: Path, path: Path = path) -> bool:
            return protected(path.relative_to(CAPTURES) / relative)
        items.append(Item(str(path.relative_to(CAPTURES)), what, size(path, kept),
                          lambda path=path, kept=kept: remove(path, kept)))
    return items, notes


def kept_in_tree(relative: Path) -> bool:
    return any(kept_here(relative.parts[:n], part) for n, part in enumerate(relative.parts))


def kept_here(folder: tuple[str, ...], name: str) -> bool:
    """Whether `name` in `folder` (parts of a path in a checkout) is kept,
    where nothing above it is."""
    return "backup" in name.lower() or name in KEPT_IN_TREES or (*folder, name) in TOPS


TOPS = {Path(top).parts for top in KEPT_AT_THE_TOP}


def backups(tree: Path) -> int:
    """The backup folders in `tree`, not following links."""
    return sum(dirs.count(BACKUP) for _directory, dirs, _files in os.walk(tree))


def checkouts(folder: Path) -> list[Path]:
    """The checkouts of this repository in `folder`, but this one and any
    holding it or its captures."""
    ours = {ROOT.resolve(), CAPTURES.resolve()}
    found = []
    for tree in sorted(folder.iterdir()) if folder.is_dir() else []:
        if tree.is_symlink() or not (tree / CHECKOUT_MARK).is_file():
            continue
        real = tree.resolve()
        if any(real == mine or real in mine.parents or mine in real.parents for mine in ours):
            continue
        found.append(tree)
    return found


def uncommitted(tree: Path) -> bool:
    """Whether git shows changes not committed in `tree`, read without writing
    its index; a copy git cannot read has none of its own to lose."""
    try:
        done = subprocess.run(["git", "--no-optional-locks", "-C", str(tree), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


def worktree(tree: Path) -> bool:
    """Whether `tree` is a git worktree of another checkout: its .git a file
    naming that checkout's repository, which keeps a record of it."""
    return (tree / ".git").is_file()


def on_main(tree: Path) -> bool:
    """Whether the commit `tree` has checked out is on main already."""
    try:
        done = subprocess.run(["git", "-C", str(tree), "merge-base", "--is-ancestor", "HEAD", "main"],
                              capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def claimed_checkouts() -> set[Path]:
    """The checkouts a suite claims emulator instances for now."""
    return {Path(line.split()[2]).resolve() for line in emulator_instances.claims() if len(line.split()) > 2}


def inside(path: Path, mounts: list[Path]) -> bool:
    return any(mount == path or path in mount.parents for mount in mounts)


def unchanged(tree: Path, cutoff: float) -> tuple[float, int] | None:
    """When `tree` last changed, by its files and folders, and the bytes in it
    not kept_in_tree; None as soon as one is newer than `cutoff`. Folders
    nearest the top first, where a checkout in use soon shows it (its .git)."""
    newest, freeable = 0.0, 0
    folders: deque[tuple[str, tuple[str, ...], bool]] = deque([(str(tree), (), False)])
    while folders:
        folder, parts, kept = folders.popleft()
        try:
            changed = os.lstat(folder).st_mtime
            entries = list(os.scandir(folder))
        except OSError:
            continue
        if changed > cutoff:
            return None
        newest = max(newest, changed)
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                folders.append((entry.path, (*parts, entry.name), kept or kept_here(parts, entry.name)))
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if info.st_mtime > cutoff:
                return None
            newest = max(newest, info.st_mtime)
            if not (kept or kept_here(parts, entry.name)):
                freeable += info.st_size
    return newest, freeable


def old_trees(folder: Path, days: float, docker_says: list[dict] | None,
              enough: float = float("inf")) -> tuple[list[Item], list[Path], list[str]]:
    """The checkouts in `folder` unchanged for `days` that may be cleared, those
    found once they hold `enough`; those in use, whose emulator images stay; and
    what is left alone, and why."""
    if docker_says is None:
        return [], checkouts(folder), ["Docker cannot say which emulators run: old checkouts are left alone."]
    mounted = [mount for c in docker_says if c["running"] for mount in c["mounts"]]
    claimed = claimed_checkouts()
    cutoff = time.time() - days * 86400
    items, in_use, notes, worktrees = [], [], [], []
    for tree in checkouts(folder):
        if sum(item.bytes for item in items) >= enough:
            break
        if tree.resolve() in claimed or inside(tree, mounted) or inside(tree.resolve(), mounted):
            in_use.append(tree)
            continue
        found = unchanged(tree, cutoff)
        if found is None:
            in_use.append(tree)
            continue
        newest, freeable = found
        if worktree(tree):
            if not uncommitted(tree) and on_main(tree) and not backups(tree):
                worktrees.append(tree)
            continue
        if not freeable:
            continue   # cleared already
        if uncommitted(tree):
            notes.append(f"{tree}: left alone, as git shows changes not committed in it")
            continue
        count = backups(tree)
        items.append(Item(str(tree), f"a checkout unchanged {int((time.time() - newest) // 86400)} days, "
                          f"down to its {count} backup{'s' if count != 1 else ''}, results, records and git",
                          freeable, lambda tree=tree, count=count: clear_tree(tree, count)))
    if worktrees:
        notes.append("Git worktrees are left alone; these look finished (old, nothing to commit, on main, "
                     "no backup), and their owner may remove them:\n"
                     + "\n".join(f"    git worktree remove {tree}" for tree in worktrees))
    return items, in_use, notes


def clear_tree(tree: Path, count: int) -> tuple[int, int]:
    freed, refused = remove(tree, kept_in_tree)
    if backups(tree) != count:   # never: kept_in_tree keeps each
        raise SystemExit(f"{tree} held {count} backups and now {backups(tree)}: stop and look")
    return freed, refused


def current_tags(trees: list[Path]) -> set[str] | None:
    """The hash each family's image.sh gives in each of `trees`; None where
    this checkout's own cannot say."""
    tags = set()
    for tree in trees:
        for script in IMAGE_SH:
            try:
                done = subprocess.run([str(tree / script), "tag"], capture_output=True, text=True,
                                      timeout=60, check=False)
                tag = done.stdout.strip() if done.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                tag = ""
            if tag:
                tags.add(tag)
            elif tree == ROOT:
                return None
    return tags


def emulator_image(names: list[str]) -> bool:
    repositories = [name.rsplit(":", 1)[0].rsplit("/", 1)[-1] for name in names]
    return any(repository == image or repository.startswith(f"{image}-")
               for repository in repositories for image in EMULATOR_IMAGES)


def old_images(docker_says: list[dict] | None, in_use: list[Path]) -> tuple[list[Item], list[str]]:
    """The emulator images no container uses, built from files no checkout in
    `in_use`, nor this one, holds now."""
    unknown = [], ["Docker cannot say which images are in use: they are left alone."]
    found = docker("images", "-q", "--no-trunc")
    if docker_says is None or found is None:
        return unknown
    tags = current_tags([ROOT, *in_use])
    if tags is None:
        return [], ["This checkout's image.sh cannot say which images are current: they are left alone."]
    ids = sorted(set(found.split()))
    listed = docker("image", "inspect", "--format",
                    "{{json .Id}}\t{{json .Size}}\t{{json .RepoTags}}\t{{json .Config.Labels}}", *ids) if ids else ""
    if listed is None:
        return unknown
    used = {c["image"] for c in docker_says}
    items = []
    for line in listed.splitlines():
        image, image_size, names, labels = (json.loads(part) for part in line.split("\t"))
        names = names or []
        built_from = (labels or {}).get(LABEL)
        if not names or not emulator_image(names) or image in used or built_from in tags:
            continue
        items.append(Item(", ".join(names), f"an emulator image built from {built_from or 'files unknown'}",
                          image_size, lambda names=names, image_size=image_size: remove_image(names, image_size),
                          parts="names"))
    return items, []


def remove_image(names: list[str], image_size: int) -> tuple[int, int]:
    refused = sum(docker("rmi", name) is None for name in names)
    return (0 if refused else image_size), refused


def plan(days: float | None, trees: Path | None, images: bool,
         enough: float = float("inf")) -> tuple[list[Item], list[str]]:
    """Everything that may go, or what is found once it is `enough`; and the
    notes on what is left alone."""
    items, notes = in_captures(DAYS if days is None else days)
    if sum(item.bytes for item in items) >= enough:
        return items, notes
    docker_says = containers() if trees is not None or images else None
    in_use: list[Path] = sorted(claimed_checkouts()) if images else []
    if trees is not None:
        found, recent, left = old_trees(trees, TREE_DAYS if days is None else days, docker_says,
                                        enough - sum(item.bytes for item in items))
        items += found
        notes += left
        in_use += recent
    if images and sum(item.bytes for item in items) < enough:
        found, left = old_images(docker_says, in_use)
        items += found
        notes += left
    return items, notes


def suggest() -> None:
    """For a suite's prep: one line saying how to clear what old checkouts
    beside this one, emulator images and captures/ hold, where HINT_AT or
    more: found as soon as there is that much, as walking old checkouts on a
    slow disk can take minutes."""
    try:
        items, _notes = plan(None, ROOT.parent, True, HINT_AT)
    except OSError:
        return
    total = sum(item.bytes for item in items)
    if total >= HINT_AT:
        print(f"Old test trees, emulator images and captures hold {human(total)} or more that can be made again: "
              f"python3 tools/repo/prune_captures.py --trees {ROOT.parent} --images lists them.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float,
                        help=f"unused for this long (default {DAYS} in captures/, {TREE_DAYS} for a checkout)")
    parser.add_argument("--trees", type=Path, metavar="FOLDER", help="and the old checkouts of this repository in FOLDER")
    parser.add_argument("--images", action="store_true", help="and the emulator images no checkout here runs on")
    parser.add_argument("--delete", action="store_true", help="remove it; without, only say what would go")
    args = parser.parse_args(argv)
    items, notes = plan(args.days, args.trees, args.images)
    for note in notes:
        print(note, file=sys.stderr)
    total = 0
    for item in items:
        if args.delete:
            freed, refused = item.delete()
            total += freed
            print(f"removed  {human(freed):>9}  {item.name}  ({item.what})"
                  + (f": {refused} {item.parts} would not go (another user's, or in use?)" if refused else ""))
        else:
            total += item.bytes
            print(f"would go {human(item.bytes):>9}  {item.name}  ({item.what})")
    print(f"{'Freed' if args.delete else 'Would free'} {human(total)}"
          + ("" if args.delete else ": --delete removes it") + ". Backups are kept wherever they are.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
