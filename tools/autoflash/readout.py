"""A readout: a copy of a device's whole memory, to help add a device this tool
does not convert yet, sent with its owner's consent to firmwareliberator.com.

Nothing is converted. A readout comes in two stages. The first is only ever
read: what the device and eWeLink say it is, and the firmware eWeLink offers
it, fetched as Sonoff's own updater would fetch it; nothing is sent to the
device. A device the tool has not seen gets that alone. The second reads its
whole memory, and only on a listed kind whose status says it is proven not to
leave the device unable to start (kinds.Status.reads_out), under the firmware
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

import io
import json
import lzma
import os
import re
import secrets
import tarfile
import time
import urllib.error
from dataclasses import dataclass
import urllib.request
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from tools.autoflash import __version__, account, detect, hostsys, kinds, output, records, prompts, runs
from tools.autoflash import failures, proof_of_work, readout_beken, readout_espressif, report, report_upload
from tools.autoflash.errors import Abort, Finished, NothingWritten
# How a device's readout ended (Became.outcome): its report's result, sent as
# each readout ends (report_upload.send_readout), and on the screen's at the end.
from tools.autoflash.report_data import (
    ACCEPTED, CANCELLED, CONVERTED_SINCE, CRASHED, FAILED, KEPT, NOT_NEEDED as UNNEEDED, NOT_READ, SKIPPED,
    WEBSITE_FULL)
from tools.ewelink import discover_ewelink, firmware_name, offered_firmware
from tools.ewelink.get_device_key import is_email

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# Where readouts go: a Worker of their own, apart from the website's, with a
# private store behind it: the project's API (website/api/index.js).
UPLOAD_URL = "https://api.firmwareliberator.com/v1/readout"
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
# What the run's record keeps, so a readout is never offered back as a conversion to finish.
STATE_KEY = "readout"
# What the run's record keeps of the one device being read out, with
# readout_espressif.BRIDGE_HOST: cleared before the next, when every device is
# read out in one run.
DEVICE_KEYS = ("device_ip", "device_mac", "stock_version", "product_model", STATE_KEY, "readout_sent")


# The stage lines a readout shows on screen, in order, as a conversion shows
# its route's steps - not the readout's two stages above, which are what it
# reads. The first is the account's, once; the rest are each device's,
# numbered again for each device of several (run_all). A readout a run turns
# into (from_run) has done the first as that run's stage 2, and numbers on
# from there.
STAGES = (
    ("account", "Querying eWeLink for devices"),
    ("locate", "Locating the device on your network"),
    ("probe", "Probing the device"),
    ("fetch", "Fetching the eWeLink firmware"),
    ("read", "Reading the device's memory"),
    ("restore", "Restoring Sonoff's firmware"),
    ("save", "Saving the readout"),
)
STAGE_KEYS = [key for key, _ in STAGES]
# The number this readout's first stage, "account", takes (lay_out_stages).
_first_stage = 1


def lay_out_stages(first: int = 1) -> None:
    """Number the readout's stages from `first` - 2 in a run's readout, whose
    stage 2 found the account's devices (from_run) - its names' column as
    wide as the longest, or the run's, where that is wider."""
    global _first_stage
    _first_stage = first
    output.set_stage_width(max(output.stage_width(), *(len(name) for _, name in STAGES)),
                           max(output.most_stages(), first + len(STAGES) - 1))


def begin(key: str) -> None:
    """The readout's stage `key` under way, the one before it ended first."""
    output.stage_end("complete")
    output.stage_begin(_first_stage + STAGE_KEYS.index(key), dict(STAGES)[key])


def skip(key: str) -> None:
    """The readout's stage `key` passed over: nothing for it to do."""
    begin(key)
    output.stage_end("skipped")


def restoring() -> None:
    """Reading done, the device's Sonoff firmware next: each chip's readout
    says when (readout_espressif, readout_beken)."""
    begin("restore")


class WebsiteFull(NothingWritten):
    """The website has taken all the readouts it can, today or until its store
    has room: a readout of every device ends there, the rest not read."""


@dataclass(frozen=True)
class Wanted:
    """The website's answer to /wanted: whether a readout would add anything,
    why not ("enough", "held", "full" for the day or "store-full"), and which
    offered files it holds."""
    wanted: bool
    why: str = ""
    held: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Batch:
    """A readout of one device among several in one run (run_all): the
    owner's contact details, asked once for them all."""
    contact: dict[str, str] | None = None


