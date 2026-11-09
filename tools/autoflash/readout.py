"""A readout: a copy of a device's whole memory, to help add a device this tool
does not convert yet, sent with its owner's consent to firmwareliberator.com.

Nothing is converted. What eWeLink says of the device settles how its memory
is read, by its chip family: an Espressif device through the bridge, which
leaves Sonoff's firmware where it is (readout_espressif.py); a Beken device
through a readout OpenBeken, which puts Sonoff's firmware back
(readout_beken.py). Either way the copy is anonymised before anything is sent,
and the device is left on its Sonoff firmware. On a device neither can read,
nothing is sent to it at all: only what eWeLink says it is.

This is what both share: the device found in the eWeLink account and on the
network, what it reports, and the readout packed, offered and sent.
"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import os
import re
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TYPE_CHECKING

from tools.autoflash import __version__, account, hostsys, kinds, output, plugs, prompts, runs
from tools.autoflash import readout_beken, readout_espressif
from tools.autoflash.errors import Abort, Finished
from tools.ewelink import discover_ewelink, firmware_name, offered_firmware

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# Where readouts go: a Worker of their own, apart from the website's, with a
# private store behind it: the project's API (website/api/index.js).
UPLOAD_URL = "https://api.firmwareliberator.com/readout"
# The work each readout carries, as the Worker asks it (its PROOF_BITS).
PROOF_BITS = 24
FORMAT = 1
# What the run's record keeps, so a readout is never offered back as a conversion to finish.
STATE_KEY = "readout"


def upload_url() -> str | None:
    """Where to send it: AUTOFLASH_READOUT_URL when set (a test's own server),
    else the website - but never from the emulator or an unattended run, which
    would send a test's flash as a device's."""
    given = os.environ.get("AUTOFLASH_READOUT_URL")
    if given:
        return given
    if os.environ.get("AUTOFLASH_EMULATOR_URL") or prompts.UNATTENDED:
        return None
    return UPLOAD_URL


def describe(device: dict[str, Any]) -> str:
    name = str(device.get("name") or "").strip() or str(device.get("deviceid") or "a device")
    return f"'{name}' ({device.get('productModel') or 'unknown model'})"


def unknown_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The account's devices of a kind this tool does not convert yet."""
    return [device for device in devices if account.account_plug(device) is None]


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
    """The account's device at `address` (--plug-ip): the one answering eWeLink's
    discovery there, else the one whose hardware address is there (a Beken plug
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
    """Whether the account's `device` does no LAN Control (a Beken plug), so
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
    return mac if plugs.MAC_RE.match(mac) else None


def locate(ctx: Context, device: dict[str, Any]) -> str:
    """The device's address on this network: the one answering eWeLink's
    discovery with its id, else typed by the person. A device that does no
    LAN Control is looked for by its hardware address instead."""
    if lan_free(device):
        return locate_by_mac(device)
    wanted = str(device.get("deviceid") or "")
    output.say("Looking for it on your network...")
    try:
        report = discover_ewelink.discover(6.0, wanted={wanted})
    except OSError:
        report = {"packets": []}
    found = answering_ids(report).get(wanted)
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
        check=account.plug_at({wanted}))
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


