"""The plug's Sonoff identity through the routes: kept at K1 on the way to
Tasmota, carried into the next firmware's settings at L1, and found again at
R1, where with no backup it and Sonoff's official firmware put the plug back."""
import hashlib
import json
import re
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import device as device_module, esphome, kinds, output, plans, plugs, sonoff_firmware
from tools.autoflash.errors import NothingWritten
from tools.autoflash.routes import espressif
from tools.autoflash.routes.espressif import hub, identity, mover, sonoff
from tools.autoflash.state import DONE, RunState
from tools.espressif import build_vendor_ota, nvs_image, sonoff_identity
from tools.espressif.inspect_stock_nvs import parse
from tools.espressif.safeboot.safeboot_migration import OFFICIAL_TABLE_SHA256, TARGET_TABLE_SHA256
from tools.ewelink import get_device_key

from tests.autoflash.test_plans import stock_flash
from tests.espressif.test_dump_stock_flash import esp_image
from tests.espressif.test_sonoff_identity import FRAME, sonoff_nvs

MAC = "12:34:56:ab:cd:ef"
IDENTITY = sonoff_identity.from_nvs(sonoff_nvs())


class FakePlug:
    """A plug's flash, read as the tool reads it through Berry."""

    def __init__(self, flash: bytes):
        self.flash = bytearray(flash)
        self.reads = []

    def read_region(self, address, length, timeout=30.0):
        self.reads.append((address, length))
        return bytes(self.flash[address:address + length])

    read_nvs = device_module.Device.read_nvs

    def sha_region(self, address, length, timeout=180.0):
        return plans.sha256(bytes(self.flash[address:address + length]))


def on_tasmota(table=None, sector=None):
    flash = bytearray(b"\xff" * 0x400000)
    flash[0x8000:0x9000] = table or bytes(0x1000)
    if sector:
        flash[sonoff_identity.SECTOR_AT:sonoff_identity.SECTOR_AT + len(sector)] = sector
    return flash


def settings_of(nvs: bytes) -> dict:
    names, values = parse(nvs)
    return {(names[space], key): value.data[:value.size] for (space, key), value in values.items()}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        for patch in (mock.patch.object(plugs, "PLUGS", self.tmp / "plugs"),
                      mock.patch.object(output, "say"), mock.patch.object(output, "tell"),
                      mock.patch.object(output, "log_only")):
            patch.start()
            self.addCleanup(patch.stop)

    def ctx(self, flash, **inputs):
        state = RunState.open(self.tmp / "run")
        for key, value in {"plug_kind": "sonoff-s60", "plug_mac": MAC, **inputs}.items():
            state.set_input(key, value)
        return SimpleNamespace(state=state, device=FakePlug(flash), dry_run=False,
                               wifi=lambda: ("Home-2G", "pw"), account_devices=lambda: [],
                               ewelink_region=lambda: "eu", ewelink_session=lambda: None)

    def back_up(self, nvs=None):
        directory = plugs.backup_dir(MAC)
        directory.mkdir(parents=True)
        flash, _ = stock_flash()
        flash = bytearray(flash)
        flash[0x9000:0x19000] = nvs or sonoff_nvs()
        (directory / "flash.bin").write_bytes(bytes(flash))
        (directory / "efuse.bin").write_bytes(b"\0" * 1024)


class DeviceReadTests(unittest.TestCase):
    def test_flash_is_read_in_pieces_as_hex(self):
        flash = bytes(range(256)) * 64
        asked = []

        def berry_value(code, name, timeout=0):
            address, size = map(int, re.search(r"flash\.read\((\d+),(\d+)\)", code).groups())
            asked.append(size)
            return '"' + flash[address:address + size].hex() + '"'

        plug = device_module.Device("192.0.2.50")
        with mock.patch.object(plug, "berry_value", side_effect=berry_value):
            self.assertEqual(plug.read_region(100, 2000), flash[100:2100])
        self.assertEqual(asked, [512, 512, 512, 464])
        with mock.patch.object(plug, "berry_value", return_value="unknown"):
            with self.assertRaises(device_module.DeviceError):
                plug.read_region(0, 4)

    def test_an_nvs_is_read_but_for_its_unused_pages(self):
        plug = FakePlug(bytes(0x9000) + sonoff_nvs())
        self.assertEqual(plug.read_nvs(0x9000, 0x10000), sonoff_nvs())
        self.assertEqual(sum(1 for _, length in plug.reads if length == 0x1000), 1)


