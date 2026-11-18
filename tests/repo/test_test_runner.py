"""The unit tests' own runner (tests/run.py): a test that leaves the tool's
screen state set is reported, as it changes how the next test in the same
worker process draws; so is one that leaves anything where the tool keeps
what it records, which is never your captures/."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import output

import run


class LeftBehindTests(unittest.TestCase):
    def test_a_screen_state_left_set_is_named_and_cleared(self):
        with mock.patch.multiple(output._s, after_blank=False, after_stage=False, waited=False):
            self.assertEqual(run.left_behind(), [])
            output._s.after_blank = True
            self.assertEqual(run.left_behind(), ["after_blank"])
            self.assertFalse(output._s.after_blank)   # the next test starts clean

    def test_every_state_named_is_one_the_screen_keeps(self):
        for name in run.SCREEN_STATE:
            self.assertIsInstance(getattr(output._s, name), bool, name)


class CapturesTests(unittest.TestCase):
    def test_what_a_test_left_in_its_captures_is_named_and_cleared(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(run, "CAPTURES", Path(folder)):
            self.assertEqual(run.left_in_captures(), [])
            (Path(folder) / "readouts-to-send").mkdir()
            (Path(folder) / "readouts-to-send" / "r.tar").write_bytes(b"")
            (Path(folder) / "settings.json").write_text("{}", encoding="utf-8")
            self.assertEqual(run.left_in_captures(), ["readouts-to-send/r.tar", "settings.json"])
            self.assertEqual(list(Path(folder).iterdir()), [])   # the next test starts clean

    def test_a_worker_has_its_own_before_the_tool_is_loaded(self):
        # Set as the worker starts, so records.CAPTURES - read as the module
        # loads - is the worker's, and every path made from it.
        with tempfile.TemporaryDirectory() as folder:
            code = ("import run, sys; run.setup_path(captures=sys.argv[1]); "
                    "from tools.autoflash import readout, records, runs; "
                    "print(records.CAPTURES == run.CAPTURES, readout.TO_SEND.parent == run.CAPTURES, "
                    "runs.SETTINGS_PATH.parent == run.CAPTURES, run.CAPTURES.parent == __import__('pathlib').Path(sys.argv[1]))")
            done = subprocess.run([sys.executable, "-c", code, folder], cwd=run.TESTS, capture_output=True, text=True,
                                  env={**os.environ, "AUTOFLASH_CAPTURES": ""}, timeout=60)
            self.assertEqual(done.stdout.split(), ["True"] * 4, done.stderr)


class NamesTests(unittest.TestCase):
    def test_a_name_that_matches_no_test_is_an_error_not_an_ok(self):
        # Seen: tests.autoflash, for autoflash, ran 0 tests and said OK.
        ids, broken = run.collect(["tests.autoflash"])
        self.assertEqual(ids, [])
        self.assertEqual([name for name, _ in broken], ["tests.autoflash"])
        ids, broken = run.collect(["repo.test_test_runner"])
        self.assertTrue(ids)
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
