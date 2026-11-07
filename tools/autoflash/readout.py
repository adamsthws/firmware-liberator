"""A readout: a copy of a device's whole memory, to help add a device this tool
does not convert yet, sent with its owner's consent to firmwareliberator.com.

Nothing is converted. On a device with a chip there is a bridge for (an
ESP32-C3 or a classic ESP32), its Sonoff updater is sent the bridge alone,
wrapped under the firmware name eWeLink gives the device: the bridge goes in
the spare app slot, reads the whole flash, and has already chosen Sonoff's slot
again for the next start, so switching the device off and on puts it back as
it was, settings and pairing kept. Only what was in the spare slot is lost
(often the Sonoff version before the last update). On any other device nothing
is sent to it at all: only what eWeLink says it is.

Before anything is sent, the copy is anonymised (tools/espressif/scrub.py): its
Wi-Fi passwords taken out, as far as its settings show them, and its eWeLink
identity replaced by a made-up one, so it still starts in the emulator as the
device did. The copy kept on the
computer, as the device's Sonoff backup, is the device's own, untouched.
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

from tools.autoflash import __version__, account, firewall, hostsys, kinds, names, output, plugs, prompts, safety_locks
from tools.autoflash import setup_network
from tools.autoflash.errors import Abort, Finished, NothingWritten
from tools.autoflash.routes.espressif import installer
from tools.autoflash import runs
from tools.autoflash.runs import ROOT
from tools.espressif import build_vendor_ota, chips, device_facts, dump_stock_flash, scrub
from tools.ewelink import discover_ewelink, firmware_name, offered_firmware

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# Where readouts go: a Worker of their own, apart from the website's, with a
# private store behind it: the project's API (website/api/index.js).
UPLOAD_URL = "https://api.firmwareliberator.com/readout"
# The work each readout carries, as the Worker asks it (its PROOF_BITS).
PROOF_BITS = 24
# The chips the bridge runs on, as a firmware name says them (firmware_name.chip_of).
BRIDGE_CHIPS = {"ESP32C3": chips.ESP32C3, "ESP32D0WD": chips.ESP32}
FORMAT = 1
READ_TRIES = 3
BACK_ON_SONOFF_WAIT = 300.0
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
    """The account's device answering eWeLink's discovery at `address` (--plug-ip)."""
    try:
        here = answering_ids(discover_ewelink.discover(6.0, None, address))
    except OSError:
        here = {}
    match = [device for device in devices if str(device.get("deviceid")) in here]
    if not match:
        raise Abort(f"no device in the eWeLink account answers at {address}; check LAN Control is on for it")
    return match[0]


def locate(ctx: Context, device: dict[str, Any]) -> str:
    """The device's address on this network: the one answering eWeLink's
    discovery with its id, else typed by the person."""
    wanted = str(device.get("deviceid") or "")
    output.say("Looking for it on your network...")
    try:
        report = discover_ewelink.discover(6.0, wanted={wanted})
    except OSError:
        report = {"packets": []}
    found = answering_ids(report).get(wanted)
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


def run(ctx: Context, device: dict[str, Any]) -> str:
    """Read out the account's `device` and offer to send it. What the run ends
    by saying; Abort before anything was sent to the device, or after, with
    what to do."""
    ctx.state.set_input(STATE_KEY, "started")
    ip = locate(ctx, device)
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
    asked = account.ask_ewelink(ctx, ip)
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
    chip = BRIDGE_CHIPS.get(learned.chip) if learned else None
    if chip is None:
        why = ("its firmware name could not be learned" if learned is None else
               f"there is no bridge for its chip ({learned.chip or 'not named'}) yet")
        output.say(f"    Its memory cannot be read from here: {why}. The readout still helps.")
        manifest["read"] = False
        return offer(ctx, manifest, None, updates)
    flash, efuse, info, report = read_out(ctx, chip, learned.name, asked.version, known=known is not None)
    manifest["read"] = True
    manifest["facts"] = device_facts.lines(flash, efuse, device_facts.parse_info(info) if info else None)
    manifest["flash"] = {"size": report["size"], "sha256": report["sha256"],
                         "matches_device": report.get("matches_plug")}
    saved, fresh = plugs.save_backup(flash, efuse, report, asked.version, kind=describe(device))
    if fresh:
        output.say(f"    Kept on this computer too, in {runs.display_path(saved)}: private, as it holds")
        output.say("    your Wi-Fi password. Going back to Sonoff's firmware later can use it.")
    back = back_on_sonoff(ctx)
    return offer(ctx, manifest, (flash, efuse, info or b"", report), updates, back=back)


def read_out(ctx: Context, chip: chips.Chip, name: str, version: str, *,
             known: bool = False) -> tuple[bytes, bytes, bytes | None, dict]:
    """Send the bridge, wait for it, and read the whole flash through it.
    `known`: a kind the tool converts, which the bridge has run on before."""
    app = chip.bridge_app()
    wrapped = ctx.dir / "autoflash-bridge-readout.ota"
    wrapped.write_bytes(build_vendor_ota.build_wrapper(app, version=build_vendor_ota.app_version(app), model=name))
    output.transient_begin()
    prompts.warning_box("Before going on", [
        "This only reads the device: its Sonoff firmware, settings and eWeLink",
        "pairing stay. A small program is put in its spare firmware slot to read",
        "it; whatever was there, often the Sonoff version before its last update,",
        "is lost. At the end you switch the device off and on, and it starts its",
        "Sonoff firmware again.",
        "",
        *(["The same program starts every conversion of this kind of device."] if known else [
            "This tool has never run on this kind of device. If the small program",
            "could not start at all, the device may need a serial cable to recover.",
            "Only go ahead with a device you can afford to lose."]),
    ])
    if not prompts.require_word(prompts.CONSENT_WORD):
        raise NothingWritten("not confirmed - nothing has been sent to the device")
    output.transient_end()
    port = firewall.choose_port(ctx, "delivery_port", firewall.DELIVERY_PORTS, "the firmware")
    firewall.ensure_firewall(ctx)
    with safety_locks.lifted(ROOT, "RECOVERY_LOCK"):
        code = hostsys.run_tool(hostsys.python_script(
            "tools/espressif/serve_tasmota_ota.py",
            "--listen-ip", ctx.listen_ip(), "--listen-port", str(port),
            "--device-ip", ctx.state.input("plug_ip"), "--firmware", str(wrapped),
            "--email", ctx.email(), "--expected-current-version", version,
            "--mdns-capture", str(ctx.dir / "mdns.json"),
            "--i-understand-stock-has-no-automatic-rollback", "--readout-model", name),
            interactive=True, env=ctx.ewelink_env())
    safety_locks.say_lock_back(ROOT, "RECOVERY_LOCK")
    ctx.state.set_input(STATE_KEY, "sent")
    host = find_bridge(ctx, sent=code == 0)
    output.say("Reading the device's memory - about a minute...")
    problem = ""
    for _ in range(READ_TRIES):
        try:
            flash, efuse, report = dump_stock_flash.read_dump(host)
            break
        except OSError as exc:
            problem = str(exc)
            output.say(f"    The read did not come through: {problem}")
    else:
        raise Abort(f"the device's memory could not be read ({problem}). Switch it off and on: it starts "
                    "its Sonoff firmware again")
    for line in dump_stock_flash.summary(report):
        output.log_only(f"    {line}")
    return flash, efuse, dump_stock_flash.read_info(host), report


def find_bridge(ctx: Context, *, sent: bool) -> str:
    """Where the bridge answers: on the home network, or on its own once this
    computer has joined it. Abort if the device went back to Sonoff instead."""
    output.say("Waiting for it to start...")
    address = installer.bridge_on_home_network(ctx, timeout=120.0)
    if address:
        return address
    if installer.bridge_answers(setup_network.SETUP_HOST):
        return setup_network.SETUP_HOST
    if installer.back_on_sonoff(ctx):
        raise Abort("the device is still on its Sonoff firmware, so nothing was read: "
                    + ("it took the program but did not start it. " if sent else
                       "the program was not delivered. ")
                    + "It works as before")
    network = prompts.find_ssid(names.bridge_networks(ctx)) or names.our_setup_network(ctx)
    installer.ask_to_join_bridge(network)
    found = installer.wait_for_bridge(ctx, network.split()[0], gone_back=lambda: installer.back_on_sonoff(ctx))
    if found == installer.BACK_ON_SONOFF:
        raise Abort("the device went back to its Sonoff firmware before it could be read. It works as before")
    return found or setup_network.SETUP_HOST


def back_on_sonoff(ctx: Context) -> bool:
    """Have the person switch it off and on, and wait for Sonoff's firmware:
    whether it answered."""
    output.transient_begin()
    prompts.section("Read. Now put it back on its Sonoff firmware", [
        "Switch the device off, wait five seconds, and switch it on again (for a",
        "wall switch, at its circuit breaker). If this computer joined the",
        "device's own network, join your usual Wi-Fi again.",
    ])
    prompts.wait_for_key("Press any key once it is switched back on, or q to quit...")
    output.transient_end()
    started = time.monotonic()
    while not installer.back_on_sonoff(ctx):
        if time.monotonic() - started > BACK_ON_SONOFF_WAIT:
            output.tell("    It has not answered as Sonoff's firmware yet. Check the eWeLink app; if it is")
            output.tell("    missing there, switch it off and on once more.")
            return False
        output.progress(f"waiting for its Sonoff firmware... {time.monotonic() - started:.0f}s")
        time.sleep(3.0)
    output.progress_clear()
    ctx.state.set_input(STATE_KEY, "read")
    output.say("    It is back on its Sonoff firmware.")
    return True


# The large files inside are compressed with xz, about a quarter smaller than
# gzip on a real dump; the tar around them is not, so the API Worker reads its
# manifest, always first, without unpacking anything.
COMPRESSED = (".bin",)
NAME = "readout.tar"
# How the owner may be reached about the device, should support for it be added.
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
TELEGRAM = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{4,31}")


def package(manifest: dict[str, Any], read: tuple[bytes, bytes, bytes, dict] | None,
            updates: list[offered_firmware.Offered] | tuple = (), contact: dict[str, str] | None = None) -> bytes:
    """The readout as sent: a tar of manifest.json first; the firmware eWeLink
    offers the device, under offered/; and, when read, the flash anonymised (its
    Wi-Fi passwords taken out, its eWeLink identity made up), the eFuses, /info
    and the dump's report. Each .bin is xz-compressed (flash.bin.xz)."""
    files = {f"offered/{item.name}": item.data for item in updates}
    blanked = replaced = 0
    if read:
        flash, efuse, info, report = read
        scrubbed, blanked = scrub.wifi(flash)
        scrubbed, replaced = scrub.identity(scrubbed)
        files.update({"flash.bin": scrubbed, "efuse.bin": efuse, "info.txt": info,
                      "report.json": json.dumps(report, indent=2).encode()})
    counts = {"wifi_blanked": blanked, "identity_replaced": replaced, **({"contact": contact} if contact else {})}
    files = {name + ".xz" if name.endswith(COMPRESSED) else name: lzma.compress(data) if name.endswith(COMPRESSED)
             else data for name, data in files.items()}
    files = {"manifest.json": json.dumps({**manifest, **counts}, indent=2).encode(), **files}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
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


def offer(ctx: Context, manifest: dict[str, Any], read: tuple[bytes, bytes, bytes, dict] | None,
          updates: list[offered_firmware.Offered] | tuple = (), *, back: bool = True) -> str:
    """Make the readout, ask, and send it; what the run ends by saying.
    `back`: whether the device answered as its Sonoff firmware again."""
    path = ctx.dir / NAME

    def keep(contact: dict[str, str] | None = None) -> bytes:
        data = package(manifest, read, updates, contact)
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return data

    url = upload_url()
    contents = ["- the device's firmware readout, anonymised;"] if read or updates else []
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
    data = keep(contact)
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
