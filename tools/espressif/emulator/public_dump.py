#!/usr/bin/env python3
"""A plug for the emulator from a public dump of a stock one, for the kinds of
plug no one here owns.

    python tools/espressif/emulator/public_dump.py KIND
    python tools/espressif/emulator/public_dump.py --remove KIND

The dump is fetched from where it is published, checked against its hash and
filed as a test copy's backup (copy_plug.py), under a MAC made up for the kind:
emu.sh --plug MAC runs it. Nothing of it reaches this repository, and no image
holds it (AGENTS.md).

A public dump is of someone's real plug, and some hold its eWeLink identity: it
is replaced with a made-up one on the way in, so that no run can ever speak as
that plug, whatever it reaches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.autoflash import kinds, plugs  # noqa: E402
from tools.espressif import build_vendor_ota, flashmap  # noqa: E402
from tools.espressif.emulator import copy_plug, rewrite_nvs  # noqa: E402
from tools.espressif.inspect_stock_nvs import parse  # noqa: E402

COMMIT = "4d3556605da2ce101382a01b6fb7d84e68c3df2a"
FLASH_DUMPS = f"https://raw.githubusercontent.com/openshwprojects/FlashDumps/{COMMIT}/IoT/ESP32"
OTA_FIRMWARE = f"https://raw.githubusercontent.com/openshwprojects/FlashDumps/{COMMIT}/IoT/zOTAz/eWeLink"
CACHE = ROOT / "captures" / "emulation" / "public-dumps"
FLASH_SIZE = 0x400000


@dataclass(frozen=True)
class Dump:
    """A public whole-flash dump, and the number its made-up identity is made from."""
    file: str
    sha256: str
    number: int

    @property
    def url(self) -> str:
        return f"{FLASH_DUMPS}/{self.file}"

    @property
    def mac(self) -> str:
        return f"12:34:56:e3:2d:{self.number:02x}"  # locally administered, so nobody's

    @property
    def deviceid(self) -> str:
        return f"10000e32{self.number:02x}"  # as long as a real one

    @property
    def apikey(self) -> str:
        return f"00000000-0000-4000-8000-0000000e32{self.number:02x}"  # in a real one's shape


DUMPS = {
    "sonoff-pow-elite": Dump("Sonoff-Powr316D.bin",
                             "d31118361c0bfdece510eed23ae4fa7ad5ebad2e7b76204d9a7bc09a66f9187e", 1),
    "sonoff-th-elite": Dump("Sonoff-THR320D.bin",
                            "538d8e6244388abe6f758e821a2c58ccdfe3a30badd248f1c64b72c23d6cdf6d", 2),
    "sonoff-powct": Dump("Sonoff-POWCT-20250518.bin",
                         "295cd9b7791d6a088216bfafc03d63bbb4622df5e68d2f26f499ed64161b3625", 3),
    "sonoff-nspanel": Dump("Sonoff-NSPanel.bin",
                           "a047dcdb5fac92d10d956cd09b0100df4aae82ef61b5df9bf71aed7270c89596", 4),
}


def fetched(dump: "Dump | Update") -> bytes:
    """The file, once fetched into CACHE, checked against its hash."""
    path = CACHE / dump.file
    data = path.read_bytes() if path.is_file() else b""
    if hashlib.sha256(data).hexdigest() != dump.sha256:
        with urllib.request.urlopen(urllib.parse.quote(dump.url, safe=":/"), timeout=120) as reply:
            data = reply.read()
        if hashlib.sha256(data).hexdigest() != dump.sha256:
            raise SystemExit(f"{dump.url} is not the file pinned here (its hash differs)")
        CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
    return data


@dataclass(frozen=True)
class Update:
    """eWeLink's public firmware update for a plug no whole dump of which is
    published: run on a stock plug of the same chip's flash in place of that
    one's firmware. It tests the firmware, not the plug's real layout, which is
    why a real one with another stops before anything is written over Sonoff's."""
    file: str
    sha256: str
    number: int
    donor: str         # the key in DUMPS or S60_DUMP of the flash it goes on
    stock_model: str   # what its firmware calls itself, read from it
    uiid: int = 0      # eWeLink's number for its kind, where its firmware checks it

    @property
    def url(self) -> str:
        return f"{OTA_FIRMWARE}/{self.file}"

    mac = Dump.mac
    deviceid = Dump.deviceid
    apikey = Dump.apikey


