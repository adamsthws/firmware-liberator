#!/usr/bin/env python3
"""The firmware each device ends on, converted on its emulator: the same check
for every device the tool converts, so a new one has it by being added.

    python -I tools/repo/emulator_check.py list [--changed-since COMMIT]
        as JSON, the devices to check and the emulator image each needs: those a
        change since COMMIT could affect, or every one that has something to run
        from (NOTHING_TO_RUN names those that have not)
    .venv/bin/python -I tools/repo/emulator_check.py DEVICE [--instances 1,2]
        its check: each final firmware installed, then each device taken to the
        other, two ways side by side (a way goes on as soon as its own run is
        done), on the two emulators numbered: two devices checked at once on one
        computer each need their own. Nothing here needs a secret or an
        account: an S60 starts from the published public devices, any other
        Espressif device from its public dump on the cloud stub, an S61 from
        upstream's public dump, and a Beken device with no public dump from a
        flash rebuilt from the S61's and its official update file
        (rebuilt_flash).
    .venv/bin/python -I tools/repo/emulator_check.py DEVICE --motor [calibrated] [--instances 1,2]
        the same for a device with a motor mode, from its test copy set up to
        drive a motor (public_dump.MOTOR_COPIES; calibrated: its travel timed
        and its outputs reversed): each firmware it ends on then shown set to
        the travel and direction carried across, driving the motor that way,
        its outputs never on together and both off after a power cut
        (motor_problem); the first firmware of each way then set to another
        travel and direction (moved_setup), which the next must carry across
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.autoflash import kinds, motor as motor_setup  # noqa: E402
from tools.repo import emulator_stats  # noqa: E402

# Each family's emulator: the image a device's job waits for, as its workflow
# publishes it, and the variable that says where the tool pulls it from.
EMULATORS = {
    "espressif": {"image_workflow": "emulator-image.yml", "image_tag": "tools/espressif/emulator/image.sh tag",
                  "image_name": "autoflash-emu", "published_variable": "AUTOFLASH_EMU_PUBLISHED"},
    "beken": {"image_workflow": "beken-emulator-image.yml", "image_tag": "tools/beken/emulator/image.sh tag",
              "image_name": "autoflash-beken-emu", "published_variable": "AUTOFLASH_BEKEN_EMU_PUBLISHED"},
}
# What every device's conversion runs on, whichever changed: devices/shared-esphome/
# is in every device's ESPHome.
SHARED = ("tools/autoflash/", "tools/ewelink/", "devices/shared-esphome/", "tools/repo/emulator_check.py", "artifacts/",
          ".github/workflows/emulator-check.yml")
LOGS = ROOT / "captures/emulation/device-check"
# Devices with nothing for an emulator to run: no public dump, nor a flash to
# rebuild from an official update file. A device is here by decision:
# tests/repo/test_emulator_check.py fails on one that has neither something to
# run from nor a place here.
NOTHING_TO_RUN: set[str] = set()
# The Beken device whose public dump gives a rebuilt flash its bootloader,
# radio data and settings (rebuilt_flash).
REBUILT_FROM = "sonoff-s61"
# That dump, as upstream BekenEmulator publishes it and beken-emu.sh dump
# fetches it into folders.FIRMWARES, by its name and SHA-256.
S61_DUMP = ("Sonoff_S61s_EUPlug_WBBK_01P_V1.3.bin", "0f858068b7b6c4107119e6afdf7a6eb05349234e726028602dbcf63b92ad6df3")
# An Espressif device's two ways, each a final firmware then the other.
ESPRESSIF_WAYS = (("tasmota", "esphome"), ("esphome", "tasmota"))


def family_of(kind: kinds.Kind) -> str:
    return "beken" if kind.chip.startswith("bk") else "espressif"


def affected(kind: kinds.Kind, changed: list[str]) -> bool:
    """Whether a change to these paths could change how `kind` converts."""
    family = family_of(kind)
    # A chip's folder holds what every device on it shares: its mover.
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
            for kind in kinds.READY if kind.key not in NOTHING_TO_RUN and (changed is None or affected(kind, changed))]


def check_public_dump(kind: kinds.Kind, instances: list[int], motor: str = "") -> int:
    """An Espressif device from its public dump, on the cloud stub: with
    `motor`, that test copy of it that drives a motor, each firmware then put
    to motor_problem."""
    from tools.espressif.emulator import public_dump, public_devices

    public_dump.make(kind.key, motor)
    dump = public_dump.recipe(kind.key, motor)
    stub = ("--stub-cloud", f"--device={dump.mac}")

    folder = LOGS / (f"{kind.key}-{motor}" if motor else kind.key)
    # What each firmware is to be set to: what the copy's Sonoff settings say.
    carried = motor_setup.sonoff_setup(json.loads(getattr(dump, "settings", None) or "{}")).over(
        motor_setup.DEFAULT)
    emulator_stats.began(folder, family=kind.family, suite="check", device=kind.key,
                         total=sum(map(len, ESPRESSIF_WAYS)), planned=sum(map(len, ESPRESSIF_WAYS)), cloud="stub",
                         workers=len(instances))

    def way(instance: int, firmwares: tuple[str, ...]) -> int:
        saved, wanted = None, carried
        for firmware in firmwares:
            keep = f"check-{kind.key}-{instance}-{firmware}"
            log = folder / f"{instance}-{firmware}.log"
            began = time.time()
            converted, said = public_devices.run(instance, saved, firmware, keep, log,
                                               extra=(*stub, *(() if saved else ("--fresh",))), kind=kind)
            start = f"on {saved.rsplit('-', 1)[1]}" if saved else "from Sonoff's"
            print(f"{'PASS' if converted else 'FAIL'}  {kind.key} {start} to {firmware}: {said}", flush=True)
            emulator_stats.check_run(folder, family=kind.family, device=kind.key,
                                     route=f"{saved.rsplit('-', 1)[1] if saved else 'sonoff'}>{firmware}",
                                     passed=converted, seconds=round(time.time() - began), cloud="stub")
            if not converted:
                return 1
            if motor:
                # Where another firmware follows, this one is then set to
                # moved_setup and kept so, for the next to carry across from
                # it rather than from the backup.
                then = moved_setup(wanted) if firmware != firmwares[-1] else None
                problem = motor_problem(instance, keep, firmware, stub, folder / f"{instance}-{firmware}-motor.log",
                                        wanted, then)
                done = f"set to {wanted.open:g} s and {wanted.close:g} s, {'' if wanted.reversed else 'not '}" \
                       "reversed, its outputs interlocked, both off after a power cut" + \
                       (f"; then set to {then.open:g} s, {'' if then.reversed else 'not '}reversed" if then else "")
                print(f"{'FAIL' if problem else 'PASS'}  {kind.key} on {firmware} drives its motor: "
                      f"{problem or done}", flush=True)
                if problem:
                    return 1
                wanted = then or wanted
            saved = keep
        return 0

    try:
        return sum(public_devices.side_by_side([lambda i=instance, w=firmwares: way(i, w) for firmwares, instance
                                              in zip(ESPRESSIF_WAYS, instances)]))
    finally:
        print(emulator_stats.send(folder), flush=True)
        for instance in instances:
            for firmware in ("tasmota", "esphome"):
                subprocess.run(["rm", "-rf", str(public_devices.SAVED / f"check-{kind.key}-{instance}-{firmware}")],
                               check=False)


# How long the emulated meter takes to show a change: one CSE7761 reading
# (update_interval, 5 s, in devices/sonoff-dualr3/esphome*.yaml) and more.
METER_SHOWS = 8.0
# How many times Tasmota is asked its relays' states, a second apart, after
# each move.
TASMOTA_LOOKS = 5
# How long a device switched off and on is given to answer again.
BACK_WITHIN = 240.0
# How long a firmware asked to move the motor is given to start it: Tasmota's
# shutter first waits for it to stop (ShutterMotorStop, 0.5 s), as ESPHome's
# interlock does (interlock_wait_time, 500 ms).
STARTS_WITHIN = 10.0
# The travel times the check sets first (seconds), through each firmware's own
# setting for them: closing twice opening's, so that the move back from where
# the open left the motor outlasts every look at its outputs - closing from 10%
# at the same speed ended within them once, the check reading its outputs
# after the motor had stopped (docs/emulator.md#a-motor).
TRAVEL_OPEN, TRAVEL_CLOSE = 60, 120
# What the check sets the first firmware of a way to once it has checked it,
# for the next to carry across (moved_setup): a travel time no test copy has.
MOVED_TRAVEL = 7.0


def moved_setup(carried: motor_setup.Setup) -> motor_setup.Setup:
    """What the first firmware of a way is set to, as a person would set
    theirs, before the device is taken to the other: MOVED_TRAVEL each way,
    and the other way round from `carried`, so that the next firmware having
    it can only have come from the firmware before, not from the backup."""
    return motor_setup.Setup(MOVED_TRAVEL, MOVED_TRAVEL, not carried.reversed)


def motor_problem(instance: int, saved: str, firmware: str, stub: tuple[str, ...], log: Path,
                  wanted: motor_setup.Setup = motor_setup.DEFAULT, then: motor_setup.Setup | None = None) -> str | None:
    """What is wrong with how the device kept as `saved`, on `firmware`, drives
    its motor; None if nothing. It is started again from where the run left
    it, left running (--keep) for this, and stopped after: it is set to the
    travel times and direction `wanted`; each output on alone moves the motor
    one way (the emulated meter shows that output's load, and no other) -
    output 2 opening where reversed; asked the other way while moving, the
    first is off before the second is on; and, one on, a power cut leaves
    both off. With `then`, the firmware is then set to it through its own
    settings, seen to keep it through a power cut, and the device kept as
    `saved` again so, for the next conversion to start from. What it saw is
    in `log`, after the run's own output."""
    from tools.espressif.emulator import fault_suite, public_devices
    container = fault_suite.name(instance)
    power_cut = lambda: subprocess.run(["docker", "exec", container, fault_suite.DEVICE_CMD, "restart"],  # noqa: E731
                                       capture_output=True, check=False)
    try:
        with open(log, "w", encoding="utf-8") as out:
            subprocess.run([str(public_devices.UNATTENDED), f"--instance={instance}", f"--from={saved}", *stub,
                            "--keep", "--", "--firmware", firmware], stdout=out, stderr=subprocess.STDOUT, check=False)
        with open(log, "a", encoding="utf-8") as out:
            began = time.monotonic()

            def note(text: str) -> None:
                out.write(f"{time.monotonic() - began:7.1f} s  {text}\n")
                out.flush()
            device = MotorDevice(f"127.0.0.1:{8080 + instance}", firmware, note)
            if not device.answers():
                return "the device did not answer after its run"
            problem = device.problem(power_cut, wanted)
            if problem or then is None:
                return problem
            problem = device.set_to(then, power_cut)
            if problem:
                return problem
            # Kept as unattended-test.sh --save keeps a device: switched off,
            # its flash as it was.
            subprocess.run(["docker", "exec", container, fault_suite.DEVICE_CMD, "off"], capture_output=True,
                           check=False)
            shutil.copyfile(fault_suite.EMU / f"state-{instance}" / "flash.bin",
                            public_devices.SAVED / saved / "flash.bin")
            return None
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)


