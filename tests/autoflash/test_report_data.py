"""report_data: a run's report as the API takes it, and nothing private in it."""

from __future__ import annotations

import json
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tools.autoflash import report_data
from tools.repo import report_schema

REPORT_ID = "0123456789abcdef0123456789abcdef"
MAC = "12:34:56:ab:cd:ef"


class PayloadTests(RouteTestCase):
    def payload(self, ctx, ending="converted", hardware=None, failure=""):
        ctx.last_failure = failure
        ctx.last_failed_step = "D4" if failure else ""
        data = report_data.payload(ctx, REPORT_ID, 512.4, False, ending, hardware)
        problems = report_schema.validate(report_schema.current(), {**data, "proof": "1794000000:1"})
        self.assertEqual(problems, [], data)
        return data

    def test_a_converted_run_fits_the_schema(self):
        ctx = self.ctx(firmware="tasmota", plug_kind="sonoff-s60")
        data = self.payload(ctx, hardware={"Relay": "switches (meter confirms)"})
        self.assertEqual((data["result"], data["stopped"], data["checks"]), ("converted", None, "passed"))
        self.assertEqual((data["device"], data["model"], data["stock"]), ("sonoff-s60", "S60TPF", "1.2.0"))
        self.assertEqual(data["seconds"], 512)

    def test_nothing_private_goes_in_it(self):
        ctx = self.ctx(firmware="tasmota", plug_kind="sonoff-s60", plug_mac=MAC, email="me@example.com",
                       device_name="kitchen")
        sent = json.dumps(self.payload(ctx, "failed",
                                       failure="nothing answers at 192.0.2.50 (12:34:56:ab:cd:ef): check it"))
        for private in ("192.0.2.50", MAC, MAC.replace(":", ""), "me@example.com", "kitchen"):
            self.assertNotIn(private.lower(), sent.lower(), private)
        self.assertIn(report_data.plug_hash(MAC), sent)

    def test_a_failed_run_says_where_and_what_kind_in_plain_words(self):
        ctx = self.ctx(firmware="esphome", plug_kind="sonoff-s60")
        data = self.payload(ctx, "failed", failure="the device at 192.0.2.50 did not answer within 30s — café")
        self.assertEqual(data["stopped"]["step"], "D4")
        self.assertEqual(data["stopped"]["error"], "device-no-answer")
        self.assertEqual(data["stopped"]["text"], "the device at <address> did not answer within 30s  caf")
        self.assertEqual(self.payload(ctx, "crashed", failure="KeyError: x")["stopped"]["error"], "crash")

    def test_a_plug_put_back_says_so_and_where_it_came_from(self):
        ctx = self.ctx(firmware="esphome", source="midway", plug_kind="sonoff-s60",
                       put_back={"came_from": "esphome", "why": "no identity"})
        data = self.payload(ctx)
        self.assertEqual((data["result"], data["from"]), ("put-back", "esphome"))

    def test_an_unknown_device_is_named_by_its_model_alone(self):
        ctx = self.ctx(product_model="NEWTHING", stock_version="not a version")
        data = self.payload(ctx, "failed", failure="the device is a NEWTHING, which this tool does not convert yet")
        self.assertEqual((data["device"], data["family"], data["model"], data["stock"]),
                         (None, None, "NEWTHING", None))
        self.assertEqual(data["stopped"]["error"], "unsupported-device")

    def test_dev_runs_are_tagged(self):
        ctx = self.ctx(firmware="tasmota", plug_kind="sonoff-s60")
        with mock.patch.dict("os.environ", {"AUTOFLASH_REPORT_TAG": "dev"}):
            self.assertEqual(self.payload(ctx)["tag"], "dev")
        with mock.patch.dict("os.environ", {"AUTOFLASH_REPORT_TAG": "anything else"}):
            self.assertEqual(self.payload(ctx)["tag"], "user")


class WordTests(RouteTestCase):
    def test_a_plug_is_the_same_hash_however_its_mac_is_written(self):
        expected = report_data.plug_hash(MAC)
        self.assertRegex(expected, r"^[0-9a-f]{64}$")
        for written in ("12-34-56-AB-CD-EF", "123456abcdef", " 12:34:56:AB:CD:EF "):
            self.assertEqual(report_data.plug_hash(written), expected, written)
        for nothing in (None, "", "12:34:56", "not a mac"):
            self.assertIsNone(report_data.plug_hash(nothing))

    def test_the_meter_in_a_word(self):
        for firmware, captures, word in (
                ("sonoff", {}, "sonoff"),
                ("tasmota", {"F4.meter": {"changed": True, "factory": True}}, "carried-factory"),
                ("tasmota", {"F4.meter": {"changed": False, "factory": False}}, "carried"),
                ("tasmota", {"F4.meter": {"skipped": "the meter did not report"}}, "skipped"),
                ("esphome", {"U2.meter": "stock NVS, factor 1.02"}, "stock-nvs"),
                ("esphome", {"U2.meter": "from backup, factor 1.00"}, "from-backup"),
                ("esphome", {"U2.meter": "missing"}, "missing"),
                ("esphome", {"K3.calibration": {"calibration": "something new"}}, "reported"),
                ("esphome", {}, "not-checked")):
            self.assertEqual(report_data.meter_word(captures, firmware), word, captures)

    def test_the_relay_check_in_a_word(self):
        for relay, word in (("switches (meter confirms)", "passed"), ("switches (through eWeLink)", "passed"),
                            ("did not switch", "failed"), ("could not be checked", "not-run"),
                            ("motor mode", "not-run"), (None, "not-run")):
            self.assertEqual(report_data.checks_word({"Relay": relay} if relay else {}), word, relay)
