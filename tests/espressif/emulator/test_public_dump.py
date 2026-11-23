"""A public dump's identity never survives into the emulator's copy of it."""

from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import kinds
from tools.espressif import ais_records, flashmap, layouts
from tools.espressif.emulator import appimage, public_dump

DUMP = public_dump.Dump("d.bin", "0" * 64, 0xAB)
BASICR4 = public_dump.UPDATES["sonoff-basicr4"]


def entry(label: bytes, kind: int, subtype: int, offset: int, size: int) -> bytes:
    return b"\xaa\x50" + bytes([kind, subtype]) + struct.pack("<II", offset, size) + label.ljust(16, b"\0") + bytes(4)


def s60_like() -> tuple[bytearray, str]:
    """A flash laid out as the S60's, its bootloader a stand-in holding the two
    instructions that say where the table is, where the S60's has them; and
    that bootloader's hash."""
    flash = bytearray(b"\xff" * 0x30000)
    size = 0x3200
    header = bytes([0xE9, 1, 2, 0x20]) + bytes(8) + struct.pack("<H", 5) + bytes(9) + b"\x01"
    flash[:24] = header
    flash[24:32] = struct.pack("<II", 0x403D0000 - 0x2000, size)
    flash[32:32 + size] = bytes(size)
    for at, register in public_dump.S60_BOOTLOADER_TABLE_AT:
        flash[at:at + 2] = public_dump.c_lui(register, 0x8)
    boot = bytearray(flash[:appimage.parse(flash).length])
    appimage.reseal(boot, appimage.parse(boot))
    flash[:len(boot)] = boot
    entries = (entry(b"nvs", 1, 2, 0x9000, 0x10000) + entry(b"reserve", 1, 2, 0x19000, 0x4000)
               + entry(b"otadata", 1, 0, 0x1D000, 0x2000) + entry(b"ota_0", 0, 0x10, 0x20000, 0x10000))
    flash[0x8000:0x8000 + len(entries)] = entries
    flash[0x9000:0x9000 + 0x3000] = b"\x5a" * 0x3000  # the settings, at the front as the generator writes them
    return flash, hashlib.sha256(boot).hexdigest()


class Relaid(unittest.TestCase):
    def relaid(self, flash: bytearray, sha256: str, table: public_dump.Table = public_dump.BASICR4_TABLE) -> bytes:
        with mock.patch.object(public_dump, "S60_BOOTLOADER_SHA256", sha256):
            return public_dump.relaid(bytes(flash), table)

    def test_the_bootloader_reads_it_where_it_went_and_still_starts(self):
        out = self.relaid(*s60_like())
        for at, register in public_dump.S60_BOOTLOADER_TABLE_AT:
            self.assertEqual(out[at:at + 2], public_dump.c_lui(register, 0xC))
        self.assertTrue(appimage.verify(out, appimage.parse(out)))

    def test_it_has_a_real_basicr4s_table_and_the_s60s_settings_in_its_nvs(self):
        flash, sha256 = s60_like()
        flash = flash.ljust(public_dump.FLASH_SIZE, b"\x5a")  # the S60's apps, which do not stay
        out = self.relaid(flash, sha256)
        self.assertEqual(len(out), public_dump.FLASH_SIZE)
        self.assertEqual(flashmap.table_at(out), 0xC000)
        self.assertEqual(out[0x8000:0xC000], b"\xff" * 0x4000)
        self.assertEqual([(p.label, p.type, p.subtype, p.offset, p.size) for p in flashmap.partitions(out)],
                         [row[:5] for row in public_dump.BASICR4_TABLE.partitions])
        self.assertEqual(flashmap.nvs(out).offset, 0x1C000)
        self.assertEqual(out[0x1C000:0x1F000], b"\x5a" * 0x3000)
        self.assertEqual(out[0x2C000:0x2E000], flash[0x1D000:0x1F000])  # otadata
        self.assertEqual(out[0x3F0000:0x3F6000], public_dump.BASICR4_FCTRY)  # made up
        for label in ("nvs_matter", "nvs_matter_bkp", "ota_0"):
            partition = next(p for p in flashmap.partitions(out) if p.label == label)
            self.assertEqual(out[partition.offset:partition.offset + partition.size].count(0xFF), partition.size)

    def test_its_table_is_the_published_one_byte_for_byte(self):
        # A real one's table is checked against it before anything is written,
        # so a stand-in that differs (esp_secure_cert's encrypted flag, say)
        # would let the emulator pass what a real device refuses.
        out = self.relaid(*s60_like())
        self.assertEqual(out[0xC000:0xD000], layouts.SONOFF_BASICR4.table)

    def test_its_checksum_row_is_esp_idfs(self):
        table = self.relaid(*s60_like())[0xC000:0xD000]
        entries = table[:len(public_dump.BASICR4_TABLE.partitions) * flashmap.ENTRY_SIZE]
        self.assertEqual(table[len(entries):len(entries) + 32], b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(entries).digest())

    def test_settings_that_would_not_fit_stop_it(self):
        flash, sha256 = s60_like()
        small = public_dump.Table(0xC000, (("nvs", 1, 2, 0x10000, 0x2000, 0),))
        with self.assertRaisesRegex(SystemExit, "does not fit"):
            self.relaid(flash, sha256, small)

    def test_another_bootloader_stops_it(self):
        flash, _ = s60_like()
        with self.assertRaisesRegex(SystemExit, "not the S60's"):
            self.relaid(flash, "0" * 64)

    def test_compressed_lui_is_encoded_as_the_s60s_bootloader_has_it(self):
        self.assertEqual(public_dump.c_lui(10, 0x8), bytes.fromhex("2165"))  # c.lui a0,0x8
        self.assertEqual(public_dump.c_lui(13, 0x8), bytes.fromhex("a166"))  # c.lui a3,0x8


