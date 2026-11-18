"""A readout: a copy of a device's whole memory, to help add a device this tool
does not convert yet, sent with its owner's consent to firmwareliberator.com.

Nothing is converted. A readout comes in two stages. The first is only ever
read: what the device and eWeLink say it is, and the firmware eWeLink offers
it, fetched as Sonoff's own updater would fetch it; nothing is sent to the
device. A device the tool has not seen, or one on another firmware now,
gets that alone. The second reads its whole memory, and only on a listed kind
whose status says it is proven not to leave the device unable to start (kinds.Status.reads_out), under the firmware
name that kind's own firmware has: an Espressif device through the bridge,
which leaves Sonoff's firmware where it is (readout_espressif.py); a Beken
device through a readout OpenBeken, which puts Sonoff's firmware back
(readout_beken.py). Either way the copy is anonymised before anything is sent,
and the device is left on its Sonoff firmware.

This is what both share: the device found in the eWeLink account and on the
network, what it reports, and the readout packed, offered and sent - for one
device, or for every device in the account the tool does not convert, each
read and sent on its own (run_all), so the website can turn down one it has
enough readouts of.
"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import os
import re
import secrets
import tarfile
import threading
import time
import urllib.error
from dataclasses import dataclass
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, TYPE_CHECKING

from tools.autoflash import __version__, account, detect, hostsys, kinds, output, records, prompts, runs
from tools.autoflash import errors, failures, openbeken, proof_of_work, readout_beken, readout_espressif, report, report_upload, signup
from tools.autoflash.errors import Abort, Finished, NothingWritten
# How a device's readout ended (Became.outcome): its report's result, sent as
# each readout ends (report_upload.send_readout), and on the screen's at the end.
from tools.autoflash.report_data import (
    ACCEPTED, CANCELLED, CRASHED, FAILED, KEPT, NOT_NEEDED as UNNEEDED, NOT_READ, SKIPPED,
    WEBSITE_FULL, device_hash)
from tools.ewelink import discover_ewelink, firmware_name, offered_firmware

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# Where readouts go: a Worker of their own, apart from the website's, with a
# private store behind it: the project's API (website/api/index.js). Its
# questions, /wanted and /notify, and first stages sent as links, /offers,
# are beside it (light_post).
UPLOAD_URL = "https://api.firmwareliberator.com/v1/readout"
OFFERS = "/offers"
# The most offers one request to /offers carries, and its most bytes (the
# API's OFFERS_PER_REQUEST and OFFERS_MAX_BYTES); and the cloud they come
# from, the only one the tool knows.
OFFERS_PER_REQUEST = 50
OFFERS_MAX_BYTES = 32 * 1024
CLOUD = "ewelink"
# The work each readout carries, as the Worker asks it (its PROOF_BITS); and
# the lighter work of /wanted and /notify (LIGHT_PROOF_BITS).
PROOF_BITS = 24
LIGHT_PROOF_BITS = 20
FORMAT = 1
# How long send() waits, each time the website says too many at once (it
# takes two a minute from one address), before sending again: short tries, so
# it goes as soon as the limit lets it, for up to a minute and a half.
RATE_WAITS = (15.0,) * 6
# What becomes of a device whose readout would add nothing, or that comes
# once the website has taken all it can today.
NOT_NEEDED = "not needed: the website has what it needs of this device already."
FULL_TODAY = "not read out: the website has taken all the readouts it can today. Try again tomorrow."
# The website's store of readouts is full, until its maintainer makes room.
STORE_FULL = "not read out: the website cannot take more readouts for now. Try again in a few weeks."
LIMIT_TODAY = "not read out: the website takes 20 readouts a day from one place. Try again tomorrow."
# What send() gives for a readout the website has enough of already.
ENOUGH = "none needed: enough readouts of this device are in already"
# What the run's record keeps, so a readout is never offered back as a conversion to finish:
# how far it got - "started", "sent" once an Espressif device has its reader, "read" once
# the device is back on Sonoff's firmware - and REPLACED while a Beken device's reader
# stands in place of Sonoff's firmware.
STATE_KEY = "readout"
REPLACED = "replaced"
# What the run's record keeps of the one device being read out, with
# readout_espressif.BRIDGE_HOST: cleared before the next, when every device is
# read out in one run.
DEVICE_KEYS = ("device_ip", "device_mac", "stock_version", "product_model", STATE_KEY, "readout_sent")


# The stage lines a readout may show on screen, in order, as a conversion shows
# its route's steps - not the readout's two stages above, which are what it
# reads. The first is the account's, once; the rest are each device's,
# numbered again for each device of several (run_all), and only those it has:
# one read only in the first stage has nothing to read or restore, so those
# are never shown (skip). A readout a run turns into (from_run) has done the
# first as that run's stage 2, and numbers on from there.
STAGES = (
    ("account", "Querying eWeLink for devices"),
    ("locate", "Locating the device on your network"),
    ("probe", "Probing the device"),
    ("fetch", "Fetching the eWeLink firmware"),
    ("read", "Reading the device's memory"),
    ("restore", "Restoring Sonoff's firmware"),
    ("save", "Saving the readout"),
)
# The number this readout's first stage, "account", takes (lay_out_stages).
_first_stage = 1
# The numbers the device's stages have taken so far, by key, each the next.
_numbered: dict[str, int] = {}


def lay_out_stages(first: int = 1) -> None:
    """Number the readout's stages from `first` - 2 in a run's readout, whose
    stage 2 found the account's devices (from_run) - its names' column as
    wide as the longest, or the run's, where that is wider."""
    global _first_stage
    _first_stage = first
    _numbered.clear()
    output.set_stage_width(max(output.stage_width(), *(len(name) for _, name in STAGES)),
                           max(output.most_stages(), first + len(STAGES) - 1))


def begin(key: str) -> None:
    """The readout's stage `key` under way, the one before it ended first."""
    output.stage_end("complete")
    number = _first_stage if key == "account" else _numbered.setdefault(key, _first_stage + 1 + len(_numbered))
    output.stage_begin(number, dict(STAGES)[key])


def skip(key: str) -> None:
    """The readout's stage `key` passed over: nothing for it to do, so no line
    for it on the screen, and no number - the log says it was passed over."""
    output.log_only(f"--- {dict(STAGES)[key]}: not needed")


def stopped_by_ctrl_c(ctx: Context) -> str:
    """What a readout stopped by Ctrl-C ends by saying. An Espressif device's
    reader sits beside Sonoff's firmware, which switching the device off and on
    starts again; a Beken device's stands in its place, and only the readout
    run again (readout_beken.read_out) - or a conversion (readout_beken.put_back)
    - puts Sonoff's firmware back."""
    if ctx.state.input(STATE_KEY) == REPLACED:
        return "Stopped with Ctrl-C. Run the readout again: it puts Sonoff's firmware back."
    return "Stopped with Ctrl-C. If it was being read, switch it off and on to go back to Sonoff."


def restoring() -> None:
    """Reading done, the device's Sonoff firmware next: each chip's readout
    says when (readout_espressif, readout_beken)."""
    begin("restore")


class Unsent(Abort):
    """A readout made but not sent: `why`, in words someone can act on
    (website_problem), and the file it is kept in to send later."""

    def __init__(self, why: str, path: Path) -> None:
        # Named as the run's log is under the report: from the checkout.
        super().__init__(f"the readout could not be sent: {why}. It is kept in {runs.display_path(path)}: send "
                         "it later with this tool's readout --send")
        self.why, self.path = why, path