S60_DUMP = Dump("Sonoff_S60TPG_UK_Plug_ESP32-C3_SM-049_1.2.0.bin",
                "a638d7251c20a20ff14cf0eef9be3058bedd26ed80e260d7d0107d6b2599dff4", 0xF0)
UPDATES = {
    "sonoff-minir4": Update("SONOFF_MINIR4_(FWSW-E32R4-MINIR4-ESP32D0WDR2V3)_ESP32_v1.2.0_user1.bin",
                            "c881ddd667bc9d8c6905acf30051a1f2e4270c4be2dcfcfa39a5bfdd137c6ec0", 6,
                            "sonoff-pow-elite", "SN-ESP32D0-MINIR4-01"),
    "sonoff-dualr3": Update("SONOFF_DUALR3-Family_(FWSWES-E32DR3-DUALR3-ESP32D0WDV3)_ESP32_v1.6.1_user1.bin",
                            "4f14b1298061ee6669fd5fcbfa89baf6c2ef3daafa8c575736f38088179240b2", 7,
                            "sonoff-th-elite", "E32-2SW-P0"),
    **{f"sonoff-m5-{gang}c": Update("SONOFF_M5-1C-3C_(FWSWES-E32T5-T5-ESP32D0WDV3)_ESP32_v1.3.0_user1.bin",
                                    "f149baf000eb226c27babd1ea2a4bef6bcc3ca2973d9c57063106730112f4e6b", 7 + gang,
                                    "sonoff-pow-elite", "ESP32D0-MSW-T5", 159 + gang)
       for gang in (1, 2, 3)},
}


def identities(frame: dict, dump: Dump) -> dict[bytes, bytes]:
    """What of the plug's own eWeLink identity to replace, and with what. The
    two keys are often one value, which then stays one."""
    new = {frame["deviceid"].encode(): dump.deviceid.encode(), frame["apikey"].encode(): dump.apikey.encode()}
    if frame.get("encrypt_key"):
        new.setdefault(frame["encrypt_key"].encode(), dump.apikey.replace("-4000-", "-4001-").encode())
    return new


# Of a donor's settings, what is not its own alone.
KEPT_SPACES = {"frame_params", "nvs.net80211", "phy", "misc", "data_backups", "user_usual", "user_unusual"}


def anonymised(flash: bytes, dump: "Dump | Update", stock_model: str | None = None, uiid: int = 0) -> bytes:
    """The dump with the plug's identity replaced throughout its settings - and,
    given `stock_model`, what it calls itself."""
    partition = flashmap.nvs(flash)
    settings = flash[partition.offset:partition.offset + partition.size]
    names, values = parse(settings)
    frame = next(json.loads(value.data.split(b"\0", 1)[0]) for (space, key), value in values.items()
                 if names[space] == "frame_params" and key == "content")
    replaced = identities(frame, dump)
    if stock_model:
        replaced[frame["model"].encode()] = stock_model.encode()
    if uiid:
        replaced[f'"uiid":{frame["uiid"]}'.encode()] = f'"uiid":{uiid}'.encode()
    rows: list[tuple[str, str, str, str]] = []
    for space in sorted({space for space, _ in values}):
        if stock_model and names[space] not in KEPT_SPACES:
            continue
        rows.append((names[space], "namespace", "", ""))
        for (_, key), value in sorted(item for item in values.items() if item[0][0] == space):
            data = value.data
            for old, new in replaced.items():
                data = data.replace(old, new)
            if names[space] == "nvs.net80211" and key in ("sta.mac", "ap.mac"):
                data = bytes.fromhex(dump.mac.replace(":", ""))
            if value.kind in rewrite_nvs.INTS:
                signed = value.kind & 0x10 != 0
                rows.append((key, "data", rewrite_nvs.INTS[value.kind],
                             str(int.from_bytes(data, "little", signed=signed))))
            elif value.kind == 0x21:
                rows.append((key, "data", "string", data.rstrip(b"\0").decode()))
            else:
                rows.append((key, "data", "hex2bin", data.hex()))
    new = rewrite_nvs.rebuild(rows, partition.size)
    out = bytearray(flash)
    out[partition.offset:partition.offset + partition.size] = new
    if any(old in out for old in replaced):
        # A copy somewhere else in the flash: rather no plug than a real identity.
        for old, new_value in replaced.items():
            if len(old) != len(new_value):  # the model, from the settings: not an identity
                continue
            at = out.find(old)
            while at >= 0:
                out[at:at + len(old)] = new_value
                at = out.find(old, at + 1)
    return bytes(out)


