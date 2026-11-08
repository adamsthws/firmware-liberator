#!/usr/bin/env python3
"""The firmware each device ends on, converted on its emulator: the same check
for every device the tool converts, so a new one has it by being added.

    python -I tools/repo/emulator_check.py list [--changed-since COMMIT]
        as JSON, the devices to check and the emulator image each needs: those a
        change since COMMIT could affect, or every one that has something to run
        from (NO_DUMP names those that have not)
    python -I tools/repo/emulator_check.py DEVICE [--instances 1,2]
        its check: each final firmware installed, then each plug taken to the
        other, two ways side by side (a way goes on as soon as its own run is
        done). Nothing here needs a secret or an account: an S60 starts from the
        published public plugs, any other Espressif device from its public dump
        on the cloud stub, and an S61 from upstream's public dump.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.autoflash import kinds  # noqa: E402

# Each family's emulator: the image a device's job waits for, as its workflow
# publishes it, and the variable that says where the tool pulls it from.
EMULATORS = {
    "espressif": {"image_workflow": "emulator-image.yml", "image_tag": "tools/espressif/emulator/image.sh tag",
                  "image_name": "autoflash-emu", "published_variable": "AUTOFLASH_EMU_PUBLISHED"},
    "beken": {"image_workflow": "beken-emulator-image.yml", "image_tag": "tools/beken/emulator/image.sh tag",
              "image_name": "autoflash-beken-emu", "published_variable": "AUTOFLASH_BEKEN_EMU_PUBLISHED"},
}
# What every device's conversion runs on, whichever changed.
SHARED = ("tools/autoflash/", "tools/ewelink/", "tools/repo/emulator_check.py", "artifacts/",
          ".github/workflows/emulator-check.yml")
LOGS = ROOT / "captures/emulation/device-check"
# Devices with no public dump, so nothing for an emulator to run. A device is
# here by decision: tests/repo/test_emulator_check.py fails on one that has
# neither a dump nor a place here.
NO_DUMP = {"sonoff-basic-1gs", "sonoff-basicr4"}
# An Espressif device's two ways, each a final firmware then the other.
ESPRESSIF_WAYS = (("tasmota", "esphome"), ("esphome", "tasmota"))


def family_of(kind: kinds.Kind) -> str:
    return "beken" if kind.chip.startswith("bk") else "espressif"


def affected(kind: kinds.Kind, changed: list[str]) -> bool:
    """Whether a change to these paths could change how `kind` converts."""
    family = family_of(kind)
    # A chip's folder holds what every plug on it shares: its mover.
    own = (f"devices/{kind.key}/", f"devices/{kind.chip}/", f"tools/{family}/", *SHARED)
    return any(path.startswith(own) for path in changed)


def changed_since(commit: str) -> list[str] | None:
    """The paths changed since `commit`; None where git cannot say, as for a
    first push, whose `before` is all zeros."""
    found = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", commit, "HEAD"],
                           capture_output=True, text=True, check=False)
    return found.stdout.split() if found.returncode == 0 else None


def jobs(changed: list[str] | None) -> list[dict[str, str]]:
    return [{"device": kind.key, "family": family_of(kind), **EMULATORS[family_of(kind)]}
            for kind in kinds.READY if kind.key not in NO_DUMP and (changed is None or affected(kind, changed))]


def check_public_dump(kind: kinds.Kind, instances: list[int]) -> int:
    """An Espressif device from its public dump, on the cloud stub."""
    from tools.espressif.emulator import public_dump, public_plugs

    public_dump.make(kind.key)
    dump = public_dump.DUMPS.get(kind.key) or public_dump.UPDATES[kind.key]
    stub = ("--stub-cloud", f"--plug={dump.mac}")

    def way(instance: int, firmwares: tuple[str, ...]) -> int:
        saved = None
        for firmware in firmwares:
            keep = f"check-{kind.key}-{instance}-{firmware}"
            log = LOGS / kind.key / f"{instance}-{firmware}.log"
            converted, said = public_plugs.run(instance, saved, firmware, keep, log,
                                               extra=(*stub, *(() if saved else ("--fresh",))))
            start = f"on {saved.rsplit('-', 1)[1]}" if saved else "from Sonoff's"
            print(f"{'PASS' if converted else 'FAIL'}  {kind.key} {start} to {firmware}: {said}", flush=True)
            if not converted:
                return 1
            saved = keep
        return 0

    try:
        return sum(public_plugs.side_by_side([lambda i=instance, w=firmwares: way(i, w) for firmwares, instance
                                              in zip(ESPRESSIF_WAYS, instances)]))
    finally:
        for instance in instances:
            for firmware in ("tasmota", "esphome"):
                subprocess.run(["rm", "-rf", str(public_plugs.SAVED / f"check-{kind.key}-{instance}-{firmware}")],
                               check=False)


def check_device(kind: kinds.Kind, instances: list[int]) -> int:
    if family_of(kind) == "beken":
        subprocess.run([str(ROOT / "tools/beken/emulator/beken-emu.sh"), "dump"], check=True)
        return subprocess.call([str(ROOT / "tools/beken/emulator/fault_suite.py"), "check"])
    from tools.espressif.emulator import public_dump
    if kind.key in public_dump.DUMPS or kind.key in public_dump.UPDATES:
        return check_public_dump(kind, instances)
    # The public plugs, made from a plug's own backup, hold nothing of Sonoff's.
    plugs = str(ROOT / "tools/espressif/emulator/public_plugs.py")
    numbers = ["--instances", ",".join(map(str, instances))]
    fetched = (ROOT / "captures/emulation/saved/public-pre-final-esphome").exists() or \
        subprocess.call([sys.executable, "-I", plugs, *numbers, "fetch"]) == 0
    return subprocess.call([sys.executable, "-I", plugs, *numbers, "test"]) if fetched else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("device", help="list, or a device's key")
    parser.add_argument("--changed-since", help="list: only what a change since this commit could affect")
    parser.add_argument("--instances", default="1,2", help="the two emulators to use (default 1,2)")
    args = parser.parse_args(argv)
    if args.device == "list":
        print(json.dumps(jobs(changed_since(args.changed_since) if args.changed_since else None)))
        return 0
    kind = next((kind for kind in kinds.READY if kind.key == args.device and kind.key not in NO_DUMP), None)
    if kind is None:
        parser.error(f"no such device: {args.device}; list says which there are")
    return check_device(kind, [int(n) for n in args.instances.split(",")][:2])


if __name__ == "__main__":
    sys.exit(main())