def website_problem(exc: BaseException) -> str:
    """Why the website could not be reached, said so someone can act on it
    (errors.plainly); the error itself goes to the log."""
    output.log_only(f"    The website could not be reached: {type(exc).__name__}: {exc}")
    return errors.plainly(exc, "the website")


def tell_failed(ctx: Context, exc: BaseException) -> None:
    """A readout that failed, as it ends: why, then - where any device was
    read out - the readout's report and the way to open an issue (end). Where
    one left unsent is kept, to send later, is the log's to say (docs/readouts.md)."""
    if isinstance(exc, Unsent):
        output.tell(f"The readout could not be sent: {exc.why}.")
        output.log_only(f"It is kept, to send later with this tool's readout --send: {exc.path}")
    else:
        output.tell(f"FAILED: {exc}")
    if readouts(ctx):
        end(ctx, "", failed=True)


class WebsiteFull(NothingWritten):
    """The website has taken all the readouts it can, today or until its store
    has room: a readout of every device ends there, the rest not read."""


@dataclass(frozen=True)
class Wanted:
    """The website's answer to /wanted: whether a readout would add anything,
    why not ("enough", "held", "full" for the day or "store-full"), which
    offered files it holds, and how a first stage sends them: "files", or
    "links" (each file's link and SHA-256 alone, to /offers), as the website
    is set to take them."""
    wanted: bool
    why: str = ""
    held: frozenset[str] = frozenset()
    send: str = "files"


@dataclass(frozen=True)
class Batch:
    """A readout of one device among several in one run (run_all)."""


# What a device not back on its Sonoff firmware still needs, said as the run ends.
SWITCH_OFF_AND_ON = "Switch the device off and on once more: it then starts its Sonoff firmware again."
# Said by either family's readout when eWeLink has not shown the device online
# again in the time waited.
NOT_ONLINE_YET = "If the eWeLink app doesn't show it yet, switch it off and on once more."

@dataclass
class Became:
    """One device's readout: filled in as it goes (run), and how it ended,
    for its report (report_data.readout_payload) and the screen's at the end.
    `name` and `detail` are for the screen alone - the device's name in the
    account, and the reference it was sent under - never for a report."""
    name: str
    product: str = ""
    model: str = ""
    version: str = ""
    whole: bool | None = None   # its whole memory read; False: what it and eWeLink say alone
    outcome: str = NOT_READ
    detail: str = ""            # the firmware it runs now, the reference, or why it was kept
    back: bool = True           # its Sonoff firmware answered again
    mac: str | None = None      # for its report's device hash, as a conversion's sends
    error: str | None = None    # how a failed one failed (failures.classify)
    report_id: str | None = None   # its report's, once kept to send (report_upload.send_readout)

    @classmethod
    def of(cls, device: dict[str, Any], **ended: Any) -> Became:
        """A readout of the account's `device`, as far as its record says."""
        return cls(describe(device), product=str(device.get("productModel") or ""),
                   model=str((device.get("extra") or {}).get("model") or ""), **ended)


# Each outcome as the report on the screen says it (report_fields), in this order.
SAID = {ACCEPTED: "Accepted", UNNEEDED: "Not needed", KEPT: "Not sent", WEBSITE_FULL: "Website full",
        SKIPPED: "Skipped", NOT_READ: "Not read out", CANCELLED: "Stopped",
        FAILED: "Failed", CRASHED: "Crashed"}
# The report's right-hand column (report.report_screen).
REPORT_RIGHT = ("Devices", "Accepted", "Not needed", "Not sent")


def named(item: Became) -> str:
    """A device as the report on the screen names it: its model and firmware
    version alone, kept together on a row (no-break spaces)."""
    return "\u00a0".join(part for part in (item.product or item.model or "unknown", item.version) if part)


def report_fields(done: list[Became]) -> list[tuple[str, str]]:
    """The readout's report for the screen, as (label, value): the tool and
    this computer, and how many devices were read out, how many the website
    accepted, how many it did not need, and how many were kept unsent, where
    any were. How it ended is the line above it (end), and each device's
    under it (report_devices)."""
    fields = [
        ("Liberator", __version__),
        ("OS", report.computer_description()),
        ("Python", report.python_description()),
        ("Devices", str(len(done))),
        ("Accepted", str(sum(item.outcome == ACCEPTED for item in done))),
        ("Not needed", str(sum(item.outcome == UNNEEDED for item in done))),
    ]
    kept = sum(item.outcome == KEPT for item in done)
    if kept:
        fields.append(("Not sent", str(kept)))
    return [(label, report.answer(value)) for label, value in fields]


def report_devices(done: list[Became]) -> list[tuple[str, str]]:
    """The devices under the report, as (label, value), as short as can be:
    each by its model and firmware version, under how it ended, each model and
    version once however many were read out. What else became of each is in
    the log."""
    listed = []
    for outcome, label in SAID.items():
        these = dict.fromkeys(named(item) for item in done if item.outcome == outcome)
        if these:
            listed.append((label, ", ".join(these)))
    return listed


def end(ctx: Context, said: str, failed: bool = False) -> None:
    """The end of a readout, as a conversion's: what the person is still to do
    (`said`), then the report - one for every device read out in the sitting -
    and the links to support the project; or for one that `failed`, the way to
    open an issue in their place (report.ask_for_an_issue)."""
    done = readouts(ctx)
    # Made before anything of it is said, a wait shown while it is, as a
    # conversion's report is (report.invite_report).
    with output.working("Generating your report..."):
        fields, devices = report_fields(done), report_devices(done)
    output.tell("")
    # How it ended, above the report, as a conversion's says: one that failed
    # has its FAILED line above already, even with a device read before it.
    if not failed and any(item.outcome in (ACCEPTED, KEPT) for item in done):
        output.show("Readout complete!", output.GREEN)
    elif not failed:
        output.show("Nothing was read out.")
    if said:
        for line in said.splitlines():
            output.tell(line)
    output.tell("")
    for line in report.report_screen(fields, right=REPORT_RIGHT, below=devices, balance=False):
        output.tell(line)
    if failed:
        # The report's id of the device that failed, as a conversion's that did.
        report_id = next((item.report_id for item in done if item.outcome in (FAILED, CRASHED)), None)
        copy = report.report_screen(fields, report.CLIPBOARD_COLUMNS, right=REPORT_RIGHT, balance=False,
                                    below=[*devices, *([(report.REPORT_ID, report_id)] if report_id else [])])
        report.ask_for_an_issue(report.clipboard_text(copy), report_id)
    else:
        report.hold_out_hat()


def readouts(ctx: Context) -> list[Became]:
    """This sitting's readouts so far, one for each device, in the order read."""
    if getattr(ctx, "readouts", None) is None:
        ctx.readouts = []
    return ctx.readouts


def became_of(ctx: Context, device: dict[str, Any]) -> Became:
    """`device`'s readout in this sitting's: the last, or a new one where run()
    made none (it never began)."""
    done = readouts(ctx)
    if not done or done[-1].name != describe(device):
        done.append(Became.of(device))
    return done[-1]


def became(ctx: Context) -> Became:
    """The readout under way (run), or one of its own for offer() called alone."""
    return getattr(ctx, "readout", None) or Became("")


def upload_url() -> str | None:
    """Where to send it: AUTOFLASH_READOUT_URL when set (a test's own server),
    else the website - but never from the emulator or an unattended run, which
    would send a test's flash as a device's."""
    given = os.environ.get("AUTOFLASH_READOUT_URL")
    if given:
        return given
    if hostsys.emulated() or prompts.UNATTENDED:
        return None
    return UPLOAD_URL