class FactoryRecord(unittest.TestCase):
    def test_it_holds_the_test_copys_made_up_identity_and_model(self):
        record = public_dump.factory_record(BASICR4)
        field = lambda name: ais_records.text(record, ais_records.FACTORY_FIELDS, name).decode()  # noqa: E731
        self.assertTrue(ais_records.is_factory(record))
        self.assertEqual(field("device_model"), "SN-ESP32C3-BASICR4-01")  # must be its own model
        self.assertEqual((field("deviceid"), field("factory_apikey")), (BASICR4.deviceid, BASICR4.apikey))
        self.assertEqual(struct.unpack_from("<H", record, ais_records.FACTORY_UIID)[0], 138)

    def test_it_is_tied_to_the_emulated_chip(self):
        record = public_dump.factory_record(BASICR4)
        field = lambda name: ais_records.text(record, ais_records.FACTORY_FIELDS, name)  # noqa: E731
        self.assertEqual((field("chip_mac"), field("sta_mac"), field("sap_mac")),
                         (b"123456E32D05", b"12:34:56:E3:2D:05", b"12:34:56:E3:2D:06"))


class Identity(unittest.TestCase):
    def test_every_identifier_is_given_a_made_up_one_of_its_length(self):
        frame = {"deviceid": "1000000001", "apikey": "a" * 36, "encrypt_key": "b" * 36}
        new = public_dump.identities(frame, DUMP)
        self.assertEqual(set(new), {b"1000000001", b"a" * 36, b"b" * 36})
        for old, made_up in new.items():
            self.assertEqual(len(old), len(made_up))

    def test_keys_that_are_one_value_stay_one(self):
        new = public_dump.identities({"deviceid": "1000000001", "apikey": "a" * 36, "encrypt_key": "a" * 36}, DUMP)
        self.assertEqual(new[b"a" * 36], DUMP.apikey.encode())

    def test_a_copy_outside_the_settings_goes_too(self):
        flash = bytearray(b"\xff" * 0x20000)
        real = b"1000000001"
        flash[0x18000:0x18000 + len(real)] = real
        rows = []
        with mock.patch.object(public_dump.flashmap, "nvs", return_value=mock.Mock(offset=0x9000, size=0x3000)), \
                mock.patch.object(public_dump, "parse", return_value=({1: "frame_params"}, {
                    (1, "content"): mock.Mock(kind=0x48, data=b'{"deviceid":"1000000001","apikey":"' + b"a" * 36 + b'"}\0')})), \
                mock.patch.object(public_dump.rewrite_nvs, "rebuild", side_effect=lambda r, size: rows.extend(r) or b"\xff" * size):
            out = public_dump.anonymised(bytes(flash), DUMP)
        self.assertNotIn(real, out)
        self.assertNotIn(real.hex(), str(rows))
        self.assertIn(DUMP.deviceid.encode(), out)


