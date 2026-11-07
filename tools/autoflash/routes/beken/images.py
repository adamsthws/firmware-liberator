"""The firmware a Beken plug is given: the OpenBeken and ESPHome images
published in artifacts/ and pinned in firmware.json under "beken", by plug,
and Sonoff's own firmware for a plug going back, made from the public dump
of its stock flash when it is needed - never published here (AGENTS.md).

Each is an rt_ota image the plug's stock bootloader applies from its download
slot (tools/beken/rbl.py); the stock firmware's updater takes one wrapped in a
FOTA container (tools/beken/fota_wrap.py), OpenBeken fetches one by URL, and
an ESPHome takes one inside a UF2 (tools/beken/uf2.py).
"""

from __future__ import annotations

import hashlib
import json
import struct
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tools.autoflash import kinds, output
from tools.autoflash.errors import Abort, NothingWritten
from tools.autoflash.runs import ROOT
from tools.beken import fota_wrap, layouts
from tools.beken import rbl, stock_app

if TYPE_CHECKING:
    from tools.autoflash.context import Context

PINS = json.loads((ROOT / "artifacts" / "firmware.json").read_text(encoding="utf-8")).get("beken", {})
# The version the FOTA container says it is (the S60's bridge is sent the same way).
FOTA_VERSION = fota_wrap.ANNOUNCED_VERSION
# The firmware a run to these installs from Sonoff's first, to back the plug
# up through (only OpenBeken reads it out), then has fetch the one chosen.
# The person, who did not choose it, is told of the interim firmware alone.
INTERIM = {"esphome": "openbeken"}
INTERIM_NAME = "the interim firmware"
# Testing only (run --simulate-bad-image [STEP]): the step whose image is sent
# spoilt, once a run - one byte of the RBL's body changed, so the plug fetches
# it whole and an update's own CRC (made after) passes, but its bootloader,
# checking the body's CRC in the RBL's header, refuses to install it.
SPOIL_STEP: str | None = None
# Testing only (run --simulate-unknown-table): the official file's table read
# as if its NET info were at another place, as a device on another table's would be.
UNKNOWN_TABLE = False
# Each kind's stock flash, as another project publishes it: where, and what it
# is, by hash.  Fetched once into the directory the emulator keeps dumps in.
def as_sent(ctx: Context, step: str, image: bytes) -> bytes:
    """`image` as `step` sends it: spoilt, where a test asked for that step's
    (SPOIL_STEP) and this run has not sent it spoilt yet - so a test may ask on
    every run, as a route reaches the step only after a restart asked for."""
    if step != SPOIL_STEP or ctx.state.data.get("captures", {}).get(f"{step}.spoilt"):
        return image
    ctx.state.capture(f"{step}.spoilt", True)
    spoilt = bytearray(image)
    spoilt[rbl.HEADER + (len(image) - rbl.HEADER) // 2] ^= 0xFF
    output.say(f"    The image is spoilt, as asked (--simulate-bad-image {step}): its bootloader will refuse it.")
    return bytes(spoilt)


PUBLIC_DUMPS = {
    "sonoff-s61": (
        "https://raw.githubusercontent.com/openshwprojects/BekenEmulator/d9a3772a26fa92158be72faf46855726ef8e21d7"
        "/firmwares/Sonoff_S61s_EUPlug_WBBK_01P_V1.3.bin",
        "0f858068b7b6c4107119e6afdf7a6eb05349234e726028602dbcf63b92ad6df3",
    ),
}
# A kind with no public dump: eWeLink's official update file for it, as another
# project publishes them, by name and hash. What it goes back to, and whose
# partition table says what its flash should be (official).
OFFICIAL_FILES_AT = ("https://raw.githubusercontent.com/openshwprojects/FlashDumps/"
                     "9d9d67ae87aa780aed8856140362670d9c0e5642/IoT/zOTAz/eWeLink/")
OFFICIAL_FILES = {
    "sonoff-basic-1gs": ("SONOFF_BASIC-1GS_(FWSW-01-BASICR5-BK7238)_BK7238_v1.0.4_user1.bin",
                         "0df1f696e9c03b076fda5b6ef103b9a658ba3ec495afa2b013586c1c40e32d25"),
}
DUMPS = ROOT / "captures" / "emulation" / "beken-firmwares"


@dataclass(frozen=True)
class Official:
    """A kind's official update file, its partition table known."""
    image: bytes                 # the rt_ota image in it, as its bootloader applies it
    name: str                    # the firmware name its wrapper gives
    version: str
    layout: layouts.Layout
    table: tuple[layouts.Partition, ...]


def official_file(kind: kinds.Kind) -> bytes:
    """A kind's official update file: fetched once (DUMPS), checked against its hash."""
    name, digest = OFFICIAL_FILES[kind.key]
    path = DUMPS / name
    data = path.read_bytes() if path.is_file() else b""
    if hashlib.sha256(data).hexdigest() == digest:
        return data
    output.say(f"Fetching eWeLink's official update file for the {kind.name} ({name})...")
    try:
        with urllib.request.urlopen(OFFICIAL_FILES_AT + urllib.parse.quote(name), timeout=120) as reply:
            data = reply.read()
    except OSError as exc:
        raise NothingWritten(f"eWeLink's official update file for the {kind.name} could not be fetched: {exc}. "
                             "Nothing was written") from exc
    if hashlib.sha256(data).hexdigest() != digest:
        raise NothingWritten(f"the official update file fetched for the {kind.name} is not the one this tool "
                             "knows (its hash differs): refused. Nothing was written")
    DUMPS.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def official(kind: kinds.Kind) -> Official:
    """A kind's official update file, refused unless its partition table is the
    one this kind is known to be on: everything a conversion writes or reads
    goes where that table says."""
    data = official_file(kind)
    try:
        ((name, version, image),) = fota_wrap.unwrap(data)
        app = rbl.unpack(image)
        if UNKNOWN_TABLE:
            app = app.replace(struct.pack("<I", 0x3E1000), struct.pack("<I", 0x1E1000))
        layout, table = layouts.known(app)
    except (ValueError, rbl.RblError) as exc:
        raise NothingWritten(f"the official update file for the {kind.name} was refused: {exc}. Its device may not "
                             "be laid out as this tool expects, so nothing was written. Please report this") from exc
    if layout.key != kind.layout:
        raise NothingWritten(f"the official update file for the {kind.name} has the {layout.key} partition table, "
                             f"not the {kind.layout} one expected. Nothing was written. Please report this")
    return Official(image, name, version, layout, table)


def first_firmware(firmware: str) -> str:
    """The firmware a run to `firmware` sends from Sonoff's: the interim one, if it has one."""
    return INTERIM.get(firmware, firmware)


def pin(kind: kinds.Kind, firmware: str) -> dict:
    try:
        return PINS[kind.key][firmware]
    except KeyError:
        raise Abort(f"no {firmware} image is published for the {kind.name} (artifacts/firmware.json)") from None


def image(ctx: Context, firmware: str) -> tuple[bytes, str]:
    """The published image of `firmware` for the run's kind of plug, checked
    against its pin - or, for ESPHome, one built by hand (--esphome), checked
    as an rt_ota app.  The image and its version."""
    kind = kinds.of_run(ctx.state)
    own = getattr(ctx, "esphome_firmware", None) if firmware == "esphome" else None
    if own:
        data = Path(own).read_bytes()
        try:
            header = rbl.header(data)
            rbl.unpack(data)
        except rbl.RblError as exc:
            raise Abort(f"{own} is not an rt_ota image for the plug: {exc}") from exc
        if header.name != "app":
            raise Abort(f"{own} is an rt_ota {header.name!r} image, not an app")
        output.say(f"Using the ESPHome built by hand at {own} ({header.version}).")
        return data, header.version
    pinned = pin(kind, firmware)
    path = ROOT / pinned["file"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise Abort(f"the published {firmware} image is missing: {exc}") from exc
    if len(data) != pinned["size"] or hashlib.sha256(data).hexdigest() != pinned["sha256"]:
        raise Abort(f"{pinned['file']} is not the file firmware.json pins: refusing to send it")
    return data, str(pinned["version"])


def stock_rbl(kind: kinds.Kind) -> tuple[bytes, str]:
    """Sonoff's own firmware for a plug of `kind`, as the rt_ota image its
    bootloader applies: made from the public dump of a stock plug, fetched
    once (DUMPS) and checked against its pinned hash - or, for a kind with
    none, its official update file's.  The image and Sonoff's version of it."""
    if kind.key in OFFICIAL_FILES:
        try:
            found = official(kind)
        except NothingWritten as exc:
            raise Abort(str(exc)) from exc
        return found.image, found.version
    try:
        url, digest = PUBLIC_DUMPS[kind.key]
    except KeyError:
        raise Abort(f"no public dump of a {kind.name}'s stock firmware is known: it cannot go back") from None
    path = DUMPS / url.rsplit("/", 1)[1]
    data = path.read_bytes() if path.is_file() else b""
    if hashlib.sha256(data).hexdigest() != digest:
        output.say(f"Fetching the public {kind.name} stock dump ({url.rsplit('/', 1)[1]}, 4 MB)...")
        try:
            with urllib.request.urlopen(url, timeout=120) as reply:
                data = reply.read()
        except OSError as exc:
            raise Abort(f"the public dump of the {kind.name}'s stock firmware could not be fetched: {exc}") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise Abort("the public dump fetched is not the one this tool knows (its hash differs): refusing it")
        DUMPS.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    try:
        return stock_app.stock_rbl(data)
    except stock_app.DumpError as exc:
        raise Abort(f"the public dump holds no stock app this tool can use: {exc}") from exc