def describe(device: dict[str, Any]) -> str:
    name = str(device.get("name") or "").strip() or str(device.get("deviceid") or "a device")
    return f"'{name}' ({device.get('productModel') or 'unknown model'})"


def unknown_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The account's devices of a kind this tool does not convert yet."""
    return [device for device in devices if account.account_device(device) is None]


def answering_ids(report: dict[str, Any]) -> dict[str, str]:
    """{device id: address} of each answer in an eWeLink discovery report."""
    found: dict[str, str] = {}
    for packet in report.get("packets", []):
        for record in packet.get("records", []):
            if record.get("type") == "TXT":
                for value in record.get("value") or []:
                    if str(value).startswith("id="):
                        found.setdefault(str(value)[3:], str(packet["source"]))
    return found


def device_at(address: str, devices: list[dict[str, Any]]) -> dict[str, Any]:
    """The account's device at `address` (--device-ip): the one whose hardware
    address, as its record gives it, answers there; else, for a record that
    gives none, the one answering eWeLink's discovery there; else the one
    device the account holds."""
    mac = hostsys.mac_of(address)
    at = hostsys.normalise_mac(mac) if mac else None
    match = [device for device in devices if at and record_mac(device) == at]
    if not match:
        try:
            here = answering_ids(discover_ewelink.discover(6.0, None, address))
        except OSError:
            here = {}
        match = [device for device in devices if str(device.get("deviceid")) in here]
    if not match and len(devices) == 1:
        match = list(devices)  # one device in the account: it is the one at this address
    if not match:
        raise Abort(f"could not tell which device in the eWeLink account is at {address}: "
                    "its hardware address is none the account gives, and it answered no discovery")
    return match[0]


def is_beken(device: dict[str, Any]) -> bool:
    """Whether the account's record of `device` names a Beken chip, by its
    model, before anything is read: its readout (readout_beken) finds what
    the device runs itself."""
    model = str((device.get("extra") or {}).get("model") or device.get("productModel") or "")
    stock_chip = firmware_name.STOCK_MODEL.match(model)
    known = kinds.for_account(str(device.get("productModel") or ""), model)
    return (stock_chip is not None and stock_chip.group("chip").startswith("BK")) or (
        known is not None and known.family == "beken")


def record_mac(device: dict[str, Any]) -> str | None:
    """The hardware address eWeLink's record of `device` gives it, if any."""
    mac = hostsys.normalise_mac(str((device.get("params") or {}).get("staMac") or ""))
    return mac if records.MAC_RE.match(mac) else None


# How long a device is looked for by its hardware address before its address
# is asked for, and how often: one sweep misses a device slow to answer ARP -
# its Wi-Fi asleep, or a busy computer's emulated one (seen: an S61 beside
# another emulator, missed by a sweep's 2.5 s) - as the conversion's own
# search looks again (join.locate_device).
LOOK_FOR = 30.0
LOOK_AGAIN = 5.0


def found_by_mac(mac: str) -> str | None:
    """Where the hardware address `mac` is on this computer's networks, looked
    for again every LOOK_AGAIN seconds until LOOK_FOR is up."""
    networks = sorted(hostsys.lan_networks())
    if not networks:
        return None
    started = time.monotonic()
    while True:
        found = hostsys.find_by_mac(mac, networks)
        if found or time.monotonic() - started >= LOOK_FOR:
            return found
        time.sleep(LOOK_AGAIN)


def answering_at(device: dict[str, Any]) -> str | None:
    """Where `device` answers eWeLink's discovery with its id, or None."""
    wanted = str(device.get("deviceid") or "")
    output.say("Looking for it on your network...")
    try:
        report = discover_ewelink.discover(6.0, wanted={wanted})
    except OSError:
        report = {"packets": []}
    return answering_ids(report).get(wanted)


def locate(ctx: Context, device: dict[str, Any]) -> str:
    """The device's address on this network: where the hardware address its
    eWeLink record gives answers - or, for a record that gives none, where it
    answers eWeLink's discovery with its id - else typed by the person, and
    checked against that hardware address."""
    mac = record_mac(device)
    if mac:
        output.say("Looking for it on your network by its hardware address...")
        found = found_by_mac(mac)
    else:
        found = answering_at(device)
    if found:
        return found

    def check(address: str) -> list[str]:
        seen = hostsys.mac_of(address)
        if mac and seen and hostsys.normalise_mac(seen) != mac:
            return [f"{address} is another device: its hardware address is not the one eWeLink has for yours."]
        return []
    output.transient_begin()
    address = prompts.ask_ipv4(
        "The device was not found on this network. Its address",
        help_lines=["Its address is in your router's device list, or the eWeLink app's device info."],
        check=check)
    output.transient_end()
    return address


def run(ctx: Context, device: dict[str, Any], address: str | None = None, batch: Batch | None = None) -> str:
    """Read out the account's `device` and offer to send it. What the person
    is still to do, for the run to end by saying ("" for nothing): what
    became of it goes in the report at the end (readouts, report_fields).
    Abort before anything was sent to the device, or after, with what to do.
    In a `batch`, it is sent as it is made, unasked.

    Under the readout's stage lines (STAGES): the one under way when it stops
    ends as stopped, with what it showed left under it - or skipped, for one
    device of several passed by."""
    ctx.readout = this = Became.of(device)
    ctx.readout_device_id = str(device.get("deviceid") or "")   # to read it out again (cli.what_next)
    readouts(ctx).append(this)
    _numbered.clear()   # its stages numbered on from the account's
    # Its own report, sent as it ends however it ends: one a readout.
    report_upload.begin_readout(ctx)
    started, waited = time.monotonic(), output.waited()
    try:
        said = _run(ctx, device, address, batch)
    except NothingWritten as exc:
        if this.outcome == NOT_READ:
            # One of several passed by at its question; one alone not
            # confirmed there, which the person chose.
            this.outcome = SKIPPED if batch is not None else CANCELLED
            this.detail = this.detail or str(exc)
        output.stage_end("skipped" if batch is not None else "stopped")
        raise
    except (prompts.QuitRun, KeyboardInterrupt):
        this.outcome = CANCELLED
        output.stage_end("stopped")
        raise
    except Abort as exc:
        this.outcome, this.error = FAILED, failures.class_of(exc)
        output.stage_end("stopped")
        raise
    except BaseException:
        this.outcome = CRASHED
        output.stage_end("stopped")
        raise
    finally:
        report_upload.send_readout(ctx, this, time.monotonic() - started - (output.waited() - waited))
    output.stage_end("complete")
    return said


