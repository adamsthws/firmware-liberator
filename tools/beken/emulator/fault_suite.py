#!/usr/bin/env python3
"""The Beken fault suite: every direction, every step that writes to or waits
on the device, every fault - on emulated Beken devices side by side (beken-emu.sh
device, from the public S61s dump), as tools/espressif/emulator/fault_suite.py
does for the Espressif devices.

    fault_suite.py prep [--first-instance N]
                                        the starting devices: one on the interim firmware,
                                        one on OpenBeken, one on ESPHome, converted by
                                        the tool from the dump
    fault_suite.py prep-update [--first-instance N]
                                        the update scenarios' starting devices: on OpenBeken
                                        and on ESPHome as an earlier release converts them
    fault_suite.py check [--instances 1,2]
                                        the final firmware, with no faults: each installed
                                        from Sonoff's, then each device taken to the other,
                                        on the two emulators numbered
                                        (tools/repo/emulator_check.py)
    fault_suite.py list                 every scenario
    fault_suite.py list quick           those of a routine run (QUICK_OUTAGES)
    fault_suite.py list openbeken       those a new OpenBeken build is checked with
                                        (docs/updating-firmware.md)
    fault_suite.py batch [FILE] [--first-instance N]
                                        the scenarios in FILE (one id a line; every
                                        scenario without), skipping those done,
                                        and those its skip.txt names as it runs
    fault_suite.py report               the latest result of each
    fault_suite.py status [PATH ...] [--ids FILE]
                                        where a batch has got to, from results files (one a
                                        machine): verdicts, those not passed, and how many are
                                        left, with about how long (tools/repo/fault_status.py)
    fault_suite.py found                what each fault and length has found, in the
                                        results here and kept (tools/repo/fault_yield.py)
    fault_suite.py share FILE N...      FILE's scenarios shared out between machines
                                        running N emulators each, to end together:
                                        FILE.1, FILE.2, ... (--times-from DIR: another
                                        machine's suite folders, for their times)

Each scenario: a run of the tool from a starting device (a fresh dump for
Sonoff's firmware, a saved one otherwise); when the step under test has been
going for a share of its usual time (40%, and 10% and 90% at the steps that
write), the fault; then the tool run again, as a person would - switching the
device off and on where the run says to - until the device is converted, or four
runs fail. Every scenario also checks the fault landed while its step was
under way (MISTIMED if not), and that the converting run's report says its
end-of-run checks passed, on a device with a meter (the S61) the emulated
meter confirming the relay switched (FAIL-CHECKS if not:
tools/repo/fault_verdicts.py),
that each run left one line a stage on its screen, none of a try that
failed (FAIL-SCREEN if not), that a device converted to OpenBeken or ESPHome
runs this checkout's release of it (FAIL-NOT-LATEST if not), and that no run
met an error the tool did not see coming, though the next converted
(FAIL-UNEXPECTED if one did, as the Espressif suite has it).

The faults: the device switched off for 1 or 30 s (deviceoff), the network
gone - the router off - for 5, 15, 30, 120 or 180 s (outage), as the
Espressif suite's, the tool killed and run again after 1 or 180 s (crash), and
Ctrl-C (ctrlc); where ESPHome raises its own network (K2), the router alone off
for 150 s (fallback), the person joining that network as the run says to
(Person); at the steps that write, a power cut and
another as the tool sends it again (double: in the same run, if it sends its
command again itself), and the router giving the device
a new address once the step is done (readdress); and at each route's
"apply", the device switched off 10, 50 or 90% of the way through its
bootloader writing the new firmware in - timed off the device's own output.
And at the steps that write, the Espressif suite's tries of the tool's own
handling and of the network spoilt (OWN_HANDLING, NETWORK): Ctrl-C twice, then
the run carried on, started afresh or to the other firmware (cancel);
Ctrl-C twice apart, and at its countdown (ctrlc); the window closed (hangup);
a second run beside it (second); the computer asleep (sleep) or off the
network (pcoff); packets lost (weak) or changed (corrupt); an outage
twice running (double). At B2 the account signed in to
again beside the run (token), and at B4 the disk filling as the backup is
written (diskfull). The readout's read has them too.
And at each step that sends an image the stock bootloader installs - Sonoff's
firmware's (B2) and OpenBeken's fetch of ESPHome (J2, O1) - an image it
refuses (badimage): the tool sends it spoilt once (--simulate-bad-image
STEP), fetched whole but failing the bootloader's CRC check, so the device
starts the firmware it had again; the scenario passes when the tool sends
it again by itself and the device is converted.

And readouts (readout_scenarios): of a device fresh from Sonoff's firmware,
with a fault in the read and as Sonoff's firmware is put back, then what a
person does until a whole readout.tar is kept and the device works on Sonoff's
firmware; and of a device on OpenBeken, which must read its first stage alone.

The routes from the interim firmware start from a device a run to ESPHome left
there, found by a new run. A device converted through the interim firmware is
also checked: by the names it asked DHCP for, it was autoflash-recovery-CDEF
there from its first request and never OpenBeken's own name; and once ESPHome
is on it, the interim's settings are erased from its flash (interim_problems).

Each emulator is a device of its own (beken-emu.sh --instance N: autoflash-beken-emu-N),
AUTOFLASH_BEKEN_EMU_WORKERS of them at once (default 2: each takes a core, and the
tool's runs share what is left), numbered from --first-instance (default 1), as
the Espressif suite's and the attended suite's are, so a batch keeps clear of
another one's emulators on the same computer: one asked to start on an instance
already running refuses (tools/repo/emulator_instances.py), as starting it
afresh would stop the other run's device. Sonoff's firmware restarts by itself once
it has an update, in the emulator as on a real device
(tools/beken/emulator/README.md).

Results: captures/emulation/beken/suite/results.jsonl (AUTOFLASH_BEKEN_EMU_SUITE_DIR
for another folder), and each scenario's run
logs and the device's serial output beside it, with what a failure leaves (its
run records, in evidence/), as the Espressif suite's; a scenario run again keeps its
earlier attempts' beside them, numbered (logs/<scenario>.1 the first). A fault that never happened is
NOT-FIRED, not a pass. Each result's notes say how short of memory the computer
was; a failure while it was starved prints "memory-starved", as the device's
connections can then fail with nothing wrong.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tools.autoflash import output  # noqa: E402
from tools.autoflash.output import CTRL_C_LOGGED  # noqa: E402
from tools.autoflash import esphome, esphome_ota, kinds, openbeken  # noqa: E402
from tools.autoflash.routes import beken  # noqa: E402
from tools.autoflash.routes.beken import images as beken_images  # noqa: E402
from tools.beken.emulator import folders  # noqa: E402
from tools.ewelink.stub_cloud import accounts  # noqa: E402
from tools.repo import batch_workers, cpu_load, disk_space, emulator_instances, emulator_stats, fault_status, fault_verdicts, fault_yield, own_build, prune_captures, scenario_times  # noqa: E402
from tools.repo.earlier_attempts import keep_earlier  # noqa: E402
from tools.repo.memory_pressure import MemoryWatch  # noqa: E402

BK_EMU = REPO / "tools/beken/emulator/beken-emu.sh"
SAVED = folders.SAVED
OUT = Path(os.environ.get("AUTOFLASH_BEKEN_EMU_SUITE_DIR", folders.SUITE))
RESULTS = OUT / "results.jsonl"
# A made-up eWeLink account: the device's eWeLink is the local stub, which takes any.
# And the emulated home Wi-Fi's details (cloud_online's), for a run sending
# them to ESPHome through its own network: given in advance, as for a run nobody
# sits through.
ACCOUNT = ("EWELINK_EMAIL=someone@example.invalid\nEWELINK_PASSWORD=made-up-password\n"
           "AUTOFLASH_WIFI_SSID=AutoflashNet\nAUTOFLASH_WIFI_PASSWORD=autoflash1234\n")

# Each direction's steps that write to or wait on the device, with how long each
# took in passing runs, in seconds: a fault lands at a share of it.
ROUTES = {
    "sonoff>openbeken": {"B2": 30, "K1": 27, "B4": 12},
    "sonoff>esphome": {"B2": 32, "J1": 28, "B4": 12, "J2": 14, "K2": 30},
    "openbeken>esphome": {"B4": 12, "O1": 12, "K2": 31},
    "openbeken>sonoff": {"O2": 24, "K3": 75},
    "esphome>openbeken": {"E1": 6, "K1": 27, "B4": 12},
    "esphome>sonoff": {"E2": 15, "K3": 80},
    # A device a run to ESPHome left on the interim firmware, found by a new run.
    # Kept as OpenBeken, J3 waits for its restart: K1 finds it answering.
    "interim>esphome": {"B4": 12, "J2": 14, "K2": 30},
    "interim>openbeken": {"J3": 15, "B4": 12},
    "interim>sonoff": {"B4": 12, "J4": 24, "K3": 75},
    # A device on the firmware of the release before, updated (prep_update).
    "outdated>openbeken": {"U3": 14, "U4": 20},
    "outdated>esphome": {"U1": 7, "U2": 20},
}
# The kind of device the suite emulates: an S61, from its public dump. Another
# Beken device's flash (AUTOFLASH_BEKEN_EMU_FLASH) is said by
# AUTOFLASH_BEKEN_EMU_FLASH_DEVICE, its key, which its emulator stats are of.
DEVICE = kinds.SONOFF_S61
STATS_DEVICE = kinds.find(os.environ.get("AUTOFLASH_BEKEN_EMU_FLASH_DEVICE")) or DEVICE
# The release of each firmware a converted device must end on: this checkout's
# (FAIL-NOT-LATEST) - after an update from an earlier release's, too.
LATEST = {firmware: beken.published_version(DEVICE, firmware) for firmware in ("openbeken", "esphome")}
# The release whose firmware an update's device is on: any before this checkout's.
EARLIER_RELEASE = "v2.3.0"
# Where its tree keeps the dumps and the devices its prep saves.
EARLIER_FIRMWARES = "captures/emulation/beken-firmwares"
EARLIER_SAVED = "captures/emulation/s61/saved"
# Where its prep chooses its instance: always 1, as it takes no --first-instance.
EARLIER_PREP_INSTANCE = "instance = Instance(1)"
# The outages as the Espressif suite's, each length where it can lead somewhere
# the others do not: within the tool's shortest timeouts, past them, and long
# enough for ESPHome to raise its own network (a minute or two).
OUTAGES = (5, 15, 30, 120, 180)
# A routine run's outages (list quick), as the Espressif suite's: the shortest
# and the longest.
QUICK_OUTAGES = (OUTAGES[0], OUTAGES[-1])
CORE = ([("deviceoff", 1), ("deviceoff", 30)] + [("outage", s) for s in OUTAGES]
        + [("crash", 1), ("crash", 180), ("ctrlc", "once")])
# Where a firmware raises its own network when it cannot join the home one:
# ESPHome, at K2. There the router stays off 150 s (fallback), the run sets it up
# through its own network, the person joining it when the run says to.
FALLBACK_STEPS = {"K2"}
# At the steps that write, the riskiest moments too: just begun, nearly done.
TIMING = [("deviceoff", 1), ("crash", 1)]
# The tool's own handling, the same at every step, as the Espressif suite's
# (its cancel, hangup, sleep, pcoff and OWN_HANDLING): tried where it matters
# most, at the steps that write. Ctrl-C twice, then the run carried on
# (continue), started again (new, --new) or to the other firmware (switch);
# Ctrl-C twice apart, which must not stop it (FAIL-STOPPED), and at the
# countdown it asks after one, which must (FAIL-NOT-STOPPED); the window
# closed, which must stop it and be said by the next run (FAIL-NO-NOTE); a
# second run beside it, which must be refused (FAIL-NOT-REFUSED) and leave it
# be (FAIL-DISTURBED); the computer asleep for 10 minutes, or off the network
# for 30 s or 2 minutes.
OWN_HANDLING = [("cancel", "continue"), ("cancel", "new"), ("ctrlc", "apart"), ("ctrlc", "countdown"),
                ("hangup", "continue"), ("second", "run"), ("sleep", 600), ("pcoff", 30), ("pcoff", 120)]
# The network between this computer and the device spoilt, as the Espressif
# suite's: one packet in ten lost for a minute (weak); a packet changed, its
# checksum made good, so only the tool's and the device's own checks can tell
# (corrupt, NO-TRAFFIC where none was); a power cut, or an outage, then
# another as the tool sends again, or twice running as the device fetches on (double).
NETWORK = [("weak", 60), ("corrupt", "once"), ("double", "deviceoff-1"), ("double", "outage-30")]
# How long a double waits for the run to send its command again before the
# second fault comes anyway, the step still under way (cut_as_sent_again).
RESENT_WITHIN = 60
# Where Ctrl-C twice can switch to the other firmware (cancel-switch); and
# what a run says, refusing to switch part way, to start again with --new.
OTHER = {"openbeken": "esphome", "esphome": "openbeken"}
SWITCH_REFUSED = "cannot switch to"
# How long the countdown after one Ctrl-C waits in the emulator, for the
# second to land in it (ctrlc-countdown; prompts.countdown_carry_on).
COUNTDOWN = 30
# Ctrl-C twice apart: past the countdown's own wait, as the Espressif suite's.
APART = 11
# Where the run sends through eWeLink, and the account is signed in to again
# beside it, each sign-in ending the one before (token-login): Sonoff's update.
TOKEN_STEPS = {"B2"}
# Where the backup is written (B4), and the disk fills as it is: the run must
# never go on without all of it (diskfull, FAIL-BAD-BACKUP).
DISKFULL = ("sonoff>openbeken", "B4")
DISKFULL_LIMIT = 1_000_000   # bytes a file may grow to while it lasts, for a minute
RUN_LIMIT = 1500       # seconds one run of the tool may take
RECOVERY_RUNS = 4      # runs after the fault before a scenario fails
# The steps whose image the stock bootloader installs, and the tool can send
# spoilt (--simulate-bad-image STEP): Sonoff's firmware's, and OpenBeken's
# fetch of ESPHome.
BAD_IMAGE_STEPS = {"B2", "J2", "O1"}
# Those of them where the device, refusing the image, is on OpenBeken, which the
# tool can restart: there the image is spoilt once more than the run sends it
# again (runner.GO_BACK_LIMIT, 2), so the run restarts the device - as a person
# switching it off and on would - and sends it afresh (badimage-restart).
RESTART_IMAGE_STEPS = {"J2", "O1"}
SPOILT_PAST_LIMIT = 3
# ESPHome in safe mode (safemode-ten), as the Espressif suite has it: a device
# on ESPHome switched off within a minute of starting until it takes that for
# a boot loop - ten starts in a row - and starts its update service alone, on
# LibreTiny's port; then the tool to each other firmware.
SAFE_MODE_ROUTES = ("esphome>openbeken", "esphome>sonoff")
SAFE_MODE_CUTS = 12          # cuts before the scenario gives up on safe mode
SAFE_MODE_ANSWERS = 55.0     # seconds its update service has to answer after a start
# A build of its own (ownbuild-replaced), as the Espressif suite has it: a
# device on ESPHome given one as an ESPHome builder that adopted it installs it
# (tools/repo/own_build.py); then the tool to ESPHome, which must leave it be,
# and with --replace-own-build put the published build back - an update, from
# "your own build" (routes/update.replace_own_build), so judged on its steps.
OWN_BUILD_ROUTE = "esphome>esphome"
OWN_BUILD_JUDGED_AS = "outdated>esphome"
OWN_BUILD_STARTS = 400.0     # seconds for the device to start its build of its own, the bootloader writing it in
# The interim firmware's settings, which it erases before the firmware it
# fetched starts (tools/beken/openbeken/README.md#the-interim-firmware): its
# own sector, and the flash variables its Wi-Fi keeps.
INTERIM_SETTINGS = ((0x3E1000, 0x1000), (0x1E5000, 0x8000))
# The name a device asks its router's DHCP for, by its firmware.
DHCP_NAME = re.compile(r"DHCPACK\(\S+\) \S+ \S+ (\S+)")
NAME_PREFIX = {"interim": "autoflash-recovery-", "openbeken": "openbeken-", "esphome": "esphome-"}
# A new OpenBeken build's check: each thing this project's driver and patches
# do on it, once - its Wi-Fi and settings taken from Sonoff's (B4 after B2),
# the interim firmware named, fetching ESPHome and wiping itself (J2), kept
# as OpenBeken (J3), each way back to Sonoff's firmware (J4, O2), and
# installed from ESPHome (K1).
OPENBEKEN_CHECK = ["sonoff>openbeken:B4:crash-1", "sonoff>esphome:J2:badimage-once", "interim>openbeken:J3:crash-1",
                   "interim>sonoff:J4:crash-1", "openbeken>sonoff:O2:crash-1", "esphome>openbeken:K1:crash-1"]

# Readouts (cli.py readout, unattended, docs/readouts.md) of a device fresh from
# Sonoff's firmware: each phase where the fault matters most, the run's events
# (output.PHASES) as it starts and ends, and how long it lasts in the emulator
# (seconds).
# The read is 256 paced chunks of 16 KB - B4 reads 8 of them in 12 s. Putting
# Sonoff's firmware back is the readout OpenBeken fetching it (O2's 24 s), then
# the bootloader writing it in and Sonoff's firmware starting (K3's 75 s): its
# faults land at 10%, in the fetch, the part only the readout has - the
# bootloader's write is the conversions' own, cut in their apply scenarios.
READOUT_PHASES = {"read": ("read", "read-done", 400), "back": ("back", "back-done", 100)}
READOUT_FAULTS = [("read", "deviceoff", 1, 40), ("read", "outage", 30, 40), ("read", "crash", 1, 40),
                  ("back", "deviceoff", 1, 10), ("back", "outage", 30, 10)]
# And in the read, the longest phase, what a conversion's steps that write
# have (OWN_HANDLING, NETWORK): the tool's own handling, the network spoilt,
# Ctrl-C once, the account signed in to again beside it; the device at a new
# address as the read ends; and the disk filling as the readout is written,
# at its end. Not Ctrl-C at the countdown: a readout asks after a Ctrl-C only
# at its next question (readout.py), so an unattended one counts down to none
# (tools/repo/fault_coverage.py NOT_EVERYWHERE).
READOUT_HANDLING = [fault for fault in OWN_HANDLING if fault != ("ctrlc", "countdown")]
READOUT_FAULTS += [("read", fault, arg, 40) for fault, arg in
                   READOUT_HANDLING + NETWORK + [("ctrlc", "once"), ("token", "login"), ("readdress", "new")]]
READOUT_FAULTS += [("read", "diskfull", "once", 40)]
READOUT = "readout.tar"
# A readout of every device in the account (readout --all) the stub lists
# beside the S61, made up (stub_cloud.accounts), each sent to the API beside the
# device and judged by what it was asked and kept, as the Espressif suite's
# are: in each account, and in the mixed one with a fault in the S61's read.
READOUT_ALL = "sonoff>readout-all"
# A route's target that is a readout, not a conversion: no conversion's checks after it.
READOUTS = ("readout", "readout-all")
READOUT_ALL_FAULTS = [("read", "deviceoff", 1), ("read", "crash", 1)]


def sid(route: str, step: str, fault: str, arg, at: int = 40) -> str:
    return f"{route}:{step}{'' if at == 40 else f'@{at}'}:{fault}-{arg}"


def scenarios(quick: bool = False) -> list[dict]:
    """Every scenario; `quick`, a routine run's: the outages only at QUICK_OUTAGES."""
    found = []
    for route, steps in ROUTES.items():
        for step in steps:
            for fault, arg in CORE:
                if quick and fault == "outage" and arg not in QUICK_OUTAGES:
                    continue
                found.append(dict(id=sid(route, step, fault, arg), route=route, step=step,
                                  fault=fault, arg=arg, at=40))
            if step in beken.DESTRUCTIVE_STEPS:
                for at in (10, 90):
                    for fault, arg in TIMING:
                        found.append(dict(id=sid(route, step, fault, arg, at), route=route, step=step,
                                          fault=fault, arg=arg, at=at))
                # The tool's own handling and the network spoilt (OWN_HANDLING,
                # NETWORK); and the device at a new address once it is sent, as a
                # router may give it.
                target = route.split(">")[1]
                for fault, arg in (OWN_HANDLING + NETWORK + [("readdress", "new")]
                                   + ([("cancel", "switch")] if target in OTHER else [])):
                    found.append(dict(id=sid(route, step, fault, arg), route=route, step=step,
                                      fault=fault, arg=arg, at=40))
            if step in TOKEN_STEPS and route.startswith("sonoff>"):
                found.append(dict(id=sid(route, step, "token", "login"), route=route, step=step,
                                  fault="token", arg="login", at=40))
            if (route, step) == DISKFULL:
                found.append(dict(id=sid(route, step, "diskfull", "once"), route=route, step=step,
                                  fault="diskfull", arg="once", at=40))
            if step in FALLBACK_STEPS:
                found.append(dict(id=sid(route, step, "fallback", 150), route=route, step=step,
                                  fault="fallback", arg=150, at=40))
        # An image the bootloader refuses, sent again.
        for step in set(steps) & BAD_IMAGE_STEPS:
            found.append(dict(id=sid(route, step, "badimage", "once"), route=route, step=step,
                              fault="badimage", arg="once", at=40))
        for step in set(steps) & RESTART_IMAGE_STEPS:
            found.append(dict(id=sid(route, step, "badimage", "restart"), route=route, step=step,
                              fault="badimage", arg="restart", at=40))
        # The device switched off while its bootloader writes the new firmware in:
        # timed off the device's own output, a share of the way through the write.
        for at in (10, 50, 90) if set(steps) & beken.DESTRUCTIVE_STEPS else ():
            found.append(dict(id=sid(route, "apply", "deviceoff", 1, at), route=route, step="apply",
                              fault="deviceoff", arg=1, at=at))
        if route in SAFE_MODE_ROUTES:
            found.append(dict(id=sid(route, "start", "safemode", "ten"), route=route, step="start",
                              fault="safemode", arg="ten", at=40))
    found.append(dict(id=sid(OWN_BUILD_ROUTE, "start", "ownbuild", "replaced"), route=OWN_BUILD_ROUTE,
                      step="start", fault="ownbuild", arg="replaced", at=40))
    return found + readout_scenarios()


