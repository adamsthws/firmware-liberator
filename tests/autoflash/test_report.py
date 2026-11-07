"""The report of a run: what it says of the firmware and the identity, and
the hardware check through ESPHome."""
import platform
import sys
import time
import unittest
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tools.autoflash import esphome, hostsys, output, prompts, report
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


class ComputerDescriptionTests(unittest.TestCase):
    """Windows 11 is named so, on a Python before 3.12 that calls it "10"."""

    def describe(self, release, build, product_type=1):
        from types import SimpleNamespace
        winver = SimpleNamespace(product_type=product_type)
        with mock.patch.object(hostsys, "IS_WINDOWS", True), \
                mock.patch.object(platform, "release", return_value=release), \
                mock.patch.object(platform, "version", return_value=build), \
                mock.patch.object(sys, "getwindowsversion",
                                  return_value=winver, create=True):
            return report.computer_description()

    def test_windows_10_stays_10(self):
        self.assertEqual(self.describe("10", "10.0.19045"), "Windows 10 (build 10.0.19045)")

    def test_windows_11_called_10_by_an_older_python(self):
        self.assertEqual(self.describe("10", "10.0.22631"), "Windows 11 (build 10.0.22631)")

    def test_windows_11_named_by_python_3_12(self):
        self.assertEqual(self.describe("11", "10.0.26100"), "Windows 11 (build 10.0.26100)")

    def test_a_server_is_not_called_11(self):
        self.assertEqual(self.describe("10", "10.0.26100", product_type=3),
                         "Windows 10 (build 10.0.26100)")


class DescribeHardwareTests(unittest.TestCase):
    def test_voltage_dropping_with_the_relay_off_proves_it_switches(self):
        result = report.describe_hardware(True, True, {"Voltage": 239, "Power": 0}, {"Voltage": 0, "Power": 0})
        self.assertEqual(result["Relay"], "switches (meter confirms)")
        self.assertEqual(result["Power meter"], "on: 239 V, 0 W; off: 0 V, 0 W")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it

    def test_a_load_losing_power_proves_it_switches(self):
        result = report.describe_hardware(True, True, {"Voltage": 238, "Power": 41.5}, {"Voltage": 238, "Power": 0})
        self.assertEqual(result["Relay"], "switches (meter confirms)")

    def test_no_change_on_the_meter_is_not_claimed_as_proof(self):
        result = report.describe_hardware(True, True, {"Voltage": 238, "Power": 0}, {"Voltage": 238, "Power": 0})
        self.assertEqual(result["Relay"], "switches (meter unchanged)")

    def test_a_relay_that_does_not_follow_the_command_is_reported(self):
        result = report.describe_hardware(True, False, None, None)
        self.assertEqual(result["Relay"], "did not switch")
        self.assertEqual(result["Power meter"], "no readings")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it


class CheckHardwareTests(unittest.TestCase):
    def ctx(self, readings, *, start="ON"):
        state = {"power": start}
        sent = []

        def command(text, timeout=None):
            sent.append(text)
            if text == "Power":
                return {"POWER": state["power"]}
            if text.startswith("Power "):
                state["power"] = text.split()[1]
                return {"POWER": state["power"]}
            if text == "Status 10":
                return {"StatusSNS": {"ENERGY": readings[state["power"]]}}
            raise AssertionError(text)

        from types import SimpleNamespace
        return SimpleNamespace(device=SimpleNamespace(command=command)), sent, state

    def run_check(self, ctx):
        with mock.patch.object(time, "sleep"), mock.patch.object(output, "say"):
            return report.check_hardware(ctx)

    def test_a_plug_that_was_off_goes_on_then_back_off(self):
        ctx, sent, state = self.ctx({"ON": {"Voltage": 240, "Power": 3}, "OFF": {"Voltage": 0, "Power": 0}},
                                    start="OFF")
        result = self.run_check(ctx)
        self.assertEqual([c for c in sent if c.startswith("Power ")], ["Power ON", "Power OFF"])
        self.assertEqual(state["power"], "OFF")
        self.assertEqual(result["Relay"], "switches (meter confirms)")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it

    def test_a_plug_that_was_on_goes_off_then_back_on(self):
        ctx, sent, state = self.ctx({"ON": {"Voltage": 231.5, "Power": 40}, "OFF": {"Voltage": 231.5, "Power": 0}})
        result = self.run_check(ctx)
        self.assertEqual([c for c in sent if c.startswith("Power ")], ["Power OFF", "Power ON"])
        self.assertEqual(state["power"], "ON")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it
        self.assertEqual(result["Power meter"], "on: 231.5 V, 40 W; off: 231.5 V, 0 W")

    def test_a_template_with_more_than_one_relay_answers_power1(self):
        # The TH Elite's Tasmota: its dry contact is a relay as well.
        state = {"power": "OFF"}

        def command(text, timeout=None):
            if text.startswith("Power"):
                state["power"] = text.split()[1] if " " in text else state["power"]
                return {"POWER1": state["power"]}
            return {}

        from types import SimpleNamespace
        result = self.run_check(SimpleNamespace(device=SimpleNamespace(command=command)))
        self.assertEqual(result["Relay"], "switches (meter unchanged)")
        self.assertEqual(state["power"], "OFF")

    def test_the_screen_starts_each_answer_with_a_capital_as_the_report_does(self):
        ctx, _, _ = self.ctx({"ON": {"Voltage": 240, "Power": 3}, "OFF": {"Voltage": 0, "Power": 0}})
        with mock.patch.object(time, "sleep"), mock.patch.object(output, "say") as said:
            report.check_hardware(ctx)
        lines = [c.args[0] for c in said.call_args_list]
        self.assertIn("    Relay: Switches (meter confirms)", lines)
        self.assertIn("    Power meter: On: 240 V, 3 W; off: 0 V, 0 W", lines)

    def test_nothing_is_asked(self):
        ctx, _, _ = self.ctx({"ON": {}, "OFF": {}})
        with mock.patch.object(prompts, "ask_yes_no", side_effect=AssertionError("asked")):
            self.run_check(ctx)

    def test_a_plug_that_stops_answering_is_reported_not_raised(self):
        from types import SimpleNamespace
        ctx = SimpleNamespace(device=SimpleNamespace(command=mock.Mock(side_effect=OSError("refused"))))
        result = self.run_check(ctx)
        self.assertEqual(result["Relay"], "could not be checked")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it


if __name__ == "__main__":
    unittest.main()
