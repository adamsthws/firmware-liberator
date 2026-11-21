#!/usr/bin/env python3
"""A tree of this repository on another machine, for running its suites there:
sent from a branch here, its own devices and saved starts made once, a
command run in it on emulator instances no other suite there holds, its
results brought back, and the tree removed - never another's tree borrowed,
nor a device's backup lost.

    tools/repo/remote_tree.py HOST send NAME [REF]
    tools/repo/remote_tree.py HOST devices NAME --from DIR MAC...
    tools/repo/remote_tree.py HOST prep NAME espressif|beken [--device MAC] [--again]
    tools/repo/remote_tree.py HOST run NAME [--emulators N --family F] [--detach] -- COMMAND...
    tools/repo/remote_tree.py HOST fetch NAME
    tools/repo/remote_tree.py HOST remove NAME [--no-fetch]

HOST is as ssh takes it, from your own ssh config or --ssh-config FILE. The
trees are in ~/autoflash-trees/ there, beside one repository of their own
(.repo.git) that each send adds to: a send bundles only what that repository
lacks. A send makes the tree's .venv with tools/repo/setup-dev, again only
when that or requirements/ changed: python3 there needs its venv module.

`devices` copies the named devices' folders from DIR, a captures/devices
folder on that machine, into the tree's, each backup's files as hard links:
the backup is not copied twice nor ever written, and removing the tree
leaves it where it was. `prep` runs the family's fault-suite prep once
(again after --again). `run` starts COMMAND in the tree, `{first}` in it
the first of N free emulator instances (emulator_instances), and waits for
it unless --detach; its output is in ~/autoflash-trees/NAME.runs/ there.
`fetch` copies back to captures/emulation/kept/HOST-NAME/ (scenario_times.KEPT),
in the main checkout's captures/ when run from a linked worktree, what the
runs left worth keeping, laid out as the tree's captures/emulation/ (FETCHED:
the suites' results and logs, each emulator's own logs and the tool's runs in
it), and the commands' output beside it in runs/; not the backups, images and
saved starts the next prep makes again. `remove` fetches first (--no-fetch
for a tree with nothing worth keeping), then deletes the tree and its ref,
and moves what was fetched to HOST-NAME-<when removed>/. A backup's file in
the tree with no other copy is kept and listed, unless it is an emulated
device's: in an emulator's own folders, or a test device the emulator's
tooling made (device.json's test_copy_of)."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.autoflash import records  # noqa: E402
from tools.repo import scenario_times  # noqa: E402

# Where the trees are kept on the other machine, apart from anything else
# there: one folder each, a repository they all fetch into, and each tree's
# runs' output beside it.
TREES = "autoflash-trees"
REPO = f"{TREES}/.repo.git"

# Each family's fault-suite prep, with its first instance: what saved starts
# a batch there starts from. The Espressif suite's on the stub, needing no
# eWeLink account; the Beken suite has none to need.
PREP = {
    "espressif": "tools/espressif/emulator/fault_suite.py --device {device} --stub-cloud "
                 "--first-instance {first} prep",
    "beken": "tools/beken/emulator/fault_suite.py prep --first-instance {first}",
}
# How many emulators each prep runs at once.
PREP_EMULATORS = {"espressif": 1, "beken": 1}

# A file a tree's prep made, so it is made once.
PREPPED = ".prepped-{family}"

# What a fetch brings back of the tree's captures/emulation/, laid out as it
# is there (rsync's filter, the first rule a path matches deciding): every
# suite's results and logs, the emulator check's and the attended suite's,
# and of each emulator's state its own logs (serial, stub, router), what the
# API beside it kept, and its computer's captures/, which hold the tool's
# runs (run.log, events.jsonl, outcomes.jsonl) and screens. Never a backup, a
# firmware image or a key, nor anything the next prep or check makes again:
# a device's flash, a computer's copy of the checkout, the saved starts, the
# earlier releases' trees, the public dumps, the build caches and the copies
# fetched before (KEPT).
FETCHED = (
    *(f"--exclude={name}" for name in ("sonoff-backup/", "*.bin", "*.rbl", "*.elf", "*.fota", "*.ota", "*.tar",
                                        "*.key", "__pycache__/", "esphome-build/", "firmware-build/",
                                        "openbeken-build/", "public-copies/")),
    *(f"--exclude=/{name}/" for name in (scenario_times.KEPT, "saved*", "legacy", "public-dumps", "cache",
                                         "stub-cloud*", "beken/saved", "beken/legacy", "beken/firmwares",
                                         "beken/rebuilt")),
    *(rule for state in ("/state*", "/beken/state*")
      for rule in (f"--include={state}/work/captures/***", f"--exclude={state}/work/*")),
)

# Run there: which of tree $1's folders a fetch copies from are there.
FETCHABLE = 'for d in "$1/captures/emulation" "$1.runs"; do [ -d "$d" ] && echo "$d"; done; :'


def tree(name: str) -> str:
    """NAME's folder on the other machine, refused where it would be anything
    but one folder under TREES."""
    if not name or name.startswith((".", "-")) or "/" in name or name != name.strip():
        raise SystemExit(f"{name!r} is not a tree's name: one word, as a folder name")
    return f"{TREES}/{name}"


def probes(commits: list[str]) -> list[str]:
    """The commits of a branch's history (newest first) to ask the other
    machine about: the newest, then ever further back, so few are asked
    and the newest it has is found near enough."""
    picked, step = [], 1
    index = 0
    while index < len(commits):
        picked.append(commits[index])
        index += step
        step *= 2
    if commits and commits[-1] not in picked:
        picked.append(commits[-1])
    return picked


def bundle_range(ref: str, probed: list[str], present: set[str]) -> list[str]:
    """What to bundle of `ref` (a ref's name: a bundle carries refs, not
    commits alone): from the newest probed commit the other machine has,
    else the whole of it. Never from `ref`'s own commit, the first probed: a
    bundle of no commit holds no ref."""
    for commit in probed[1:]:
        if commit in present:
            return [f"{commit}..{ref}"]
    return [ref]


def first_instance(command: list[str], first: int | None) -> list[str]:
    """COMMAND with {first} replaced by the first instance, refused where it
    names {first} with none chosen (--emulators)."""
    if first is None:
        if any("{first}" in word for word in command):
            raise SystemExit("the command names {first}: give --emulators N (and --family) to choose them")
        return command
    return [word.replace("{first}", str(first)) for word in command]


# Run there in a tree: the first of N instances of a family free of every
# running emulator and every suite's claim (emulator_instances).
FREE = ("import sys; from tools.repo import emulator_instances as e; "
        "f, n = sys.argv[1], int(sys.argv[2]); "
        "print(e.first_free(e.running(f) | e.claimed_elsewhere(f), n) or '')")

# Run there in tree $1: its .venv made as a contributor makes theirs
# (tools/repo/setup-dev, CONTRIBUTING.md "Setting up"), which the Espressif
# emulator and the linters run from. Made again only when what it is made
# from changed since; pip's own cache there keeps the wheels between trees.
# Prints what it did; setup-dev's output goes to stderr, shown if it fails.
VENV = r"""set -eu
cd "$1"
[ -x tools/repo/setup-dev ] || { echo "no .venv (no tools/repo/setup-dev at this commit)"; exit 0; }
made=$(cat tools/repo/setup-dev requirements/*.txt | cksum)
[ "$(cat .venv/.made-from 2>/dev/null)" = "$made" ] && { echo ".venv as it was"; exit 0; }
python3 -c 'import venv, ensurepip' 2>/dev/null || {
    echo "python3 here cannot make a .venv: install its venv module (python3-venv on Debian and Ubuntu), then send again" >&2
    exit 1
}
tools/repo/setup-dev >&2
printf '%s\n' "$made" > .venv/.made-from
echo ".venv made"
"""


# Run there: tree $1 deleted, but for a backup's file in it with no other copy
# (one link) that is not an emulated device's, which it lists; and its ref
# in the repository the trees share. An emulated device's backup is in an
# emulator's own folders (captures/emulation/: the backups the tool takes
# there are of the device the emulator runs, saved starts too), or one of
# the test devices the emulator's tooling makes from public dumps or as
# copies (device.json's test_copy_of: public_dump.py, copy_device.py). The
# ref gone, its commits are left to git gc, run when enough has gathered
# (--auto), as git itself would. Fails where anything else is left.
REMOVE = r"""set -u
tree=$1 repo=${1%/*}/.repo.git
unref() {
    [ -d "$repo" ] || return 0
    git -C "$repo" update-ref -d "refs/trees/${tree##*/}" 2>/dev/null || :
    git -C "$repo" gc --auto --quiet 2>/dev/null || :
}
[ -d "$tree" ] || { echo "no $tree"; unref; exit 0; }
for pid in "$tree".runs/*.pid; do
    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        echo "$tree is still running $(cat "$pid") (${pid%.pid}): stop it first" >&2; exit 1
    fi
done
find "$tree" -xdev -type f -path '*/sonoff-backup/*' -links 1 ! -path "$tree/captures/emulation/*" 2>/dev/null |
while IFS= read -r file; do
    grep -qs '"test_copy_of": *"[^"]' "${file%%/sonoff-backup/*}/device.json" || printf '%s\n' "$file"
done > "$tree.kept"
find "$tree" -xdev \( -type f -o -type l \) ! \( -path '*/sonoff-backup/*' -type f -links 1 \) -delete 2>/dev/null
find "$tree" -xdev -type f -path '*/sonoff-backup/*' -links 1 2>/dev/null | grep -vxF -f "$tree.kept" |
while IFS= read -r file; do rm -f "$file"; done
find "$tree" -xdev -depth -type d -empty -delete 2>/dev/null
rm -rf "$tree.runs"
if [ -s "$tree.kept" ]; then
    echo "kept, as the only copy of a backup:"; sed 's/^/  /' "$tree.kept"
fi
left=$(find "$tree" ! -type d 2>/dev/null | grep -cvxF -f "$tree.kept")
rm -f "$tree.kept"
unref
if [ "$left" -gt 0 ]; then
    echo "$tree: $left files could not be deleted:" >&2; find "$tree" ! -type d | head -n 5 >&2; exit 1
fi
[ -d "$tree" ] && echo "$tree left holding only those" || echo "$tree removed"
"""


# Run there: device $3's folder from $1, a captures/devices folder, copied
# into tree $2's; its backup's files hard links, so neither copied twice nor
# written, and kept where they were when the tree goes. One already there is
# left as it is.
DEVICES = r"""set -eu
src=$(cd "$1/$3" 2>/dev/null && pwd) || { echo "no $1/$3" >&2; exit 1; }
dst=$HOME/$2/captures/devices/$3
[ -e "$dst" ] && { echo "$dst already there: left as it is"; exit 0; }
mkdir -p "$dst"
rsync -a --exclude=/sonoff-backup "$src/" "$dst/"
[ -d "$src/sonoff-backup" ] && cp -al "$src/sonoff-backup" "$dst/sonoff-backup"
echo "$dst from $src (backup linked)"
"""


# Run there: command $2 started in tree $1, apart from the ssh connection;
# prints its log's path, with the command, its pid and its exit status
# beside it. The log is named for when it started and by this shell's pid,
# as two runs started in the same second (a prep of each family, say) must
# not write one log, nor read each other's exit.
START = r"""set -eu
cd "$1"; runs=$HOME/$1.runs; mkdir -p "$runs"
log=$runs/$(date +%Y%m%dT%H%M%S)-$$.log
printf '%s\n' "$2" > "$log.command"
export PYTHONUNBUFFERED=1  # the log followed as it is written
nohup sh -c "$2; echo \$? > '$log.exit'" > "$log" 2>&1 < /dev/null &
echo $! > "$log.pid"; echo "$log"
"""


class Remote:
    def __init__(self, host: str, config: str | None):
        self.host = host
        self.ssh = ["ssh", *(["-F", config] if config else []), host]
        self.rsh = shlex.join(self.ssh[:-1])

    def run(self, script: str, *args: str, stdin: bytes | None = None, check: bool = True,
            capture: bool = True) -> subprocess.CompletedProcess:
        """`script` run by sh in the home folder there, with `args` as $1...
        (`stdin` given, it reads that instead of the script's text)."""
        command = f"sh -c {shlex.quote(script)} - {shlex.join(args)}" if args else f"sh -c {shlex.quote(script)}"
        done = subprocess.run([*self.ssh, command], input=stdin, capture_output=capture, check=False)
        if check and done.returncode:
            sys.stderr.write((done.stderr or b"").decode(errors="replace"))
            raise SystemExit(f"{self.host}: failed ({done.returncode})")
        return done

    def text(self, script: str, *args: str) -> str:
        return self.run(script, *args).stdout.decode()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def send(remote: Remote, name: str, ref: str) -> None:
    where = tree(name)
    commit = git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    remote.run(f'mkdir -p {TREES} && {{ [ -d {REPO} ] || git init -q --bare {REPO}; }}')
    probed = probes(git("rev-list", "--first-parent", "--max-count=4096", commit).split())
    have = remote.text(f'for c in "$@"; do git -C {REPO} cat-file -e "$c^{{commit}}" 2>/dev/null && echo "$c" || :; done',
                       *probed)
    # The tags too: a suite's prep-update builds the release before from its
    # tag (git archive), in either family.
    bundle = subprocess.run(["git", "bundle", "create", "-", *bundle_range(ref, probed, set(have.split())), "--tags"],
                            cwd=ROOT, capture_output=True, check=True).stdout
    remote.run(f'cat > {TREES}/.incoming.bundle && git -C {REPO} fetch -q "$HOME/{TREES}/.incoming.bundle" '
               f'"+{ref}:refs/trees/{name}" "+refs/tags/*:refs/tags/*" && rm -f {TREES}/.incoming.bundle', stdin=bundle)
    # A bundle leaves out a lightweight tag (each release's) on a commit the
    # other machine had already: each is set there by hand.
    tags = git("for-each-ref", "--format=%(objectname) %(refname)", "refs/tags/")
    remote.run(f'while read -r sha ref; do git -C {REPO} cat-file -e "$sha" 2>/dev/null '
               f'&& git -C {REPO} update-ref "$ref" "$sha" || :; done', stdin=tags.encode())
    remote.run(f'set -e; [ -d {where} ] || git clone -q --shared --no-checkout {REPO} {where}; '
               f'git -C {where} fetch -q origin "+refs/tags/*:refs/tags/*"; git -C {where} checkout -q --detach {commit}')
    venv = remote.text(VENV, where).strip()
    print(f"{remote.host}:~/{where} at {commit[:12]} ({len(bundle) // 1024} kB sent), {venv}")


def devices(remote: Remote, name: str, source: str, macs: list[str]) -> None:
    """Each device's folder from `source`, copied, its backup linked."""
    where = tree(name)
    for mac in macs:
        folder = mac.lower().replace(":", "-")
        print(remote.text(DEVICES, source.rstrip("/"), where, folder).strip())


def free(remote: Remote, name: str, family: str, count: int) -> int:
    found = remote.text(f'cd {tree(name)} && python3 -c {shlex.quote(FREE)} "$@"', family, str(count)).strip()
    if not found:
        raise SystemExit(f"{remote.host}: no {count} {family} emulator instances in a row free")
    return int(found)


def run(remote: Remote, name: str, line: str, detach: bool) -> int:
    """The shell command `line` started in the tree, apart from this ssh
    connection so that losing it stops nothing; waited for, its output
    shown, unless `detach`. Its exit status, or 255 where none was written."""
    started = remote.text(START, tree(name), line).strip()
    print(f"{remote.host}: {line}\n  output: {started}", flush=True)
    if detach:
        return 0
    remote.run('tail -n +1 --pid="$(cat "$1.pid")" -f "$1"', started, capture=False, check=False)
    code = remote.text('cat "$1.exit" 2>/dev/null || echo 255', started).strip()
    return int(code) if code.isdigit() else 255


def captures() -> Path:
    """This computer's captures/: the main checkout's, also from a linked
    worktree, whose own goes when the worktree does; AUTOFLASH_CAPTURES's
    where that is set, as records has it."""
    if os.environ.get("AUTOFLASH_CAPTURES"):
        return records.CAPTURES
    try:
        common = git("rev-parse", "--git-common-dir").strip()
    except (OSError, subprocess.CalledProcessError):
        return records.CAPTURES
    return (ROOT / common).resolve().parent / "captures"


def kept(host: str, name: str) -> Path:
    """Where a fetch of HOST's tree NAME puts what it brings back."""
    return captures() / "emulation" / scenario_times.KEPT / f"{host}-{name}"


def fetch(remote: Remote, name: str) -> Path | None:
    """What the tree's runs left that is worth keeping (FETCHED), and its
    commands' output, copied here; None where it has nothing of either."""
    where = tree(name)
    there = remote.text(FETCHABLE, where).split()
    if not there:
        print(f"{remote.host}:~/{where}: nothing to fetch")
        return None
    folder = kept(remote.host, name)
    folder.mkdir(parents=True, exist_ok=True)
    rsync = ["rsync", "-az", "-e", remote.rsh, "--prune-empty-dirs"]
    if f"{where}/captures/emulation" in there:
        subprocess.run([*rsync, *FETCHED, f"{remote.host}:{where}/captures/emulation/", f"{folder}/"], check=True)
    if f"{where}.runs" in there:
        subprocess.run([*rsync, f"{remote.host}:{where}.runs/", f"{folder}/runs/"], check=True)
    if not any(folder.iterdir()):
        folder.rmdir()
        print(f"{remote.host}:~/{where}: nothing worth keeping")
        return None
    print(f"results in {folder}")
    return folder


def remove(remote: Remote, name: str, fetch_first: bool) -> None:
    """The tree fetched (unless not `fetch_first`), then deleted there, its
    ref too; what was fetched of it moved to a folder named for when it was
    removed, so that a tree of the same name later starts a folder of its own."""
    if fetch_first:
        fetch(remote, name)
    print(remote.text(REMOVE, tree(name)).strip())
    folder = kept(remote.host, name)
    if folder.is_dir():
        done = folder.with_name(f"{folder.name}-{time.strftime('%Y%m%dT%H%M%S')}")
        folder.rename(done)
        print(f"what was fetched of it is in {done}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ssh-config", help="an ssh config file to read HOST from (default: your own)")
    parser.add_argument("host")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("send", help="the tree made or moved to REF")
    one.add_argument("name")
    one.add_argument("ref", nargs="?", default="HEAD")
    one = commands.add_parser("devices", help="devices' folders copied in, their backups linked")
    one.add_argument("name")
    one.add_argument("--from", dest="source", required=True, help="a captures/devices folder there")
    one.add_argument("macs", nargs="+")
    one = commands.add_parser("prep", help="the family's saved starts, made once")
    one.add_argument("name")
    one.add_argument("family", choices=sorted(PREP))
    one.add_argument("--device", help="espressif: the device's MAC, in the tree's captures/devices")
    one.add_argument("--again", action="store_true", help="made again, though made before")
    one = commands.add_parser("run", help="a command in the tree")
    one.add_argument("name")
    one.add_argument("--family", choices=sorted(PREP), help="the emulators' family, for {first}")
    one.add_argument("--emulators", type=int, help="how many emulators the command runs, for {first}")
    one.add_argument("--detach", action="store_true", help="started, not waited for")
    one = commands.add_parser("fetch", help="the results brought back")
    one.add_argument("name")
    one = commands.add_parser("remove", help="the tree fetched, then deleted, all but a real device's backup "
                                             "with no other copy")
    one.add_argument("name")
    one.add_argument("--no-fetch", dest="fetch", action="store_false",
                     help="not fetched first: a tree whose runs left nothing worth keeping")
    argv = sys.argv[1:] if argv is None else argv
    # run's command is everything after "--", whatever options it takes.
    cmd = argv[argv.index("--") + 1:] if "--" in argv else []
    args = parser.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    remote = Remote(args.host, args.ssh_config)
    if args.command == "send":
        send(remote, args.name, args.ref)
    elif args.command == "devices":
        devices(remote, args.name, args.source, args.macs)
    elif args.command == "prep":
        if args.family == "espressif" and not args.device:
            parser.error("prep espressif needs --device MAC")
        marker = f"{tree(args.name)}/{PREPPED.format(family=args.family)}"
        if not args.again and remote.run(f"[ -f {marker} ]", check=False).returncode == 0:
            print(f"{args.family} saved starts already made in {args.name} (--again to make them anew)")
            return 0
        first = free(remote, args.name, args.family, PREP_EMULATORS[args.family])
        command = PREP[args.family].format(device=args.device, first=first)
        return run(remote, args.name, f"{command} && touch {PREPPED.format(family=args.family)}", detach=False)
    elif args.command == "run":
        if not cmd:
            parser.error("run needs a command, after --")
        first = None
        if args.emulators:
            if not args.family:
                parser.error("--emulators needs --family")
            first = free(remote, args.name, args.family, args.emulators)
        command = first_instance(cmd, first)
        return run(remote, args.name, shlex.join(command), args.detach)
    elif args.command == "fetch":
        fetch(remote, args.name)
    elif args.command == "remove":
        remove(remote, args.name, args.fetch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