def donor_rows(update: public_dump.Update) -> dict[tuple[str, str], str]:
    """The settings `update`'s test copy is given on a donor: (space, key) to value."""
    names = {1: "frame_params", 2: "user_usual", 3: "user_unusual"}
    values = {(1, "content"): mock.Mock(kind=0x48, data=b'{"uiid":181,"deviceid":"1000000001","apikey":"'
                                        + b"a" * 36 + b'","model":"DONOR"}'),
              (2, "content"): mock.Mock(kind=0x48, data=b'{"init":"true","startup0":2,"chl0state":2}'),
              (3, "content"): mock.Mock(kind=0x48, data=b'{"init":"true","finalUserApikey":"' + b"c" * 36 + b'"}')}
    rows: list[tuple[str, str, str, str]] = []
    with mock.patch.object(public_dump.flashmap, "nvs", return_value=mock.Mock(offset=0x9000, size=0x3000)), \
            mock.patch.object(public_dump, "parse", return_value=(names, values)), \
            mock.patch.object(public_dump.rewrite_nvs, "rebuild", side_effect=lambda r, size: rows.extend(r) or b"\xff" * size):
        public_dump.anonymised(b"\xff" * 0x20000, update, update.stock_model, update.uiid, update.settings)
    # rebuild writes each row as a line of CSV, unquoted: a comma would cut it short.
    assert not any("," in field for row in rows for field in row), rows
    space, out = "", {}
    for key, kind, encoding, value in rows:
        if kind == "namespace":
            space = key
        else:
            out[space, key] = bytes.fromhex(value).decode() if encoding == "hex2bin" else value
    return out


