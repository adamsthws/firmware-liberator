"""What a backup says about a plug, for the run's log: enough to add a device
the tool has not met from one person's report. None of it is secret: no Wi-Fi,
account or identity is read, and the MAC is left out.
"""

from __future__ import annotations

import re
import struct

from tools.espressif import flashmap

APP_DESC_MAGIC = 0xABCD5432
FIRMWARE_NAME = re.compile(rb"FW[A-Z]+-[A-Z0-9]+-[A-Z0-9-]+")


def _text(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("ascii", errors="replace")


def _word(efuse: bytes, index: int) -> int:
    return struct.unpack_from("<I", efuse, index * 4)[0]


def security(efuse: bytes) -> dict[str, object]:
    """Chip revision, secure boot and flash encryption, from the eFuse file:
    for a classic ESP32 the revision is only known as at least this, as telling
    2 from 3 takes a register no file holds."""
    if len(efuse) == flashmap.ESP32_EFUSE_SIZE:
        crypt_count = (_word(efuse, 0) >> 20) & 0x7F
        rev1, rev2 = (_word(efuse, 3) >> 15) & 1, (_word(efuse, 5) >> 20) & 1
        return {"revision": f"at least {2 if rev1 and rev2 else rev1}",
                "secure_boot": bool(_word(efuse, 6) & 0x30),
                "flash_encryption": bin(crypt_count).count("1") % 2 == 1}
    crypt_count = (_word(efuse, 2) >> 18) & 7
    return {"revision": str((_word(efuse, 9) >> 18) & 7),
            "secure_boot": bool((_word(efuse, 3) >> 20) & 1),
            "flash_encryption": bin(crypt_count).count("1") % 2 == 1}


def _app(flash: bytes, slot: flashmap.Partition) -> str:
    app = flash[slot.offset:slot.offset + slot.size]
    if app[:1] != bytes([flashmap.IMAGE_MAGIC]):
        return "empty"
    described = ""
    if struct.unpack_from("<I", app, 32)[0] == APP_DESC_MAGIC:
        described = (f"{_text(app[80:112])} version {_text(app[48:80])}, "
                     f"ESP-IDF {_text(app[144:176])}")
    names = sorted({name.decode() for name in FIRMWARE_NAME.findall(app)})
    return f"{described or 'no description'}; firmware names: {', '.join(names) or 'none found'}"


def lines(flash: bytes, efuse: bytes | None) -> list[str]:
    """The facts, a line each, for the log."""
    found = [f"Flash size: {len(flash) // 0x100000} MB"]
    try:
        found.insert(0, f"Chip: {flashmap.chip_of(flash)}")
    except ValueError as exc:
        found.append(f"Chip: not known ({exc})")
    if efuse:
        fuses = security(efuse)
        found.append(f"Chip revision: {fuses['revision']}")
        found.append(f"Secure boot: {'on' if fuses['secure_boot'] else 'off'}")
        found.append(f"Flash encryption: {'on' if fuses['flash_encryption'] else 'off'}")
    try:
        for part in flashmap.partitions(flash):
            found.append(f"Partition: {part.label} type {part.type:#04x}/{part.subtype:#04x} "
                         f"at {part.offset:#x}, {part.size:#x} bytes")
        for slot in flashmap.app_slots(flash):
            found.append(f"App in {slot.label}: {_app(flash, slot)}")
    except ValueError as exc:
        found.append(f"Partitions: not readable ({exc})")
    return found