# What a device not back on its Sonoff firmware still needs, said as the run ends.
SWITCH_OFF_AND_ON = "Switch the device off and on once more: it then starts its Sonoff firmware again."

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

    @classmethod
    def of(cls, device: dict[str, Any], **ended: Any) -> Became:
        """A readout of the account's `device`, as far as its record says."""
        return cls(describe(device), product=str(device.get("productModel") or ""),
                   model=str((device.get("extra") or {}).get("model") or ""), **ended)


# Each outcome as the report on the screen says it (report_fields), in this order.
SAID = {ACCEPTED: "Accepted", UNNEEDED: "Not needed", KEPT: "Not sent", WEBSITE_FULL: "Website full",
        CONVERTED_SINCE: "Converted since", SKIPPED: "Skipped", NOT_READ: "Not read out", CANCELLED: "Stopped",
        FAILED: "Failed", CRASHED: "Crashed"}
# The report's right-hand column (report.report_screen).
REPORT_RIGHT = ("Devices", "Accepted", "Not needed", "Not sent")


def named(item: Became) -> str:
    """A device as the report on the screen names it: its model and firmware
    version alone, kept together on a row (no-break spaces)."""
    return "\u00a0".join(part for part in (item.product or item.model or "unknown", item.version) if part)


def report_fields(done: list[Became]) -> list[tuple[str, str]]:
    """The readout's report for the screen, as (label, value): how it ended,
    the tool and this computer, and how many devices were read out, how many
    the website accepted, how many it did not need, and how many were kept
    unsent, where any were."""
    fields = [
        ("Result", SAID[done[0].outcome] if len(done) == 1 else
         "read out" if any(item.outcome in (ACCEPTED, KEPT) for item in done) else "nothing read out"),
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
    one by its model and firmware version; several listed under how each
    ended, each model and version once however many were read out. What
    else became of each is in the log."""
    if len(done) == 1:
        return [("Device", named(done[0]))]
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
    output.tell("")
    if any(item.outcome in (ACCEPTED, KEPT) for item in done):
        output.show("Readout complete!", output.GREEN)
    if said:
        for line in said.splitlines():
            output.tell(line)
    output.tell("")
    for line in report.report_screen(report_fields(done), right=REPORT_RIGHT, below=report_devices(done)):
        output.tell(line)
    if failed:
        report.ask_for_an_issue()
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
    """The account's device at `address` (--device-ip): the one answering eWeLink's
    discovery there, else the one whose hardware address is there (a Beken device
    does no LAN Control, so it answers no discovery - it is found by its MAC, as
    the conversion finds it), else the one device the account holds."""
    try:
        here = answering_ids(discover_ewelink.discover(6.0, None, address))
    except OSError:
        here = {}
    match = [device for device in devices if str(device.get("deviceid")) in here]
    if not match:
        mac = hostsys.mac_of(address)
        if mac:
            at = hostsys.normalise_mac(mac)
            match = [device for device in devices
                     if (device.get("params") or {}).get("staMac")
                     and hostsys.normalise_mac(str((device["params"]["staMac"]))) == at]
    if not match and len(devices) == 1:
        match = list(devices)  # one device in the account: it is the one at this address
    if not match:
        raise Abort(f"could not tell which device in the eWeLink account is at {address}: "
                    "it answered no discovery and its hardware address was not found there")
    return match[0]


def lan_free(device: dict[str, Any]) -> bool:
    """Whether the account's `device` does no LAN Control (a Beken device), so
    answers no local discovery. The account record's model says the chip
    family before anything is read."""
    model = str((device.get("extra") or {}).get("model") or device.get("productModel") or "")
    stock_chip = firmware_name.STOCK_MODEL.match(model)
    known = kinds.for_account(str(device.get("productModel") or ""), model)
    return (stock_chip is not None and stock_chip.group("chip").startswith("BK")) or (
        known is not None and known.family == "beken")


def record_mac(device: dict[str, Any]) -> str | None:
    """The hardware address eWeLink's record of `device` gives it, if any."""
    mac = hostsys.normalise_mac(str((device.get("params") or {}).get("staMac") or ""))
    return mac if records.MAC_RE.match(mac) else None


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
    """The device's address on this network: the one answering eWeLink's
    discovery with its id, else typed by the person. A device that does no
    LAN Control is looked for by its hardware address instead."""
    if lan_free(device):
        return locate_by_mac(device)
    wanted = str(device.get("deviceid") or "")
    found = answering_at(device)
    if found:
        return found
    # Not answering as Sonoff's firmware - on the program a readout stopped
    # part way left on it, say: where its hardware address is.
    mac = record_mac(device)
    networks = sorted(hostsys.lan_networks()) if mac else []
    found = hostsys.find_by_mac(mac, networks) if networks else None
    if found:
        return found
    output.transient_begin()
    address = prompts.ask_ipv4(
        "The device did not answer here. Its address",
        help_lines=["Check LAN Control is on for it in the eWeLink app. Its address is in your",
                    "router's device list, or the app's device information."],
        check=account.device_at({wanted}))
    output.transient_end()
    return address


def locate_by_mac(device: dict[str, Any]) -> str:
    """A LAN-free device's address: the one with the hardware address its
    eWeLink record gives, found on this computer's networks, else typed by
    the person and checked against it."""
    mac = record_mac(device)
    if mac:
        output.say("Looking for it on your network by its hardware address...")
        networks = sorted(hostsys.lan_networks())
        found = hostsys.find_by_mac(mac, networks) if networks else None
        if found:
            return found

    def check(address: str) -> list[str]:
        seen = hostsys.mac_of(address)
        if mac and seen and seen != mac:
            return [f"{address} is another device: its hardware address is not the one eWeLink has for yours."]
        return []
    output.transient_begin()
    address = prompts.ask_ipv4(
        "The device was not found on this network. Its address",
        help_lines=["It answers no local discovery, so it was looked for by the hardware address",
                    "eWeLink has for it. Its address is in your router's device list, or the",
                    "eWeLink app's device information."],
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
    readouts(ctx).append(this)
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
        this.outcome, this.error = FAILED, failures.classify(str(exc))
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
    elif record_mac(device) is None and not lan_free(device):
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
    # A device that does no LAN Control (a Beken device) is asked of eWeLink by
    # its account id.
    # So is one a readout stopped part way left on its reading program (the
    # bridge), which answers no discovery either.
    begin("probe")
    on_bridge = ip is not None and not lan_free(device) and readout_espressif.installer.bridge_answers(ip)
    try:
        if first_only:
            asked = ask_first_stage(ctx, device)
        elif lan_free(device) or on_bridge:
            asked = account.ask_account(ctx, str(device.get("deviceid") or ""))
        else:
            asked = account.ask_ewelink(ctx, ip)
    except account.NotOnSonoff as exc:
        # One the account still lists, converted since: nothing gone wrong,
        # and none of Sonoff's firmware left on it to read.
        ctx.readout.outcome, ctx.readout.detail = CONVERTED_SINCE, detect.NAMES[exc.firmware]
        return ""
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
    whole = whole_readout(listed, learned)
    prompts.section("What the device reported", [
        f"Model            {asked.model or 'not given'}",
        f"Product          {asked.product or 'not given'}",
        f"Firmware now     {asked.version}",
        f"Firmware name    {learned.name if learned else 'not learned'}",
    ])
    begin("fetch")
    # Before the firmware eWeLink offers it is fetched, anything is sent to it,
    # or the readout's own work is done: whether it would add anything at all,
    # and which of the files eWeLink offers it are held already.
    offers = asked.latest.get("offers") or []
    with output.working("Asking the website whether a readout of it is wanted..."):
        answer = ask_wanted(asked.product, asked.model, asked.version, "whole" if whole else "first",
                            offered_digests(offers))
    if not answer.wanted:
        full = {"full": FULL_TODAY, "store-full": STORE_FULL, "limit": LIMIT_TODAY}.get(answer.why)
        ctx.readout.outcome, ctx.readout.detail = (WEBSITE_FULL, full) if full else (UNNEEDED, "")
        if full and batch is not None:
            raise WebsiteFull(full)
        if batch is not None:
            return ""
        return single_not_wanted(full or NOT_NEEDED, asked)
    manifest = {
        "format": FORMAT, "tool": __version__, "product": asked.product, "stock_model": asked.model,
        "version": asked.version, "brand": str(device.get("brandName") or ""),
        "uiid": (device.get("extra") or {}).get("uiid"),
        "firmware_name": learned.name if learned else "", "name_from": learned.source if learned else "",
        "chip": learned.chip if learned else "",
    }
    # The firmware eWeLink offers it as well: often another version than the
    # one it runs, so two to test against. Nothing is sent to the device for it.
    # Those the website holds already are left out - but for a Beken device read
    # in full, which needs Sonoff's own to put back.
    beken_whole = bool(learned and whole and learned.chip.startswith("BK"))
    with output.working("Downloading the firmware eWeLink offers it..."):
        updates, problems = offered_firmware.fetch(offers, asked.deviceid,
                                                   skip=frozenset() if beken_whole else answer.held)
    for problem in problems:
        output.log_only(f"    Offered firmware: {problem}")
    sent = updates if not beken_whole else [item for item in updates if item.sha256 not in answer.held]
    # Each file sent named by its place in the readout, which the website
    # checks it against; each held already, fetched for a Beken device or not,
    # by its SHA-256 alone.
    manifest["offered"] = [{"file": f"offered/{item.name}", "version": item.version, "size": len(item.data),
                            "sha256": item.sha256} for item in sent]
    manifest["offered"] += [{"file": None, "version": item.version, "sha256": item.sha256, "held": True}
                            for item in updates if item not in sent]
    manifest["offered"] += [{"file": None, "version": version, "sha256": digest, "held": True}
                            for digest, version in offered_digests(offers, versions=True)
                            if digest in answer.held and all(item.sha256 != digest for item in updates)]
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
        if learned is None:
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
        return offer(ctx, manifest, None, sent, batch=batch, device=device)
    ctx.readout.whole, ctx.readout.back = True, back
    begin("save")
    return offer(ctx, manifest, files, sent, back=back, batch=batch, device=device)


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
        return account.Asked({"packets": []}, str(params.get("fwVersion") or ""),
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
            updates: list[offered_firmware.Offered] | tuple = (), contact: dict[str, str] | None = None) -> bytes:
    """The readout as sent: a tar of manifest.json first; the firmware eWeLink
    offers the device, under offered/, as it is; and `files` - the anonymised
    flash and its companions (flash.bin, efuse.bin, info.txt, report.json),
    already anonymised by the chip family that read them. Each of those .bin
    is xz-compressed (flash.bin.xz), so the tar stays readable without
    unpacking it."""
    if contact:
        manifest = {**manifest, "contact": contact}
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


# What the owner is told their address is kept for, wherever it is asked.
CONTACT_HELP = ["You will be emailed when your device's support changes, and may be asked to",
                "try it. You are first sent an email to confirm: nothing is kept unless you",
                "do. Your address is kept for that alone, and every such email has a link",
                "to stop them."]
# What the owner is told once an address is given.
CONFIRM_SENT = "Check your inbox: follow the link in the email just sent to confirm your address."
# Devices one sign-up may name, as the website takes them (MAX_DEVICES).
NOTIFY_MOST = 20


def ask_contact(when_supported: bool = False) -> dict[str, str] | None:
    """How the owner may be reached about the device, if they choose to say:
    {"email": ...}, or None. Quit stops the run unsent. `when_supported`: no
    readout goes, only the wish to be told when the device's status changes."""
    if prompts.UNATTENDED:
        prompts.answered_unattended("Contact details", "Submit without contact information")
        return None
    output.transient_begin()
    if when_supported:
        choice = prompts.ask_choice("Be told when this device is supported", [
            "Add an email address",
            "No, thank you",
        ], help_lines=CONTACT_HELP)
    else:
        choice = prompts.ask_choice("Add a way to reach you", [
            "Add an email address",
            "Submit without contact information",
        ], help_lines=CONTACT_HELP)
    contact = None
    if choice == 0:
        contact = {"email": prompts.ask_text("Your email address", private="email", validate=lambda text: (
            is_email(text), "That does not look like an email address."))}
    output.transient_end()
    if contact:
        contact = {key: value.strip() for key, value in contact.items()}
        if not when_supported:
            output.say("    Your contact details go with it.")
    return contact


def offer(ctx: Context, manifest: dict[str, Any], files: dict[str, bytes] | None = None,
          updates: list[offered_firmware.Offered] | tuple = (), *, back: bool = True,
          batch: Batch | None = None, device: dict[str, Any] | None = None) -> str:
    """Make the readout, ask, and send it; what the person is still to do.
    `files`: the anonymised flash and its companions, or None if not read.
    `back`: whether the device answered as its Sonoff firmware again.
    In a `batch` it is sent unasked, and kept under `device`'s id if not."""
    if batch is not None:
        return sent_in_batch(ctx, package(manifest, files, updates, batch.contact), device or {}, back)
    ended = became(ctx)
    path = ctx.dir / NAME

    def keep(contact: dict[str, str] | None = None) -> bytes:
        data = package(manifest, files, updates, contact)
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return data

    url = upload_url()
    contents = ["- the device's firmware readout, anonymised;"] if files or updates else []
    # The offer and its question go together once answered: what the run then
    # says - sent, or not - is the answer.  Left if sending fails, to say what.
    with output.until_done():
        prompts.section("Send this firmware readout, to help add support for this device?", [
            "What is sent:",
            *contents,
            "- what the device and eWeLink said it is, and this tool's version.",
        ])
        if url is None:
            keep()
            output.say(f"    Not sent from a test run: kept in {path}.")
            ended.outcome, ended.detail = KEPT, "a test run"
            return "" if back else SWITCH_OFF_AND_ON
        try:
            contact = ask_contact()
        except prompts.QuitRun:
            keep()
            ended.outcome, ended.detail = KEPT, "as asked"
            return (f"The readout is kept in {path}, if you would like to send it later."
                    + ("" if back else f" {SWITCH_OFF_AND_ON}"))
        said = sent(ctx, keep(contact), url, path, back)
        if contact and notify(contact["email"], [(manifest.get("product", ""), manifest.get("stock_model", ""))]):
            said = f"{said} {CONFIRM_SENT}".strip()
        return said


def sent(ctx: Context, data: bytes, url: str, path: Path, back: bool) -> str:
    """offer()'s readout sent to `url`; what the person is still to do. Its
    reference is the report's to say (became)."""
    try:
        reference = send(data, url)
    except (OSError, ValueError) as exc:
        raise Abort(f"the readout could not be sent ({exc}). It is kept in {path}: send it later "
                    "with this tool's readout --send") from exc
    path.unlink(missing_ok=True)
    ended = became(ctx)
    ended.outcome, ended.detail = (UNNEEDED, "") if reference == ENOUGH else (ACCEPTED, reference)
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
        output.say(f"    Not sent from a test run: kept in {path}.")
        ended.outcome, ended.detail = KEPT, "a test run"
    else:
        try:
            reference = send(data, url)
        except (OSError, ValueError) as exc:
            path.write_bytes(data)
            os.chmod(path, 0o600)
            ended.outcome, ended.detail = KEPT, f"not sent: {exc}"
            todo.append(f"{ended.name}: its readout is kept in {path}: send it later with this tool's "
                        "readout --send.")
        else:
            ended.outcome, ended.detail = (UNNEEDED, "") if reference == ENOUGH else (ACCEPTED, reference)
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


def light_post(path: str, value: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    """POST `value` to the website's `path` beside where readouts go, with the
    lighter work /wanted and /notify ask; its JSON answer. OSError or ValueError
    when it cannot say."""
    url = upload_url()
    if url is None:
        raise OSError("not from a test run")
    # A random field of its own: two alike questions asked within one second
    # would otherwise carry the same proof, which the website takes once.
    body = json.dumps({**value, "nonce": secrets.token_hex(8)}).encode()
    request = urllib.request.Request(url.rsplit("/", 1)[0] + path, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"Autoflash/{__version__}",
        "X-Proof": proof(body, bits=LIGHT_PROOF_BITS)})
    with hostsys.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_wanted(product: str, model: str, version: str, stage: str, firmware: list[str]) -> Wanted:
    """Whether the website wants this readout: one of a product, firmware model
    and version, at `stage` ("first" or "whole"), that it has none like. Where
    it cannot say - unreachable, or an earlier website - the readout goes
    ahead, and the website decides once it is sent."""
    if upload_url() is None or not product:
        return Wanted(True)
    try:
        answer = light_post("/wanted", {"format": FORMAT, "product": product, "model": model or "-",
                                        "version": version or "-", "stage": stage, "firmware": firmware})
    except (OSError, ValueError) as exc:   # an HTTPError is an OSError
        output.log_only(f"    Whether a readout is wanted: not answered ({exc}); it goes ahead.")
        return Wanted(True)
    held = frozenset(str(sha) for sha in answer.get("held") or [] if str(sha) in firmware)
    return Wanted(answer.get("wanted") is not False, str(answer.get("why") or ""), held)


def notify(email: str, devices: list[tuple[str, str]]) -> bool:
    """Ask the website to tell `email` when any of these devices' status
    changes ((product, firmware model) each), wanted as readouts or not.
    Whether it was taken; never raises."""
    named = [{"product": product, "model": model or "-"} for product, model in dict.fromkeys(devices) if product]
    if not named or upload_url() is None:
        return False
    try:
        light_post("/notify", {"format": FORMAT, "email": email, "devices": named[:NOTIFY_MOST]})
    except (OSError, ValueError) as exc:
        output.log_only(f"    Asking to be told of a change: not taken ({exc}).")
        return False
    return True


def single_not_wanted(said: str, asked: account.Asked) -> str:
    """A lone readout the website does not want: offered, still, to be told
    when the device's status changes. What the person is still to do."""
    output.tell(f"    {said[0].upper()}{said[1:]}")
    if upload_url() is None or prompts.UNATTENDED:
        return ""
    try:
        contact = ask_contact(when_supported=True)
    except prompts.QuitRun:
        contact = None
    if contact and notify(contact["email"], [(asked.product, asked.model)]):
        return CONFIRM_SENT
    return ""


def send(data: bytes, url: str = UPLOAD_URL, timeout: float = 120.0) -> str:
    """POST a readout; the reference the store gave it. One turned away as too
    many at once - the website takes two a minute from one address, which a
    readout of every device reaches - is sent again every 15 seconds, for up
    to a minute and a half, with the same work, good for 15 minutes."""
    started = time.monotonic()
    with output.working("Getting it ready to send: a puzzle that keeps floods away, a few seconds to a "
                        "minute or two..."):
        work = proof(data, tick=lambda: output.progress(f"working on it... {time.monotonic() - started:.0f}s"))
    output.progress_clear()
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-tar", "User-Agent": f"Autoflash/{__version__}",
        "X-Proof": work})
    for wait in (*RATE_WAITS, None):
        try:
            with hostsys.urlopen(request, timeout=timeout) as response:
                answer = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or wait is None:
                raise OSError(f"the website answered {exc.code}") from exc
            with output.working("The website takes two readouts a minute: sending it again shortly..."):
                time.sleep(wait)
    if answer.get("enough"):
        return ENOUGH
    reference = str(answer.get("id") or "")
    if not reference:
        raise ValueError("the website gave no reference")
    return reference