def readout_scenarios() -> list[dict]:
    """A readout of a device fresh from Sonoff's firmware, unattended; the same
    with a fault in each phase where it matters most (READOUT_FAULTS); and one
    started on a device on OpenBeken, which must read its first stage alone."""
    def one(route: str, step: str, fault: str, arg, at: int = 40) -> dict:
        return dict(id=sid(route, step, fault, arg, at), route=route, step=step, fault=fault, arg=arg, at=at)
    return [one("sonoff>readout", "start", "none", "unattended"),
            *(one("sonoff>readout", step, fault, arg, at) for step, fault, arg, at in READOUT_FAULTS),
            # Its reading firmware sent spoilt: refused by the bootloader, the
            # device back on Sonoff's, and read whole by the next run.
            one("sonoff>readout", "start", "badimage", "once"),
            one("openbeken>readout", "start", "none", "first-stage"),
            *(one(READOUT_ALL, "start", "none", account) | {"account": account} for account in accounts.ACCOUNTS),
            *(one(READOUT_ALL, step, fault, arg) | {"account": "mixed"} for step, fault, arg in READOUT_ALL_FAULTS)]


def route_steps(route: str) -> list[str]:
    """Every step a run of `route` records, after the start all routes share."""
    source, target = route.split(">")
    return [step for step, _, _ in beken.steps_for(target, source)]


def interim_problems(route: str, names: list[str], flash: bytes | None) -> list[str]:
    """What a converted device shows wrong of the interim firmware: from the
    names it asked DHCP for, in order, and its flash once stopped (None when
    the route does not need it). On the interim firmware its name is
    autoflash-recovery-CDEF from its first request - never OpenBeken's own -
    and once it has fetched ESPHome its settings are gone."""
    source, target = route.split(">")
    steps = route_steps(route)
    problems = []
    if "J1" in steps and not any(name.startswith(NAME_PREFIX["interim"]) for name in names):
        problems.append("the interim firmware never asked DHCP by its own name")
    if "J2" in steps and any(name.startswith(NAME_PREFIX["openbeken"]) for name in names):
        problems.append("the interim firmware asked DHCP by OpenBeken's own name")
    if target in NAME_PREFIX and not (names and names[-1].startswith(NAME_PREFIX[target])):
        problems.append(f"the device's last name was {names[-1] if names else 'none'}, not {target}'s")
    if "J2" in steps and flash is not None:
        for start, size in INTERIM_SETTINGS:
            if flash[start:start + size] != b"\xff" * size:
                problems.append(f"the interim firmware's settings at {start:#x} were not erased")
    return problems