def _run(ctx: Context, device: dict[str, Any], address: str | None, batch: Batch | None) -> str:
    ctx.state.set_input(STATE_KEY, "started")
    # One the account says can have only the first stage is not looked for on
    # the network at all: that stage is eWeLink's to tell, so it works for a
    # device that answers no discovery (a Matter one, say), and asks nothing of it.
    first_only = not reads_out(device)
    ip = seen = None   # `seen`: where a first-stage device answered, for its MAC alone
    if address:
        skip("locate")
        ip, seen = (None, address) if first_only else (address, None)
    elif not first_only:
        begin("locate")
        ip = locate(ctx, device)
    elif record_mac(device) is None:
        # Its report names the device by its MAC, as a conversion's does, so
        # a readout and a later conversion of it are known as one: where
        # eWeLink's record gives none, looked for on the network - never
        # asked for, as this stage needs no address.
        begin("locate")
        seen = answering_at(device)
    else:
        skip("locate")
    if ip:
        ctx.state.set_input("device_ip", ip)
    looked_at = ip or seen
    mac = (hostsys.mac_of(looked_at) if looked_at else None) or (device.get("params") or {}).get("staMac")
    if mac and records.MAC_RE.match(hostsys.normalise_mac(mac)):
        mac = hostsys.normalise_mac(mac)
        ctx.readout.mac = mac
        ctx.state.set_input("device_mac", mac)
        if batch is None:
            # Under the device's own folder, as a conversion's run is: its log, the
            # readout kept unsent, and the backup beside them.
            runs.file_device(ctx, mac, None)
        elif not first_only:
            # A run of several devices stays where it is; one written to is held
            # all the same, so no other run converts it meanwhile.
            runs.hold_device(ctx, mac)
        records.remember_device(mac, ewelink_name=str(device.get("name") or "").strip() or None,
                            ewelink_id=str(device.get("deviceid") or "") or None,
                            model=str(device.get("productModel") or "") or None)
    # Asked of eWeLink by its account id, once what answers at its address is
    # known to be Sonoff's firmware (runs_now). One on another firmware -
    # converted by this tool or by hand - has none of Sonoff's left to read,
    # but eWeLink's record of it and the firmware eWeLink offers it are still
    # its first stage.
    begin("probe")
    converted = runs_now(device, ip) if ip else None
    if first_only or converted:
        asked = ask_first_stage(ctx, device)
    else:
        asked = account.ask_account(ctx, str(device.get("deviceid") or ""))
    ctx.readout.product = asked.product or ctx.readout.product
    ctx.readout.model = asked.model or ctx.readout.model
    ctx.readout.version = asked.version
    learned, problems = firmware_name.learn(asked.latest.get("offers") or [], asked.deviceid,
                                            asked.product, asked.model)
    for problem in problems:
        output.log_only(f"    Firmware name: {problem}")
    known = kinds.for_account(asked.product, asked.model)
    # Ready or not: a readout's second stage is for any kind listed whose status allows it.
    listed = kinds.for_account(asked.product, asked.model, among=kinds.EVERY)
    if learned is None and listed is not None and listed.status.reads_out and listed.family == "espressif":
        # A kind the tool converts: its own name for it, as a conversion sends.
        learned = firmware_name.Learned(listed.wrapper_model, firmware_name.chip_of(listed.wrapper_model),
                                        "this tool's list of devices")
    ctx.state.set_input("stock_version", asked.version)
    ctx.state.set_input("product_model", asked.product)
    whole = not converted and whole_readout(listed, learned)
    # Logged, as a conversion's is (account.py): the chooser named the device.
    prompts.section("What the device reported", [
        f"Model            {asked.model or 'not given'}",
        f"Product          {asked.product or 'not given'}",
        f"Firmware now     {asked.version}",
        f"Firmware name    {learned.name if learned else 'not learned'}",
    ], quiet=True)
    begin("fetch")
    # Before the firmware eWeLink offers it is fetched, anything is sent to it,
    # or the readout's own work is done: whether it would add anything at all,
    # and which of the files eWeLink offers it are held already.
    offers = asked.latest.get("offers") or []
    with output.working("Asking the website whether a readout of it is wanted..."):
        answer = ask_wanted(asked.product, asked.model, asked.version, "whole" if whole else "first",
                            offered_digests(offers), device_hash(ctx.readout.mac))
    if not answer.wanted:
        full = {"full": FULL_TODAY, "store-full": STORE_FULL, "limit": LIMIT_TODAY}.get(answer.why)
        ctx.readout.outcome, ctx.readout.detail = (WEBSITE_FULL, full) if full else (UNNEEDED, "")
        if full and batch is not None:
            raise WebsiteFull(full)
        if batch is not None:
            return ""
        # Said under the report's headline (end), not in the stage: told there,
        # it went with the stage's notes as the stage passed, and the person saw
        # only "Nothing was read out." - not why, nor when to try again.
        said = full or NOT_NEEDED
        return f"{said[0].upper()}{said[1:]}"
    manifest = {
        "format": FORMAT, "tool": __version__, "model": asked.product or "-", "stock_model": asked.model,
        "stock_version": asked.version, "brand": str(device.get("brandName") or ""),
        "uiid": (device.get("extra") or {}).get("uiid"),
        "firmware_name": learned.name if learned else "", "name_from": learned.source if learned else "",
        "chip": learned.chip if learned else "",
        # What it runs now, where that is not Sonoff's: eWeLink's record of
        # its version is then of the last Sonoff firmware it ran.
        **({"runs": converted} if converted else {}),
    }
    # The firmware eWeLink offers it as well: often another version than the
    # one it runs, so two to test against. Nothing is sent to the device for it.
    # Those the website holds already are left out - but for a Beken device read
    # in full, which needs Sonoff's own to put back.
    beken_whole = bool(learned and whole and learned.chip.startswith("BK"))
    # Where the website takes links, it is sent each file's link in place of
    # the file, which it fetches itself: a first stage to /offers
    # (sent_links), a whole readout in its manifest. Only links of the shape
    # known (offered_firmware.unlinked): a file with another is downloaded and
    # sent as in files, and a first stage with one goes as files whole, as an
    # offer must name every file. Nothing else is downloaded - but for a Beken
    # device read in full, as above.
    unlinked = offered_firmware.unlinked(offers)
    links = answer.send == "links" and (whole or not unlinked)
    as_links = links and not whole
    linked = [item for item in offered_firmware.links(offers) if item["sha256"] not in answer.held] if links else []
    by_link = {item["sha256"] for item in linked}
    updates, problems = [], []
    if not links or beken_whole or unlinked:
        with output.working("Downloading the firmware eWeLink offers it..."):
            updates, problems = offered_firmware.fetch(
                offers, asked.deviceid, skip=frozenset() if beken_whole else answer.held | by_link)
    for problem in problems:
        output.log_only(f"    Offered firmware: {problem}")
    sent = [item for item in updates
            if item.sha256 not in by_link and not (beken_whole and item.sha256 in answer.held)]
    # Each file sent named by its place in the readout, which the website
    # checks it against; each held already, fetched for a Beken device or not,
    # by its SHA-256 alone; each sent as a link by its path.
    manifest["offered"] = [{"file": f"offered/{item.name}", "version": item.version, "size": len(item.data),
                            "sha256": item.sha256} for item in sent]
    manifest["offered"] += [{"file": None, "version": item.version, "sha256": item.sha256, "held": True}
                            for item in updates if item not in sent and item.sha256 not in by_link]
    manifest["offered"] += held_offered(offers, answer.held, [item.sha256 for item in updates])
    manifest["offered"] += [{"file": None, "version": item["version"], "sha256": item["sha256"],
                             "link": item["path"]} for item in linked]
    if linked:
        manifest["cloud"] = CLOUD
    chip = readout_espressif.BRIDGE_CHIPS.get(learned.chip) if learned and whole else None
    beken_stock = (readout_beken.stock_from_offered(updates)
                   if learned and whole and learned.chip.startswith("BK") else None)
    if chip is not None or beken_stock is not None:
        begin("read")
    else:
        skip("read")
        skip("restore")
    if chip is not None:
        files, back = readout_espressif.read_and_anonymise(ctx, manifest, chip, learned.name, asked.version, device,
                                                           known=known is not None, skippable=batch is not None)
    elif beken_stock is not None:
        files, back = readout_beken.read_and_anonymise(ctx, manifest, learned.name, asked.version, device,
                                                       beken_stock, skippable=batch is not None)
    else:
        if converted:
            why = f"it runs {detect.NAMES[converted]} now, not Sonoff's firmware"
        elif learned is None:
            why = "its firmware name could not be learned"
        elif not whole:
            why = ("this tool has not proven a full readout safe on this device yet, so nothing is "
                   "sent to it: this readout is what it and eWeLink say, and the firmware eWeLink offers it")
        elif learned.chip.startswith("BK"):
            why = "its stock firmware could not be read from the file eWeLink offers it"
        else:
            why = f"there is no bridge for its chip ({learned.chip or 'not named'}) yet"
        output.say(f"    Its memory cannot be read from here: {why}. The readout still helps.")
        manifest["read"] = ctx.readout.whole = False
        begin("save")
        if as_links:
            return sent_links(ctx, an_offer(asked.product, asked.model, asked.version, offers,
                                            device_hash(ctx.readout.mac)), batch)
        return offer(ctx, manifest, None, sent, batch=batch, device=device)
    ctx.readout.whole, ctx.readout.back = True, back
    begin("save")
    return offer(ctx, manifest, files, sent, back=back, batch=batch, device=device)


