"""tools/repo/suite_coverage.py: each suite - the attended scenarios, the fault
suites - tries each situation in every cell it can be, or lists it as not
yet tried."""

import unittest

from tools.repo import suite_coverage


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suites = [(suite, suite.tried()) for suite in suite_coverage.suites()]

    def test_every_cell_a_situation_is_left_out_of_is_listed_as_not_yet(self):
        # A situation added in one cell asks for the others; one added since
        # to the cell not_yet names is taken off it, so the list only shrinks.
        for suite, cells in self.suites:
            found = suite_coverage.gaps(suite, cells)
            for name in sorted(set(found) | set(suite.not_yet)):
                with self.subTest(suite=suite.name, situation=name):
                    listed = set(suite.not_yet.get(name, ()))
                    self.assertFalse(found.get(name, set()) - listed,
                                     "tried in another cell: add it to these, or say in NOT_EVERYWHERE why it "
                                     "cannot be")
                    self.assertFalse(listed - found.get(name, set()), "tried there now: take it off NOT_YET")

    def test_every_situation_named_is_one_the_suite_tries(self):
        words = {*suite_coverage.COMMANDS, *suite_coverage.FAMILIES}
        for suite, cells in self.suites:
            for name in [*suite.not_everywhere, *suite.not_in, *suite.not_yet]:
                with self.subTest(suite=suite.name, situation=name):
                    self.assertIn(name, cells)
            for name, (within, why) in suite.not_everywhere.items():
                with self.subTest(suite=suite.name, situation=name):
                    self.assertTrue(within and why)
                    self.assertLessEqual(set(within), words)

    def test_a_cell_ruled_out_alone_is_one_the_situation_is_for_and_not_tried(self):
        # Else NOT_EVERYWHERE says it, or the cell is tried after all.
        for suite, cells in self.suites:
            for name, ruled_out in suite.not_in.items():
                for where, why in ruled_out.items():
                    with self.subTest(suite=suite.name, situation=name, cell=where):
                        self.assertIn(where, suite_coverage.cells_for(suite.not_everywhere.get(name, ((), ""))[0]))
                        self.assertNotIn(where, cells[name])
                        self.assertNotIn(where, suite.not_yet.get(name, ()))
                        self.assertTrue(why)

    def test_a_situation_is_for_the_cells_its_narrowings_all_name(self):
        self.assertEqual(len(suite_coverage.cells_for()), 4)
        self.assertEqual(suite_coverage.cells_for(("readout",)), {("readout", "espressif"), ("readout", "beken")})
        self.assertEqual(suite_coverage.cells_for(("conversion", "beken")), {("conversion", "beken")})
        suite = suite_coverage.Suite("made up", dict, {"update": (("conversion",), "why")},
                               {"fallback": {("readout", "beken"): "why"}})
        self.assertEqual(suite_coverage.gaps(suite, {"update": {("conversion", "espressif")},
                                               "fallback": {("readout", "espressif"), ("conversion", "beken"),
                                                            ("conversion", "espressif")}}),
                         {"update": {("conversion", "beken")}})


if __name__ == "__main__":
    unittest.main()
