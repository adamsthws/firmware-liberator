"""Where a fault-suite batch has got to (tools/repo/fault_status.py)."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.repo import fault_status


def results(folder: Path, *rows: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "results.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def at(minute: int, hour: int = 10) -> str:
    return f"2026-11-19T{hour:02}:{minute:02}:00"


class StatusTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_verdicts_are_counted_and_those_not_passed_named(self):
        one = results(self.root / "one", {"id": "a", "verdict": "FAIL", "at": at(0)},
                      {"id": "b", "verdict": "PASS", "at": at(10)}, {"id": "a", "verdict": "PASS", "at": at(20)})
        # The other machine's, a Beken row from before 2026-11-19 among them.
        two = results(self.root / "two", {"id": "c", "result": "MISTIMED", "finished": "2026-11-19 10:05:00"},
                      {"id": "d", "verdict": "NO-TRAFFIC", "at": at(15)})
        lines = fault_status.status([one, two], ["a", "b", "c", "d"])
        self.assertEqual(lines[0], "4 scenarios in 2 results files")
        self.assertIn("  PASS                 2", lines)
        self.assertIn("  MISTIMED             1", lines)
        self.assertEqual(lines[lines.index("Not passed:") + 1:-1],
                         ["  MISTIMED         c", "  NO-TRAFFIC       d"])

    def test_the_newest_row_of_a_scenario_stands_across_machines(self):
        one = results(self.root / "one", {"id": "a", "verdict": "FAIL", "at": at(30)})
        two = results(self.root / "two", {"id": "a", "verdict": "PASS", "at": at(10)})
        self.assertIn("  FAIL             a", fault_status.status([one, two], ["a"]))

    def test_whats_left_and_how_long_at_the_recent_pace(self):
        # Two machines, each ending one scenario every 10 minutes: 12 an hour together.
        one = results(self.root / "one", *({"id": f"a{n}", "verdict": "PASS", "at": at(10 * n)} for n in range(4)))
        two = results(self.root / "two", *({"id": f"b{n}", "verdict": "FAIL", "at": at(10 * n)} for n in range(4)))
        wanted = [f"a{n}" for n in range(4)] + [f"b{n}" for n in range(4)] + [f"c{n}" for n in range(6)]
        self.assertEqual(fault_status.status([one, two], wanted)[-1],
                         "Left: 6 of the batch's 14, about 30 min at the recent pace (12 an hour)")
        self.assertEqual(fault_status.how_long(2.25), "2.2 h")

    def test_a_failure_from_an_earlier_batch_is_left_for_this_one(self):
        # Hours apart: the earlier batch's failure is run again by this one, its pass is not.
        path = results(self.root / "one", {"id": "a", "verdict": "FAIL", "at": at(0, 2)},
                       {"id": "b", "verdict": "PASS", "at": at(10, 2)},
                       {"id": "c", "verdict": "PASS", "at": at(0)}, {"id": "d", "verdict": "PASS", "at": at(10)})
        self.assertEqual([row["id"] for row in fault_status.this_batch(fault_status.rows_of(path))], ["c", "d"])
        self.assertTrue(fault_status.status([path], ["a", "b", "c", "d", "e"])[-1].startswith("Left: 2 of"))

    def test_the_pace_is_the_last_hours(self):
        rows = [{"id": "x", "verdict": "PASS", "at": stamp} for stamp in
                (at(0, 8), at(0), at(15), at(30), at(45))]   # one at 8:00, then one every 15 minutes
        self.assertEqual(fault_status.pace(rows), 4.0)
        self.assertIsNone(fault_status.pace(rows[:1]))
        self.assertIsNone(fault_status.pace([]))

    def test_with_no_pace_yet_nor_anything_left_it_says_so(self):
        path = results(self.root / "one", {"id": "a", "verdict": "PASS", "at": at(0)})
        self.assertEqual(fault_status.status([path], ["a", "b"])[-1], "Left: 1 of the batch's 2, the pace not known yet")
        self.assertEqual(fault_status.status([path], ["a"])[-1], "Left: 0 of the batch's 1")

    def test_a_folder_or_its_results_file_is_read(self):
        path = results(self.root / "suite", {"id": "a", "verdict": "PASS", "at": at(0)})
        self.assertEqual(fault_status.results_file(self.root / "suite"), path)
        self.assertEqual(fault_status.results_file(path), path)
        with self.assertRaises(ValueError):
            fault_status.results_file(self.root)

    def test_without_a_path_the_results_written_last_here(self):
        import os
        older = results(self.root / "suite", {"id": "a", "verdict": "PASS", "at": at(0)})
        newer = results(self.root / "suite-stub", {"id": "b", "verdict": "FAIL", "at": at(0)})
        os.utime(older, (1, 1))
        self.assertEqual(fault_status.newest(self.root), [newer])
        with redirect_stdout(io.StringIO()) as said:
            self.assertEqual(fault_status.main([], [{"id": "a"}, {"id": "b"}], self.root, "status"), 0)
        self.assertIn("  FAIL             b", said.getvalue())

    def test_the_batch_is_the_ids_file_given(self):
        path = results(self.root / "suite", {"id": "a", "verdict": "PASS", "at": at(0)})
        ids = self.root / "ids.txt"
        ids.write_text("a stub\nb any\n", encoding="utf-8")   # as `list` writes them
        with redirect_stdout(io.StringIO()) as said:
            fault_status.main([str(path.parent), "--ids", str(ids)], [{"id": n} for n in "abcd"], self.root, "status")
        self.assertIn("Left: 1 of the batch's 2", said.getvalue())


if __name__ == "__main__":
    unittest.main()