def converted(log: Path) -> bool:
    """Whether the last run in a scenario's log (tool.log, each run after a
    fault_verdicts.RUN_MARK line) ended with the device converted, as the
    Espressif suite judges it: by the run's own word
    (fault_verdicts.run_result, which says why that and not the steps its
    record has done)."""
    try:
        return fault_verdicts.converted(fault_verdicts.run_result(log.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def route_after(route: str, switched: bool) -> str:
    """The route a scenario's device is judged on - its firmware's release, its
    names, the interim firmware's settings: `route`, or after a switch
    to the other firmware (cancel-switch) a conversion to that one - from the
    firmware an update's device ran (outdated>esphome's ESPHome), which takes
    no update's steps but a conversion's."""
    source, target = route.split(">")
    if not switched:
        return route
    return f"{target if source == 'outdated' else source}>{OTHER[target]}"


def meter_demand(state: dict) -> str | None:
    """What the end-of-run checks must find of the meter
    (fault_verdicts.meter_demand), by the kind a run's record
    (autoflash-state.json) says it converted: one with a meter (the S61) must
    confirm the switch, one without (the Basic Gen5) has nothing to. A run that
    stopped before it knew the kind is held to the dump's device, the S61."""
    kind = kinds.by_key((state.get("inputs") or {}).get("device_kind"))
    return fault_verdicts.meter_demand(kind or kinds.SONOFF_S61)


class Instance:
    """One emulated device, its Wi-Fi and its computer."""

    def __init__(self, n: int):
        self.n = n
        # As beken-emu.sh --instance N names them.
        self.device = f"autoflash-beken-emu-{n}"
        self.network = f"autoflash-beken-emu-{n}"
        self.computer = f"autoflash-beken-emu-computer-{n}"
        self.state = folders.state(n)
        self.before: set[Path] = set()   # runs the starting device came with, not this scenario's
        self.env = {key: value for key, value in os.environ.items() if key != "AUTOFLASH_BEKEN_EMU_NETWORK"}
        self.env["AUTOFLASH_BEKEN_EMU_COMPUTER"] = self.computer

    def command(self, *args) -> list[str]:
        return [str(BK_EMU), "--instance", str(self.n), *args]

    def sh(self, *args, timeout=None, check=False):
        return subprocess.run(self.command(*args), env=self.env, capture_output=True, text=True,
                              timeout=timeout, check=check)

    def stop(self) -> None:
        # Long enough for the device to keep its flash as it stops (device.py).
        for name in (self.computer, self.device):
            subprocess.run(["docker", "stop", "-t", "30", name], capture_output=True)
        # The API beside it (beken-emu.sh computer) goes too: its watcher would
        # take it away only after the device has gone, and the next scenario's
        # computer could find it still there, keeping what it is sent in the
        # state reset() is about to clear.
        subprocess.run(["docker", "rm", "-f", f"{self.device}-api"], capture_output=True)
        # And gone (--rm), before a container of the same name is started again.
        for _ in range(30):
            found = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^({self.computer}|{self.device})$"],
                                   capture_output=True, text=True).stdout.strip()
            if not found:
                return
            time.sleep(1)

    def reset(self, start: str) -> None:
        """The device as `start` (sonoff: the dump, untouched; else saved by prep)."""
        self.stop()
        folders.clear(self.state)
        if start != "sonoff":
            shutil.copytree(SAVED / start, self.state)
        (self.state / "work/captures").mkdir(parents=True, exist_ok=True)
        (self.state / "work/captures/tool.env").write_text(ACCOUNT, encoding="utf-8")
        self.before = {fault_verdicts.filed_as(path) for path in self.all_runs()}

    def dhcp_log(self) -> str:
        return subprocess.run(["docker", "exec", self.device, "cat", "/run/autoflash-beken-emu/dnsmasq.log"],
                              capture_output=True, text=True).stdout

    def dhcp_names(self) -> list[str]:
        """The names the device asked its router's DHCP for, in order."""
        return [found.group(1) for found in DHCP_NAME.finditer(self.dhcp_log())]

    def lease(self) -> str | None:
        """The device's address: its lease, or - released while it goes on
        using the address, as ESPHome does on rejoining after its network was
        gone - the last its router gave it."""
        leases = subprocess.run(["docker", "exec", self.device, "cat", "/run/autoflash-beken-emu/leases"],
                                capture_output=True, text=True).stdout
        return fault_verdicts.device_address(leases, self.dhcp_log())

    def answering(self) -> bool:
        address = self.lease()
        if not address:
            return False
        try:
            with socket.create_connection((address, 80), timeout=3):
                return True
        except OSError:
            return False

    def stub_said(self) -> bytes:
        try:
            return (self.state / "stub.log").read_bytes()
        except OSError:
            return b""

    def online(self, since: int = 0) -> bool:
        """Sonoff's firmware in its eWeLink: the time zone it asks once there -
        since the stub's log was `since` bytes long."""
        return b'"TZ":{' in self.stub_said()[since:]

    def account(self, sc: dict, folder: Path) -> None:
        """The scenario's account, as the stub lists it and the API beside the
        device holds it - the device alone, unless a readout of every device
        names one (stub_cloud.accounts), as the Espressif suite's run_tool
        sets them: its other devices (AUTOFLASH_EMU_STUB_OTHERS, listed in
        `folder`), in the enough account the products the API has enough of
        (AUTOFLASH_API_ENOUGH), and in the many account the API taking one
        readout a minute, so its limit is reached however slowly they come."""
        for setting in ("AUTOFLASH_EMU_STUB_OTHERS", "AUTOFLASH_API_ENOUGH", "AUTOFLASH_LOCAL_READOUT_LIMIT",
                        "AUTOFLASH_EMULATOR_COUNTDOWN"):
            self.env.pop(setting, None)
        # The countdown after one Ctrl-C long enough for a second to land in it.
        if (sc.get("fault"), sc.get("arg")) == ("ctrlc", "countdown"):
            self.env["AUTOFLASH_EMULATOR_COUNTDOWN"] = str(COUNTDOWN)
        name = sc.get("account")
        if not name:
            return
        listed = folder / "others.json"
        listed.write_text(json.dumps(accounts.ACCOUNTS[name], indent=1) + "\n", encoding="utf-8")
        self.env["AUTOFLASH_EMU_STUB_OTHERS"] = str(listed)
        if name == "enough":
            self.env["AUTOFLASH_API_ENOUGH"] = ",".join(
                [kinds.SONOFF_S61.example_product_model, accounts.ACCOUNTS["enough"][0]["productModel"]])
        if name == "many":
            self.env["AUTOFLASH_LOCAL_READOUT_LIMIT"] = "1"

    def start(self, firmware: str, wait: float = 300) -> bool:
        self.sh("device", timeout=600, check=True)
        return self.ready(firmware, wait)

    def ready(self, firmware: str, wait: float = 300) -> bool:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if (self.online() if firmware == "sonoff" else self.answering()):
                return True
            time.sleep(3)
        return False

    def power(self, how: str) -> None:
        self.sh("power", how, timeout=60, check=True)

    def router(self, how: str) -> None:
        """The home Wi-Fi's router "off" or "on" (beken-emu.sh router)."""
        self.sh("router", how, timeout=60, check=True)

    def tool(self, target: str, log: Path, new: bool = False, more: tuple[str, ...] = (),
             api: bool = False) -> subprocess.Popen:
        """A run of the tool to `target`, or a readout (target "readout"), on a
        terminal of its own as a person's (tools/repo/unattended_run.py): the
        screen it ends on is printed after it, for FAIL-SCREEN. A readout is
        kept unsent (--no-api), for readout_problems to judge; with `api`, sent
        to the API beside the emulator, for api_problems to judge."""
        args = ([*([] if api else ["--no-api"]), "computer", "tools/repo/unattended_run.py", "--command",
                 "readout", "--", "--env-file", "captures/tool.env"] + (["--new"] if new else [])
                if target == "readout" else
                ["--no-api", "computer", "tools/repo/unattended_run.py", "--", "--env-file", "captures/tool.env",
                 "--firmware", target] + (["--new"] if new else [])) + list(more)
        out = open(log, "a", encoding="utf-8")
        # Where the run starts in the scenario's log, as the Espressif suite marks it.
        out.write(f"{fault_verdicts.RUN_MARK}{' '.join(args)}\n")
        out.flush()
        return subprocess.Popen(self.command(*args), env=self.env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)

    def records(self) -> Path:
        """The tool's records in the instance (fault_verdicts.records_under)."""
        return fault_verdicts.records_under(self.state / "work")

    def all_runs(self) -> list[Path]:
        """Every run's log in the instance (records)."""
        found = []
        for log in self.records().glob("*/runs/*/run.log"):
            try:  # the tool files a run under its device once it knows the device
                found.append((log.stat().st_mtime, log))
            except FileNotFoundError:
                pass
        return [log for _, log in sorted(found)]

    def runs(self) -> list[Path]:
        """This scenario's runs, oldest first."""
        return [path for path in self.all_runs() if fault_verdicts.filed_as(path) not in self.before]

    def run_texts(self) -> list[str]:
        """The words of each of this scenario's runs, oldest first."""
        found = []
        for path in self.runs():
            try:
                found.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass   # filed away as it was read
        return found

    def last_outcome(self, since: float) -> dict | None:
        """How the newest run since `since` ended, as it said itself
        (fault_verdicts.run_outcomes): None for none."""
        found = fault_verdicts.run_outcomes(self.state / "work/captures", since)
        return found[-1] if found else None

    def last_state(self) -> dict:
        logs = self.runs()
        try:
            return json.loads((logs[-1].parent / "autoflash-state.json").read_text(encoding="utf-8"))
        except (IndexError, OSError, ValueError):
            return {}

    def last_events(self) -> list[dict]:
        """What the newest run did (output.EVENTS), from its start."""
        logs = self.runs()
        return fault_verdicts.last_run_events(output.events(logs[-1].parent) if logs else [])

    def all_events(self) -> list[dict]:
        """What each of this scenario's runs did, oldest first."""
        return [item for path in self.runs() for item in output.events(path.parent)]

    def last_log(self) -> str:
        logs = self.runs()
        try:
            return logs[-1].read_text(encoding="utf-8", errors="replace") if logs else ""
        except FileNotFoundError:
            return ""   # filed away just now: looked at again next time


# Often enough that a fault timed from a step's start lands where it should: an
# update's send takes about 7 s, so 90% of it leaves under a second.
STEP_POLL = 0.1


def happened(instance: Instance, event: str, **fields) -> bool:
    """Whether the newest run has had `event`, with `fields` (output.EVENTS)."""
    return any(item["event"] == event and all(item.get(k) == v for k, v in fields.items())
               for item in instance.last_events())


def wait_for_step(instance: Instance, step: str, run: subprocess.Popen, limit: float = RUN_LIMIT) -> bool:
    """Until the run starts `step`; False if it ends, or takes too long, first."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline and run.poll() is None:
        if happened(instance, "began", step=step):
            return True
        time.sleep(STEP_POLL)
    return False


def asked_to_switch(instance: Instance) -> bool:
    """Whether the newest run's last words told the person to switch the device
    off and on (fault_verdicts.advised_to_switch)."""
    return fault_verdicts.advised_to_switch(instance.last_events())


def not_applied(notes: dict, what: str) -> None:
    notes.setdefault("not_applied", []).append(what)


def running(container: str) -> bool:
    return subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                          capture_output=True, text=True).stdout.strip() == "true"


def must(notes: dict, *command: str, **options) -> subprocess.CompletedProcess:
    """A command that starts a fault, or ends one the run needs ended."""
    done = subprocess.run(command, capture_output=True, text=True, **options)
    if done.returncode:
        not_applied(notes, " ".join(command[:3]))
    return done


# What the device says once it has really stopped (device.py): the switch itself is
# only a file written, which says nothing of whether the device saw it.
SWITCHED_OFF = b"[device] switched off"


def cut_power(instance: Instance, seconds: float, wait: float = 30) -> bool:
    """The device off for `seconds`, and on again; False if it never went off."""
    serial = instance.state / "serial.log"
    try:
        offset = serial.stat().st_size
    except OSError:
        offset = 0
    instance.power("off")
    went_off, deadline = False, time.monotonic() + wait
    while not went_off and time.monotonic() < deadline:
        time.sleep(0.2)
        try:
            with open(serial, "rb") as output:
                output.seek(offset)
                went_off = SWITCHED_OFF in output.read()
        except OSError:
            pass
    time.sleep(seconds)
    instance.power("on")
    return went_off


def computer_address(instance: Instance, notes: dict) -> str:
    """The computer's address on the Wi-Fi, to bring it back on at: "" (noted)
    where it has none."""
    address = subprocess.run(["docker", "inspect", "-f",
                              "{{(index .NetworkSettings.Networks \"%s\").IPAddress}}" % instance.network,
                              instance.computer], capture_output=True, text=True).stdout.strip()
    notes["outage_address"] = address
    if not address:
        not_applied(notes, "the computer's address on the Wi-Fi")
    return address


def computer_back(instance: Instance, address: str, notes: dict) -> None:
    """The computer back on the Wi-Fi. A run may end while it is off: then
    there is no computer to bring back, and the fault still happened."""
    back = subprocess.run(["docker", "network", "connect", "--ip", address, instance.network, instance.computer],
                          capture_output=True, text=True)
    if back.returncode and running(instance.computer):
        not_applied(notes, "docker network connect")


# The tool's own process on the computer, under the recorder: signalled
# itself, so a second Ctrl-C, or a window closed, reaches it as a terminal's
# would, not the recorder (unattended_run.py) around it.
TOOL = "tools/autoflash/cli.py"


def signal_tool(instance: Instance, sig: str, notes: dict, required: bool = True) -> None:
    """`sig` to the run; `required` false for one it may have ended already
    (the second Ctrl-C of two)."""
    done = subprocess.run(["docker", "exec", instance.computer, "pkill", f"-{sig}", "-f", TOOL],
                          capture_output=True, text=True)
    if done.returncode and required:
        not_applied(notes, f"{sig} to the run")


def as_you(instance: Instance, *command: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """`command` on the computer as your user, in its copy of the checkout, as
    the run there is (computer.sh): the stub's CA trusted."""
    return subprocess.run(["docker", "exec", "-u", f"{os.getuid()}:{os.getgid()}", "-w", "/work",
                           "-e", "HOME=/tmp", "-e", "SSL_CERT_FILE=/tmp/ca-bundle.pem", instance.computer,
                           *command], capture_output=True, text=True, timeout=timeout)


def as_root(instance: Instance, script: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """A shell `script` on the computer as its root, who may change its network."""
    return subprocess.run(["docker", "exec", "-u", "0", instance.computer, "sh", "-c", script],
                          capture_output=True, text=True, timeout=timeout)


# One packet in ten lost on the computer's Wi-Fi each way, and the rest
# delayed, as the Espressif suite's weak network (netem out, a drop in).
WEAK_ON = ("tc qdisc add dev eth0 root netem delay 150ms 100ms loss 10% && "
           "tc qdisc add dev eth0 handle ffff: ingress && "
           "tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 "
           "action gact ok random netrand drop 10")
WEAK_OFF = "tc qdisc del dev eth0 root; tc qdisc del dev eth0 ingress"
# A TCP packet of 128 bytes or more in 20 (in 2 at a short step), a byte of
# its data changed past any header and its checksum made good, each way on the
# computer's Wi-Fi: the Espressif suite's corrupt_filter, by the same length
# bands, as the computer sends and takes them.
SHORT_STEP = 5


def corrupt_on(every: int) -> str:
    from tools.espressif.emulator.fault_suite import LENGTHS, corrupt_filter
    return "; ".join(["tc qdisc add dev eth0 clsact"] + [
        f"tc filter add dev eth0 {way} pref {base + i} " + " ".join(corrupt_filter(value, mask, every))
        for way, base in (("egress", 10), ("ingress", 20)) for i, (value, mask) in enumerate(LENGTHS)])


CORRUPTED = re.compile(r"pedit.*?Sent \d+ bytes (\d+) pkt", re.S)


def corrupt(instance: Instance, length: float, notes: dict) -> None:
    """Packets changed until one has been, or a minute, as the Espressif suite's."""
    done = as_root(instance, corrupt_on(2 if length <= SHORT_STEP else 20))
    if done.returncode:
        not_applied(notes, f"the packet changer: {done.stderr.strip()[-120:]}")
        as_root(instance, "tc qdisc del dev eth0 clsact")
        return
    edited, deadline = 0, time.monotonic() + 60
    while not edited and time.monotonic() < deadline:
        time.sleep(0.2)
        shown = as_root(instance, "tc -s filter show dev eth0 egress; tc -s filter show dev eth0 ingress").stdout
        edited = sum(int(n) for n in CORRUPTED.findall(shown))
    as_root(instance, "tc qdisc del dev eth0 clsact")
    notes["corrupted_packets"] = edited


def second_run(instance: Instance, target: str, notes: dict) -> None:
    """A second window, the same command, while the run works: it must be
    refused (runs.py), the first left be."""
    command = (["readout"] if target in READOUTS else ["run", "--firmware", target])
    try:
        done = as_you(instance, "timeout", "150", "python", TOOL, *command, "--unattended",
                      "--env-file", "captures/tool.env", timeout=200)
        said = re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr))
    except subprocess.TimeoutExpired:
        said = ""
    notes["second_refused"] = "another run is converting this device" in said
    notes["second_said"] = said[-300:]


