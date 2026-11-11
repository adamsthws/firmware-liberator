"""The report of a run: what it says of the firmware and the identity, and
the hardware check through ESPHome."""
import platform
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tools.autoflash import esphome, hostsys, output, prompts, report
from tools.autoflash.state import DONE


class EndingTests(RouteTestCase):
    def test_the_report_says_esphome(self):
        fields = dict(report.report_fields(self.ctx(firmware="esphome")))
        self.assertEqual(fields["Firmware"], "ESPHome")
        # A run of 1.x, from before there was a choice, installed Tasmota.
        legacy = self.ctx(run="tasmota-run")
        legacy.state.mark("D1", DONE)
        self.assertEqual(dict(report.report_fields(legacy))["Firmware"], "Tasmota")

    def test_it_says_whether_a_setup_network_was_joined(self):
        for captures, joined in (({}, "No"),
                                 ({"D1.upload_route": "bridge network"}, "Yes"),
                                 ({"D1.resumed": True}, "Yes"),
                                 ({"D1.upload_route": "home network"}, "Yes"),
                                 ({"D1.upload_route": "home network", "D1.wifi": "preset"}, "No"),
                                 ({"F4.wifi_route": "setup network"}, "Yes")):
            with self.subTest(captures):
                ctx = self.ctx(run=f"run-{len(captures)}-{joined}-{sorted(captures)}", firmware="tasmota")
                ctx.state.data["captures"] = captures
                self.assertEqual(dict(report.report_fields(ctx))["Setup network"].lower(), joined.lower())

    def test_a_run_that_never_found_its_device_does_not_say_it_was_on_sonoff(self):
        # Seen on a real device switched off: the report said "Converted from: Sonoff".
        never = self.ctx(stock_version="")
        self.assertEqual(dict(report.report_fields(never))["From"], "__")
        self.assertEqual(dict(report.report_fields(self.ctx(run="found", source="sonoff")))
                         ["From"], "Sonoff 1.2.0")

    def test_from_sonoff_gives_its_version_and_from_another_firmware_none(self):
        # A converted device's record keeps its backup's Sonoff version: not
        # what it ran, so not given with Tasmota's name.
        fields = dict(report.report_fields(self.ctx(run="from-sonoff", source="sonoff", stock_version="")))
        self.assertEqual(fields["From"], "Sonoff")   # stopped before it was read: not asked for
        fields = dict(report.report_fields(self.ctx(run="from-tasmota", source="tasmota", firmware="esphome")))
        self.assertEqual(fields["From"], "Tasmota")
        self.assertNotIn("Stock", fields)

    def test_a_run_back_to_sonoff_says_where_the_identity_came_from(self):
        for taken, said in (("backup", "Backup"), ("kept", "Device"),
                            ("account", "EWeLink account"), (None, "Not read")):
            with self.subTest(taken=taken):
                ctx = self.ctx(firmware="sonoff", source="tasmota", run=f"sonoff-{taken}")
                if taken:
                    ctx.state.capture("R1.identity", taken)
                self.assertEqual(dict(report.report_fields(ctx))["Identity"], said)
        # Left out of every other run's report, whatever it read.
        for firmware in ("tasmota", "esphome"):
            ctx = self.ctx(firmware=firmware, run=f"to-{firmware}")
            ctx.state.capture("R1.identity", "backup")
            self.assertNotIn("Identity", dict(report.report_fields(ctx)))

    def test_a_device_put_back_says_so_and_where_it_came_from(self):
        # Sonoff's firmware could not be put together (sonoff.put_back): not
        # "Converted", and from the firmware it began on, not "part way".
        ctx = self.ctx(firmware="esphome", source="midway", run="put-back")
        ctx.state.set_input("put_back", {"came_from": "esphome", "why": "no identity"})
        fields = dict(report.report_fields(ctx, result=report.run_result(ctx, "converted")))
        self.assertEqual((fields["Result"], fields["From"], fields["Firmware"]),
                         ("Put back on ESPHome", "ESPHome", "ESPHome"))

    def test_the_meter_is_not_in_it(self):
        # A device's readings a few percent out is no news to the person
        # converting it: the report sent holds how its calibration went.
        ctx = self.ctx(firmware="tasmota", run="meter-tasmota")
        ctx.state.set_input("device_kind", "sonoff-pow-elite")
        ctx.state.capture("F4.meter", {"changed": True, "factory": True, "factor": 1.02})
        self.assertNotIn("Meter", dict(report.report_fields(ctx)))

    def test_on_the_screen_the_device_is_on_the_right_the_result_and_run_on_the_left(self):
        # Half the rows of one column; the answers a fault suite reads off the
        # screen - the device's - each the last on its row.
        fields = [("Result", "Converted"), ("Firmware", "Tasmota"), ("Liberator", "2.4.0"), ("Backup", "Completed"),
                  ("OS", "Ubuntu 24.04.5 LTS"), ("Checks", "Pass")]
        rows = report.report_screen(fields, columns=100)
        self.assertEqual([row.split() for row in rows], [["Result:", "Converted", "Firmware:", "Tasmota"],
                                                         ["Liberator:", "2.4.0", "Backup:", "Completed"],
                                                         ["OS:", "Ubuntu", "24.04.5", "LTS", "Checks:", "Pass"]])

    def test_a_long_answer_wraps_within_its_column_under_itself(self):
        fields = [("Result", "Converted"), ("Liberator", "2.4.0"),
                  ("Firmware", "Tasmota with a long name that takes more than one line of its column")]
        rows = report.report_screen(fields, columns=100)
        side = (100 - 4 - 3 - 2) // 2
        right = [row[4 + side + 3:] for row in rows]
        self.assertEqual(right[0], "Firmware: Tasmota with a long name that takes")
        self.assertTrue(all(len(row) <= side for row in right), rows)
        self.assertEqual(right[1:], [" " * 10 + "more than one line of its column"])

    def test_every_value_on_the_left_starts_in_one_column_those_below_too(self):
        # A readout's report: its devices listed under the rest, with longer
        # labels than the left-hand column's own.
        fields = [("Result", "Read out"), ("Liberator", "3.0.0"), ("OS", "Ubuntu"), ("Devices", "2")]
        below = [("Accepted", "BASICR4\u00a01.1.0"), ("Not needed", "S60TPG\u00a01.2.0")]
        for columns in (100, 60):   # two columns, and one
            with self.subTest(columns=columns):
                rows = report.report_screen(fields, columns=columns, right=("Devices",), below=below)
                starts = {row.index(value) for row in rows
                          for value in ("Read out", "3.0.0", "Ubuntu", "BASICR4 1.1.0", "S60TPG 1.2.0") if value in row}
                self.assertEqual(len(starts), 1, "\n".join(rows))

    def test_a_stopped_runs_result_is_one_word_on_screen(self):
        for result, shown in (("Stopped at stage 6 of 15 (Installing Tasmota): it went quiet", "Stopped"),
                              ("Crashed at the end: KeyError at tools/autoflash/report.py:212", "Crashed"),
                              ("Converted", "Converted"), ("Put back on Sonoff", "Put back on Sonoff")):
            self.assertEqual(report.briefly(result), shown)

    def test_a_window_too_narrow_for_two_has_one_column(self):
        fields = [("Result", "Converted"), ("Firmware", "Tasmota"), ("Liberator", "2.4.0")]
        self.assertEqual([row.split()[0] for row in report.report_screen(fields, columns=60)],
                         ["Result:", "Firmware:", "Liberator:"])

    def test_the_hardware_check_switches_through_esphome(self):
        relay = {"on": True}

        def switch(host, on):
            relay["on"] = on
            return True

        def meter(host):
            return {"Voltage": 236.0, "Power": 0.0} if relay["on"] else {"Voltage": 0.0, "Power": 0.0}

        done = []
        with mock.patch.object(report.time, "sleep", side_effect=lambda seconds: done.append(seconds)), \
                mock.patch.object(esphome, "relay", side_effect=lambda host: relay["on"]), \
                mock.patch.object(esphome, "switch_relay", side_effect=lambda host, on: done.append(on) or switch(host, on)), \
                mock.patch.object(esphome, "meter", side_effect=lambda host: done.append("read") or meter(host)):
            result = report.check_esphome_hardware(self.ctx(firmware="esphome"))
        self.assertTrue(relay["on"], "left as it was found")
        self.assertEqual(result, {"Relay": "switches (meter confirms)",
                                  "Power meter": "on: 236 V, 0 W; off: 0 V, 0 W"})
        # Each reading once the meter has settled after its switch.
        self.assertEqual(done, [False, report.METER_SETTLE, "read", True, report.METER_SETTLE, "read"])

    def test_a_device_that_does_not_answer_is_reported_not_raised(self):
        clock = iter(range(0, 1000, 5))
        with mock.patch.object(esphome, "relay", return_value=None), mock.patch.object(report.time, "sleep"), \
                mock.patch.object(report.time, "monotonic", side_effect=lambda: next(clock)), \
                mock.patch.object(output, "say") as said:
            result = report.check_esphome_hardware(self.ctx(firmware="esphome"))
        self.assertEqual(result["Relay"], "could not be checked")
        self.assertIn("    Relay: Could not be checked", [c.args[0] for c in said.call_args_list])

    def test_a_device_still_starting_as_the_run_ends_is_checked_once_it_answers(self):
        answers = iter([None, None, False])   # still starting, then answering: off
        with mock.patch.object(report, "METER_SETTLE", 0), mock.patch.object(report.time, "sleep"), \
                mock.patch.object(esphome, "relay", side_effect=lambda host: next(answers)), \
                mock.patch.object(esphome, "switch_relay", return_value=True) as switched, \
                mock.patch.object(esphome, "meter", return_value={}):
            result = report.check_esphome_hardware(self.ctx(firmware="esphome"))
        self.assertEqual([c.args[1] for c in switched.call_args_list], [True, False])
        self.assertNotEqual(result["Relay"], "could not be checked")