def held_offered(offers: list[dict[str, Any]], held: frozenset[str],
                 listed: Iterable[str] = ()) -> list[dict[str, Any]]:
    """A readout's manifest's entry for each file eWeLink offers that the
    website holds already, by its SHA-256 alone, but those `listed` already."""
    return [{"file": None, "version": version, "sha256": digest, "held": True}
            for digest, version in offered_digests(offers, versions=True)
            if digest in held and digest not in listed]


def runs_now(device: dict[str, Any], ip: str) -> str | None:
    """The firmware other than Sonoff's that `device` answers at `ip` with, as
    detect.NAMES has it (account.already_converting), or None for Sonoff's.
    Not asked of a reading program a readout stopped part way left on it -
    the bridge, or a Beken device's readout OpenBeken - whose readout
    carries on (readout_espressif, readout_beken)."""
    if (openbeken.is_readout(ip) if is_beken(device) else readout_espressif.installer.bridge_answers(ip)):
        return None
    try:
        account.already_converting(ip)
    except account.NotOnSonoff as exc:
        return exc.firmware
    return None


def reads_out(device: dict[str, Any]) -> bool:
    """Whether the account's record of `device` names a kind listed whose
    status allows a whole readout: only then can there be a second stage."""
    model = str((device.get("extra") or {}).get("model") or "")
    listed = kinds.for_account(str(device.get("productModel") or ""), model, among=kinds.EVERY)
    return listed is not None and listed.status.reads_out


def ask_first_stage(ctx: Context, device: dict[str, Any]) -> account.Asked:
    """What eWeLink says of `device`, by its id; where its cloud has no update
    record for it (one that never speaks to eWeLink's cloud itself, as a Matter
    device may not), what the account's record of it says, so the first stage
    still has something to send."""
    deviceid = str(device.get("deviceid") or "")
    try:
        return account.ask_account(ctx, deviceid)
    except Abort as exc:
        output.say(f"    eWeLink could not say what it runs ({exc}): the account's record of it is sent.")
        params = device.get("params") or {}
        return account.Asked(str(params.get("fwVersion") or ""),
                             str((device.get("extra") or {}).get("model") or ""),
                             str(device.get("productModel") or ""), [], {}, deviceid)


def whole_readout(listed: kinds.Kind | None, learned: firmware_name.Learned | None) -> bool:
    """Whether the device may be sent a program to read its whole memory: a
    listed kind whose status allows it, under the firmware name that kind's
    firmware has. A device with another name - a variant sharing its product
    model, a Matter version, say - is one the tool has not seen.

    The BASICR4 is why: its partition table is where earlier bridges did not
    look, and one sent to it would have stopped there, leaving it unable to
    start until it was opened (devices/sonoff-basicr4/README.md)."""
    return (listed is not None and listed.status.reads_out and learned is not None
            and learned.name == listed.wrapper_model)


# The large files read from the device are compressed with xz, about a quarter
# smaller than gzip on a real dump; the tar around them is not, so the API
# Worker reads its manifest, always first, without unpacking anything. The
# firmware eWeLink offers goes in as it is: the Worker checks each file against
# the SHA-256 the manifest gives it, which it cannot do through xz, before
# marking it held.
COMPRESSED = (".bin",)
NAME = "readout.tar"


def package(manifest: dict[str, Any], files: dict[str, bytes] | None = None,
            updates: list[offered_firmware.Offered] | tuple = ()) -> bytes:
    """The readout as sent: a tar of manifest.json first; the firmware eWeLink
    offers the device, under offered/, as it is; and `files` - the anonymised
    flash and its companions (flash.bin, efuse.bin, info.txt, report.json),
    already anonymised by the chip family that read them. Each of those .bin
    is xz-compressed (flash.bin.xz), so the tar stays readable without
    unpacking it."""
    out = {"manifest.json": json.dumps(manifest, indent=2).encode()}
    out.update({f"offered/{item.name}": item.data for item in updates})
    for name, data in (files or {}).items():
        compressed = name.endswith(COMPRESSED)
        out[name + ".xz" if compressed else name] = lzma.compress(data) if compressed else data
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in out.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mtime = len(data), int(time.time())
            archive.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


# Devices, and readouts, one sign-up may name, as the website takes them
# (MAX_DEVICES, MAX_READOUTS).
NOTIFY_MOST = 20
# The email sign-up is sent as the tool exits, someone waiting on it: one
# quick try again (seconds), each try given less time than a question asked
# within a stage - one not sent goes at the next sitting's end
# (signup.PENDING), so a long wait now buys little.
NOTIFY_WAITS = (2,)
NOTIFY_TIMEOUT = 8.0


def offer(ctx: Context, manifest: dict[str, Any], files: dict[str, bytes] | None = None,
          updates: list[offered_firmware.Offered] | tuple = (), *, back: bool = True,
          batch: Batch | None = None, device: dict[str, Any] | None = None) -> str:
    """Make the readout and send it, unasked: the docs say what it holds, and
    a question here would only stop the readout part way. What the person is
    still to do. `files`: the anonymised flash and its companions, or None if
    not read. `back`: whether the device answered as its Sonoff firmware
    again. In a `batch` it is kept, if not sent, under `device`'s id."""
    data = package(manifest, files, updates)
    if batch is not None:
        return sent_in_batch(ctx, data, device or {}, back)
    path = ctx.dir / NAME
    path.write_bytes(data)
    os.chmod(path, 0o600)
    url = upload_url()
    if url is None:
        output.say(f"    Not sent from a test run: kept in {runs.display_path(path)}.")
        ended = became(ctx)
        ended.outcome, ended.detail = KEPT, "a test run"
        return "" if back else SWITCH_OFF_AND_ON
    return sent(ctx, data, url, path, back)


# How a first stage sent as links ends, by the website's answer for it.
LINKS_ENDED = {"taken": (ACCEPTED, ""), "have": (UNNEEDED, ""), "full": (WEBSITE_FULL, FULL_TODAY)}


