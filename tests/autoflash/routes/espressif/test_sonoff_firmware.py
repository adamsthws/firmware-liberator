"""Sonoff's own firmware for a plug with no backup: the file eWeLink offers
it, else the public one, checked, and the flash made from it."""
import hashlib
import struct
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.espressif.test_dump_stock_flash import esp_image
from tests.espressif.test_sonoff_identity import FRAME
from tests.autoflash.routes.espressif.test_identity import IDENTITY
from tools.autoflash import esphome, kinds, plans
from tools.autoflash.routes.espressif import sonoff_firmware
from tools.espressif import build_vendor_ota, sonoff_identity


def app_image(project, size=0x3000):
    """An ESP32-C3 app, its description naming `project`, long enough to be
    written in two halves."""
    small = esp_image("1.2.0", project)
    data = bytearray(small[32:32 + 256]) + bytes(size)
    header = bytearray(small[:24])
    body = bytes(header) + struct.pack("<II", 0x3C000020, len(data)) + bytes(data)
    checksum = 0xEF
    for value in data:
        checksum ^= value
    padded = body + bytes(((len(body) // 16) + 1) * 16 - 1 - len(body)) + bytes([checksum])
    return padded + hashlib.sha256(padded).digest()


SONOFF_APP = app_image("esp32_fw")


class SonoffFirmwareTests(unittest.TestCase):
    def wrapped(self, app=SONOFF_APP, model=kinds.SONOFF_S60.wrapper_model):
        return build_vendor_ota.build_wrapper(app, version="1.2.0", model=model)

    def test_the_app_is_taken_from_its_update_file_and_checked(self):
        app = sonoff_firmware.app_in_file(self.wrapped(), kinds.SONOFF_S60.wrapper_model)
        self.assertEqual(sonoff_firmware.checked_app(app, kinds.SONOFF_S60, 0x1F0000), SONOFF_APP)
        with self.assertRaisesRegex(sonoff_firmware.FirmwareError, "no firmware called"):
            sonoff_firmware.app_in_file(self.wrapped(), kinds.SONOFF_POW_ELITE.wrapper_model)
        damaged = bytearray(self.wrapped())
        damaged[-40] ^= 1
        with self.assertRaisesRegex(sonoff_firmware.FirmwareError, "not whole"):
            sonoff_firmware.app_in_file(bytes(damaged), kinds.SONOFF_S60.wrapper_model)

    def test_anything_but_sonoffs_firmware_for_the_chip_is_refused(self):
        for app, kind, why in ((app_image("autoflash_bridge"), kinds.SONOFF_S60, "not Sonoff's"),
                               (SONOFF_APP, kinds.SONOFF_POW_ELITE, "not for"),
                               (SONOFF_APP[:-1] + bytes([SONOFF_APP[-1] ^ 1]), kinds.SONOFF_S60, "hash")):
            with self.subTest(why=why), self.assertRaisesRegex(sonoff_firmware.FirmwareError, why):
                sonoff_firmware.checked_app(app, kind, 0x1F0000)

    def test_eWeLink_is_asked_as_the_device_asks(self):
        identity_ = IDENTITY
        ctx = SimpleNamespace(ewelink_session=lambda: ("eu", "token"))
        offer = {"offers": [{"binList": [{"name": "user2.bin", "downloadUrl": "https://example.test/2"},
                                         {"name": "user1.bin", "downloadUrl": "https://example.test/1?x=1"}]}]}
        with mock.patch.object(sonoff_firmware, "latest_offer", return_value=offer) as asked, \
                mock.patch.object(sonoff_firmware, "fetch", return_value=b"file") as fetched:
            self.assertEqual(sonoff_firmware.offered(ctx, identity_), b"file")
        self.assertEqual(asked.call_args.args[2], {"deviceid": FRAME["deviceid"], "model": FRAME["model"],
                                                   "version": "0.0.1"})
        self.assertEqual(fetched.call_args.args, ("https://example.test/1?x=1",
                                                  {"deviceid": FRAME["deviceid"], "ts": 1, "sign": 0}))

    def test_else_the_newest_public_copy_for_the_device(self):
        listed = ["SONOFF_S60TPG-UK_(FWSW-E32S60-S60-ESP32C3FN4)_ESP32-C3_v1.1.0_user1.bin",
                  "SONOFF_S60TPG-UK_(FWSW-E32S60-S60-ESP32C3FN4)_ESP32-C3_v1.2.0_user1.bin",
                  "SONOFF_POWR316D_(FWSW-E32POWP-POWPRO-ESP32D0WDV3)_ESP32_v2.0.0_user1.bin"]
        with mock.patch.object(sonoff_firmware.firmware_name, "public_files", return_value=listed), \
                mock.patch.object(sonoff_firmware, "fetch", return_value=b"file") as fetched:
            self.assertEqual(sonoff_firmware.public(kinds.SONOFF_S60), b"file")
        self.assertTrue(fetched.call_args.args[0].endswith("ESP32-C3_v1.2.0_user1.bin"))
        self.assertIn(sonoff_firmware.firmware_name.PUBLIC_COMMIT, fetched.call_args.args[0])

    def test_the_stub_is_all_a_run_against_it_reaches(self):
        ctx = SimpleNamespace(ewelink_session=lambda: None)
        layout = esphome.layout_of(kinds.SONOFF_S60)
        with mock.patch.dict("os.environ", {"AUTOFLASH_EWELINK_API_BASE": "https://apia.example.test"}), \
                mock.patch.object(sonoff_firmware, "public", side_effect=AssertionError("not reached")):
            with self.assertRaisesRegex(sonoff_firmware.FirmwareError, "could not be fetched"):
                sonoff_firmware.official_app(ctx, kinds.SONOFF_S60, IDENTITY, layout)
        with mock.patch.dict("os.environ", {"AUTOFLASH_EWELINK_API_BASE": ""}), \
                mock.patch.object(sonoff_firmware, "public", return_value=self.wrapped()):
            app, where = sonoff_firmware.official_app(ctx, kinds.SONOFF_S60, IDENTITY, layout)
        self.assertEqual((app, where), (SONOFF_APP, "the public copy of Sonoff's update file (FlashDumps)"))

    def test_what_a_backup_would_hold_is_written_as_one(self):
        layout = esphome.layout_of(kinds.SONOFF_S60)
        settings = sonoff_identity.nvs(IDENTITY, 0x10000)
        flash = sonoff_firmware.flash_of(esphome.sonoff_table(), settings, SONOFF_APP, layout)
        with mock.patch.object(plans, "app_length", return_value=len(SONOFF_APP)):
            first, second = plans.back_to_sonoff(flash, running=1, layout=layout)
        self.assertEqual(plans.stock_slot(flash, layout), 0)
        self.assertEqual(second.blobs["settings"][:len(settings)], settings)


if __name__ == "__main__":
    unittest.main()
