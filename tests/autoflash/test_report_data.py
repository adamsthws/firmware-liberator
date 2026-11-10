"""report_data: a run's report as the API takes it, and nothing private in it."""

from __future__ import annotations

import hashlib
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
        # Held to the schema the API holds it to, as written: report-schema.json.
        written = json.loads(report_schema.PATH.read_text(encoding="utf-8"))
        problems = report_schema.validate(written, {**data, "proof": "1794000000:1"})
        self.assertEqual(problems, [], data)
        return data

    def test_a_converted_run_fits_the_schema(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60")
        data = self.payload(ctx, hardware={"Relay": "switches (meter confirms)"})
        self.assertEqual((data["result"], data["stopped"], data["checks"]), ("converted", None, "passed"))
        self.assertEqual((data["device"], data["model"], data["stock"]), ("sonoff-s60", "S60TPF", "1.2.0"))
        self.assertEqual(data["seconds"], 512)

    def test_nothing_private_goes_in_it(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60", device_mac=MAC, email="me@example.com",
                       device_name="kitchen")
        sent = json.dumps(self.payload(ctx, "failed",
                                       failure="nothing answers at 192.0.2.50 (12:34:56:ab:cd:ef): check it"))
        for private in ("192.0.2.50", MAC, MAC.replace(":", ""), "me@example.com", "kitchen"):
            self.assertNotIn(private.lower(), sent.lower(), private)
        self.assertIn(report_data.device_hash(MAC), sent)

    def test_a_failed_run_says_where_and_what_kind_in_plain_words(self):
        ctx = self.ctx(firmware="esphome", device_kind="sonoff-s60")
        data = self.payload(ctx, "failed", failure="the device at 192.0.2.50 did not answer within 30s — café")
        self.assertEqual(data["stopped"]["step"], "D4")
        self.assertEqual(data["stopped"]["error"], "device-no-answer")
        self.assertEqual(data["stopped"]["text"], "the device at <address> did not answer within 30s  caf")
        self.assertEqual(self.payload(ctx, "crashed", failure="KeyError: x")["stopped"]["error"], "crash")

    def test_a_failure_is_classed_by_the_words_sent_but_for_words_cut_short(self):
        ctx = self.ctx(firmware="esphome", device_kind="sonoff-s60")
        # The API checks the words sent read as their class (website/api/review.js).
        data = self.payload(ctx, "failed", failure="the delivery stopped part way: x")
        self.assertEqual((data["stopped"]["error"], data["stopped"]["text"]),
                         ("upload-cut", "the delivery stopped part way: x"))
        long = "x " * 150 + "the delivery stopped part way"
        data = self.payload(ctx, "failed", failure=long)
        self.assertTrue(data["stopped"]["text"].endswith("..."))
        self.assertEqual(data["stopped"]["error"], "upload-cut", "from the whole reason, as the words sent lose it")
        data = self.payload(ctx, "failed", failure="y" * 300)
        self.assertEqual(len(data["stopped"]["text"]), 200)   # the API's limit, as written in its schema

    def test_a_device_put_back_says_so_and_where_it_came_from(self):
        ctx = self.ctx(firmware="esphome", source="midway", device_kind="sonoff-s60",
                       put_back={"came_from": "esphome", "why": "no identity"})
        data = self.payload(ctx)
        self.assertEqual((data["result"], data["from"]), ("put-back", "esphome"))

    def test_an_unknown_device_is_named_by_its_model_alone(self):
        ctx = self.ctx(product_model="NEWTHING", stock_version="not a version")
        data = self.payload(ctx, "failed", failure="the device is a NEWTHING, which this tool does not convert yet")
        self.assertEqual((data["device"], data["family"], data["model"], data["stock"]),
                         (None, None, "NEWTHING", None))
        self.assertEqual(data["stopped"]["error"], "unsupported-device")

    def test_the_model_and_stock_version_come_from_the_devices_record_if_the_run_has_none(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60", device_mac=MAC, product_model="",
                       stock_version="")
        with mock.patch.object(report_data.records, "read_device",
                               return_value={"model": "s60tpf", "stock_version": "1.2.0"}) as read:
            data = self.payload(ctx)
        read.assert_called_once_with(MAC)
        self.assertEqual((data["model"], data["stock"]), ("S60TPF", "1.2.0"))

    def test_the_signal_is_the_weakest_reading_of_any_step(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60")
        for key, value in {"D1.signal_dbm": -48, "D4.signal_dbm": -61, "D4.seconds": 30, "E1.signal_dbm": True,
                           "H1.signal_dbm": "-90"}.items():
            ctx.state.capture(key, value)
        self.assertEqual(self.payload(ctx)["signal"], -61)

    def test_whether_the_setup_network_was_joined(self):
        for captures, joined in (({}, False),
                                 ({"D1.upload_route": "bridge network"}, True),
                                 ({"D1.resumed": True}, True),
                                 ({"D1.resumed": "yes"}, False),
                                 ({"D1.upload_route": "home network", "D1.wifi": "preset"}, False),
                                 ({"D1.upload_route": "home network", "D1.wifi": "typed"}, True),
                                 ({"F4.wifi_route": "setup network"}, True),
                                 ({"F4.wifi_route": "home network"}, False)):
            with self.subTest(captures=captures):
                ctx = self.ctx(run=str(len(captures)) + str(joined) + str(captures), firmware="tasmota",
                               device_kind="sonoff-s60")
                for key, value in captures.items():
                    ctx.state.capture(key, value)
                self.assertIs(self.payload(ctx)["setup_network"], joined)

    def test_what_is_asked_of_each_family(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60", wifi_typed=True, power_cycles=2)
        data = self.payload(ctx)
        self.assertEqual((data["wifi_typed"], data["setup_network"], data["power_cycled"]), (True, False, None))
        ctx = self.ctx(run="beken", firmware="openbeken", device_kind="sonoff-s61", wifi_typed=True, power_cycles=2)
        data = self.payload(ctx)
        self.assertEqual((data["wifi_typed"], data["setup_network"], data["power_cycled"]), (None, None, True))

    def test_dev_runs_are_tagged(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60")
        with mock.patch.dict("os.environ", {"AUTOFLASH_REPORT_TAG": "dev"}):
            self.assertEqual(self.payload(ctx)["tag"], "dev")
        with mock.patch.dict("os.environ", {"AUTOFLASH_REPORT_TAG": "anything else"}):
            self.assertEqual(self.payload(ctx)["tag"], "user")


class WordTests(RouteTestCase):
    def test_a_device_is_the_same_hash_however_its_mac_is_written(self):
        expected = report_data.device_hash(MAC)
        self.assertRegex(expected, r"^[0-9a-f]{64}$")
        for written in ("12-34-56-AB-CD-EF", "123456abcdef", " 12:34:56:AB:CD:EF "):
            self.assertEqual(report_data.device_hash(written), expected, written)
        for nothing in (None, "", "12:34:56", "not a mac"):
            self.assertIsNone(report_data.device_hash(nothing))

    def test_the_hash_is_of_the_mac_in_one_written_form(self):
        # The API knows a device again from any computer's report only if each hashes the same.
        self.assertEqual(report_data.device_hash("12-34-56-AB-CD-EF"),
                         hashlib.sha256(b"firmware-liberator device:12:34:56:ab:cd:ef").hexdigest())

    def test_the_operating_system_in_words_the_api_allows(self):
        for said, version in (("Linux Ubuntu 24.04.1 LTS", "Ubuntu 24.04.1 LTS"),
                              ("Linux Debian\u00a0GNU/Linux 13 (trixie) \u2014 x86_64", "DebianGNU/Linux 13 (trixie)  x86_64"),
                              ("Linux " + "a" * 60, "a" * 48),
                              ("Linux \u2014", "unknown"),
                              ("", "unknown")):
            with self.subTest(said=said), mock.patch.object(report_data.report, "computer_description",
                                                            return_value=said):
                self.assertEqual(report_data.os_info()["version"], version)

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