def sent_links(ctx: Context, item: dict[str, Any], batch: Batch | None) -> str:
    """A first stage as the website asks for it (Wanted.send): its offer, each
    file's link and SHA-256 (an_offer), sent to /offers in place of the files,
    which the website fetches itself once enough readouts agree on them.
    Nothing is kept to send later. What the person is still to do."""
    try:
        with output.working("Sending the readout..."):
            result, why = send_offers([item])[1][0]
    except (OSError, ValueError) as exc:
        raise Abort(f"the readout could not be sent: {website_problem(exc)}") from exc
    if result not in LINKS_ENDED:   # "refused", why saying why
        raise Abort(f"the website turned the readout down: {why or result or 'no reason given'}")
    ended = became(ctx)
    ended.outcome, ended.detail = LINKS_ENDED[result]
    output.log_only(f"    The readout, as links: {ended.outcome}.")
    if ended.outcome == WEBSITE_FULL and batch is None:
        return f"{FULL_TODAY[0].upper()}{FULL_TODAY[1:]}"
    return ""


def sent(ctx: Context, data: bytes, url: str, path: Path, back: bool) -> str:
    """offer()'s readout sent to `url`; what the person is still to do. Its
    reference is the report's to say (became)."""
    try:
        reference = send(data, url)
    except (OSError, ValueError) as exc:
        keep_to_send(path)
        raise Unsent(website_problem(exc), path) from exc
    path.unlink(missing_ok=True)
    ended = became(ctx)
    ended.outcome, ended.detail = (UNNEEDED, "") if reference == ENOUGH else (ACCEPTED, reference)
    signup.sent_readout(ended.detail)
    output.log_only(f"    The readout: {ended.outcome}" + (f", reference {reference}." if ended.detail else "."))
    return "" if back else SWITCH_OFF_AND_ON


def sent_in_batch(ctx: Context, data: bytes, device: dict[str, Any], back: bool) -> str:
    """A readout made in a batch, sent; what the person is still to do with
    it (what became of it goes in the report: became). Kept, if not sent,
    under the device's id, beside the others."""
    path = ctx.dir / f"readout-{device.get('deviceid') or 'device'}.tar"
    url = upload_url()
    ended = became(ctx)
    todo = []
    if url is None:
        path.write_bytes(data)
        os.chmod(path, 0o600)
        output.say(f"    Not sent from a test run: kept in {runs.display_path(path)}.")
        ended.outcome, ended.detail = KEPT, "a test run"
    else:
        try:
            reference = send(data, url)
        except (OSError, ValueError) as exc:
            path.write_bytes(data)
            os.chmod(path, 0o600)
            keep_to_send(path)   # sent beside the next run: nothing for the person to do
            ended.outcome, ended.detail = KEPT, f"not sent: {website_problem(exc)}"
        else:
            ended.outcome, ended.detail = (UNNEEDED, "") if reference == ENOUGH else (ACCEPTED, reference)
            signup.sent_readout(ended.detail)
            output.log_only(f"    {ended.name}'s readout: {ended.outcome}"
                            + (f", reference {reference}." if ended.detail else "."))
    if not back:
        todo.append(f"{ended.name}: {SWITCH_OFF_AND_ON[0].lower()}{SWITCH_OFF_AND_ON[1:]}")
    return "\n".join(todo)


def proof(data: bytes, now: float | None = None, bits: int | None = None,
          tick: Callable[[], None] | None = None) -> str:
    """The work the Worker asks of each readout, for its body: about 17
    million tries, some tens of seconds on a recent computer, but an older or
    smaller one may take minutes (proof_of_work.solve)."""
    return proof_of_work.solve(proof_of_work.body_subject(data), PROOF_BITS if bits is None else bits, now, tick)


def offered_digests(offers: list[dict[str, Any]], versions: bool = False) -> list[Any]:
    """The SHA-256 eWeLink gives each file it offers (its `digest`), or with
    `versions` (digest, version) pairs: what /wanted is told of the firmware."""
    found = []
    for offer in offers:
        for item in offer.get("binList") or []:
            digest = str(item.get("digest") or "").lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest) and digest not in [d for d, _ in found]:
                found.append((digest, str(offer.get("version") or "")))
    return found if versions else [digest for digest, _ in found][:8]


def light_proof(body: bytes) -> str:
    """The lighter work /wanted, /notify and /offers ask of `body`, made here."""
    return proof(body, bits=LIGHT_PROOF_BITS)


def light_post(path: str, value: dict[str, Any], timeout: float = 15.0,
               waits: tuple[float, ...] = hostsys.SEND_WAITS,
               prove: Callable[[bytes], str] = light_proof) -> dict[str, Any]:
    """POST `value` to the website's `path` beside where readouts go, with the
    lighter work /wanted and /notify ask (`prove` makes it), tried again
    through a blip after each of `waits`; its JSON answer. OSError or
    ValueError when it cannot say."""
    url = upload_url()
    if url is None:
        raise OSError("not from a test run")

    def attempt() -> dict[str, Any]:
        # A random field of its own: two alike questions asked within one
        # second - or this one again, its first try having reached the
        # website before it was cut off - would otherwise carry the same
        # proof, which the website takes once.
        body = json.dumps({**value, "nonce": secrets.token_hex(8)}).encode()
        request = urllib.request.Request(url.rsplit("/", 1)[0] + path, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": f"Autoflash/{__version__}",
            "X-Proof": prove(body)})
        with hostsys.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    return hostsys.again_after_passing(attempt, waits, f"Asking the website ({path})", output.log_only)


def ask_wanted(product: str, model: str, version: str, stage: str, firmware: list[str],
               device: str | None = None) -> Wanted:
    """Whether the website wants this readout: one of a product, firmware model
    and version, at `stage` ("first" or "whole"), that it has none like. Where
    it cannot say - unreachable, or an earlier website - the readout goes
    ahead, and the website decides once it is sent. `device`: its hash, as a
    report's (report_data.device_hash), so the website counts devices asked
    of, not questions."""
    if upload_url() is None:
        return Wanted(True)
    try:
        answer = light_post("/wanted", {"format": FORMAT, "model": product or "-", "stock_model": model or "-",
                                        "stock_version": version or "-", "stage": stage, "firmware": firmware,
                                        "device_hash": device})
    except (OSError, ValueError) as exc:   # an HTTPError is an OSError
        output.log_only(f"    Whether a readout is wanted: not answered ({exc}); it goes ahead.")
        return Wanted(True)
    held = frozenset(str(sha) for sha in answer.get("held") or [] if str(sha) in firmware)
    # An earlier website gives no `send`: it takes files.
    return Wanted(answer.get("wanted") is not False, str(answer.get("why") or ""), held,
                  "links" if answer.get("send") == "links" else "files")


def an_offer(product: str, model: str, version: str, offers: list[dict[str, Any]],
             device: str | None) -> dict[str, Any]:
    """One device's offer as /offers takes it: the device as /wanted is asked
    of it, `device` its hash (report_data.device_hash) or None, and each file
    eWeLink offers it by its link and SHA-256 (offered_firmware.links) - none
    where eWeLink offers nothing, which the website only counts."""
    return {"model": product or "-", "stock_model": model or "-", "stock_version": version or "-", "device": device,
            "files": offered_firmware.links(offers)}


def offers_request(offers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"format": FORMAT, "tool_version": __version__, "cloud": CLOUD, "offers": offers}