def countdown(instance: Instance, notes: dict) -> None:
    """Ctrl-C, then Ctrl-C again as the run counts down to carrying on:
    it must stop at once."""
    signal_tool(instance, "INT", notes)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if "You pressed Ctrl-C" in instance.last_log():
            time.sleep(3)
            signal_tool(instance, "INT", notes, required=False)
            notes["countdown_pressed"] = True
            return
        time.sleep(0.5)


def fill_disk(instance: Instance, notes: dict) -> None:
    """The run's files held to DISKFULL_LIMIT bytes for a minute, as a disk
    filling: as your user, whose soft limit it is, so it can be lifted."""
    pid = subprocess.run(["docker", "exec", instance.computer, "pgrep", "-o", "-f", TOOL],
                         capture_output=True, text=True).stdout.strip()
    if not pid or as_you(instance, "prlimit", f"--pid={pid}", f"--fsize={DISKFULL_LIMIT}:").returncode:
        not_applied(notes, "the run's file size limited")
        return
    notes["diskfull_pid"] = pid
    time.sleep(60)
    as_you(instance, "prlimit", f"--pid={pid}", "--fsize=unlimited:")


def sign_in_again(instance: Instance, notes: dict) -> None:
    """The account signed in to beside the run, as the app on a phone does:
    eWeLink keeps one sign-in, so the run's ends."""
    done = as_you(instance, "sh", "-c", "set -a && . captures/tool.env && exec python -c 'import os; "
                  "from tools.ewelink.get_device_key import login; "
                  "login(os.environ[\"EWELINK_EMAIL\"], os.environ[\"EWELINK_PASSWORD\"], \"+44\", \"eu\"); "
                  "print(\"signed in again\")'")
    notes["token"] = (done.stdout + done.stderr).strip()[-200:]
    if done.returncode:
        not_applied(notes, "the sign-in beside the run")


def inject(instance: Instance, fault: str, arg, notes: dict, target: str = "", length: float = 30) -> None:
    """The fault, now: `target`, what the run is to (for a second run beside
    it); `length`, how long its step usually takes."""
    if fault == "deviceoff":
        if not cut_power(instance, arg):
            not_applied(notes, "the device switched off")
    elif fault == "outage":
        # The network gone, as the Espressif suite's outage is, the router off:
        # the device's access point off the air, and this computer off it too.
        address = computer_address(instance, notes)
        if not address:
            return
        try:
            instance.router("off")
        except subprocess.CalledProcessError:
            not_applied(notes, "the router switched off")
        must(notes, "docker", "network", "disconnect", instance.network, instance.computer)
        time.sleep(arg)
        try:
            instance.router("on")
        except subprocess.CalledProcessError:
            not_applied(notes, "the router switched on again")
        computer_back(instance, address, notes)
    elif fault in ("pcoff", "sleep"):
        # This computer off the network - and asleep, everything of it
        # stopped - the device's Wi-Fi left as it is, then all back at once.
        address = computer_address(instance, notes)
        if not address:
            return
        must(notes, "docker", "network", "disconnect", instance.network, instance.computer)
        if fault == "sleep":
            must(notes, "docker", "pause", instance.computer)
        time.sleep(arg)
        if fault == "sleep":
            must(notes, "docker", "unpause", instance.computer)
        computer_back(instance, address, notes)
    elif fault == "weak":
        if as_root(instance, WEAK_ON).returncode:
            not_applied(notes, "the weak network")
        time.sleep(arg)
        as_root(instance, WEAK_OFF)
    elif fault == "corrupt":
        corrupt(instance, length, notes)
    elif fault == "cancel":
        signal_tool(instance, "INT", notes)
        time.sleep(1)
        signal_tool(instance, "INT", notes, required=False)
    elif fault == "ctrlc" and arg == "apart":
        signal_tool(instance, "INT", notes)
        time.sleep(APART)
        signal_tool(instance, "INT", notes, required=False)
    elif fault == "ctrlc" and arg == "countdown":
        countdown(instance, notes)
    elif fault == "hangup":
        signal_tool(instance, "HUP", notes)
    elif fault == "second":
        second_run(instance, target, notes)
    elif fault == "diskfull":
        fill_disk(instance, notes)
    elif fault == "token":
        sign_in_again(instance, notes)
    elif fault == "fallback":
        # The router alone off, long enough for ESPHome to raise its own network.
        try:
            instance.router("off")
            time.sleep(arg)
            instance.router("on")
        except subprocess.CalledProcessError:
            not_applied(notes, "the router switched off and on")
    elif fault == "crash":
        must(notes, "docker", "kill", instance.computer)
    elif fault == "ctrlc":
        must(notes, "docker", "kill", "--signal", "INT", instance.computer)


def recover(instance: Instance, target: str, notes: dict) -> None:
    """What a person does before running the tool again: switch the device off
    and on, where the last run said to."""
    if asked_to_switch(instance):
        instance.power("cycle")
        instance.ready(target, wait=300)
        notes["power_cycles"] = notes.get("power_cycles", 0) + 1


# The bootloader's own lines as it writes an image in (rt_ota), and as it is done.
APPLY_BEGINS = re.compile(rb"OTA firmware\(app\) upgrade\(")
APPLY_PROGRESS = re.compile(rb"OTA Write: \[[=> ]*\] (\d+)%")
APPLY_ENDS = re.compile(rb"ota_erase_dl_rbl erase")


class ApplyCut(threading.Thread):
    """Switch the device off for a second when its bootloader is `at`% of the
    way through writing an image in, whichever run, or restart, that is in."""

    def __init__(self, instance: Instance, at: int):
        super().__init__(daemon=True)
        self.instance, self.at = instance, at
        self.fired: int | None = None
        self.done = threading.Event()

    def run(self) -> None:
        offset, seen, begun = 0, b"", False
        while not self.done.is_set():
            try:
                with open(self.instance.state / "serial.log", "rb") as serial:
                    serial.seek(offset)
                    new = serial.read()
            except OSError:
                new = b""
            offset += len(new)
            seen = seen[-4096:] + new
            # One read may hold an apply's end and the next one's start, and more.
            while True:
                if not begun:
                    start = APPLY_BEGINS.search(seen)
                    if not start:
                        break
                    begun, seen = True, seen[start.end():]
                end = APPLY_ENDS.search(seen)
                for found in APPLY_PROGRESS.finditer(seen[:end.start()] if end else seen):
                    if int(found.group(1)) >= self.at:
                        if cut_power(self.instance, 1):
                            self.fired = int(found.group(1))
                        return
                if not end:
                    break
                begun, seen = False, seen[end.end():]   # over before its moment: the next apply, if any
            time.sleep(0.2)


def readdress(instance: Instance, notes: dict) -> None:
    """The router forgets the device's address and gives it another when it next asks."""
    address = instance.lease() or ""
    last = int(address.rsplit(".", 1)[1]) if address else 50
    must(notes, "docker", "exec", instance.device, "/run/autoflash-beken-emu/router", "55" if last < 55 else "50", timeout=60)
    notes["moved_from"] = address


