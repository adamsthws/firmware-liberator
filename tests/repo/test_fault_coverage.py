"""tools/repo/fault_coverage.py: the fault suites' scenarios named as the
situations and cells tools/repo/suite_coverage.py checks."""

import unittest

from tools.repo import fault_coverage


class SituationTests(unittest.TestCase):
    def test_a_situation_is_its_fault_and_the_kind_its_argument_names(self):
        def named(fault, arg):
            return fault_coverage.situation({"fault": fault, "arg": arg})
        self.assertEqual(named("outage", 30), "outage")
        self.assertEqual(named("cancel", "new"), "cancel-new")
        self.assertEqual(named("double", "outage-30"), "double-outage")
        self.assertEqual(named("legacy", "v2.0.2+U5off"), "legacy")
        self.assertEqual(fault_coverage.cell("beken", {"route": "sonoff>readout-all"}), ("readout", "beken"))
        self.assertEqual(fault_coverage.cell("espressif", {"route": "tasmota>esphome"}),
                         ("conversion", "espressif"))


if __name__ == "__main__":
    unittest.main()
