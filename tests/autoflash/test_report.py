"""The report of a run: what it says of the firmware and the identity, and
the hardware check through ESPHome."""
import unittest
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tools.autoflash import esphome, report
from tools.autoflash.state import DONE


class EndingTests(RouteTestCase):
    def test_the_report_says_esphome(self):
        fields = dict(report.report_fields(self.ctx(firmware="esphome"), 300, False))
        self.assertEqual(fields["Firmware"], "ESPHome")
        # A run of 1.x, from before there was a choice, installed Tasmota.
        legacy = self.ctx(run="tasmota-run")
        legacy.state.mark("D1", DONE)
        self.assertEqual(dict(report.report_fields(legacy, 300, False))["Firmware"], "Tasmota")

    def test_a_run_that_never_found_its_device_does_not_say_it_was_on_sonoff(self):
        # Seen on a real plug switched off: the report said "Converted from: Sonoff".
        never = self.ctx(stock_version="")
        self.assertEqual(dict(report.report_fields(never, 300, False))["Converted from"], "__")
        self.assertEqual(dict(report.report_fields(self.ctx(run="found", source="sonoff"), 300, False))
                         ["Converted from"], "Sonoff")

    def test_a_run_back_to_sonoff_says_where_the_identity_came_from(self):
        for taken, said in (("backup", "From the backup on this computer"), ("kept", "From the device"),
                            ("account", "Rebuilt from your eWeLink account"), (None, "Not read yet")):
            with self.subTest(taken=taken):
                ctx = self.ctx(firmware="sonoff", source="tasmota", run=f"sonoff-{taken}")
                if taken:
                    ctx.state.capture("R1.identity", taken)
                self.assertEqual(dict(report.report_fields(ctx, 300, False))["Sonoff identity"], said)
        # Left out of every other run's report, whatever it read.
        for firmware in ("tasmota", "esphome"):
            ctx = self.ctx(firmware=firmware, run=f"to-{firmware}")
            ctx.state.capture("R1.identity", "backup")
            self.assertNotIn("Sonoff identity", dict(report.report_fields(ctx, 300, False)))

    def test_the_hardware_check_switches_through_esphome(self):
        relay = {"on": True}

        def switch(host, on):
            relay["on"] = on
            return True

        def meter(host):
            return {"Voltage": 236.0, "Power": 0.0} if relay["on"] else {"Voltage": 0.0, "Power": 0.0}

        with mock.patch.object(report, "METER_SETTLE", 0), \
                mock.patch.object(esphome, "relay", side_effect=lambda host: relay["on"]), \
                mock.patch.object(esphome, "switch_relay", side_effect=switch), \
                mock.patch.object(esphome, "meter", side_effect=meter):
            result = report.check_esphome_hardware(self.ctx(firmware="esphome"))
        self.assertTrue(relay["on"], "left as it was found")
        self.assertIn("236 V", result["Power meter"])

    def test_a_plug_that_does_not_answer_is_reported_not_raised(self):
        with mock.patch.object(esphome, "relay", return_value=None):
            result = report.check_esphome_hardware(self.ctx(firmware="esphome"))
        self.assertEqual(result["Relay"], "could not be checked")


if __name__ == "__main__":
    unittest.main()
