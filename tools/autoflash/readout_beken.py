"""A readout of a Beken (BK7238) device the tool does not convert yet.

A BK7238 has one app area, not two, so reading its whole flash means
installing firmware that replaces Sonoff's for a few minutes, then putting
Sonoff's back. This installs a pin-free OpenBeken (artifacts/openbeken-readout,
which drives nothing on an unmapped device), reads the flash through it, and
has it fetch the device's own Sonoff firmware back - the file eWeLink offers
the device, the same the stock bootloader would apply.

Nothing is written until the offered file's partition table is checked to be
the 4 MB BK7238 one the readout OpenBeken is built for: on any other table its
AutoflashRevertToStock would refuse, and the download slot could be wrong.

Each stage leans on the conversion's own helpers, with their retries: the
firmware is delivered through eWeLink (tools/ewelink/ota_push), the flash read
in paced, retried chunks (tools/autoflash/openbeken.read_flash), the revert
served until the device has every byte (routes/beken/onward.serve_to_plug).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, TYPE_CHECKING

from tools.autoflash import firewall, openbeken, output, plugs, prompts, runs, safety_locks
from tools.autoflash.errors import Abort, NothingWritten
from tools.autoflash.routes.beken import arrived, onward, stock
from tools.autoflash.runs import ROOT
from tools.beken import fota_wrap, layouts, rbl
from tools.ewelink import ota_push

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# The readout OpenBeken, as firmware.json pins it: the one image for any BK7238.
PIN = json.loads((ROOT / "artifacts" / "firmware.json").read_text(encoding="utf-8"))["beken"]["readout"]["openbeken"]
WHOLE_FLASH = 0x400000
DELIVERY_WAIT = 300.0
# How long to wait for the readout OpenBeken, then Sonoff's firmware, to answer.
ARRIVE_WAIT = 300.0
SONOFF_WAIT = 300.0
REVERT_URL_NAME = onward.IMAGE_NAME   # firmware.rbl


class Stock:
    """The device's own Sonoff firmware, from the file eWeLink offers it: the
    rt_ota RBL its bootloader applies, and the 4 MB table that file holds."""

    def __init__(self, rbl_image: bytes, version: str, layout: "layouts.Layout") -> None:
        self.rbl, self.version, self.layout = rbl_image, version, layout


def stock_from_offered(updates) -> Stock | None:
    """The offered file that is a whole stock image on a table this tool knows:
    its RBL, to revert with, and the table. None if none qualifies - then the
    device cannot be read out, and only what it reported is sent."""
    for item in updates:
        try:
            images = fota_wrap.unwrap(item.data)
            if len(images) != 1:
                continue
            rbl_image = images[0][2]
            app = rbl.unpack(rbl_image)
            layout, _table = layouts.known(app)
        except (ValueError, rbl.RblError, layouts.LayoutError):
            continue
        return Stock(rbl_image, images[0][1] or item.version, layout)
    return None


def readout_image() -> bytes:
    """The readout OpenBeken's rt_ota RBL, checked against its pin."""
    data = (ROOT / PIN["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != PIN["sha256"]:
        raise Abort(f"{PIN['file']} is not the file firmware.json pins: refusing to send it")
    return data


def deliver_readout(ctx: Context, name: str, version: str, device: dict[str, Any]) -> None:
    """Send the readout OpenBeken to the device through eWeLink, wrapped under
    the firmware name its updater expects. The first write."""
    container = fota_wrap.wrap(readout_image(), name=name, version=fota_wrap.ANNOUNCED_VERSION)
    plug_ip = ctx.state.require("plug_ip")[0]
    port = firewall.choose_port(ctx, "delivery_port", firewall.DELIVERY_PORTS, "the firmware")
    firewall.ensure_firewall(ctx)

    def check(found: dict[str, Any], model: str) -> None:
        reported = str((found.get("params") or {}).get("fwVersion"))
        if reported != version:
            raise ota_push.EwelinkError(f"the device reports firmware {reported}, not the {version} it reported")

    output.say("Sending the readout firmware through eWeLink...")
    with safety_locks.lifted(ROOT, "RECOVERY_LOCK"):
        delivered = ota_push.deliver(
            email=ctx.email(), password=ctx.ewelink_env()["EWELINK_PASSWORD"], country_code="+44", region="eu",
            device_id=device["deviceid"], firmware=container, version=fota_wrap.ANNOUNCED_VERSION,
            listen_ip=ctx.listen_ip(), listen_port=port, device_ip=plug_ip, names=("user1.bin",),
            wait=DELIVERY_WAIT, check_device=check, say=output.say, live=False)
    safety_locks.say_lock_back(ROOT, "RECOVERY_LOCK")
    if not delivered.sent:
        raise NothingWritten(f"the readout firmware was not sent: {delivered.error or 'eWeLink did not take it'}. "
                             f"{ota_push.NOT_SENT}")
    if stock.BUSY.search(delivered.error or ""):
        raise Abort("the device turned the firmware down: it is still busy with an update cut off part way. "
                    "Switch the device off and on - it keeps its Sonoff firmware - then run this again")
    if not delivered.complete:
        raise OSError(f"the device fetched {delivered.bytes_served:,} of {len(container):,} bytes before the "
                      f"delivery stopped ({delivered.error or 'it stopped asking'})")
    ctx.state.set_input("readout_sent", True)
    output.say("    The device has the whole image; its bootloader installs it as it restarts.")


def wait_for_openbeken(ctx: Context, wait: float = ARRIVE_WAIT) -> str:
    """The address OpenBeken answers at, once it has started and joined the
    Wi-Fi. Abort if the device stayed on, or went back to, Sonoff's firmware."""
    output.say("Waiting for the readout firmware to start and join your Wi-Fi...")

    def look(host: str) -> str | None:
        return host if openbeken.info(host, timeout=3.0) else None

    found = arrived.until_back(ctx, lambda restarted: _poll(ctx, look, wait),
                               "The readout firmware", (
        "The readout firmware has not answered on your Wi-Fi. It installs as the device restarts, then reads "
        "the device's Wi-Fi from its stock store and joins; if it has not, switch the device off and on, then "
        "run this again"))
    output.say(f"    OpenBeken answered at {found}.")
    return found


def _poll(ctx: Context, look, wait: float):
    """look(host) until it returns, at the plug's address or wherever its
    hardware address moved to; NotBack if the time runs out."""
    import tools.autoflash.join as join
    try:
        return join.wait_for(ctx, look, "the readout firmware on your Wi-Fi", timeout=wait)
    except Abort as exc:
        raise arrived.NotBack from exc


def read_whole_flash(ctx: Context, host: str) -> bytes:
    """The device's whole flash, read through OpenBeken (paced, retried chunks)."""
    output.say("Reading the device's memory - a few minutes...")

    def progress(done: int, total: int) -> None:
        output.progress(f"reading the device's memory: {100 * done // total}%")

    try:
        flash = openbeken.read_flash(host, 0, WHOLE_FLASH, progress=progress)
    finally:
        output.progress_clear()
    if len(flash) != WHOLE_FLASH:
        raise OSError(f"only {len(flash):,} of {WHOLE_FLASH:,} bytes were read")
    output.say(f"    Read {len(flash):,} bytes.")
    return flash


def revert_to_sonoff(ctx: Context, host: str, found: Stock) -> None:
    """Have OpenBeken fetch the device's own Sonoff firmware back into the
    stock download slot - its AutoflashRevertToStock checks the slot first -
    and the bootloader applies it on the restart that follows."""
    output.say("Putting the device's Sonoff firmware back...")

    def fetch(url: str) -> None:
        try:
            openbeken.command(host, f"AutoflashRevertToStock {url}")
        except openbeken.CommandError as exc:
            raise Abort(f"the readout firmware would not fetch Sonoff's firmware back: {exc}. The device's own "
                        "data is read; switch it off and on and send its Sonoff firmware again") from exc

    onward.serve_to_plug(ctx, found.rbl, f"Sonoff's firmware {found.version}", fetch,
                         unstick=lambda: openbeken.restart(host))


def wait_for_sonoff(ctx: Context, device_id: str, wait: float = SONOFF_WAIT) -> bool:
    """Wait for Sonoff's firmware to start and show online in eWeLink again.
    Whether it did; the readout is kept and sent either way."""
    from tools.ewelink import relay as ewelink_relay
    output.say("Waiting for Sonoff's firmware to start and rejoin eWeLink...")
    started = time.monotonic()
    while time.monotonic() - started < wait:
        devices = ctx.account_devices() if device_id else None
        online = any(found.get("deviceid") == device_id and found.get("online") for found in devices or [])
        if online and ewelink_relay.answers(email=ctx.email(), password=ctx.ewelink_env()["EWELINK_PASSWORD"],
                                             country_code="+44", region="eu", device_id=device_id):
            output.progress_clear()
            output.say(f"    eWeLink shows the device online again after {round(time.monotonic() - started)}s.")
            return True
        output.progress(f"waiting for eWeLink to show the device online... {time.monotonic() - started:.0f}s")
        time.sleep(10)
    output.progress_clear()
    output.tell("    eWeLink has not shown the device online yet. Switch it off and on; if it stays missing in "
                "the app, its Sonoff firmware may need sending again.")
    return False


def read_out(ctx: Context, name: str, version: str, device: dict[str, Any],
             found: Stock) -> tuple[bytes, dict[str, str], bool]:
    """Install the readout firmware, read the whole flash, put Sonoff back.
    The whole flash, what OpenBeken said of itself, and whether Sonoff answered."""
    output.transient_begin()
    prompts.warning_box("Before going on", [
        "This reads the whole memory of a device this tool has never run on. To",
        "read it, the device's Sonoff firmware is replaced for a few minutes with",
        "a small program that only reads; then the tool puts the device's own",
        "Sonoff firmware back, and its settings and eWeLink pairing are kept.",
        "",
        "DO NOT UNPLUG IT during the process. If the small program could not start",
        "at all, the device may need a serial cable to recover. Only go ahead with",
        "a device you can afford to lose.",
    ])
    if not prompts.require_word(prompts.CONSENT_WORD):
        raise NothingWritten("not confirmed - nothing has been sent to the device")
    output.transient_end()
    deliver_readout(ctx, name, version, device)
    host = wait_for_openbeken(ctx)
    info = openbeken.info(host) or {}
    flash = read_whole_flash(ctx, host)
    mac = ctx.state.input("plug_mac")
    if mac:
        directory, saved = plugs.save_beken_backup(mac, flash[plugs.BEKEN_REGION[0]:], found.rbl, found.version,
                                                   kind="a device being added")
        if saved:
            output.say(f"    The device's own data is kept on this computer, in {runs.display_path(directory)}.")
    revert_to_sonoff(ctx, host, found)
    back = wait_for_sonoff(ctx, device["deviceid"])
    return flash, {"openbeken": str(info.get("build") or ""), "chipset": str(info.get("chipset") or "")}, back
