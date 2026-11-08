"""What both fault suites judge a run by beyond its steps: the report's
end-of-run checks."""

import unittest

from tools.autoflash import report
from tools.repo import fault_verdicts


def run_log(*lines, before=""):
    """A run log as the tool writes one: each line with its time first."""
    return before + "".join(f"[10:00:0{n}] {line}\n" for n, line in
                            enumerate(["=== run started, tool version 2.3.0 ===", *lines, "=== run ended ==="]))


class EndChecksTests(unittest.TestCase):
    def test_a_relay_that_switched_passes_on_every_firmware(self):
        log = run_log("    Relay: Switches (meter confirms)", "    Checks passed: True")
        for firmware in ("tasmota", "esphome", "openbeken", "sonoff"):
            self.assertIsNone(fault_verdicts.checks_problem(log, firmware), firmware)

    def test_checks_that_failed_say_what_the_relay_check_said(self):
        # As every Espressif run back to Sonoff's firmware ended before 83c517d:
        # the relay asked through eWeLink before the plug was there.
        log = run_log("    Relay: Could not be checked", "    Device model:   S60TPG", "    Checks passed: False")
        self.assertEqual(fault_verdicts.checks_problem(log, "sonoff"),
                         "Checks passed: False; Relay: Could not be checked")

    def test_checks_never_run_fail_only_where_the_emulator_can_run_them(self):
        log = run_log("    Checks passed: Not run")
        self.assertIsNone(fault_verdicts.checks_problem(log, "sonoff"))   # no eWeLink device id kept
        for firmware in ("tasmota", "esphome", "openbeken"):
            self.assertEqual(fault_verdicts.checks_problem(log, firmware),
                             "Checks passed: Not run; Relay: not said", firmware)

    def test_only_the_last_run_counts(self):
        earlier = run_log("    Relay: Did not switch", "    Checks passed: False")
        self.assertIsNone(fault_verdicts.checks_problem(
            run_log("    Checks passed: True", before=earlier), "tasmota"))
        self.assertIsNotNone(fault_verdicts.checks_problem(
            run_log("    Checks passed: False", before=run_log("    Checks passed: True")), "tasmota"))

    def test_the_report_is_read_as_the_tool_lays_it_out(self):
        fields = [("Result", "Converted"), ("Device Wi-Fi signal", "-60 dBm"), ("Checks passed", "False")]
        log = run_log("    Relay: Did not switch", *report.report_screen(fields))
        self.assertEqual(fault_verdicts.checks_problem(log, "tasmota"), "Checks passed: False; Relay: Did not switch")

    def test_a_run_with_no_report_has_nothing_to_judge(self):
        self.assertIsNone(fault_verdicts.checks_problem(run_log("Nothing to convert."), "tasmota"))
        self.assertIsNone(fault_verdicts.checks_problem("", "tasmota"))


if __name__ == "__main__":
    unittest.main()