def not_fired(fault: str, notes: dict) -> bool:
    """A device converted though its fault never happened proves nothing."""
    return bool(notes.get("not_applied")
                or (fault == "fallback" and not notes.get("joined_own_network"))
                or (fault == "double" and not notes.get("second_cut"))
                or (fault == "readdress" and not (notes.get("moved_from") and notes.get("moved_to")
                                                  and notes["moved_from"] != notes["moved_to"]))
                or (fault in ("ctrlc", "cancel") and not notes.get("ctrlc_seen"))
                or (fault == "badimage" and notes.get("spoilt") is False))


def handling_verdict(sc: dict, notes: dict, ok: bool, runs: int, texts: Callable[[], list[str]]) -> str | None:
    """What the tool's own handling, or the network spoilt, did wrong, as the
    Espressif suite judges it (None for nothing): `ok`, whether the scenario
    got to its end; `runs`, the runs of the tool from the fault on; `texts`,
    each run's words, asked for where they are needed."""
    fault, arg = sc["fault"], sc["arg"]
    if fault == "corrupt" and not notes.get("corrupted_packets"):
        return "NO-TRAFFIC"
    if fault == "ctrlc" and arg in ("once", "apart") and ok and runs > 1:
        return "FAIL-STOPPED"     # one Ctrl-C, or two apart, must not stop the run
    if fault == "ctrlc" and arg == "countdown" and not notes.get("countdown_pressed"):
        return "NOT-FIRED"
    if fault in ("hangup", "ctrlc") and arg in ("continue", "countdown") and runs == 1:
        return "FAIL-NOT-STOPPED"  # the window closed, or Ctrl-C at the countdown, stops it
    # The next run must say why the last one stopped, carrying it on: a
    # readout is never carried on (readout.py), so says nothing of it.
    if (fault == "hangup" and sc.get("route", "").split(">")[-1] not in READOUTS
            and "when its window was closed" not in re.sub(r"\s+", " ", "".join(texts()))):
        return "FAIL-NO-NOTE"
    if fault == "second" and not notes.get("second_refused"):
        return "FAIL-NOT-REFUSED"
    if fault == "second" and ok and runs > 1:
        return "FAIL-DISTURBED"    # the run refused must not disturb the first
    return None


def wait_for(run: subprocess.Popen, seen: Callable[[], bool], limit: float = RUN_LIMIT) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline and run.poll() is None:
        if seen():
            return True
        time.sleep(0.5)
    return False


def finish(run: subprocess.Popen, instance: Instance) -> None:
    try:
        run.wait(timeout=RUN_LIMIT)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", instance.computer], capture_output=True)


def start_of(route: str) -> str:
    """The saved device a route starts from: an update's is for its firmware."""
    source, target = route.split(">")
    return f"outdated-{target}" if source == "outdated" else source


JOIN_AGAIN_EVERY = 15.0   # seconds between tries of a join that has not taken, while the run asks