def run(ctx: Context, device: dict[str, Any], address: str | None = None) -> str:
    """Read out the account's `device` and offer to send it. What the run ends
    by saying; Abort before anything was sent to the device, or after, with
    what to do."""
    ctx.state.set_input(STATE_KEY, "started")
    ip = address or locate(ctx, device)
    ctx.state.set_input("plug_ip", ip)
    mac = hostsys.mac_of(ip) or (device.get("params") or {}).get("staMac")
    if mac and plugs.MAC_RE.match(hostsys.normalise_mac(mac)):
        mac = hostsys.normalise_mac(mac)
        ctx.state.set_input("plug_mac", mac)
        # Under the device's own folder, as a conversion's run is: its log, the
        # readout kept unsent, and the backup beside them.
        runs.file_plug(ctx, mac, None)
        plugs.remember_plug(mac, ewelink_name=str(device.get("name") or "").strip() or None,
                            ewelink_id=str(device.get("deviceid") or "") or None,
                            model=str(device.get("productModel") or "") or None)
    # A device that does no LAN Control (a Beken plug) is asked of eWeLink by
    # its account id.
    # So is one a readout stopped part way left on its reading program (the
    # bridge), which answers no discovery either.
    on_bridge = not lan_free(device) and readout_espressif.installer.bridge_answers(ip)
    asked = (account.ask_account(ctx, str(device.get("deviceid") or "")) if lan_free(device) or on_bridge
             else account.ask_ewelink(ctx, ip))
    learned, problems = firmware_name.learn(asked.latest.get("offers") or [], asked.deviceid,
                                            asked.product, asked.model)
    for problem in problems:
        output.log_only(f"    Firmware name: {problem}")
    known = kinds.for_account(asked.product, asked.model)
    if learned is None and known is not None and known.family == "espressif":
        # A kind the tool converts: its own name for it, as a conversion sends.
        learned = firmware_name.Learned(known.wrapper_model, firmware_name.chip_of(known.wrapper_model),
                                        "this tool's list of devices")
    ctx.state.set_input("stock_version", asked.version)
    ctx.state.set_input("product_model", asked.product)
    manifest = {
        "format": FORMAT, "tool": __version__, "product": asked.product, "stock_model": asked.model,
        "version": asked.version, "brand": str(device.get("brandName") or ""),
        "uiid": (device.get("extra") or {}).get("uiid"),
        "firmware_name": learned.name if learned else "", "name_from": learned.source if learned else "",
        "chip": learned.chip if learned else "",
    }
    # The firmware eWeLink offers it as well: often another version than the
    # one it runs, so two to test against. Nothing is sent to the device for it.
    updates, problems = offered_firmware.fetch(asked.latest.get("offers") or [], asked.deviceid)
    for problem in problems:
        output.log_only(f"    Offered firmware: {problem}")
    manifest["offered"] = [{"file": f"offered/{item.name}", "version": item.version, "size": len(item.data),
                            "sha256": item.sha256} for item in updates]
    prompts.section("What the device reported", [
        f"Model            {asked.model or 'not given'}",
        f"Product          {asked.product or 'not given'}",
        f"Firmware now     {asked.version}",
        f"Firmware name    {learned.name if learned else 'not learned'}",
    ])
    chip = readout_espressif.BRIDGE_CHIPS.get(learned.chip) if learned else None
    beken_stock = readout_beken.stock_from_offered(updates) if learned and learned.chip.startswith("BK") else None
    if chip is not None:
        files, back = readout_espressif.read_and_anonymise(ctx, manifest, chip, learned.name, asked.version, device,
                                                           known=known is not None)
    elif beken_stock is not None:
        files, back = readout_beken.read_and_anonymise(ctx, manifest, learned.name, asked.version, device,
                                                       beken_stock)
    else:
        if learned is None:
            why = "its firmware name could not be learned"
        elif learned.chip.startswith("BK"):
            why = "its stock firmware could not be read from the file eWeLink offers it"
        else:
            why = f"there is no bridge for its chip ({learned.chip or 'not named'}) yet"
        output.say(f"    Its memory cannot be read from here: {why}. The readout still helps.")
        manifest["read"] = False
        return offer(ctx, manifest, None, updates)
    return offer(ctx, manifest, files, updates, back=back)


# The large files inside are compressed with xz, about a quarter smaller than
# gzip on a real dump; the tar around them is not, so the API Worker reads its
# manifest, always first, without unpacking anything.
COMPRESSED = (".bin",)
NAME = "readout.tar"
# How the owner may be reached about the device, should support for it be added.
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
TELEGRAM = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{4,31}")


def package(manifest: dict[str, Any], files: dict[str, bytes] | None = None,
            updates: list[offered_firmware.Offered] | tuple = (), contact: dict[str, str] | None = None) -> bytes:
    """The readout as sent: a tar of manifest.json first; the firmware eWeLink
    offers the device, under offered/; and `files` - the anonymised flash and
    its companions (flash.bin, efuse.bin, info.txt, report.json), already
    anonymised by the chip family that read them. Each .bin is xz-compressed
    (flash.bin.xz), so the tar stays readable without unpacking it."""
    out = {f"offered/{item.name}": item.data for item in updates}
    out.update(files or {})
    if contact:
        manifest = {**manifest, "contact": contact}
    out = {name + ".xz" if name.endswith(COMPRESSED) else name: lzma.compress(data) if name.endswith(COMPRESSED)
           else data for name, data in out.items()}
    out = {"manifest.json": json.dumps(manifest, indent=2).encode(), **out}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in out.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mtime = len(data), int(time.time())
            archive.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


