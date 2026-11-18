"""tools/repo/fault_coverage.py: both fault suites try each situation in every
cell it can be, or list it as not yet tried."""

import unittest

from tools.repo import fault_coverage


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = fault_coverage.covered(fault_coverage.suites())

    def test_every_cell_a_situation_is_left_out_of_is_listed_as_not_yet(self):
        # A fault added to one suite asks for the others; one added since to
        # the cell NOT_YET names is taken off it, so the list only shrinks.
        found = fault_coverage.gaps(self.cells)
        for name in sorted(set(found) | set(fault_coverage.NOT_YET)):
            with self.subTest(situation=name):
                listed = set(fault_coverage.NOT_YET.get(name, ()))
                self.assertFalse(found.get(name, set()) - listed,
                                 "tried in another cell: add it to these, or say in NOT_EVERYWHERE why it cannot be")
                self.assertFalse(listed - found.get(name, set()), "tried there now: take it off NOT_YET")

    def test_every_situation_named_is_one_a_suite_tries(self):
        for name in [*fault_coverage.NOT_EVERYWHERE, *fault_coverage.NOT_YET]:
            with self.subTest(situation=name):
                self.assertIn(name, self.cells)
        words = {*fault_coverage.COMMANDS, *fault_coverage.FAMILIES}
        for name, (within, why) in fault_coverage.NOT_EVERYWHERE.items():
            with self.subTest(situation=name):
                self.assertLessEqual(set(within), words)
                self.assertTrue(why)

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