def in_requests(offers: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """`offers` in as few requests as the website takes: at most
    OFFERS_PER_REQUEST each, and OFFERS_MAX_BYTES with room for light_post's
    nonce."""
    requests: list[list[dict[str, Any]]] = []
    for item in offers:
        last = requests[-1] if requests else None
        if last is None or len(last) >= OFFERS_PER_REQUEST \
                or len(json.dumps(offers_request([*last, item])).encode()) > OFFERS_MAX_BYTES - 64:
            requests.append([item])
        else:
            last.append(item)
    return requests


def website_sends(prove: Callable[[bytes], str] = light_proof) -> str:
    """How the website takes offers now, asked with none (an empty /offers,
    which it counts nowhere): "links", or "files" - an earlier website, or one
    that cannot say, too. OSError or ValueError when it cannot be asked."""
    return "links" if light_post(OFFERS, offers_request([]), prove=prove).get("send") == "links" else "files"


def send_offers(offers: list[dict[str, Any]],
                prove: Callable[[bytes], str] = light_proof) -> tuple[str, list[tuple[str, str]]]:
    """Send offers (an_offer) to /offers, as many a request as it takes
    (in_requests), each with the light work /wanted asks: how the website
    takes them ("links" only where every answer said so; with "files" it kept
    none), and its answer for each, in order, as (result, why) - "taken",
    "have" (held already, or sent before), "full" (none more today) or
    "refused" (why says why). OSError or ValueError when it cannot say."""
    results: list[tuple[str, str]] = []
    sends = "links"
    for these in in_requests(offers):
        answer = light_post(OFFERS, offers_request(these), prove=prove)
        if answer.get("send") != "links":
            sends = "files"
        each = answer.get("offers")
        if not isinstance(each, list) or len(each) != len(these) or not all(isinstance(item, dict) for item in each):
            raise ValueError("the website did not answer for each offer")
        results += [(str(item.get("result") or ""), str(item.get("why") or "")) for item in each]
    return sends, results


def notify(email: str, devices: Iterable[tuple[str, str]], readouts: Iterable[str] = (), *,
           update: bool = False) -> bool:
    """Ask the website to email `email` as more devices are supported, and
    of these devices' changes ((product, firmware model) each): the owner's
    eWeLink account's, none where it was never seen; with `readouts`, the
    references of those they sent. `update`: a later sitting's, added only
    to an address confirmed already, never emailed if it is not. Whether it
    was taken; never raises."""
    named = [{"model": product, "stock_model": model or "-"} for product, model in dict.fromkeys(devices) if product]
    if upload_url() is None:
        return False
    try:
        with output.working("Updating your email sign-up..." if update else "Sending your email sign-up..."):
            light_post("/notify", {"format": FORMAT, "email": email, "devices": named[:NOTIFY_MOST],
                                   "readouts": list(dict.fromkeys(readouts))[:NOTIFY_MOST], "update": update},
                       timeout=NOTIFY_TIMEOUT, waits=NOTIFY_WAITS)
    except (OSError, ValueError) as exc:
        output.log_only(f"    Asking to be emailed: not taken ({exc}).")
        return False
    return True


def send(data: bytes, url: str = UPLOAD_URL, timeout: float = 120.0) -> str:
    """POST a readout; the reference the store gave it. One turned away as too
    many at once - the website takes two a minute from one address, which a
    readout of every device reaches - is sent again every 15 seconds, for up
    to a minute and a half, with the same work, good for 15 minutes. Those
    kept from before wait while it goes (send_kept)."""
    _own_sending.set()
    try:
        started = time.monotonic()
        # The puzzle (proof) keeps floods of readouts away from the website.
        with output.working("Preparing to send - a few seconds to a minute or two..."):
            work = proof(data, tick=lambda: output.progress(f"working on it... {time.monotonic() - started:.0f}s"))
        output.progress_clear()
        for wait in (*RATE_WAITS, None):
            try:
                return post(data, url, work, timeout)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or wait is None:
                    raise OSError(f"the website answered {exc.code}") from exc
                with output.working("The website takes two readouts a minute: sending it again shortly..."):
                    time.sleep(wait)
        raise AssertionError("unreachable: the last try returns or raises")
    finally:
        _own_sending.clear()


def busy_for_now(exc: Exception) -> bool:
    """A passing trouble (hostsys.passing) but too many at once (429), which
    whoever sends the readout waits out its own way: send() for a minute and
    a half, send_kept() until a later run."""
    return hostsys.passing(exc) and getattr(exc, "code", None) != 429


def post(data: bytes, url: str, work: str, timeout: float = 120.0) -> str:
    """POST a readout with its work, tried again through a blip on the way
    (hostsys.SEND_WAITS): the reference the store gave it, or ENOUGH. The
    website turning it away is an HTTPError, as urllib raises it; the same
    work goes each time, as the website takes it again within 15 minutes."""
    global _posted
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-tar", "User-Agent": f"Autoflash/{__version__}",
        "X-Proof": work})

    def attempt() -> dict[str, Any]:
        with hostsys.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    try:
        answer = hostsys.again_after_passing(attempt, hostsys.SEND_WAITS, "Sending the readout", output.log_only,
                                             passes=busy_for_now)
    finally:
        _posted = time.monotonic()
    if answer.get("enough"):
        return ENOUGH
    reference = str(answer.get("id") or "")
    if not reference:
        raise ValueError("the website gave no reference")
    return reference


# Readouts that could not be sent, one small file each naming where it is kept
# ({"path": ...}): sent beside a later run, as it starts (send_kept). Only
# these: one kept by a test run, which sends nothing, is never sent later.
TO_SEND = records.CAPTURES / "readouts-to-send"
# One being sent is claimed by its name's suffix, so a second run beside it
# leaves it be; one claimed longer ago than this was left by a run that ended
# part way, and is sent again.
SENDING = ".sending"
CLAIM_LASTS = 3600.0
# The website takes two readouts a minute from one address, and twenty a day:
# those kept from before go one every SEND_KEPT_PACE seconds, never while one
# of the run's own is being sent (send), nor sooner than that after it; the
# daily limit, or any other turning away for now, leaves the rest for a later
# run.
SEND_KEPT_PACE = 30.0
_posted = 0.0                      # when this process last sent a readout (time.monotonic)
_own_sending = threading.Event()   # set while one of the run's own is being sent
# What the website answers a readout it may take later: too many at once
# (429), its proof of work no longer good - made, then waited on - (403), or
# no more today, or not yet (5xx). Anything else it will never take.
FOR_NOW = (403, 429)


def to_send(path: Path) -> Path:
    """The entry that names the readout kept at `path` among TO_SEND."""
    return TO_SEND / f"{hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]}.json"


def keep_to_send(path: Path) -> None:
    """A readout kept at `path` because it could not be sent: sent beside the
    next run (send_kept)."""
    TO_SEND.mkdir(parents=True, exist_ok=True)
    to_send(path).write_text(json.dumps({"path": str(path.resolve())}), encoding="utf-8")


def forget_to_send(path: Path) -> None:
    """The readout kept at `path`, sent: no later run sends it again."""
    for entry in (to_send(path), to_send(path).with_name(to_send(path).name + SENDING)):
        entry.unlink(missing_ok=True)


def kept_to_send(now: float | None = None) -> list[Path]:
    """The readouts to send, oldest first: each not claimed, or claimed by a
    run that ended part way."""
    if not TO_SEND.is_dir():
        return []
    now = time.time() if now is None else now
    found = []
    for path in [*TO_SEND.glob("*.json"), *TO_SEND.glob(f"*.json{SENDING}")]:
        try:
            made = path.stat().st_mtime
        except FileNotFoundError:   # claimed by another run as this one looked
            continue
        if not path.name.endswith(SENDING) or now - made > CLAIM_LASTS:
            found.append((made, path))
    return [path for _, path in sorted(found)]


