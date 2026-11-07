"""Back to Sonoff's firmware with no backup on this computer (R1): the flash
its backup would have held, put together."""
import json
import unittest
from unittest import mock

from tests.espressif.test_sonoff_identity import FRAME
from tests.autoflash.routes.espressif.test_identity import IDENTITY, MAC, Fixture, settings_of
from tests.autoflash.routes.espressif.test_sonoff_firmware import SONOFF_APP
from tools.autoflash import esphome, plans
from tools.autoflash.errors import NothingWritten
from tools.autoflash.routes.espressif import hub, sonoff, sonoff_firmware
from tools.espressif import sonoff_identity
from tools.ewelink import get_device_key


class RebuiltTests(Fixture):
    """R1 with no backup: Sonoff's firmware, its layout and the identity."""

    def restore(self, ctx):
        with mock.patch.object(sonoff_firmware, "official_app",
                               return_value=(SONOFF_APP, "the file eWeLink offers the device")) as fetched, \
                mock.patch.object(sonoff, "out_of_sonoffs_slot", return_value=1), \
                mock.patch.object(plans, "app_length", return_value=len(SONOFF_APP)), \
                mock.patch.object(hub, "run_plan", return_value={"plan": "x"}) as run:
            result = sonoff.step_sonoff_restore(ctx)
        first, second = (call.args[1] for call in run.call_args_list)
        return result, fetched, first, second

    def test_from_the_identity_the_plug_kept(self):
        flash = bytearray(b"\xff" * 0x400000)
        flash[0x8000:0x9000] = esphome.sonoff_table()
        flash[0x9000:0x19000] = sonoff_identity.nvs(IDENTITY, 0x10000)
        ctx = self.ctx(flash)
        result, fetched, first, second = self.restore(ctx)
        self.assertEqual(fetched.call_args.args[2], IDENTITY)
        self.assertEqual(ctx.state.data["captures"]["R1.rebuilt"]["identity"], "kept")
        self.assertEqual(first.steps[0].address, 0x20000)
        written = settings_of(second.blobs["settings"])
        frame = json.loads(written[("frame_params", "content")])
        self.assertEqual((frame["deviceid"], frame["ssid"], frame["passwd"]), (FRAME["deviceid"], "Home-2G", "pw"))
        self.assertEqual(written[("productId", "productId")], b"24440700000000\r\n")
        self.assertEqual(result["from"], "the Sonoff identity the device kept")

    def test_from_the_sector_when_the_startup_firmware_wiped_the_settings(self):
        # As it does on the way from the mover or ESPHome to the hub; the end of
        # ota_1 is past every image written there since.
        flash = bytearray(b"\xff" * 0x400000)
        flash[0x8000:0x9000] = esphome.sonoff_table()
        flash[0x3FF000:0x400000] = sonoff_identity.to_sector(IDENTITY)
        ctx = self.ctx(flash)
        _, fetched, _, second = self.restore(ctx)
        self.assertEqual(fetched.call_args.args[2], IDENTITY)
        self.assertEqual(ctx.state.data["captures"]["R1.rebuilt"]["identity"], "kept")

    def test_from_the_account_record_when_the_plug_kept_none(self):
        ctx = self.ctx(b"\xff" * 0x400000)
        record = {"deviceid": "10000e32ab", "devicekey": "made-up-key", "params": {"staMac": MAC.upper()},
                  "extra": {"model": "SN-ESP32C3-S60-01", "uiid": 190}}
        ctx.account_devices = lambda: [{"deviceid": "other", "params": {"staMac": "12:34:56:00:00:01"}}, record]
        _, _, _, second = self.restore(ctx)
        frame = json.loads(settings_of(second.blobs["settings"])[("frame_params", "content")])
        self.assertEqual((frame["deviceid"], frame["apikey"], frame["dis_server"]),
                         ("10000e32ab", "made-up-key", get_device_key.dispatch_server("eu")[0]))
        self.assertEqual(ctx.state.data["captures"]["R1.rebuilt"]["identity"], "account")

    def test_with_nothing_to_rebuild_from_nothing_is_written(self):
        with self.assertRaisesRegex(NothingWritten, "no record"):
            self.restore(self.ctx(b"\xff" * 0x400000))

    def test_the_backup_comes_first(self):
        self.back_up()
        ctx = self.ctx(b"\xff" * 0x400000)
        with mock.patch.object(sonoff, "rebuilt", side_effect=AssertionError("not with a backup")):
            result, _, _, second = self.restore(ctx)
        self.assertEqual(result["from"], "backup")


if __name__ == "__main__":
    unittest.main()