class ReportColumnTests(unittest.TestCase):
    def test_the_columns_end_together(self):
        # Seen on an S61: its device answers ran two rows below this computer's.
        fields = [("Result", "Converted"), ("Liberator", "2.4.0"), ("Firmware", "OpenBeken"), ("OS", "Ubuntu"),
                  ("From", "Sonoff 1.0.2"), ("Python", "3.12"), ("Model", "S61STPF"),
                  ("Backup", "Completed"), ("Signal", "-45 dBm"), ("Checks", "Pass"), ("Extra", "Not checked")]
        rows = report.report_screen(fields, columns=100)
        self.assertEqual(len(rows), 6, "\n".join(rows))
        extra = next(row for row in rows if "Extra:" in row)
        self.assertLess(extra.index("Extra:"), 20)   # on the left now
        checks = next(row for row in rows if "Checks:" in row)
        self.assertTrue(checks.rstrip().endswith("Pass"))   # a suite's, still last on its row


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

    def test_linux_by_its_own_name_else_its_kernel(self):
        for release, expected in (({"PRETTY_NAME": "Ubuntu 24.04.5 LTS"}, "Ubuntu 24.04.5 LTS"),
                                  ({}, "Linux 6.8.0")):
            with self.subTest(expected), mock.patch.object(hostsys, "IS_WINDOWS", False), \
                    mock.patch.object(hostsys, "IS_MAC", False), \
                    mock.patch.object(platform, "freedesktop_os_release", return_value=release, create=True), \
                    mock.patch.object(platform, "release", return_value="6.8.0"):
                self.assertEqual(report.computer_description(), expected)

    def test_a_macs_python_that_start_sh_installed_is_named_so(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            framework = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
            for mac, executable, marked, expected in (
                    (True, framework, True, "start.sh's"),
                    (True, framework, False, "system"),        # one installed otherwise
                    (True, "/usr/bin/python3", True, "system"),
                    (False, framework, True, "system")):
                marker = Path(home) / "installed-by-autoflash"
                marker.touch() if marked else marker.unlink(missing_ok=True)
                with self.subTest(mac=mac, executable=executable, marked=marked), \
                        mock.patch.object(hostsys, "IS_MAC", mac), mock.patch.object(sys, "executable", executable), \
                        mock.patch.object(sys, "prefix", sys.base_prefix), \
                        mock.patch.dict("os.environ", {"AUTOFLASH_PYTHON_HOME": home}):
                    self.assertTrue(report.python_description().endswith(f"({expected})"))

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

    def test_a_meter_with_two_channels_is_read_by_relay_1s(self):
        # Tasmota's Status 10 on a Dual R3: Power a list, Voltage common.
        result = report.describe_hardware(True, True, {"Voltage": 230, "Power": [100, 0]},
                                          {"Voltage": 0, "Power": [0, 0]})
        self.assertEqual(result["Relay"], "switches (meter confirms)")
        self.assertEqual(result["Power meter"], "on: 230 V, 100 W; off: 0 V, 0 W")

    def test_proof_needs_mains_with_it_on_and_next_to_none_with_it_off(self):
        for on, off, confirms in (
                ({"Voltage": report.MAINS_VOLTS}, {"Voltage": report.DEAD_VOLTS - 0.1}, True),
                ({"Voltage": report.MAINS_VOLTS - 0.1}, {"Voltage": 0}, False),   # too low to be mains
                ({"Voltage": 230}, {"Voltage": report.DEAD_VOLTS}, False),        # not clearly off
                ({"Voltage": 230}, {"Voltage": 25}, False),
                ({"Voltage": 110}, {"Voltage": 0}, True),                          # 110 V mains
                ({"Power": report.LOAD_WATTS}, {"Power": report.IDLE_WATTS - 0.1}, True),
                ({"Power": report.LOAD_WATTS - 0.1}, {"Power": 0}, False),
                ({"Power": 40}, {"Power": report.IDLE_WATTS}, False),
                ({"Power": 40}, {"Power": 0.6}, False),
                ({"Power": 2}, {"Power": 0}, True)):                                  # a phone charger
            with self.subTest(on=on, off=off):
                self.assertEqual(report.describe_hardware(True, True, on, off)["Relay"],
                                 "switches (meter confirms)" if confirms else "switches (meter unchanged)")

    def test_a_reading_with_neither_number_says_so(self):
        self.assertEqual(report.describe_hardware(True, True, {"Voltage": 231}, {"Current": 0})["Power meter"],
                         "on: 231 V; off: no reading")

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
        return SimpleNamespace(device=SimpleNamespace(command=command, wait_reachable=lambda timeout: 0.0)), sent, state

    def run_check(self, ctx):
        with mock.patch.object(time, "sleep"), mock.patch.object(output, "say"):
            return report.check_hardware(ctx)

    def test_a_device_that_was_off_goes_on_then_back_off(self):
        ctx, sent, state = self.ctx({"ON": {"Voltage": 240, "Power": 3}, "OFF": {"Voltage": 0, "Power": 0}},
                                    start="OFF")
        result = self.run_check(ctx)
        self.assertEqual([c for c in sent if c.startswith("Power ")], ["Power ON", "Power OFF"])
        self.assertEqual(state["power"], "OFF")
        self.assertEqual(result["Relay"], "switches (meter confirms)")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it

    def test_a_device_that_was_on_goes_off_then_back_on(self):
        ctx, sent, state = self.ctx({"ON": {"Voltage": 231.5, "Power": 40}, "OFF": {"Voltage": 231.5, "Power": 0}})
        result = self.run_check(ctx)
        self.assertEqual([c for c in sent if c.startswith("Power ")], ["Power OFF", "Power ON"])
        self.assertEqual(state["power"], "ON")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it
        self.assertEqual(result["Power meter"], "on: 231.5 V, 40 W; off: 231.5 V, 0 W")

    def test_tasmota_is_given_time_to_drop_its_readings_once_every_relay_is_off(self):
        # With every relay off Tasmota stops taking the meter's readings and
        # keeps the last until its energy watchdog (ENERGY_WATCHDOG, 4 s, on
        # its 1 s tick) zeroes them: 5 s after, on a Dual R3 in the emulator.
        clock = {"now": 0.0, "off_at": 0.0}
        state = {"power": "OFF"}
        on = {"Voltage": 230, "Power": [100, 0]}

        def command(text, timeout=None):
            if text.startswith("Power "):
                state["power"] = text.split()[1]
                clock["off_at"] = clock["now"]
            if text.startswith("Power"):
                return {"POWER1": state["power"]}
            held = state["power"] == "OFF" and clock["now"] - clock["off_at"] < 5
            return {"StatusSNS": {"ENERGY": on if state["power"] == "ON" or held
                                  else {"Voltage": 0, "Power": [0, 0]}}}

        def sleep(seconds):
            clock["now"] += seconds

        from types import SimpleNamespace
        ctx = SimpleNamespace(device=SimpleNamespace(command=command, wait_reachable=lambda timeout: 0.0))
        with mock.patch.object(time, "sleep", side_effect=sleep), mock.patch.object(output, "say"):
            result = report.check_hardware(ctx)
        self.assertEqual(result["Relay"], "switches (meter confirms)")

    def test_a_template_with_more_than_one_relay_answers_power1(self):
        # The TH Elite's Tasmota: its dry contact is a relay as well.
        state = {"power": "OFF"}

        def command(text, timeout=None):
            if text.startswith("Power"):
                state["power"] = text.split()[1] if " " in text else state["power"]
                return {"POWER1": state["power"]}
            return {}

        from types import SimpleNamespace
        result = self.run_check(SimpleNamespace(device=SimpleNamespace(command=command, wait_reachable=lambda timeout: 0.0)))
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

    def test_a_device_that_stops_answering_is_reported_not_raised(self):
        from types import SimpleNamespace
        ctx = SimpleNamespace(device=SimpleNamespace(command=mock.Mock(side_effect=OSError("refused")),
                                                     wait_reachable=lambda timeout: 0.0))
        result = self.run_check(ctx)
        self.assertEqual(result["Relay"], "could not be checked")
        self.assertNotIn("Mains voltage", result)  # the power meter line shows it

    def test_a_check_the_device_stopped_answering_still_says_what_it_found(self):
        # Seen in the classic fault suite: the screen gave no Relay line, so the
        # suite recorded "Relay: not said".
        from types import SimpleNamespace
        ctx = SimpleNamespace(device=SimpleNamespace(command=mock.Mock(side_effect=OSError("refused")),
                                                     wait_reachable=lambda timeout: 0.0))
        with mock.patch.object(time, "sleep"), mock.patch.object(output, "say") as said:
            report.check_hardware(ctx)
        lines = [c.args[0] for c in said.call_args_list]
        self.assertIn("    Relay: Could not be checked", lines)
        self.assertIn("    Power meter: Could not be checked", lines)

    def test_a_device_still_starting_as_the_run_ends_is_checked_once_it_answers(self):
        # Seen in the fault suite (update>tasmota:U6:deviceoff-5): switched off and
        # on as the update's last step finished, it refused the check a second on.
        ctx, sent, state = self.ctx({"ON": {"Voltage": 240, "Power": 3}, "OFF": {"Voltage": 0, "Power": 0}},
                                    start="OFF")
        starting = {"answers": False}

        def wait_reachable(timeout):
            self.assertEqual(sent, [])   # nothing sent before it answers
            starting["answers"] = True
            return 20.0
        ctx.device.wait_reachable = wait_reachable
        result = self.run_check(ctx)
        self.assertTrue(starting["answers"])
        self.assertEqual(result["Relay"], "switches (meter confirms)")
        self.assertEqual(state["power"], "OFF")

    def test_a_router_restart_as_the_run_ends_is_waited_out(self):
        # update>tasmota:U6:outage-180: the network back 150 s into the wait,
        # then Tasmota rejoining it.
        self.assertGreaterEqual(report.ANSWER_WAIT, 180 + 60)

    def test_a_device_that_never_answers_again_is_reported_not_raised(self):
        from types import SimpleNamespace
        never = mock.Mock(side_effect=report.DeviceError("192.0.2.50 did not answer within 180s"))
        ctx = SimpleNamespace(device=SimpleNamespace(command=mock.Mock(), wait_reachable=never))
        self.assertEqual(self.run_check(ctx)["Relay"], "could not be checked")
        ctx.device.command.assert_not_called()

class StoppedRunIssueTests(RouteTestCase):
    """A run that failed has the way to open an issue, and its log, where the
    links to support the project would be."""

    def ended(self, ctx, ending):
        lines = []
        with mock.patch.object(output, "tell", side_effect=lambda text="": lines.append(text)), \
                mock.patch.object(output, "show", side_effect=lambda text, *_: lines.append(text)), \
                mock.patch.object(output, "show_link", lambda url, title=None, indent="    ": lines.append(("link", url))), \
                mock.patch.object(output, "show_links", lambda links, **_: lines.append(("links", links))), \
                mock.patch.object(output, "log_path", return_value=Path("/runs/run.log")), \
                mock.patch.object(report.report_upload, "send"):
            report.invite_stopped_report(ctx, ending)
        return lines

    def test_a_stopped_run_gives_the_short_issues_address_and_its_log_after_its_report(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60")
        ctx.last_failure, ctx.last_failed_step = "the device at 192.0.2.50 did not answer within 30s", ""
        ctx.run_started, ctx.run_resumed = time.monotonic(), False
        lines = self.ended(ctx, "failed")
        text = [line for line in lines if isinstance(line, str)]
        result = next(index for index, line in enumerate(text) if line.lstrip().startswith("Result:"))
        asked = text.index("If it should not have stopped, please open an issue here:")
        self.assertLess(result, asked, "the report first, then the way to open an issue")
        self.assertIn(("link", report.ISSUES_URL), lines)
        self.assertEqual(text[text.index("This run's log:") + 1], f"    {Path('/runs/run.log')}")
        self.assertFalse([line for line in lines if isinstance(line, tuple) and line[0] == "links"],
                         "no links to support the project after a failure")
        self.assertFalse([line for line in text if "192.0.2.50" in line or "How it went" in line])

    def test_a_run_that_worked_shows_no_log_even_of_a_kind_wanting_testers(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-powct")
        lines = []
        with mock.patch.object(output, "tell", side_effect=lambda text="": lines.append(text)), \
                mock.patch.object(output, "show_link") as link:
            report.offer_report(ctx, [("Result", "Converted")])
        self.assertNotIn("This run's log:", lines)
        link.assert_not_called()

    def test_a_run_the_person_stopped_sends_its_report_and_shows_none(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60")
        ctx.run_started, ctx.run_resumed = time.monotonic(), False
        with mock.patch.object(output, "tell") as told, mock.patch.object(output, "show_link") as link, \
                mock.patch.object(report.report_upload, "send") as sent:
            report.send_cancelled_report(ctx, "window-closed")
        (to, ending, _seconds, resumed), = [c.args for c in sent.call_args_list]
        self.assertEqual((to, ending, resumed, ctx.stopped_by), (ctx, "cancelled", False, "window-closed"))
        told.assert_not_called()
        link.assert_not_called()

    def test_a_stopped_run_sends_its_report_too(self):
        ctx = self.ctx(firmware="tasmota", device_kind="sonoff-s60")
        ctx.last_failure, ctx.last_failed_step = "the device did not answer within 30s", ""
        ctx.run_started, ctx.run_resumed = time.monotonic(), True
        with mock.patch.object(output, "show_link"), mock.patch.object(output, "tell"), \
                mock.patch.object(report.report_upload, "send") as sent:
            report.invite_stopped_report(ctx, "crashed")
        (to, ending, _seconds, resumed), = [c.args for c in sent.call_args_list]
        self.assertEqual((to, ending, resumed), (ctx, "crashed", True))

    def test_a_title_just_short_enough_is_kept_whole(self):
        self.assertEqual(report.public_text("x" * 240), "x" * 240)
        self.assertEqual(report.public_text("x" * 241), "x" * 237 + "...")


if __name__ == "__main__":
    unittest.main()