def with_update(donor: bytes, update: Update) -> bytes:
    """The donor plug's flash with the update's firmware in place of its own,
    and started."""
    firmware = fetched(update)
    app = firmware[build_vendor_ota.WRAPPER_SIZE:]
    flash = bytearray(anonymised(donor, update, update.stock_model, update.uiid))
    first, second = flashmap.app_slots(flash)[:2]
    if len(app) > first.size:
        raise SystemExit(f"{update.file} does not fit the donor's slot")
    flash[first.offset:first.offset + first.size] = app.ljust(first.size, b"\xff")
    flash[second.offset:second.offset + second.size] = b"\xff" * second.size
    otadata = next(p for p in flashmap.partitions(flash) if p.type == flashmap.TYPE_DATA and p.subtype == 0)
    flash[otadata.offset:otadata.offset + otadata.size] = b"\xff" * otadata.size
    return bytes(flash)


def make(key: str) -> Path:
    dump = DUMPS.get(key) or UPDATES[key]
    target = plugs.PLUGS / plugs.folder_name(dump.mac)
    if target.exists():
        return target
    if isinstance(dump, Update):
        donor = S60_DUMP if dump.donor == "s60" else DUMPS[dump.donor]
        flash = with_update(fetched(donor), dump)
    else:
        flash = anonymised(fetched(dump), dump)
    chip = flashmap.chip_of(flash)
    if len(flash) != FLASH_SIZE:
        raise SystemExit(f"{dump.url} is not a whole flash")
    backup = target / "sonoff-backup"
    backup.mkdir(parents=True, mode=0o700)
    report = {"note": f"From the public dump {dump.url}, its identity made up."}
    (backup / "flash.bin").write_bytes(flash)
    # An emulated C3 is the revision its firmware needs, or its bootloader stops there.
    (backup / "efuse.bin").write_bytes(flashmap.esp32_efuse(dump.mac) if chip == "esp32" else flashmap.esp32c3_efuse(
        dump.mac, flashmap.min_chip_revision(flash, flashmap.app_slots(flash)[0])))
    (backup / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (backup / "README.txt").write_text(f"{report['note']}\nA test copy, for the emulator.\n", encoding="utf-8")
    (target / "plug.json").write_text(json.dumps({
        "backed_up": time.strftime("%Y-%m-%d"), "ewelink_name": "Emulator test copy",
        "mac": dump.mac, "stock_version": "", "test_copy_of": dump.url,
        "product_model": kinds.find(key).example_product_model if kinds.find(key) else "",
    }, indent=2) + "\n", encoding="utf-8")
    for path in backup.iterdir():
        path.chmod(0o400)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=sorted({*DUMPS, *UPDATES}))
    parser.add_argument("--remove", action="store_true", help="delete the test copy")
    args = parser.parse_args(argv)
    mac = (DUMPS.get(args.kind) or UPDATES[args.kind]).mac
    if args.remove:
        copy_plug.remove(mac)
        print(f"Removed the test copy of {args.kind}.")
        return 0
    print(f"{make(args.kind)}: run it with tools/espressif/emulator/emu.sh --plug {mac}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