def send_kept_beside() -> None:
    """Readouts kept unsent before, sent beside this run as it starts, never in
    its way. Not from the emulator: a readout one scenario kept would be sent
    in the next, which the suites count as one sent unwanted."""
    url = upload_url()
    if url is None or hostsys.emulated() or not kept_to_send():
        return
    report_upload.in_background(lambda: send_kept(url))


def wait_turn() -> None:
    """Until a readout kept from before may go: none of the run's own being
    sent, and SEND_KEPT_PACE since the last readout this process sent."""
    while _own_sending.is_set() or time.monotonic() < _posted + SEND_KEPT_PACE:
        time.sleep(1.0)


def send_kept(url: str) -> None:
    """Every kept readout sent to `url`, oldest first, paced (wait_turn), each
    forgotten, and its file deleted, once the website has it; turned away for
    now, it and the rest are left for a later run. One the website will
    never take - its format too old, say - is forgotten, its file left where
    it is, for readout --send. Each is claimed only as it is sent: one waited
    on as the tool exits is left for the next run as it was."""
    for entry in kept_to_send():
        waiting = entry.with_name(entry.name.removesuffix(SENDING))
        claimed = waiting.with_name(waiting.name + SENDING)
        try:
            path = Path(json.loads(entry.read_text(encoding="utf-8"))["path"])
        except FileNotFoundError:   # another run sent it meanwhile
            continue
        except (OSError, ValueError, KeyError) as exc:   # not one this tool wrote
            entry.unlink(missing_ok=True)
            output.log_only(f"Kept readouts: {waiting.name} forgotten ({type(exc).__name__}: {exc}).")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:   # its readout gone: nothing to send
            entry.unlink(missing_ok=True)
            output.log_only(f"Kept readouts: {path} forgotten ({type(exc).__name__}: {exc}).")
            continue
        wait_turn()
        work = report_upload.proof_of(report_upload.work_beside(proof_of_work.body_subject(data), PROOF_BITS))
        wait_turn()   # one of the run's own may have begun meanwhile
        try:
            os.replace(entry, claimed)
        except OSError:   # another run took it first
            continue
        os.utime(claimed)
        try:
            if not work:
                raise TimeoutError("its work was not made in time")
            reference = post(data, url, work)
        except urllib.error.HTTPError as exc:
            if exc.code in FOR_NOW or exc.code >= 500:   # a later run sends it
                os.replace(claimed, waiting)
                output.log_only(f"Kept readouts: {path} not sent this time: the website answered {exc.code}.")
                return
            claimed.unlink()
            output.log_only(f"Kept readouts: {path} turned away for good ({exc.code}): left for readout --send.")
            continue
        except (OSError, ValueError) as exc:
            os.replace(claimed, waiting)
            output.log_only(f"Kept readouts: {path} not sent this time: {type(exc).__name__}: {exc}")
            return
        path.unlink(missing_ok=True)
        claimed.unlink()
        signup.sent_readout("" if reference == ENOUGH else reference)
        output.log_only(f"Kept readouts: {path} sent" + ("" if reference == ENOUGH else f", reference {reference}") + ".")


# The question when the account holds several devices; its first option is
# every device. The attended suite answers it by this name.
CHOOSE = "Which device should be read out"


def choose(ctx: Context, devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The device to read out, or None for every device in the account
    (run_all), offered first where there are two or more: those the tool
    converts too, as one may run a firmware version the website has no
    readout of - new firmware is what a conversion meets unready."""
    if len(devices) == 1:
        return devices[0]
    every = [f"Every device in the account ({len(devices)})"]
    output.transient_begin()
    index = prompts.ask_choice(CHOOSE,
                               [*every, *(describe(device) for device in devices)])
    output.transient_end()
    return None if index < len(every) else devices[index - len(every)]


def read_out_chosen(ctx: Context, devices: list[dict[str, Any]]) -> str:
    """The readout the owner chooses, of one device or of every one in the
    account; what the run ends by saying."""
    device = choose(ctx, devices)
    return run_all(ctx, devices) if device is None else run(ctx, device)


def run_all(ctx: Context, devices: list[dict[str, Any]]) -> str:
    """Read out each of `devices` in turn, sending each readout as it is made,
    on its own, so the website can turn down a device it has enough of. What
    the run ends by saying: what became of each.

    Each gets the readout it would alone: most only the first stage, which
    sends nothing to the device, done first so they are in before any full
    readout asks to go ahead; a full one still asks, s there passes the device
    by, and q quits, as everywhere. One that fails part way stops the rest, as
    it may need seeing to."""
    ordered = sorted(devices, key=reads_out)
    todo = []
    for number, device in enumerate(ordered, 1):
        output.tell(f"Reading out {describe(device)} ({number} of {len(ordered)})...")
        for key in (*DEVICE_KEYS, readout_espressif.BRIDGE_HOST):
            ctx.state.set_input(key, None)
        left = ordered[number:]
        try:
            said = run(ctx, device, batch=Batch())
        except prompts.QuitRun:
            became_of(ctx, device).detail = "stopped as asked"
            readouts(ctx).extend(Became.of(other, detail="stopped as asked") for other in left)
            break
        except WebsiteFull as exc:
            became_of(ctx, device).outcome = WEBSITE_FULL
            readouts(ctx).extend(Became.of(other, outcome=WEBSITE_FULL, detail=str(exc)) for other in left)
            break
        except NothingWritten as exc:
            this = became_of(ctx, device)
            this.detail = this.detail or str(exc)
        except Abort as exc:
            if reads_out(device):
                # One that fails part way stops the rest: it may need seeing to.
                raise Abort("\n".join([f"{describe(device)}: {exc}", *todo,
                                        *([f"Not read out yet: {', '.join(map(describe, left))}."]
                                          if left else [])])) from exc
            became_of(ctx, device).detail = str(exc)
        else:
            if said:
                todo.append(said)
    return "\n".join(todo)


def from_run(ctx: Context, devices: list[dict[str, Any]]) -> None:
    """A1's way out to a readout: run it, then end the run (errors.Finished).
    The run's stage 2, which found the account's devices, ends first, and the
    readout's stages carry on from 3: its first, the account's, is done."""
    output.stage_end("complete")
    lay_out_stages(first=2)
    ctx.readouts = []
    try:
        said = read_out_chosen(ctx, devices)
    except (Abort, OSError, ValueError) as exc:
        if not readouts(ctx):
            raise   # none begun: the run's own failure, as any stage's
        # Ended as a readout on its own ends (cli.read_out), with its report:
        # not the run's too, nor the run's way to carry on, as nothing was
        # converted.
        tell_failed(ctx, exc)
        ctx.reported = True
        raise Finished("", code=1) from exc
    end(ctx, said)
    # Said already, with the report: the run has nothing more to say.
    raise Finished("")


def send_saved(path: Path) -> str:
    """readout --send: a readout kept from before. Its reference goes with
    the email sign-up, as one sent as it was made does."""
    url = upload_url()
    if url is None:
        raise Abort("readouts are not sent from a test run")
    try:
        reference = send(path.read_bytes(), url)
    except (OSError, ValueError) as exc:
        raise Abort(f"the readout could not be sent: {website_problem(exc)}") from exc
    forget_to_send(path)
    signup.sent_readout("" if reference == ENOUGH else reference)
    return reference