class MotorDevice:
    """A device on Tasmota or ESPHome that drives a motor, through its web page
    as the emulator's browser port gives it: each moved, which way its
    firmware says the motor is moving, and which outputs are on meanwhile."""

    def __init__(self, address: str, firmware: str, note=lambda text: None) -> None:
        self.address, self.firmware, self.note = address, firmware, note

    def get(self, path: str, post: bool = False) -> str | None:
        # Tasmota answers a page but its first only to a request whose Referer
        # names it, as a browser's does.
        request = urllib.request.Request(f"http://{self.address}{path}", data=b"" if post else None,
                                         headers={"Referer": f"http://{self.address}/"})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as reply:
                return reply.read().decode("utf-8", errors="replace")
        except OSError:
            return None

    def tasmota(self, command: str) -> dict:
        body = self.get("/cm?" + urllib.parse.urlencode({"cmnd": command}))
        try:
            found = json.loads(body) if body else {}
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}

    def answers(self, within: float = BACK_WITHIN) -> bool:
        started = time.monotonic()
        while time.monotonic() - started < within:
            if self.get("/cm?cmnd=Power" if self.firmware == "tasmota" else "/cover/Motor"):
                return True
            time.sleep(3)
        return False

    def set_travel(self) -> None:
        if self.firmware == "tasmota":
            self.tasmota(f"Backlog ShutterOpenDuration1 {TRAVEL_OPEN}; ShutterCloseDuration1 {TRAVEL_CLOSE}")
        else:
            for name, seconds in (("Open duration", TRAVEL_OPEN), ("Close duration", TRAVEL_CLOSE)):
                self.get(f"/number/{urllib.parse.quote(name)}/set?value={seconds}", post=True)
        time.sleep(2)

    def move(self, way: str) -> None:
        """"open" or "close": Tasmota's relays switched as a person would -
        Power1 opens, Power2 closes - ESPHome's cover asked."""
        if self.firmware == "tasmota":
            self.tasmota({"open": "Power1 ON", "close": "Power2 ON"}[way])
        else:
            self.get(f"/cover/Motor/{way}", post=True)

    def moving(self) -> str | None:
        """Which way the firmware says the motor is moving, "open" or "close";
        None while it says it is still, or says nothing: Tasmota's shutter by
        its direction, ESPHome's cover by its current operation."""
        if self.firmware == "tasmota":
            shutter = self.tasmota("Status 10").get("StatusSNS", {}).get("Shutter1", {})
            self.note(f"Tasmota's shutter: {shutter}")
            return {1: "open", -1: "close"}.get(shutter.get("Direction")) if isinstance(shutter, dict) else None
        try:
            cover = json.loads(self.get("/cover/Motor") or "{}")
        except ValueError:
            cover = {}
        self.note(f"ESPHome's cover: {cover}")
        return {"OPENING": "open", "CLOSING": "close"}.get(cover.get("current_operation")) \
            if isinstance(cover, dict) else None

    def outputs(self) -> tuple[bool, bool] | None:
        """Whether each output is on: on ESPHome, whose relays are the cover's
        own, as the emulated meter reads each channel's load, once it has had
        time to show it; on Tasmota, as it says it drives its relays - under
        Tasmota the emulated CSE7761 reads both channels' load while either
        relay is on (docs/emulator.md), so its meter cannot tell them apart.
        None where nothing can be read."""
        if self.firmware == "tasmota":
            states = self.tasmota("Power0")
            if "POWER1" not in states or "POWER2" not in states:
                return None
            found = (states["POWER1"] == "ON", states["POWER2"] == "ON")
        else:
            time.sleep(METER_SHOWS)
            readings = []
            for name in ("Power 1", "Power 2"):
                body = self.get("/sensor/" + urllib.parse.quote(name))
                try:
                    readings.append(float(json.loads(body)["value"]) >= 1.0)
                except (TypeError, ValueError, KeyError):
                    return None
            found = (readings[0], readings[1])
        self.note(f"outputs: {'on' if found[0] else 'off'}, {'on' if found[1] else 'off'}")
        return found

    def settled(self) -> tuple[bool, bool] | None:
        """The outputs once they have had time to settle: on Tasmota read a
        few times over, a second apart, both on at any one taken as both on."""
        if self.firmware != "tasmota":
            return self.outputs()
        seen = None
        for _ in range(TASMOTA_LOOKS):
            time.sleep(1)
            seen = self.outputs()
            if seen is None or all(seen):
                return seen
        return seen

    def driving(self, way: str) -> tuple[bool, bool] | str:
        """The outputs on while the firmware moves the motor `way`, just asked
        of it: a look counted only if the firmware says it moves that way after
        it too (one after the motor stopped finds both off), every look the
        same, and both on at any one taken as both on; Tasmota looked at
        TASMOTA_LOOKS times, a second apart, ESPHome's meter once. What is
        wrong instead: it never moved that way, or the outputs cannot be read."""
        started = time.monotonic()
        while self.moving() != way:
            # Both on before it starts counts too.
            if self.firmware == "tasmota" and self.outputs() == (True, True):
                return True, True
            if time.monotonic() - started > STARTS_WITHIN:
                return f"the motor did not start to {way}"
            time.sleep(0.5)
        counted: set[tuple[bool, bool]] = set()
        for _ in range(TASMOTA_LOOKS if self.firmware == "tasmota" else 1):
            if self.firmware == "tasmota":
                time.sleep(1)
            found = self.outputs()
            if found is None:
                return "the outputs could not be read"
            if all(found):
                return found
            if self.moving() != way:
                break
            counted.add(found)
        if len(counted) > 1:
            return f"the outputs changed while it moved: {sorted(counted)}"
        return counted.pop() if counted else "the motor stopped before its outputs could be read"

    def setup(self) -> motor_setup.Setup:
        """The travel times and direction the firmware is set to."""
        if self.firmware == "tasmota":
            asked = SimpleNamespace(command=lambda text, timeout=None: self.tasmota(text))
            return motor_setup.tasmota_setup(asked, kinds.find("sonoff-dualr3"))
        return motor_setup.esphome_setup(self.address)

    def set_to(self, setup: motor_setup.Setup, power_cut) -> str | None:
        """The firmware set to `setup` through its own settings, as a person
        would set theirs: Tasmota's travel times and its template's relays
        (motor_setup.tasmota_template), which it restarts for; ESPHome's
        Open duration, Close duration and Reverse direction. What is wrong
        where it has not got them, before or after a power cut; None if
        nothing."""
        self.note(f"set to {setup}")
        if self.firmware == "tasmota":
            template = motor_setup.tasmota_template(kinds.find("sonoff-dualr3"), bool(setup.reversed))
            self.tasmota(f"Template {template}")
            self.tasmota(f"Backlog {motor_setup.TASMOTA_OPEN} {setup.open:g}; "
                         f"{motor_setup.TASMOTA_CLOSE} {setup.close:g}; Module 0")
        else:
            for name, seconds in ((motor_setup.ESPHOME_OPEN, setup.open), (motor_setup.ESPHOME_CLOSE, setup.close)):
                self.get(f"/number/{urllib.parse.quote(name)}/set?value={seconds:g}", post=True)
            self.get(f"/switch/{urllib.parse.quote(motor_setup.ESPHOME_REVERSE)}/"
                     f"{'turn_on' if setup.reversed else 'turn_off'}", post=True)
        found = self.until_set(setup)
        if found != setup:
            return f"set to {setup}, the motor is set to {found}"
        power_cut()
        found = self.until_set(setup)
        self.note(f"after a power cut, set to {found}")
        return None if found == setup else f"set to {setup}, after a power cut the motor is set to {found}"

    def until_set(self, setup: motor_setup.Setup) -> motor_setup.Setup:
        """What the firmware says the motor is set to, asked until it says
        `setup` or BACK_WITHIN has passed: through a restart it answers
        nothing, then some settings before others."""
        started = time.monotonic()
        found = self.setup()
        while found != setup and time.monotonic() - started < BACK_WITHIN:
            time.sleep(3)
            found = self.setup()
        return found

    def problem(self, power_cut, wanted: motor_setup.Setup = motor_setup.DEFAULT) -> str | None:
        if self.firmware == "tasmota":
            settings = {name: self.tasmota(name).get(name)
                        for name in ("Interlock", "ShutterRelay1", "ShutterMode", "PowerOnState")}
            if settings != {"Interlock": "ON", "ShutterRelay1": 1, "ShutterMode": 1, "PowerOnState": 0}:
                return f"Tasmota is not set up for its motor: {settings}"
        elif not self.get("/cover/Motor"):
            return "ESPHome has no cover"
        found = self.setup()
        if found != wanted:
            return f"the motor is set to {found}, not {wanted}"
        self.set_travel()
        # Tasmota's relays by their numbers, which its template swaps over a
        # reversed motor's outputs; ESPHome's outputs by their pins, as metered.
        swapped = bool(wanted.reversed) and self.firmware == "esphome"
        for way, wanted_on in (("open", (True, False)), ("close", (False, True))):
            wanted_on = wanted_on[::-1] if swapped else wanted_on
            self.note(f"asked to {way}")
            self.move(way)   # closing while it opens: the second while the first is on
            found = self.driving(way)
            if isinstance(found, str):
                return f"asked to {way}, {found}"
            if found != wanted_on:
                return f"asked to {way}, output 1 is {'on' if found[0] else 'off'} and output 2 " \
                       f"{'on' if found[1] else 'off'}"
        if self.firmware == "tasmota":
            # Both asked for at once, as two wall switches could: never both.
            self.note("asked for both at once")
            self.tasmota("Backlog Power1 ON; Power2 ON")
            if self.settled() == (True, True):
                return "both outputs came on together"
        self.note("asked to open, then a power cut")
        self.move("open")
        time.sleep(2)
        power_cut()
        if not self.answers():
            return "the device did not answer after a power cut"
        found = self.settled()
        if found != (False, False):
            return f"after a power cut the outputs read {found}"
        return None


