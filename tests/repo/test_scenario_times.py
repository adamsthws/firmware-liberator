import json
import tempfile
import unittest
from pathlib import Path

from tools.repo import scenario_times


def results(folder: Path, *rows: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class TimesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_a_row_without_its_time_takes_the_gap_since_its_instances_last(self):
        # The Espressif suite's rows: each instance's worker runs one scenario after another.
        results(self.root / "suite",
                {"id": "a", "instance": 1, "finished": "2026-11-08 10:00:00"},
                {"id": "b", "instance": 2, "finished": "2026-11-08 10:01:00"},
                {"id": "c", "instance": 1, "finished": "2026-11-08 10:05:00"},
                {"id": "d", "instance": 2, "finished": "2026-11-08 10:31:00"})
        self.assertEqual(scenario_times.times(self.root), {"c": 300.0, "d": 1800.0})

    def test_a_row_saying_its_own_time_is_taken_at_its_word(self):
        results(self.root / "suite", {"id": "a", "at": "2026-11-08T10:00:00", "seconds": 412})
        self.assertEqual(scenario_times.times(self.root), {"a": 412.0})

    def test_the_latest_run_of_a_scenario_counts(self):
        results(self.root / "one", {"id": "a", "at": "2026-11-07T10:00:00", "seconds": 100})
        results(self.root / "two", {"id": "a", "at": "2026-11-08T10:00:00", "seconds": 200})
        self.assertEqual(scenario_times.times(self.root), {"a": 200.0})

    def test_a_pause_between_batches_is_not_a_scenario(self):
        results(self.root / "suite",
                {"id": "a", "instance": 1, "finished": "2026-11-08 01:00:00"},
                {"id": "b", "instance": 1, "finished": "2026-11-08 09:00:00"})
        self.assertEqual(scenario_times.times(self.root), {})

    def test_each_family_reads_its_own_suites_only(self):
        # The Beken suite's are in beken/, a level down from the Espressif suite's.
        results(self.root / "suite", {"id": "a", "at": "2026-11-08T10:00:00", "seconds": 100})
        results(self.root / "beken/suite", {"id": "b", "at": "2026-11-08T10:00:00", "seconds": 100})
        self.assertEqual(scenario_times.times(self.root), {"a": 100.0})
        self.assertEqual(scenario_times.times(self.root / "beken"), {"b": 100.0})


class OrderTests(unittest.TestCase):
    def test_the_longest_start_first(self):
        found = scenario_times.longest_first([{"id": "short"}, {"id": "long"}, {"id": "middle"}],
                                             {"short": 60, "long": 1800, "middle": 300})
        self.assertEqual([sc["id"] for sc in found], ["long", "middle", "short"])

    def test_one_never_run_is_put_as_the_typical_one_and_its_faults_length(self):
        took = {"a": 100, "b": 200, "c": 300}
        found = scenario_times.longest_first([{"id": "a"}, {"id": "c"}, {"id": "new", "arg": 180}], took)
        self.assertEqual([sc["id"] for sc in found], ["new", "c", "a"])   # 200 + 180
        found = scenario_times.longest_first([{"id": "a"}, {"id": "c"}, {"id": "new", "arg": "once"}], took)
        self.assertEqual([sc["id"] for sc in found], ["c", "new", "a"])   # 200

    def test_those_as_long_keep_their_order(self):
        found = scenario_times.longest_first([{"id": "x"}, {"id": "y"}], {})
        self.assertEqual([sc["id"] for sc in found], ["x", "y"])


if __name__ == "__main__":
    unittest.main()
