"""What both fault suites judge a run by beyond its steps being done: the
end-of-run checks the report of a converting run gives, the readout.tar a
readout keeps, and the screen each run left. A run whose steps all passed can
still end with a relay that did not switch, or a check that never got to run,
and say so only there; or leave a stage's failed try on the screen
(docs/testing.md#the-fault-suites)."""
from __future__ import annotations

import io
import json
import lzma
import re
import tarfile

from tools.repo import unattended_run

# As the run's log has them: the report's answer (tools/autoflash/report.py,
# checks_passed), and the line its check said of the relay.
CHECKS_PASSED = re.compile(r"^\[[\d:]+\] +Checks passed: +(.+?) *$", re.M)
RELAY = re.compile(r"^\[[\d:]+\] +Relay: +(.+?) *$", re.M)

# The answers a run ending on each firmware may give in the emulator. On
# Tasmota, ESPHome and OpenBeken the check needs only the firmware, which the
# emulator runs: it switches the relay, so anything but True is a failure -
# "Not run" there is a check never tried. Back on Sonoff's it switches the
# relay through eWeLink, the stub's or the real one, by the device id the plug's
# record keeps: "Not run" is a plug with none (one converted before it was
# kept), which the emulator cannot change. False is a failure everywhere: the
# relay did not switch, or could not be checked, as when eWeLink had not shown
# the plug online yet.
PASSING = {"tasmota": {"True"}, "esphome": {"True"}, "openbeken": {"True"}, "sonoff": {"True", "Not run"}}


def last_run(log: str) -> str:
    """The last run in a run log: one log holds every run carried on in it."""
    return log[log.rfind("=== run started"):] if "=== run started" in log else log


def checks_problem(log: str, firmware: str) -> str | None:
    """What the end-of-run checks of the last run in `log` said, when they did
    not pass for a plug on `firmware`: "Checks passed: False; Relay: Did not
    switch". None when they passed, or the run gave no report (a plug already
    on the firmware asked for has nothing to check)."""
    run = last_run(log)
    answers = CHECKS_PASSED.findall(run)
    if not answers or answers[-1] in PASSING.get(firmware, {"True", "Not run"}):
        return None
    relay = RELAY.findall(run)
    return f"Checks passed: {answers[-1]}; Relay: {relay[-1] if relay else 'not said'}"


# What a readout keeps (docs/readouts.md, "What is in one"), by chip family:
# each .bin in it xz-compressed, and the firmware eWeLink offers under offered/.
READOUT_FILES = {"espressif": ("manifest.json", "flash.bin.xz", "efuse.bin.xz", "info.txt", "report.json"),
                 "beken": ("manifest.json", "flash.bin.xz", "info.txt")}
# The flash of every plug the emulators run: 4 MB.
WHOLE_FLASH = 0x400000


def readout_problems(data: bytes, family: str) -> list[str]:
    """What a readout.tar lacks of a whole readout of a `family` device, as
    docs/readouts.md lists it, unsent from an unattended run: [] for none."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            files = {member.name: tar.extractfile(member).read() for member in tar.getmembers() if member.isfile()}
    except (tarfile.TarError, OSError) as exc:
        return [f"not a tar: {exc}"]
    problems = [f"no {name}" for name in READOUT_FILES[family] if name not in files]
    if list(files)[:1] != ["manifest.json"]:
        problems.append("manifest.json is not first")
    if not any(name.startswith("offered/") for name in files):
        problems.append("no offered firmware")
    try:
        manifest = json.loads(files.get("manifest.json") or b"")
    except ValueError:
        return problems + ["manifest.json is not JSON"]
    if manifest.get("read") is not True:
        problems.append("the manifest says the memory was not read")
    if "contact" in manifest:
        problems.append("a contact in it, from an unattended run")
    flash = manifest.get("flash") or {}
    if family == "espressif" and flash.get("matches_device") is not True:
        problems.append("the flash read does not match the device's own hash")
    try:
        size = len(lzma.decompress(files["flash.bin.xz"])) if "flash.bin.xz" in files else None
    except lzma.LZMAError:
        return problems + ["flash.bin.xz is not xz"]
    if size is not None and (size != WHOLE_FLASH or flash.get("size") != size):
        problems.append(f"flash.bin is {size:,} bytes, the manifest says {flash.get('size')}: "
                        f"not the whole {WHOLE_FLASH:,}")
    return problems


# As tools/repo/unattended_run.py prints the screen a run ended on, in the
# output the fault suites keep of each run.
SCREEN = re.compile(r"^The screen at the end of the run \(\d+x\d+\):\n=+\n(?P<screen>.*?)^=+$", re.M | re.S)


def screen_problems(output: str) -> list[str]:
    """What each run in `output` - every recorded run of a scenario, as its
    log keeps them - left of a stage on its screen that it should have taken
    away (unattended_run.stage_problems): [] for nothing, or no screen."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)  # printed in colour unless NO_COLOR is set
    return [problem for found in SCREEN.finditer(plain)
            for problem in unattended_run.stage_problems(found["screen"])]