def choose(ctx: Context, devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The device to read out, or None for every device in the account
    (run_all), offered first where there are two or more: those the tool
    converts too, as one may run a firmware version the website has no
    readout of - new firmware is what a conversion meets unready."""
    if len(devices) == 1:
        return devices[0]
    every = [f"Every device in the account ({len(devices)})"]
    output.transient_begin()
    index = prompts.ask_choice("Which device should be read out",
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
    output.transient_begin()
    prompts.section(f"Read out {len(ordered)} devices, to help add support for them", [
        *(f"- {describe(device)}: " + ("its whole memory, once you agree" if reads_out(device) else
                                       "what it and eWeLink say, and the firmware eWeLink offers it")
          for device in ordered),
        "",
        "Only a device the website has no readout like - its model and firmware",
        "version - is read. Each readout is anonymised, and sent on its own as it",
        "is made.",
    ])
    contact = None
    if upload_url() is not None:
        try:
            contact = ask_contact()
        except prompts.QuitRun:
            output.transient_end()
            return "Nothing was read out or sent."
    output.transient_end()
    if contact and notify(contact["email"], [(str(device.get("productModel") or ""),
                                              str((device.get("extra") or {}).get("model") or ""))
                                             for device in ordered]):
        # Every device, its readout wanted or not: told when any one's changes.
        output.tell(f"    {CONFIRM_SENT}")
    todo = []
    for number, device in enumerate(ordered, 1):
        output.tell(f"Reading out {describe(device)} ({number} of {len(ordered)})...")
        for key in (*DEVICE_KEYS, readout_espressif.BRIDGE_HOST):
            ctx.state.set_input(key, None)
        left = ordered[number:]
        try:
            said = run(ctx, device, batch=Batch(contact))
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
    end(ctx, read_out_chosen(ctx, devices))
    # Said already, with the report: the run has nothing more to say.
    raise Finished("")


def send_saved(path: Path) -> str:
    """readout --send: a readout kept from before."""
    url = upload_url()
    if url is None:
        raise Abort("readouts are not sent from a test run")
    return send(path.read_bytes(), url)