class OwnSettings(unittest.TestCase):
    def test_the_account_it_was_paired_to_is_the_stubs(self):
        # The stub's account's apikey is the device's own; another, the DualR3's
        # firmware takes for a new owner, and puts its settings back to defaults.
        update = public_dump.Update("u.bin", "0" * 64, 0xAC, "donor", "OWN")
        rows = donor_rows(update)
        self.assertEqual(json.loads(rows["user_unusual", "content"])["finalUserApikey"], update.apikey)

    def test_a_kind_with_its_own_settings_has_them_in_place_of_the_donors(self):
        rows = donor_rows(public_dump.Update("u.bin", "0" * 64, 0xAC, "donor", "OWN", 5, '{"init":"true","x":1}'))
        self.assertEqual(rows["user_usual", "content"], '{"init":"true","x":1}')
        self.assertIn('"uiid":5', rows["frame_params", "content"])

    def test_without_them_the_donors_stay(self):
        rows = donor_rows(public_dump.Update("u.bin", "0" * 64, 0xAC, "donor", "OWN"))
        self.assertIn('"chl0state":2', rows["user_usual", "content"])
        self.assertIn('"uiid":181', rows["frame_params", "content"])

    def test_the_dualr3_starts_as_a_dualr3_in_relay_mode(self):
        # Its firmware runs as a motor (deviceMode 2) when it finds no
        # deviceMode, as on the TH Elite's settings, and refuses every switch.
        dualr3 = public_dump.UPDATES["sonoff-dualr3"]
        rows = donor_rows(dualr3)
        self.assertEqual(json.loads(rows["user_usual", "content"]).get("deviceMode"), 1)
        self.assertEqual(json.loads(rows["frame_params", "content"])["uiid"], 126)

    def test_the_dualr3_has_a_second_copy_that_drives_a_motor(self):
        # Sonoff's app's motor mode, beside the copy in relay mode, under a MAC of its own.
        relays, motor = public_dump.recipe("sonoff-dualr3"), public_dump.recipe("sonoff-dualr3", "motor")
        self.assertEqual(json.loads(donor_rows(motor)["user_usual", "content"]).get("deviceMode"), 2)
        self.assertNotEqual(motor.mac, relays.mac)
        self.assertEqual((motor.file, motor.uiid), (relays.file, relays.uiid))
        macs = [dump.mac for dump in (*public_dump.DUMPS.values(), *public_dump.UPDATES.values())]
        self.assertNotIn(motor.mac, macs)
        self.assertEqual(set(public_dump.MOTOR_COPIES), {kind.key for kind in kinds.EVERY if kind.motor_mode})

    def test_and_a_third_whose_motor_is_calibrated_and_reversed(self):
        # As Sonoff's firmware keeps a motor it timed (calibrate_time in ms)
        # with its outputs reversed in the app, under a MAC of its own again.
        calibrated = public_dump.recipe("sonoff-dualr3", "calibrated")
        settings = json.loads(donor_rows(calibrated)["user_usual", "content"])
        self.assertEqual((settings["deviceMode"], settings["motorCalibrate"], settings["calibrate_time"],
                          settings["motorDriverReverse"]), (2, True, 8000, True))
        # Its wall switches hold to run and swapped, unlike Sonoff's default.
        self.assertEqual((settings["motorSwMode"], settings["motorKeyReverse"]), (3, True))
        self.assertNotIn(calibrated.mac, {public_dump.recipe("sonoff-dualr3", "motor").mac,
                                          public_dump.recipe("sonoff-dualr3").mac})

    def test_and_a_fourth_whose_wall_switches_are_each_one_push_button(self):
        one_button = public_dump.recipe("sonoff-dualr3", "one-button")
        settings = json.loads(donor_rows(one_button)["user_usual", "content"])
        self.assertEqual((settings["deviceMode"], settings["motorSwMode"]), (2, 1))
        macs = {public_dump.recipe("sonoff-dualr3", copy).mac for copy in ("motor", "calibrated")}
        self.assertNotIn(one_button.mac, macs | {public_dump.recipe("sonoff-dualr3").mac})



class Make(unittest.TestCase):
    """A test copy is what the recipe makes now, never one an earlier one made."""

    def setUp(self):
        devices = tempfile.TemporaryDirectory()
        self.addCleanup(devices.cleanup)
        patcher = mock.patch.object(public_dump.records, "DEVICES", Path(devices.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.flash = b"\x11" * public_dump.FLASH_SIZE

    def make(self) -> Path:
        with mock.patch.object(public_dump, "built", return_value=(self.flash, b"efuse")), \
                mock.patch("sys.stderr"):
            return public_dump.make("sonoff-basicr4")

    def flash_of(self, target: Path) -> bytes:
        return (target / "sonoff-backup" / "flash.bin").read_bytes()

    def test_one_as_the_recipe_makes_it_is_kept(self):
        target = self.make()
        made = (target / "device.json").stat().st_mtime_ns
        self.assertEqual(self.make(), target)
        self.assertEqual((target / "device.json").stat().st_mtime_ns, made)

    def test_one_an_earlier_recipe_made_is_made_again(self):
        target = self.make()
        self.flash = b"\x22" * public_dump.FLASH_SIZE
        self.make()
        self.assertEqual(self.flash_of(target), self.flash)
        self.assertEqual(json.loads((target / "device.json").read_text(encoding="utf-8"))["test_copy_of"], BASICR4.url)

    def test_a_real_devices_backup_in_its_place_is_left_alone(self):
        target = public_dump.records.DEVICES / public_dump.records.folder_name(BASICR4.mac)
        (target / "sonoff-backup").mkdir(parents=True)
        (target / "sonoff-backup" / "flash.bin").write_bytes(b"stock")
        (target / "device.json").write_text(json.dumps({"mac": BASICR4.mac}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.make()
        self.assertEqual(self.flash_of(target), b"stock")


if __name__ == "__main__":
    unittest.main()