def ask_contact() -> dict[str, str] | None:
    """How the owner may be reached about the device, if they choose to say:
    {"email": ...}, {"telegram": "@..."}, or None. Quit stops the run unsent."""
    if prompts.UNATTENDED:
        prompts.answered_unattended("Contact details", "Submit without contact information")
        return None
    output.transient_begin()
    choice = prompts.ask_choice("Add a way to reach you", [
        "Add an email address",
        "Add a Telegram username",
        "Submit without contact information",
    ], help_lines=["If support for your device is added, you can be told, and asked to try it."])
    contact = None
    if choice == 0:
        contact = {"email": prompts.ask_text("Your email address", private="email", validate=lambda text: (
            bool(EMAIL.fullmatch(text.strip())), "That does not look like an email address."))}
    elif choice == 1:
        name = prompts.ask_text("Your Telegram username", private="telegram", example="@example_name",
                                validate=lambda text: (bool(TELEGRAM.fullmatch(text.strip())),
                                                       "A Telegram username is 5 to 32 letters, digits or _."))
        contact = {"telegram": "@" + name.strip().lstrip("@")}
    output.transient_end()
    if contact:
        contact = {key: value.strip() for key, value in contact.items()}
        output.say("    Your contact details go with it.")
    return contact


def offer(ctx: Context, manifest: dict[str, Any], files: dict[str, bytes] | None = None,
          updates: list[offered_firmware.Offered] | tuple = (), *, back: bool = True) -> str:
    """Make the readout, ask, and send it; what the run ends by saying.
    `files`: the anonymised flash and its companions, or None if not read.
    `back`: whether the device answered as its Sonoff firmware again."""
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
            output.say("    Not sent from a test run.")
            return f"Readout saved in {path}, not sent."
        try:
            contact = ask_contact()
        except prompts.QuitRun:
            keep()
            return f"Not sent. The readout is kept in {path}, if you would like to send it later."
        return sent(keep(contact), url, path, back)


def sent(data: bytes, url: str, path: Path, back: bool) -> str:
    """offer()'s readout sent to `url`; what the run ends by saying."""
    try:
        reference = send(data, url)
    except (OSError, ValueError) as exc:
        raise Abort(f"the readout could not be sent ({exc}). It is kept in {path}: send it later "
                    "with this tool's readout --send") from exc
    path.unlink(missing_ok=True)
    return (f"Sent, thank you! Its reference is {reference}: quote it if you post about this device. "
            + ("The device is running its Sonoff firmware as before." if back else
               "Switch the device off and on once more: it then starts its Sonoff firmware again."))


def proof(data: bytes, now: float | None = None, bits: int | None = None) -> str:
    """"<time>:<nonce>" whose SHA-256 with `data`'s, as "<time>:<data's
    SHA-256>:<nonce>", starts with `bits` zero bits: the work the Worker asks
    of each readout, about 10 to 30 seconds on a computer, so a flood of them
    costs its sender."""
    stamp = int(time.time() if now is None else now)
    prefix = hashlib.sha256(f"{stamp}:{hashlib.sha256(data).hexdigest()}:".encode())
    target = 1 << (256 - (PROOF_BITS if bits is None else bits))
    nonce = 0
    while True:
        attempt = prefix.copy()
        attempt.update(str(nonce).encode())
        if int.from_bytes(attempt.digest(), "big") < target:
            return f"{stamp}:{nonce}"
        nonce += 1


def send(data: bytes, url: str = UPLOAD_URL, timeout: float = 120.0) -> str:
    """POST a readout; the reference the store gave it."""
    output.say("    Getting it ready to send - up to half a minute...")
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-tar", "User-Agent": f"Autoflash/{__version__}",
        "X-Proof": proof(data)})
    try:
        with hostsys.urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OSError(f"the website answered {exc.code}") from exc
    if answer.get("enough"):
        return "none needed: enough readouts of this device are in already"
    reference = str(answer.get("id") or "")
    if not reference:
        raise ValueError("the website gave no reference")
    return reference


def choose(ctx: Context, devices: list[dict[str, Any]]) -> dict[str, Any]:
    if len(devices) == 1:
        return devices[0]
    output.transient_begin()
    index = prompts.ask_choice("Which device should be read out", [describe(device) for device in devices])
    output.transient_end()
    return devices[index]


def from_run(ctx: Context, devices: list[dict[str, Any]]) -> None:
    """A1's way out to a readout: run it, then end the run (errors.Finished)."""
    raise Finished(run(ctx, choose(ctx, devices)))


def send_saved(path: Path) -> str:
    """readout --send: a readout kept from before."""
    url = upload_url()
    if url is None:
        raise Abort("readouts are not sent from a test run")
    return send(path.read_bytes(), url)