def rebuilt(kind: kinds.Kind) -> bool:
    """Whether a Beken device's check starts from a rebuilt flash: any but the
    S61, whose public dump it is rebuilt from."""
    return kind.family == "beken" and kind.key != REBUILT_FROM


def rebuilt_flash(kind: kinds.Kind) -> Path:
    """The whole stock flash of a device with no public dump, as rebuild_dump.py
    makes it from the public S61 dump (beken-emu.sh dump fetched it) and the
    device's official update file (fetched here), each checked against its
    pinned hash. Both are Sonoff's, and so is the flash made from them: it is
    kept in captures/ only. Made again at each check, in a second or two, so
    it is never older than what it is made from."""
    import hashlib

    from tools.autoflash.routes import public_copies
    from tools.beken import rebuild_dump
    from tools.beken.emulator import folders

    name, digest = S61_DUMP
    dump = (folders.FIRMWARES / name).read_bytes()
    if hashlib.sha256(dump).hexdigest() != digest:
        raise SystemExit(f"{folders.FIRMWARES / name} is not the public dump this tool pins")
    try:
        flash = rebuild_dump.rebuild(dump, public_copies.fetch(kind))
    except public_copies.Unavailable as exc:
        raise SystemExit(f"{exc}.") from exc
    out = folders.REBUILT / f"{kind.key}.bin"
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f"{out.name}.{os.getpid()}.part")
    part.write_bytes(flash)
    part.replace(out)   # whole, for a check already started from it
    print(f"{kind.name}: a whole flash rebuilt from the public S61 dump and its official update file, "
          f"in {out.relative_to(ROOT)}.", flush=True)
    return out