class KeptOnTasmotaTests(Fixture):
    def test_found_by_what_the_sector_says_it_is(self):
        flash = on_tasmota(sector=sonoff_identity.to_sector(IDENTITY))
        flash[0x8000:0x9000] = b"\x01" * 0x1000
        ctx = self.ctx(flash)
        with mock.patch.object(ctx.device, "sha_region", return_value=TARGET_TABLE_SHA256):
            self.assertEqual(identity.kept_in_sector(ctx), IDENTITY)
        output.say.assert_not_called()
        # Its table's entry gone with Tasmota written over it whole: said, and found all the same.
        with mock.patch.object(ctx.device, "sha_region", return_value=OFFICIAL_TABLE_SHA256):
            self.assertEqual(identity.kept_in_sector(ctx), IDENTITY)
        self.assertIn("no longer has the entry", output.say.call_args_list[0].args[0])

    def test_no_sector_or_a_damaged_one_is_none(self):
        self.assertIsNone(identity.kept_in_sector(self.ctx(on_tasmota())))
        sector = bytearray(sonoff_identity.to_sector(IDENTITY))
        sector[40] ^= 1
        self.assertIsNone(identity.kept_in_sector(self.ctx(on_tasmota(sector=bytes(sector)))))
        # Only as much as the record is long is read.
        ctx = self.ctx(on_tasmota(sector=sonoff_identity.to_sector(IDENTITY)))
        identity.kept_in_sector(ctx)
        self.assertLess(sum(length for _, length in ctx.device.reads), 1000)


class KeepIdentityTests(Fixture):
    """K1: from the backup on this computer, else the plug's own settings."""

    def run_k1(self, ctx):
        with mock.patch.object(hub, "sonoff_layout_back"), \
                mock.patch.object(hub, "run_plan", return_value={"plan": "keep_identity"}) as run:
            result = identity.step_keep_identity(ctx)
        return result, (run.call_args.args[1] if run.called else None)

    def test_from_the_backup(self):
        self.back_up()
        result, plan = self.run_k1(self.ctx(b"\xff" * 0x400000))
        self.assertEqual(result["from"], "its Sonoff backup on this computer")
        self.assertEqual(plan.steps, [plans.Write("identity", sonoff_identity.SECTOR_AT, whole=True)])
        self.assertEqual(sonoff_identity.from_sector(plan.blobs["identity"]), IDENTITY)

    def test_from_the_plugs_own_settings_with_no_backup(self):
        flash, _ = stock_flash()
        flash = bytearray(flash)
        flash[0x9000:0x19000] = sonoff_nvs()
        result, plan = self.run_k1(self.ctx(flash))
        self.assertEqual(result["from"], "its own settings")
        self.assertEqual(sonoff_identity.from_sector(plan.blobs["identity"]), IDENTITY)

    def test_with_neither_nothing_is_written(self):
        result, plan = self.run_k1(self.ctx(b"\xff" * 0x400000))
        self.assertEqual((result, plan), ({"kept": False}, None))

    def test_a_run_past_its_commit_leaves_it_out(self):
        ctx = self.ctx(b"\xff" * 0x400000)
        ctx.state.mark("E3", DONE)
        self.assertIn("skipped", self.run_k1(ctx)[0])

    def test_it_comes_first_on_the_way_to_tasmota(self):
        steps = [step for step, _, _ in espressif.steps_for("tasmota", "sonoff")]
        self.assertEqual(steps[steps.index("D5") + 1:steps.index("E1")], ["K1"])
        self.assertIn("K1", espressif.RETRY_STEPS)


class CarriedOffTasmotaTests(Fixture):
    """L1: the settings of the next firmware hold the identity."""

    def test_the_kept_identity_goes_into_sonoffs_settings(self):
        ctx = self.ctx(on_tasmota(sector=sonoff_identity.to_sector(IDENTITY)))
        settings = mover.settings_to_carry(ctx, esphome.layout_of(kinds.SONOFF_S60))
        self.assertEqual(sonoff_identity.from_nvs(settings + b"\xff" * (0x10000 - len(settings))), IDENTITY)
        self.assertNotIn(b"Home-2G", settings)  # nothing of the Wi-Fi
        self.assertEqual(len(settings) % nvs_image.PAGE, 0)

    def test_a_plug_converted_before_carries_its_backups(self):
        self.back_up()
        settings = mover.settings_to_carry(self.ctx(on_tasmota()), esphome.layout_of(kinds.SONOFF_S60))
        self.assertEqual(sonoff_identity.from_nvs(settings + b"\xff" * (0x10000 - len(settings))), IDENTITY)

    def test_with_neither_the_region_is_cleared_as_before(self):
        self.assertIsNone(mover.settings_to_carry(self.ctx(on_tasmota()), esphome.layout_of(kinds.SONOFF_S60)))


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


class DispatchTests(unittest.TestCase):
    def test_a_rebuilt_plug_asks_its_regions_dispatch_server(self):
        with mock.patch.object(get_device_key, "_STUB_API_BASE", ""):
            self.assertEqual(get_device_key.dispatch_server("eu"), ("eu-dispd.coolkit.cc", 443))
            self.assertEqual(get_device_key.dispatch_server("cn"), ("cn-dispd.coolkit.cn", 443))
        with mock.patch.object(get_device_key, "_STUB_API_BASE", "https://apia.autoflash-stub.test"):
            self.assertEqual(get_device_key.dispatch_server("eu"), ("disp.autoflash-stub.test", 443))


if __name__ == "__main__":
    unittest.main()