class Person(threading.Thread):
    """As a person at the computer: joins the device's own network when a run
    says to - again while the run still waits, should a try not take - and goes
    back to the home network when it says that, from the computer's Wi-Fi
    menu (computer/bin/wifi). What it did goes in `notes`. What the run asks it
    reads from the run's events (output.EVENTS: join, joined, home)."""

    def __init__(self, instance: Instance, notes: dict):
        super().__init__(daemon=True)
        self.instance, self.notes = instance, notes
        self.done = threading.Event()
        self.acted: dict[Path, tuple[int, int]] = {}   # each run's joins asked and homes told, done
        self.tried = -JOIN_AGAIN_EVERY                  # when a join was last tried

    def wifi(self, *args: str) -> bool:
        """`wifi ARGS` on the computer, as your user with its Wi-Fi's rights."""
        script = ('exec setpriv --reuid="$HOST_UID" --regid="$HOST_GID" --clear-groups '
                  '--inh-caps +net_admin,+net_raw --ambient-caps +net_admin,+net_raw '
                  '/work/tools/beken/emulator/computer/bin/wifi "$@"')
        try:
            done = subprocess.run(["docker", "exec", self.instance.computer, "sh", "-c", script, "wifi", *args],
                                  capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return False
        return done.returncode == 0

    def run(self) -> None:
        while not self.done.wait(2):
            self.look()

    def look(self) -> None:
        """One look at what the newest run asks, done."""
        logs = self.instance.runs()
        if not logs:
            return
        found = [item["event"] for item in output.events(logs[-1].parent)]
        asked, told = found.count("join"), found.count("home")
        joined, homed = self.acted.get(logs[-1], (0, 0))
        if told > homed:
            self.wifi("home")
            self.acted[logs[-1]] = (asked, told)
        elif asked > joined:
            if "joined" in found[len(found) - found[::-1].index("join"):]:
                self.acted[logs[-1]] = (asked, homed)
            elif time.monotonic() - self.tried >= JOIN_AGAIN_EVERY:
                self.tried = time.monotonic()
                if self.wifi("join"):
                    self.notes["joined_own_network"] = self.notes.get("joined_own_network", 0) + 1


def run_scenario(instance: Instance, sc: dict) -> dict:
    source, target = sc["route"].split(">")
    folder = fault_verdicts.scenario_folder(OUT / "logs", sc["id"])
    keep_earlier(folder)
    folder.mkdir(parents=True)
    log = folder / "tool.log"
    started, began = time.monotonic(), time.time()
    notes: dict = {}
    memory = MemoryWatch()
    memory.start()
    instance.reset(start_of(sc["route"]))
    instance.account(sc, folder)
    if not instance.start(source):
        notes["memory"] = memory.note()
        return dict(verdict="NOT-READY", notes=notes)
    person = Person(instance, notes)
    person.start()
    if target == "readout":
        outcome = readout_scenario(instance, sc, log, notes)
    elif target == "readout-all":
        outcome = readout_all_scenario(instance, sc, log, notes)
    elif sc["step"] == "apply":
        outcome = apply_scenario(instance, sc, target, log, notes)
    elif sc["fault"] == "badimage":
        outcome = bad_image_scenario(instance, sc, target, log, notes)
    elif sc["fault"] == "safemode":
        outcome = safe_mode_scenario(instance, sc, target, log, notes)
    elif sc["fault"] == "ownbuild":
        outcome = own_build_scenario(instance, log, notes)
    else:
        outcome = step_scenario(instance, sc, target, log, notes)
    # The route the runs went: another firmware's, where the person switched to
    # it after Ctrl-C twice (cancel-switch), the device then judged on that.
    route = outcome.pop("route", sc["route"])
    target = route.split(">")[1]
    person.done.set()
    person.join(10)
    if outcome["verdict"] in ("PASS", "FAIL") and fault_verdicts.unexpected(
            instance.run_texts(), fault_verdicts.run_outcomes(instance.state / "work/captures", began)):
        # As the Espressif suite: a run stopped by an error the tool did not see
        # coming is a bug, whatever the fault, though the next run converts.
        outcome = dict(outcome, verdict="FAIL-UNEXPECTED")
    # Asked while it runs: a route through J2 stops it below, for its flash.
    release = release_of(instance, target) if outcome["verdict"] == "PASS" and target in LATEST else None
    if outcome["verdict"] == "PASS" and target not in READOUTS:
        names = instance.dhcp_names()
        flash = None
        if "J2" in route_steps(route):
            instance.stop()   # its flash kept as it stops
            flash = (instance.state / "flash.bin").read_bytes()
        notes["dhcp_names"] = names
        problems = interim_problems(route, names, flash)
        if problems:
            outcome = dict(outcome, verdict="FAIL")
            notes["interim"] = problems
        checks = fault_verdicts.checks_problem(instance.last_log(), target, meter_demand(instance.last_state()),
                                               instance.last_outcome(began))
        if checks and not problems:
            outcome = dict(outcome, verdict="FAIL-CHECKS")
            notes["checks"] = checks
        if target in LATEST and release != LATEST[target] and outcome["verdict"] == "PASS":
            # Converted, but not on this checkout's release: an update from an
            # earlier one, or a conversion that release began, left behind.
            outcome = dict(outcome, verdict="FAIL-NOT-LATEST")
            notes["release"] = f"{target} {release}"
    if outcome["verdict"] == "PASS" and log.exists():
        screen = fault_verdicts.screen_problems(log.read_text(encoding="utf-8", errors="replace"))
        if screen:
            outcome = dict(outcome, verdict="FAIL-SCREEN")
            notes["screen"] = screen
    fault_verdicts.keep_runs(instance.runs(), folder)
    fault_verdicts.keep_dhcp_log(instance.dhcp_log(), folder)
    if log.exists():   # every run's recording: what each stage showed on the way
        fault_verdicts.keep_recordings(log.read_text(encoding="utf-8", errors="replace"),
                                       instance.state / "work", folder)
    # Its serial output and the stub's log, each begun afresh with the scenario (reset).
    fault_verdicts.keep_log(instance.state / "serial.log", folder / "serial.log")
    fault_verdicts.keep_log(instance.state / "stub.log", folder / "stub.log")
    if outcome["verdict"] != "PASS":
        fault_verdicts.keep_evidence(instance.records(), folder)
    notes["memory"] = memory.note()
    notes["waits"] = fault_verdicts.waits(instance.all_events(), began)
    return dict(**outcome, notes=notes, seconds=round(time.monotonic() - started))


def release_of(instance: Instance, firmware: str) -> str | None:
    """The release of this project's `firmware` the device runs: OpenBeken's, as
    its build string says (openbeken.version); ESPHome's, as its API says
    (project_version).  None where it does not answer as that.  A few tries:
    it may be starting."""
    for _ in range(3):
        address = instance.lease()
        if address and firmware == "openbeken":
            found = openbeken.info(address)
            if found:
                return openbeken.version(found)
        elif address:
            info = esphome.api_device_info(address)
            if info:
                if info["project_name"] != kinds.SONOFF_S61.esphome_project:
                    return None   # not this project's ESPHome
                return str(info["project_version"])
        time.sleep(5)
    return None


def apply_scenario(instance: Instance, sc: dict, target: str, log: Path, notes: dict) -> dict:
    cut = ApplyCut(instance, sc["at"])
    cut.start()
    try:
        runs = 0
        while not converted(log) and runs <= RECOVERY_RUNS + 1:
            if runs:
                recover(instance, target, notes)
            finish(instance.tool(target, log, new=not runs), instance)
            runs += 1
    finally:
        cut.done.set()
        cut.join(5)
    notes["cut_at"] = cut.fired
    if cut.fired is None:
        return dict(verdict="NOT-FIRED", runs=runs)
    return dict(verdict="PASS" if converted(log) else "FAIL", runs=runs)


def bad_image_scenario(instance: Instance, sc: dict, target: str, log: Path, notes: dict) -> dict:
    """The step's image sent spoilt, once - each run is asked, as a route may
    reach the step only after a restart, and the tool spoils it once a run's
    record - and runs again, as a person would, until the device is converted:
    which must come from a run that saw the image was not installed and sent
    it again."""
    spoil = ("--simulate-bad-image",
             f"{sc['step']}:{SPOILT_PAST_LIMIT}" if sc["arg"] == "restart" else sc["step"])
    finish(instance.tool(target, log, new=True, more=spoil), instance)
    runs = 1
    while not converted(log) and runs <= RECOVERY_RUNS:
        recover(instance, target, notes)
        finish(instance.tool(target, log, more=spoil), instance)
        runs += 1
    # The image found not installed, and sent again (errors.GoBack, output.EVENTS).
    again = [item for item in instance.all_events() if item["event"] == "again" and item.get("step") == sc["step"]]
    notes["sent_again"] = bool(again)
    ok = converted(log) and notes["sent_again"]
    if sc["arg"] == "restart":
        # Converted by the run that restarted the device, not by a person's run after.
        notes["restarted"] = any(item.get("restarted") for item in again)
        ok = ok and notes["restarted"] and runs == 1
    return dict(verdict="PASS" if ok else "FAIL", runs=runs)


def ports_open(host: str | None, *ports: int) -> set[int]:
    """Those of `ports` the device at `host` takes a connection on."""
    found = set()
    for port in ports if host else ():
        try:
            with socket.create_connection((host, port), timeout=1.5):
                found.add(port)
        except OSError:
            pass
    return found


def safe_mode_scenario(instance: Instance, sc: dict, target: str, log: Path, notes: dict) -> dict:
    """safemode-ten: the device on ESPHome switched off and on as soon as its
    update service answers after each start, before its page is up, until
    ESPHome takes the starts for a boot loop and starts in safe mode - its
    update service alone, on LibreTiny's port, and no page
    (docs/beken-research.md#esphome-in-safe-mode); then the tool, as a person
    would run it, until the device is converted. That must have known it for
    ESPHome in safe mode and waited it out (routes/source.esphome_safe_mode),
    never taken it for a device on Sonoff's firmware (FAIL-TAKEN-FOR-SONOFF).
    Safe mode lasts five minutes, the device's time held to the wall's
    (device.hold_to_wall): a run that found ESPHome itself again came too late
    (MISTIMED)."""
    service = esphome_ota.LIBRETINY_PORT
    for cuts in range(1, SAFE_MODE_CUTS + 1):
        instance.power("cycle")
        deadline = time.monotonic() + SAFE_MODE_ANSWERS
        while service not in ports_open(instance.lease(), service):
            if time.monotonic() > deadline:
                notes["safemode_cuts"] = cuts
                return dict(verdict="NOT-FIRED", runs=0)   # its update service never came up
            time.sleep(0.5)
        time.sleep(3)   # a normal start has its page up by now (seen: 3 s after)
        if 80 in ports_open(instance.lease(), 80):
            continue
        time.sleep(5)
        if 80 not in ports_open(instance.lease(), 80):
            break
    else:
        notes["safemode_cuts"] = SAFE_MODE_CUTS
        return dict(verdict="NOT-FIRED", runs=0)
    notes["safemode_cuts"] = cuts
    finish(instance.tool(target, log, new=True), instance)
    runs = 1
    while not converted(log) and runs <= RECOVERY_RUNS:
        recover(instance, target, notes)
        finish(instance.tool(target, log), instance)
        runs += 1
    texts = instance.run_texts()
    notes["waited_out"] = any("ESPHome in safe mode" in text for text in texts)
    if not converted(log):
        return dict(verdict="FAIL", runs=runs)
    if notes["waited_out"]:
        return dict(verdict="PASS", runs=runs)
    found_itself = texts and re.search(r"This run is for the device at \S+.*, running ESPHome", texts[0])
    return dict(verdict="MISTIMED" if found_itself else "FAIL-TAKEN-FOR-SONOFF", runs=runs)


def esphome_now(instance: Instance) -> tuple[str | None, str | None]:
    """When the device's ESPHome says it was built, and what its Calibration
    sensor says, as the tool asks them (esphome.api_device_info,
    calibration_state): Nones where it does not answer."""
    address = instance.lease()
    if not address:
        return None, None
    info = esphome.api_device_info(address)
    return (info and info["built"]) or None, esphome.calibration_state(address)


def own_build_scenario(instance: Instance, log: Path, notes: dict) -> dict:
    """ownbuild-replaced: the device on ESPHome given a build of its own, as an
    ESPHome builder that adopted it installs one (tools/repo/own_build.py); the
    tool to ESPHome must say it is the device's own and leave it be, then with
    --replace-own-build put the published build back, its calibration kept
    (own_build.problem). Judged after as an update (OWN_BUILD_JUDGED_AS)."""
    pin = beken_images.pin(DEVICE, "esphome")
    built, notes["calibration"] = esphome_now(instance)
    image, own = own_build.beken((REPO / pin["file"]).read_bytes(), pin["built"], f"esphome-{DEVICE.key}")
    try:
        esphome_ota.upload(instance.lease(), image, port=esphome_ota.LIBRETINY_PORT, timeout=30)
    except (OSError, esphome_ota.OTAError) as exc:
        notes["own_not_sent"] = str(exc)
        return dict(verdict="NOT-FIRED", runs=0)
    deadline = time.monotonic() + OWN_BUILD_STARTS
    while built != own and time.monotonic() < deadline:
        time.sleep(5)
        built = esphome_now(instance)[0]
    if built != own:
        notes["own_not_started"] = built
        return dict(verdict="NOT-FIRED", runs=0)
    notes["own"] = own
    before = log.stat().st_size if log.exists() else 0
    finish(instance.tool("esphome", log, new=True), instance)
    with open(log, encoding="utf-8", errors="replace") as said:
        said.seek(before)
        notes["refused"] = "It is a build of its own" in re.sub(r"\s+", " ", said.read())
    notes["refused_built"] = esphome_now(instance)[0]
    finish(instance.tool("esphome", log, more=("--replace-own-build",)), instance)
    runs = 2
    while not converted(log) and runs <= RECOVERY_RUNS + 1:
        recover(instance, "esphome", notes)
        finish(instance.tool("esphome", log), instance)
        runs += 1
    if not converted(log):
        return dict(verdict="FAIL", runs=runs, route=OWN_BUILD_JUDGED_AS)
    notes["built_now"], notes["calibration_now"] = esphome_now(instance)
    return dict(verdict=own_build.problem(notes, pin["built"]) or "PASS", runs=runs, route=OWN_BUILD_JUDGED_AS)


def cut_as_sent_again(instance: Instance, run: subprocess.Popen, sc: dict, notes: dict) -> bool:
    """A double's second cut, as the run under way sends its command again: one
    that does converts by itself, and no run again would get the cut. One that
    rides the first out with nothing sent again - an outage, the device
    fetching on - gets the second as the step goes on, twice running, as the
    Espressif suite's double is."""
    def resent() -> int:   # the command sent again (tools/ewelink/ota_push, output.EVENTS)
        return sum(item["event"] == "resent" for item in instance.last_events())
    before = resent()
    delay = ROUTES[sc["route"]][sc["step"]] * sc["at"] / 100
    if doubled(sc)[0] == "outage":
        # The device fetches on through an outage, needing nothing sent again:
        # the second as soon as the step has gone on a while.
        time.sleep(delay)
        if run.poll() is not None or happened(instance, "done", step=sc["step"]):
            return False
    elif wait_for(run, lambda: resent() > before, limit=RESENT_WITHIN):
        time.sleep(delay)
    elif run.poll() is not None or happened(instance, "done", step=sc["step"]):
        return False
    inject(instance, *doubled(sc), notes)
    notes["second_cut"] = True
    return True


def doubled(sc: dict) -> tuple[str, int]:
    """A double's fault, each time: "deviceoff-1" a 1 s power cut, "outage-30"
    a 30 s outage."""
    fault, length = sc["arg"].rsplit("-", 1)
    return fault, int(length)


def backup_whole(instance: Instance) -> bool:
    """Whether the device's backup this run took is there, every file of it as
    its manifest says (records.save_beken_backup)."""
    import hashlib
    found = list((instance.state / "work/captures/devices").glob("*/sonoff-backup/backup.json"))
    for manifest in found:
        try:
            regions = json.loads(manifest.read_text(encoding="utf-8"))["regions"]
            if any(hashlib.sha256((manifest.parent / name).read_bytes()).hexdigest() != region["sha256"]
                   for name, region in regions.items()):
                return False
        except (OSError, ValueError, KeyError):
            return False
    return bool(found)


def step_scenario(instance: Instance, sc: dict, target: str, log: Path, notes: dict) -> dict:
    run = instance.tool(target, log, new=True)
    # A run a fault ended reaches its later steps only through runs again, with
    # the switch off and on the tool asks for, as a person would.
    reruns = 0
    while not wait_for_step(instance, sc["step"], run):
        run.wait(timeout=60)
        reruns += 1
        if reruns > RECOVERY_RUNS or converted(log):
            return dict(verdict="NOT-FIRED")
        recover(instance, target, notes)
        run = instance.tool(target, log)
    length = ROUTES[sc["route"]][sc["step"]]
    if sc["fault"] == "readdress":
        if not wait_for(run, lambda: happened(instance, "done", step=sc["step"])):
            notes["mistimed"] = True
        readdress(instance, notes)
    else:
        time.sleep(length * sc["at"] / 100)
        if happened(instance, "done", step=sc["step"]):
            notes["mistimed"] = True
        fault, arg = doubled(sc) if sc["fault"] == "double" else (sc["fault"], sc["arg"])
        inject(instance, fault, arg, notes, target=target, length=length)
        if fault == "crash":
            run.wait(timeout=60)
            time.sleep(arg)
        if sc["fault"] == "double":
            cut_as_sent_again(instance, run, sc, notes)
    finish(run, instance)
    # After Ctrl-C twice, the person runs it to the other firmware (switch) or
    # afresh (new), as the Espressif suite's cancel does; else as it was.
    route = route_after(sc["route"], (sc["fault"], sc["arg"]) == ("cancel", "switch"))
    target = route.split(">")[1]
    afresh = (sc["fault"], sc["arg"]) == ("cancel", "new")
    second = sc["fault"] == "double" and not notes.get("second_cut")
    runs = 1
    while not converted(log) and runs <= RECOVERY_RUNS:
        recover(instance, target, notes)
        run = instance.tool(target, log, new=afresh)
        if second and wait_for_step(instance, sc["step"], run):
            # The second fault, as the tool sends the step again.
            time.sleep(length * sc["at"] / 100)
            inject(instance, *doubled(sc), notes)
            notes["second_cut"] = True
            second = False
        finish(run, instance)
        runs += 1
        # A switch to the other firmware part way is refused, the run saying to
        # start again with --new: as the person told, as the Espressif suite does.
        afresh = (sc["fault"], sc["arg"]) == ("cancel", "switch") and SWITCH_REFUSED in instance.last_log()
        if afresh:
            notes["switch_refused"] = True
    if sc["fault"] == "readdress":
        notes["moved_to"] = instance.lease()
    if sc["fault"] in ("ctrlc", "cancel"):
        notes["ctrlc_seen"] = any(CTRL_C_LOGGED in text for text in instance.run_texts())
    ok = converted(log)
    if notes.get("mistimed"):
        return dict(verdict="MISTIMED", runs=runs)
    if ok and not_fired(sc["fault"], notes):
        return dict(verdict="NOT-FIRED", runs=runs)
    wrong = handling_verdict(sc, notes, ok, runs, instance.run_texts)
    if sc["fault"] == "diskfull" and not backup_whole(instance):
        wrong = "FAIL-BAD-BACKUP"   # never on without the whole backup
    if wrong:
        return dict(verdict=wrong, runs=runs, route=route)
    return dict(verdict="PASS" if ok else "FAIL", runs=runs, route=route)


def to_api(sc: dict) -> bool:
    """Whether the scenario's readout goes to the API beside the emulator: the
    one fresh from Sonoff's firmware with no fault, so both sides of a readout
    are seen as a person's goes; each other is kept unsent, for what a fault
    left of it."""
    return sc["route"] == "sonoff>readout" and sc["fault"] == "none"


def api_problems(instance: Instance, log: Path) -> list[str] | None:
    """What the API beside the emulator was asked and kept of this scenario's
    newest readout (fault_verdicts.readout_api_problems), as "VERDICT: why";
    [] for nothing wrong, None with no run. What it was asked and kept is kept
    beside the scenario's log."""
    runs = instance.runs()
    if not runs:
        return None
    api = log.with_name(f"{log.stem}-api")
    shutil.rmtree(api, ignore_errors=True)
    if (instance.state / "api").is_dir():
        shutil.copytree(instance.state / "api", api)
    else:
        api.mkdir()
    kept = sorted(path.name for path in runs[-1].parent.glob("readout*.tar"))
    run = fault_verdicts.last_run(runs[-1].read_text(encoding="utf-8", errors="replace"))
    problems, _ = fault_verdicts.readout_api_problems(run, True, api, 1, kinds.SONOFF_S61.example_product_model,
                                                      "beken", kept)
    return [f"{verdict}: {why}" for verdict, why in problems]


def readout_problems(instance: Instance) -> list[str] | None:
    """What the newest readout.tar this scenario's runs kept lacks
    (fault_verdicts.readout_problems), or None when none kept one."""
    kept = [path.parent / READOUT for path in instance.runs() if (path.parent / READOUT).is_file()]
    if not kept:
        return None
    return fault_verdicts.readout_problems(kept[-1].read_bytes(), "beken")


def settle(instance: Instance, since: int, wait: float = 120) -> None:
    """Until the device has started on Sonoff's firmware (in eWeLink since the
    stub's log was `since` long) or on OpenBeken (answering), or `wait` runs out."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and not (instance.online(since) or instance.answering()):
        time.sleep(3)


def sonoff_works(instance: Instance, wait: float = 300) -> bool:
    """Whether the device, switched off and on, starts Sonoff's firmware and is
    back in eWeLink: on its Sonoff firmware, and working."""
    since = len(instance.stub_said())
    instance.power("cycle")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if instance.online(since):
            return not instance.answering()
        time.sleep(3)
    return False


def readout_scenario(instance: Instance, sc: dict, log: Path, notes: dict) -> dict:
    """A readout of the device, with the fault in the phase under test; then
    what a person does, up to RECOVERY_RUNS times: switch the device off and on
    where the run said to, put Sonoff's firmware back with the tool where the
    device was left on OpenBeken, else read it out again - until a whole readout
    is kept. It passes when one is, and the device works on Sonoff's firmware."""
    if sc["route"].split(">")[0] != "sonoff":
        return converted_readout(instance, log, notes)
    sent = to_api(sc)

    def problems_now() -> list[str] | None:
        return api_problems(instance, log) if sent else readout_problems(instance)
    spoil = ("--simulate-bad-image", "readout") if sc["fault"] == "badimage" else ()
    run = instance.tool("readout", log, api=sent, **({"more": spoil} if spoil else {}))
    second = sc["fault"] == "double"
    if sc["fault"] not in ("none", "badimage"):
        begins, ends, length = READOUT_PHASES[sc["step"]]
        if not wait_for(run, lambda: happened(instance, "phase", phase=begins)):
            finish(run, instance)
            return dict(verdict="NOT-FIRED", runs=1)
        if sc["fault"] in ("readdress", "diskfull"):
            # As the read ends: the device then asked for at the address it had,
            # or the readout written as the disk fills.
            if not wait_for(run, lambda: happened(instance, "phase", phase=ends)):
                notes["mistimed"] = True
            if sc["fault"] == "readdress":
                readdress(instance, notes)
            else:
                inject(instance, "diskfull", sc["arg"], notes)
        else:
            time.sleep(length * sc["at"] / 100)
            if happened(instance, "phase", phase=ends):
                notes["mistimed"] = True
            fault, arg = doubled(sc) if second else (sc["fault"], sc["arg"])
            inject(instance, fault, arg, notes, target="readout", length=length)
            if fault == "crash":
                run.wait(timeout=60)
                time.sleep(arg)
            if second and not wait_for(run, lambda: happened(instance, "phase", phase=ends), limit=RESENT_WITHIN):
                # Ridden out, the read going on: the second, twice running.
                if run.poll() is None:
                    inject(instance, fault, arg, notes)
                    notes["second_cut"], second = True, False
    finish(run, instance)
    if spoil:   # the reading firmware sent spoilt, as asked (images.as_sent)
        notes["spoilt"] = bool((instance.last_state().get("captures") or {}).get("readout.spoilt"))
    afresh = (sc["fault"], sc["arg"]) == ("cancel", "new")
    runs = 1
    while problems_now() != [] and runs <= RECOVERY_RUNS:
        since = len(instance.stub_said())
        recover(instance, "sonoff", notes)
        settle(instance, since)
        again = "sonoff" if instance.answering() else "readout"
        notes.setdefault("then", []).append(again)
        run = instance.tool(again, log, new=afresh and again == "readout", api=sent and again == "readout")
        afresh = afresh and again != "readout"   # --new the first time it is read out again
        if second and again == "readout" and wait_for(run, lambda: happened(instance, "phase", phase="read")):
            # A double's second fault: as the next readout reads again.
            time.sleep(READOUT_PHASES["read"][2] * sc["at"] / 100)
            inject(instance, *doubled(sc), notes)
            notes["second_cut"], second = True, False
        finish(run, instance)
        runs += 1
    if sc["fault"] == "readdress":
        notes["moved_to"] = instance.lease()
    if sc["fault"] in ("ctrlc", "cancel"):
        notes["ctrlc_seen"] = any(CTRL_C_LOGGED in text for text in instance.run_texts())
    problems = problems_now()
    notes["readout"] = problems if problems is not None else ["none kept"]
    if notes.get("mistimed"):
        return dict(verdict="MISTIMED", runs=runs)
    if not_fired(sc["fault"], notes):
        return dict(verdict="NOT-FIRED", runs=runs)
    wrong = handling_verdict(sc, notes, problems == [], runs, instance.run_texts)
    if wrong:
        return dict(verdict=wrong, runs=runs)
    if problems != []:
        # No whole readout kept; or sent, the API's verdict (api_problems).
        first = notes["readout"][0].split(":")[0]
        return dict(verdict=first if sent and first.startswith("FAIL-") else "FAIL-READOUT", runs=runs)
    return dict(verdict="PASS" if sonoff_works(instance) else "FAIL-NOT-SONOFF", runs=runs)


def api_run(instance: Instance, log: Path, number: int, since: int, code: int, sc: dict) -> dict:
    """One run of a readout of every device, judged by what the API beside the
    device was asked and kept (fault_verdicts.readout_api_problems): the API
    goes on as the device does, so its requests.log from `since` bytes, this
    run's alone, kept beside the scenario's log with what it holds."""
    api = log.with_name(f"{log.stem}-api-{number}")
    folders.clear(api)
    if (instance.state / "api").is_dir():
        shutil.copytree(instance.state / "api", api)
        requests = api / "requests.log"
        if requests.exists():
            requests.write_bytes(requests.read_bytes()[since:])
    else:
        api.mkdir()
    runs = instance.runs()
    run = fault_verdicts.last_run(runs[-1].read_text(encoding="utf-8", errors="replace")) if runs else ""
    kept = sorted(path.name for path in runs[-1].parent.glob("readout*.tar")) if runs else []
    problems, summary = fault_verdicts.readout_api_problems(
        run, code == 0, api, len(accounts.ACCOUNTS[sc["account"]]) + 1, kinds.SONOFF_S61.example_product_model,
        "beken", kept)
    return {"code": code, "problems": problems, **summary}


def readout_all_scenario(instance: Instance, sc: dict, log: Path, notes: dict) -> dict:
    """A readout of every device in the account (readout --all) the stub lists
    beside the S61 fresh from Sonoff's firmware, each readout sent to the API
    beside it - with the fault, where there is one, in the S61's read. Then, as
    a person would, the readout of every device run again until one finishes,
    switching the device off and on where the run said to, up to RECOVERY_RUNS
    times. It passes when every run is as the API says it should be, the last
    finished, and the device works on Sonoff's firmware (FAIL-NOT-SONOFF); in
    the many account, NOT-FIRED where the API never answered 429."""
    requests = instance.state / "api/requests.log"

    def sent_so_far() -> int:
        return requests.stat().st_size if requests.exists() else 0

    judged = []
    since = sent_so_far()
    run = instance.tool("readout", log, api=True, more=("--all",))
    if sc["fault"] != "none":
        begins, ends, length = READOUT_PHASES[sc["step"]]
        if not wait_for(run, lambda: happened(instance, "phase", phase=begins)):
            finish(run, instance)
            return dict(verdict="NOT-FIRED", runs=1)
        time.sleep(length * sc["at"] / 100)
        if happened(instance, "phase", phase=ends):
            notes["mistimed"] = True
        inject(instance, sc["fault"], sc["arg"], notes)
        if sc["fault"] == "crash":
            run.wait(timeout=60)
            time.sleep(sc["arg"])
    finish(run, instance)
    judged.append(api_run(instance, log, 1, since, run.returncode, sc))
    while judged[-1]["code"] != 0 and len(judged) <= RECOVERY_RUNS:
        recover(instance, "sonoff", notes)
        since = sent_so_far()
        again = instance.tool("readout", log, api=True, more=("--all",))
        finish(again, instance)
        judged.append(api_run(instance, log, len(judged) + 1, since, again.returncode, sc))
    notes["runs"] = judged
    problems = [problem for one in judged for problem in one["problems"]]
    if notes.get("mistimed"):
        return dict(verdict="MISTIMED", runs=len(judged))
    if sc["fault"] != "none" and not_fired(sc["fault"], notes):
        return dict(verdict="NOT-FIRED", runs=len(judged))
    if problems:
        return dict(verdict=problems[0][0], runs=len(judged))
    if judged[-1]["code"] != 0:
        return dict(verdict="FAIL-NOT-FINISHED", runs=len(judged))
    if sc["account"] == "many" and not any(one["429"] for one in judged):
        return dict(verdict="NOT-FIRED", runs=len(judged))   # the API's limit never reached: proves nothing
    return dict(verdict="PASS" if sonoff_works(instance) else "FAIL-NOT-SONOFF", runs=len(judged))


def converted_readout(instance: Instance, log: Path, notes: dict) -> dict:
    """A readout started on a device on OpenBeken: its first stage alone
    (FAIL-READOUT), ending with no failure (FAIL-EXIT), nothing served to the
    device (FAIL-SENT), which is left answering on its firmware (FAIL-DISTURBED)."""
    run = instance.tool("readout", log)
    finish(run, instance)
    said = instance.last_log()
    notes["said"] = (re.findall(r"FAILED: (.*)", said) or ["nothing"])[-1]
    kept = [path.parent / READOUT for path in instance.runs() if (path.parent / READOUT).is_file()]
    problems = fault_verdicts.first_stage_problems(kept[-1].read_bytes(), "openbeken") if kept else None
    notes["readout"] = problems if problems is not None else ["none kept"]
    if happened(instance, "phase", phase="serving"):   # the readout firmware, or Sonoff's
        return dict(verdict="FAIL-SENT", runs=1)
    if run.returncode != 0:
        return dict(verdict="FAIL-EXIT", runs=1)
    if notes["readout"]:
        return dict(verdict="FAIL-READOUT", runs=1)
    return dict(verdict="PASS" if instance.answering() else "FAIL-DISTURBED", runs=1)


def all_passed(ids_file: str | None = None) -> bool:
    """Whether every scenario of `ids_file` (else every one) is done
    (fault_verdicts.DONE) by its latest result."""
    wanted = scenario_times.read_ids(ids_file) if ids_file else [sc["id"] for sc in scenarios()]
    passed = {i for i, entry in fault_verdicts.latest(RESULTS).items() if entry["verdict"] in fault_verdicts.DONE}
    return all(i in passed for i in wanted)


def record(sc: dict, outcome: dict) -> None:
    fault_verdicts.append_row(RESULTS, {"id": sc["id"], "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **outcome})


def batch(ids_file: str | None = None, first: int = 1) -> None:
    every = {sc["id"]: sc for sc in scenarios()}
    wanted = scenario_times.read_ids(ids_file) if ids_file else list(every)
    earlier = fault_verdicts.latest(RESULTS)   # those run here before: a re-run's pass rate is apart (emulator_stats)
    passed = {i for i, entry in earlier.items() if entry["verdict"] in fault_verdicts.DONE}
    todo: queue.Queue = queue.Queue()
    # The longest first, so the batch does not end on one started last (scenario_times).
    for sc in scenario_times.longest_first([every[i] for i in wanted if i in every and i not in passed],
                                           scenario_times.times(*suite_roots())):
        todo.put(sc)
    count = int(os.environ.get("AUTOFLASH_BEKEN_EMU_WORKERS", "2"))
    numbers = emulator_instances.take("beken", first, count)
    lock = threading.Lock()
    workers = batch_workers.Workers(lambda line: print(line, flush=True))

    def work(n: int) -> None:
        instance = Instance(n)
        try:
            while workers.going():
                try:
                    sc = todo.get_nowait()
                except queue.Empty:
                    return
                if scenario_times.skipped(OUT, sc["id"]):
                    continue
                if not disk_space.wait_for_room(OUT, lambda line: print(line, flush=True), workers.stopping):
                    if workers.going():
                        workers.stop(disk_space.stop_reason(OUT))
                    return
                cpu_load.wait_for_room(lambda line: print(line, flush=True))
                started = time.time()
                try:
                    outcome = run_scenario(instance, sc)
                except Exception as exc:  # noqa: BLE001 - one scenario's failure is recorded, not the batch's
                    # Where it was raised too, as the Espressif suite keeps it: the error alone
                    # (a FileNotFoundError, say) does not tell which file.
                    outcome = dict(verdict="ERROR", notes={"error": repr(exc), "trace": traceback.format_exc()})
                with lock:
                    record(sc, outcome)
                    print(fault_verdicts.batch_line(n, outcome["verdict"], sc["id"], outcome.get("notes", {})),
                          flush=True)
                # The device's eWeLink is always the local stub (ACCOUNT).
                emulator_stats.fault_run(OUT, family=STATS_DEVICE.family, device=STATS_DEVICE.key, scenario=sc,
                                         verdict=outcome["verdict"], tries=outcome.get("runs"),
                                         seconds=round(time.time() - started), cloud="stub", workers=count,
                                         rerun=sc["id"] in earlier,
                                         outcomes=fault_verdicts.run_outcomes(instance.state / "work/captures",
                                                                              started),
                                         error=outcome.get("notes", {}).get("error"),
                                         starved=outcome.get("notes", {}).get("memory", {}).get("starved"))
        finally:
            instance.stop()   # an emulator never left running

    emulator_stats.began(OUT, family=STATS_DEVICE.family, suite="fault", device=STATS_DEVICE.key, total=len(every),
                         planned=todo.qsize(), cloud="stub", workers=count,
                         tier=emulator_stats.tier(wanted, every, (sc["id"] for sc in scenarios(quick=True))))
    for n in numbers:
        workers.add(work, n).start()
    workers.join()
    print(emulator_stats.send(OUT), flush=True)
    workers.end()


def prep(first: int = 1) -> None:
    """The starting devices, converted by the tool from the dump on instance
    `first`: on the interim firmware (a run to ESPHome stopped once that has
    started), on OpenBeken, then on ESPHome, each saved with the captures its
    runs left."""
    instance = Instance(emulator_instances.take("beken", first)[0])
    try:
        instance.reset("sonoff")
        if not instance.start("sonoff"):
            raise SystemExit("the device did not come online")
        log = OUT / "prep-interim.log"
        OUT.mkdir(parents=True, exist_ok=True)
        run = instance.tool("esphome", log, new=True)
        if not wait_for_step(instance, "B4", run):
            raise SystemExit(f"the device did not start the interim firmware: see {log}")
        subprocess.run(["docker", "kill", instance.computer], capture_output=True)
        save(instance, "interim")
        instance.reset("sonoff")
        if not instance.start("sonoff"):
            raise SystemExit("the device did not come online")
        for target in ("openbeken", "esphome"):
            log = OUT / f"prep-{target}.log"
            OUT.mkdir(parents=True, exist_ok=True)
            for attempt in range(RECOVERY_RUNS):
                instance.tool(target, log, new=attempt == 0).wait(timeout=RUN_LIMIT)
                if converted(log):
                    break
                if asked_to_switch(instance):
                    instance.power("cycle")
                    instance.ready(target)
            else:
                raise SystemExit(f"the device was not converted to {target}: see {log}")
            save(instance, target)
            instance.start(target)
    finally:
        instance.stop()
    prune_captures.suggest()


def save(instance: Instance, firmware: str) -> None:
    instance.stop()
    folders.clear(SAVED / firmware)
    shutil.copytree(instance.state, SAVED / firmware, ignore=keep_for_saving)
    print(f"saved the device on {firmware} in {SAVED / firmware}", flush=True)


def prep_update(first: int = 1) -> None:
    """The update scenarios' starting devices: on OpenBeken and on ESPHome as
    EARLIER_RELEASE converts them, which no run of this checkout can make. That
    release's own suite makes them, from its tagged files, on instance `first`:
    in its tree, its folders are named as that release names them (v2.3.0's
    after the S61)."""
    emulator_instances.take("beken", first)
    tree = folders.LEGACY / EARLIER_RELEASE
    folders.clear(tree)
    (tree / "captures/emulation").mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(REPO), "archive", EARLIER_RELEASE], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    shutil.copytree(folders.FIRMWARES, tree / EARLIER_FIRMWARES)
    earlier_on(tree / "tools/beken/emulator/fault_suite.py", first)
    try:
        subprocess.run([str(tree / "tools/beken/emulator/fault_suite.py"), "prep"], check=True)
        for firmware in ("openbeken", "esphome"):
            kept = SAVED / f"outdated-{firmware}"
            folders.clear(kept)
            shutil.copytree(tree / EARLIER_SAVED / firmware, kept)
            print(f"saved the device on {EARLIER_RELEASE}'s {firmware} in {kept}", flush=True)
    finally:
        folders.clear(tree)


def earlier_on(suite: Path, first: int) -> None:
    """EARLIER_RELEASE's suite, in its tree, made to prep on instance `first`:
    it takes no --first-instance, its prep always on instance 1
    (EARLIER_PREP_INSTANCE)."""
    code = suite.read_text(encoding="utf-8")
    if EARLIER_PREP_INSTANCE not in code:
        raise SystemExit(f"{EARLIER_RELEASE}'s suite does not start its prep as expected: "
                         f"no {EARLIER_PREP_INSTANCE!r} in {suite}")
    suite.write_text(code.replace(EARLIER_PREP_INSTANCE, f"instance = Instance({first})"), encoding="utf-8")


# The check: two ways, each a final firmware installed, then that device taken to
# the other, as tools/espressif/emulator/public_devices.py's for the S60.
CHECK_WAYS = (("sonoff>openbeken", "openbeken>esphome"), ("sonoff>esphome", "esphome>openbeken"))


def check_way(n: int, routes: tuple[str, ...]) -> int:
    instance = Instance(n)
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        instance.reset("sonoff")
        if not instance.start("sonoff"):
            print(f"FAIL  {routes[0]}: the device did not come online", flush=True)
            return 1
        for route in routes:
            started = time.time()
            target = route.split(">")[1]
            log = OUT / f"check-{route.replace('>', '-')}.log"
            instance.before = {fault_verdicts.filed_as(path) for path in instance.all_runs()}
            for attempt in range(RECOVERY_RUNS):
                instance.tool(target, log, new=attempt == 0).wait(timeout=RUN_LIMIT)
                if converted(log):
                    break
                if asked_to_switch(instance):
                    instance.power("cycle")
                    instance.ready(target)
            problems = [] if converted(log) else ["not converted"]
            problems += interim_problems(route, instance.dhcp_names(), None)
            checks = fault_verdicts.checks_problem(instance.last_log(), target, meter_demand(instance.last_state()),
                                                   instance.last_outcome(started))
            if checks:
                problems.append(checks)
            print(f"{'FAIL' if problems else 'PASS'}  {route}{': ' + ', '.join(problems) if problems else ''}", flush=True)
            emulator_stats.check_run(OUT, family=STATS_DEVICE.family, device=STATS_DEVICE.key, route=route, passed=not problems,
                                     seconds=round(time.time() - started), cloud="stub")
            if problems:
                return 1
            instance.stop()
            if not instance.start(target):
                print(f"FAIL  {route}: the device did not start again on {target}", flush=True)
                return 1
            if board_inputs(STATS_DEVICE)["buttons"]:
                problem = inputs_problem(instance, target)
                print(f"{'FAIL' if problem else 'PASS'}  {route}, its button: "
                      f"{problem or 'each press switched its relay'}", flush=True)
                if problem:
                    return 1
        return 0
    finally:
        instance.stop()


def inputs_problem(instance: Instance, firmware: str) -> str | None:
    """What is wrong with how the device, running `firmware`, follows its
    button as its board presses it (emulator_check.Device.inputs_problem);
    None if nothing."""
    from tools.beken.emulator import board
    from tools.repo import emulator_check
    address = instance.lease()
    if not address:
        return "the device has no address"
    device = emulator_check.Device(address, firmware)
    if not device.answers(60):
        return "the device did not answer"
    return device.inputs_problem(board.buttons(instance.state), board_inputs(STATS_DEVICE))


def board_inputs(kind: kinds.Kind) -> dict[str, tuple[int, ...]]:
    """The buttons and wall switches on `kind`'s emulated board (board.py,
    which device.py runs its flash on): none for a kind with no board there."""
    from tools.beken.emulator import board
    return board.inputs(kind.key)


def check(instances: tuple[int, ...] = (1, 2)) -> int:
    """The final firmware of every way, side by side, each on its emulator of
    `instances`: no faults, no account. A way passes only by getting to its
    end: one whose thread raised has failed."""
    emulator_instances.refuse_taken("beken", instances)
    failed = [1] * len(CHECK_WAYS)
    emulator_stats.began(OUT, family=STATS_DEVICE.family, suite="check", device=STATS_DEVICE.key,
                         total=sum(map(len, CHECK_WAYS)), planned=sum(map(len, CHECK_WAYS)), cloud="stub", workers=2)

    def way(index: int) -> None:
        try:
            failed[index] = check_way(instances[index], CHECK_WAYS[index])
        except Exception as exc:  # noqa: BLE001 - said, and the way failed
            print(f"FAIL  {' then '.join(CHECK_WAYS[index])}: {exc!r}", flush=True)
    threads = [threading.Thread(target=way, args=(index,)) for index in range(len(CHECK_WAYS))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(emulator_stats.send(OUT), flush=True)
    return 1 if any(failed) else 0


def keep_for_saving(folder: str, names: list[str]) -> list[str]:
    """What a saved device leaves out: its logs, power switch and buttons, its stub's
    certificates (for its own network's router: each instance makes its own),
    the backup B4 made (so a route that takes B4 has it to do), and of its
    computer's copy of the checkout all but the captures its runs left."""
    if Path(folder).name == "work":
        return [name for name in names if name != "captures"]
    return [name for name in names if name.endswith(".log")
            or name in ("power", "buttons", "certs.part", "certs-for", "ca.crt", "leaf.crt", "leaf.key", "sonoff-backup")]


def suite_roots() -> list[Path]:
    """This suite's folders here, and those kept from other machines."""
    return [folders.BEKEN, *scenario_times.kept(folders.EMU, folders.BEKEN.name)]


def report() -> None:
    found = fault_verdicts.latest(RESULTS)
    for sc in scenarios():
        entry = found.get(sc["id"], {})
        label = fault_verdicts.starved_label(entry.get("verdict"), entry.get("notes", {}))
        print(f"{entry.get('verdict', '-'):9} {sc['id']}{label}")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    emulator_stats.mark("check" if command == "check" else "fault")   # its runs not taken for ones by hand
    for line in folders.migrate():
        print(line, file=sys.stderr)
    if command in ("prep", "prep-update", "batch"):
        import argparse
        parser = argparse.ArgumentParser(prog=f"fault_suite.py {command}")
        if command == "batch":
            parser.add_argument("file", nargs="?")
        emulator_instances.add_first_instance(parser)
        args = parser.parse_args(argv[1:])
        if command == "prep":
            prep(args.first_instance)
        elif command == "prep-update":
            prep_update(args.first_instance)
        else:
            batch(args.file, args.first_instance)
            return 0 if all_passed(args.file) else 1
    elif command == "check":
        import argparse
        parser = argparse.ArgumentParser(prog="fault_suite.py check")
        parser.add_argument("--instances", default="1,2", help="the two emulators to use (default 1,2)")
        numbers = parser.parse_args(argv[1:]).instances.split(",")
        if len(numbers) != len(CHECK_WAYS) or not all(number.isdigit() for number in numbers):
            parser.error(f"--instances needs {len(CHECK_WAYS)} numbers, such as 3,4")
        return check(tuple(map(int, numbers)))
    elif command == "list":
        if argv[1:] not in ([], ["openbeken"], ["quick"]):
            print("list takes openbeken or quick", file=sys.stderr)
            return 2
        for i in (OPENBEKEN_CHECK if argv[1:] == ["openbeken"] else
                  [sc["id"] for sc in scenarios(quick=argv[1:] == ["quick"])]):
            print(i)
    elif command == "report":
        report()
    elif command == "status":
        return fault_status.main(argv[1:], scenarios(), folders.BEKEN, "fault_suite.py status")
    elif command == "found":
        for line in fault_yield.found(fault_yield.rows(*suite_roots())):
            print(line)
    elif command == "share":
        # share IDS WORKERS... [--times-from DIR]..., as the Espressif suite's
        import argparse
        parser = argparse.ArgumentParser(prog="fault_suite.py share")
        parser.add_argument("file")
        parser.add_argument("workers", nargs="+", type=int)
        parser.add_argument("--times-from", action="append", default=[], type=Path, metavar="DIR")
        args = parser.parse_args(argv[1:])
        try:
            for line in scenario_times.write_shares(args.file, args.workers, scenarios(),
                                                    scenario_times.times(*suite_roots(), *args.times_from)):
                print(line)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