def check_beken(kind: kinds.Kind, instances: list[int]) -> int:
    """A Beken device: the S61 from upstream's public dump, any other from its
    rebuilt flash. Its logs are kept as an Espressif device's are (LOGS)."""
    subprocess.run([str(ROOT / "tools/beken/emulator/beken-emu.sh"), "dump"], check=True)
    env = {name: value for name, value in os.environ.items() if name != "AUTOFLASH_BEKEN_EMU_FLASH"}
    env["AUTOFLASH_BEKEN_EMU_SUITE_DIR"] = str(LOGS / kind.key)
    if rebuilt(kind):
        env["AUTOFLASH_BEKEN_EMU_FLASH"] = str(rebuilt_flash(kind))
        env["AUTOFLASH_BEKEN_EMU_FLASH_DEVICE"] = kind.key   # whose runs its emulator stats are
    return subprocess.call([str(ROOT / "tools/beken/emulator/fault_suite.py"), "check",
                            "--instances", ",".join(map(str, instances))], env=env)


def check_device(kind: kinds.Kind, instances: list[int], motor: str = "") -> int:
    if family_of(kind) == "beken":
        return check_beken(kind, instances)
    from tools.espressif.emulator import public_dump
    if motor or kind.key in public_dump.DUMPS or kind.key in public_dump.UPDATES:
        return check_public_dump(kind, instances, motor)
    # The public devices, made from a device's own backup, hold nothing of Sonoff's.
    devices = str(ROOT / "tools/espressif/emulator/public_devices.py")
    numbers = ["--instances", ",".join(map(str, instances))]
    fetched = (ROOT / "captures/emulation/saved/public-pre-final-esphome").exists() or \
        subprocess.call([sys.executable, "-I", devices, *numbers, "fetch"]) == 0
    return subprocess.call([sys.executable, "-I", devices, *numbers, "test"]) if fetched else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("device", help="list, or a device's key")
    parser.add_argument("--changed-since", help="list: only what a change since this commit could affect")
    parser.add_argument("--instances", default="1,2", help="the two emulators to use (default 1,2)")
    parser.add_argument("--motor", nargs="?", const="motor", default="", choices=("motor", "calibrated"),
                        help="a test copy of the device that drives a motor (public_dump.MOTOR_COPIES)")
    args = parser.parse_args(argv)
    if args.device == "list":
        print(json.dumps(jobs(changed_since(args.changed_since) if args.changed_since else None)))
        return 0
    kind = next((kind for kind in kinds.READY if kind.key == args.device and kind.key not in NOTHING_TO_RUN), None)
    if kind is None:
        parser.error(f"no such device: {args.device}; list says which there are")
    if args.motor and not kind.motor_mode:
        parser.error(f"{kind.key} has no motor mode")
    missing = missing_packages(kind)
    if missing:
        print(missing, file=sys.stderr)
        return 2
    return check_device(kind, [int(n) for n in args.instances.split(",")][:2], args.motor)


# What an Espressif device's check needs beyond the standard library: ESP-IDF's
# NVS generator, which its public dump is made with (public_dump, through
# rewrite_nvs and sanitise_device) and emu.sh asks for too. In requirements/ci.txt.
ESPRESSIF_PACKAGES = ("esp_idf_nvs_partition_gen",)


def missing_packages(kind: kinds.Kind) -> str | None:
    """What to install for `kind`'s check, said before anything is made: a
    package missing stopped one part way through making its public dump, a
    traceback for an answer. None where nothing is missing."""
    import importlib.util
    lacking = [name for name in ESPRESSIF_PACKAGES if family_of(kind) == "espressif"
               and importlib.util.find_spec(name) is None]
    if not lacking:
        return None
    return (f"The {kind.key} check needs {', '.join(lacking)}, which {sys.executable} lacks. Run it with the "
            f"checkout's .venv, made as CI makes it:\n    python3 -m venv .venv && .venv/bin/pip install "
            "--require-hashes --no-deps -r requirements/ci.txt\n    .venv/bin/python -I tools/repo/emulator_check.py "
            f"{kind.key}")


if __name__ == "__main__":
    sys.exit(main())
