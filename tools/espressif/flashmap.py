"""What a whole-flash dump of an Espressif plug holds, read from the dump itself:
which chip it is for, where its partition table says things are, and how to
read the MAC from its eFuses. No plug's layout is assumed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

TABLE_AT = 0x8000
TABLE_SIZE = 0x1000
ENTRY_SIZE = 32
ENTRY_MAGIC = b"\xaa\x50"
IMAGE_MAGIC = 0xE9
CHIP_ID_AT = 12  # in an ESP image's header

# ESP-IDF's chip ids, and where each chip's ROM looks for the bootloader.
CHIP_IDS = {0: "esp32", 5: "esp32c3"}
BOOTLOADER_AT = {"esp32": 0x1000, "esp32c3": 0x0}
TYPE_APP = 0
TYPE_DATA = 1
SUBTYPE_NVS = 2

# The eFuse files QEMU reads. An ESP32-C3's are the read registers from
# EFUSE_RD_WR_DIS on, 1024 bytes as kept here; a classic ESP32's are its four
# blocks' read registers, 31 words.
ESP32_EFUSE_SIZE = 124
ESP32C3_EFUSE_SIZE = 1024


@dataclass(frozen=True)
class Partition:
    label: str
    type: int
    subtype: int
    offset: int
    size: int


def partitions(flash: bytes) -> list[Partition]:
    """The partition table's entries, in the order it lists them."""
    found = []
    for at in range(TABLE_AT, TABLE_AT + TABLE_SIZE, ENTRY_SIZE):
        entry = flash[at:at + ENTRY_SIZE]
        if entry[:2] != ENTRY_MAGIC:
            break
        offset, size = struct.unpack_from("<II", entry, 4)
        found.append(Partition(entry[12:28].split(b"\0", 1)[0].decode("ascii"), entry[2], entry[3], offset, size))
    if not found:
        raise ValueError("no partition table at 0x8000: an encrypted dump, or not a whole flash")
    return found


def nvs(flash: bytes) -> Partition:
    """The first NVS partition, where the stock firmware keeps its settings."""
    for partition in partitions(flash):
        if partition.type == TYPE_DATA and partition.subtype == SUBTYPE_NVS:
            return partition
    raise ValueError("the partition table has no NVS partition")


def app_slots(flash: bytes) -> list[Partition]:
    """The app partitions."""
    return [partition for partition in partitions(flash) if partition.type == TYPE_APP]


def chip_of(flash: bytes) -> str:
    """The chip a dump is for, by its bootloader's header: "esp32" or "esp32c3"."""
    for chip, at in sorted(BOOTLOADER_AT.items(), key=lambda item: item[1]):
        if flash[at:at + 1] == bytes([IMAGE_MAGIC]):
            found = CHIP_IDS.get(struct.unpack_from("<H", flash, at + CHIP_ID_AT)[0])
            if found != chip:
                raise ValueError(f"the bootloader at 0x{at:x} is not one for {chip}")
            return chip
    raise ValueError("no bootloader at 0x0 or 0x1000: an encrypted dump, or not a whole flash")


def crc8(data: bytes) -> int:
    """The CRC a classic ESP32 keeps its MAC with (espefuse's calc_crc)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def esp32_efuse(mac: str) -> bytes:
    """A classic ESP32's eFuse file with this MAC and nothing else in it."""
    raw = bytes.fromhex(mac.replace(":", ""))
    efuse = bytearray(ESP32_EFUSE_SIZE)
    # BLOCK0's words 1 and 2: the MAC's last four bytes, first byte highest,
    # then its first two and their CRC.
    efuse[4:8] = raw[:1:-1]
    efuse[8:10] = raw[1::-1]
    efuse[10] = crc8(raw)
    return bytes(efuse)


def min_chip_revision(flash: bytes, slot: Partition) -> int:
    """The lowest chip revision the app in a slot says it runs on: its header's byte 14."""
    return flash[slot.offset + 14]


def esp32c3_efuse(mac: str, revision: int = 0) -> bytes:
    """An ESP32-C3's eFuse file with this MAC and chip revision, and nothing else."""
    efuse = bytearray(ESP32C3_EFUSE_SIZE)
    efuse[0x18:0x1E] = bytes.fromhex(mac.replace(":", ""))[::-1]
    # EFUSE_RD_MAC_SPI_SYS_3's WAFER_VERSION, bits 18 to 20.
    efuse[0x24:0x28] = struct.pack("<I", (revision & 7) << 18)
    return bytes(efuse)


def mac_of_efuse(efuse: bytes) -> str:
    """The plug's MAC from its eFuse file, whichever chip's it is."""
    if len(efuse) == ESP32_EFUSE_SIZE:
        raw = efuse[4:8][::-1]
        raw = efuse[8:10][::-1] + raw
    else:
        raw = efuse[0x18:0x1E][::-1]
    return ":".join(f"{byte:02x}" for byte in raw)
