"""Which emulator instances already run here (tools/repo/emulator_instances.py)."""

import fcntl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import emulator_instances


def own_locks(test):
    """Claims made in a folder of the test's own, none held yet: never the
    computer's."""
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    for patcher in (mock.patch.dict(emulator_instances.os.environ, {"AUTOFLASH_EMU_NO_CLAIMS": "0"}),
                    mock.patch.object(emulator_instances, "LOCKS", Path(folder.name)),
                    mock.patch.object(emulator_instances, "HELD", {})):
        patcher.start()
        test.addCleanup(patcher.stop)
    test.addCleanup(lambda: [handle.close() for handle in emulator_instances.HELD.values()])


def held_elsewhere(test, family, number):
    """`number` claimed as another suite would: its own open lock file."""
    emulator_instances.LOCKS.mkdir(parents=True, exist_ok=True)
    handle = open(emulator_instances.lock_path(family, number), "a+", encoding="utf-8")  # noqa: SIM115
    test.addCleanup(handle.close)
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle.write("4242 /home/someone/their-checkout 2026-11-19T01:00:00\n")
    handle.flush()


def docker_ps(*names):
    return mock.patch.object(emulator_instances.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, stdout="".join(f"{n}\n" for n in names)))


class RunningTests(unittest.TestCase):
    def test_each_familys_containers_are_counted_by_their_instance(self):
        with docker_ps("autoflash-emu-3", "autoflash-emu-4-api", "autoflash-beken-emu-81",
                       "autoflash-beken-emu-computer-82", "autoflash-beken-emu-83-api", "autoflash-emu-computer",
                       "someone-elses-container"):
            self.assertEqual(emulator_instances.running("espressif"), {3, 4})
            self.assertEqual(emulator_instances.running("beken"), {81, 82, 83})

    def test_no_docker_is_none_running(self):
        with mock.patch.object(emulator_instances.subprocess, "run", side_effect=FileNotFoundError("docker")):
            self.assertEqual(emulator_instances.running("beken"), set())


class RefuseTests(unittest.TestCase):
    def setUp(self):
        # Whatever the computer running the tests has set.
        patcher = mock.patch.dict(emulator_instances.os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in emulator_instances.OLD_SETTINGS:
            emulator_instances.os.environ.pop(name, None)
        own_locks(self)

    def test_numbers_in_use_are_refused_naming_where_to_start_instead(self):
        with docker_ps("autoflash-beken-emu-1", "autoflash-beken-emu-4"):
            with self.assertRaises(SystemExit) as refused:
                emulator_instances.refuse_taken("beken", range(1, 3))
        self.assertIn("beken emulator instance 1 already running", str(refused.exception))
        self.assertIn("--first-instance 2", str(refused.exception))

    def test_the_old_setting_is_refused_naming_the_option_to_give(self):
        with docker_ps(), mock.patch.dict(emulator_instances.os.environ, {"AUTOFLASH_EMU_FIRST_INSTANCE": "81"}):
            with self.assertRaises(SystemExit) as refused:
                emulator_instances.refuse_taken("espressif", [1])
        self.assertIn("AUTOFLASH_EMU_FIRST_INSTANCE is no longer read", str(refused.exception))
        self.assertIn("--first-instance 81", str(refused.exception))

    def test_free_numbers_pass(self):
        with docker_ps("autoflash-beken-emu-1", "autoflash-emu-2"):
            emulator_instances.refuse_taken("beken", [2, 3])

    def test_numbers_the_emulator_does_not_take_are_refused_before_docker_is_asked(self):
        # --first-instance 101: unattended-test.sh refused every run with its usage.
        with mock.patch.object(emulator_instances, "running") as running:
            with self.assertRaises(SystemExit) as refused:
                emulator_instances.refuse_taken("espressif", range(101, 105))
        running.assert_not_called()
        self.assertIn("101, 102, 103, 104 out of range", str(refused.exception))
        self.assertIn("--first-instance at most 96 for 4", str(refused.exception))
        with docker_ps(), self.assertRaises(SystemExit):
            emulator_instances.refuse_taken("beken", [0])
        with docker_ps():
            emulator_instances.refuse_taken("beken", range(96, 100))

    def test_no_free_number_is_suggested_past_the_last(self):
        taken = set(range(1, emulator_instances.LAST - 1))
        self.assertIsNone(emulator_instances.first_free(taken, 3))
        with docker_ps(*(f"autoflash-emu-{n}" for n in taken)):
            with self.assertRaises(SystemExit) as refused:
                emulator_instances.refuse_taken("espressif", [1, 2, 3])
        self.assertIn("stop it, or run fewer at once", str(refused.exception))

    def test_the_last_is_what_the_emulator_scripts_take(self):
        root = Path(__file__).resolve().parents[2]
        for script in ("tools/espressif/emulator/emu.sh", "tools/beken/emulator/beken-emu.sh",
                       "tools/espressif/emulator/unattended-test.sh"):
            text = (root / script).read_text(encoding="utf-8")
            self.assertIn("[1-9][0-9]", text, script)
            self.assertNotIn("[1-9][0-9][0-9]", text, script)
        self.assertEqual(emulator_instances.LAST, 99)

    def test_the_first_free_run_of_numbers_is_found(self):
        self.assertEqual(emulator_instances.first_free(set(), 3), 1)
        self.assertEqual(emulator_instances.first_free({1, 4}, 2), 2)
        self.assertEqual(emulator_instances.first_free({2, 5}, 3), 6)



class ClaimTests(unittest.TestCase):
    def setUp(self):
        own_locks(self)

    def test_a_number_another_suite_holds_is_refused_while_its_emulator_is_stopped(self):
        # Seen: a suite restarting its emulator between scenarios left a gap
        # docker ps could not show, and another suite took its number.
        held_elsewhere(self, "espressif", 1)
        with docker_ps(), self.assertRaises(SystemExit) as refused:
            emulator_instances.refuse_taken("espressif", [1, 2])
        self.assertIn("espressif emulator instance 1 claimed by another suite", str(refused.exception))
        self.assertIn("--first-instance 2", str(refused.exception))
        self.assertEqual(emulator_instances.HELD, {}, "none claimed where one is refused")

    def test_each_family_claims_its_own_numbers(self):
        held_elsewhere(self, "beken", 81)
        with docker_ps():
            emulator_instances.refuse_taken("espressif", [81])
        self.assertIn(("espressif", 81), emulator_instances.HELD)

    def test_a_suite_claims_again_what_it_holds_already(self):
        with docker_ps():
            emulator_instances.refuse_taken("espressif", [1, 2])
            emulator_instances.refuse_taken("espressif", [2])
        self.assertEqual(sorted(emulator_instances.HELD), [("espressif", 1), ("espressif", 2)])

    def test_a_claim_ends_with_the_suite_that_made_it(self):
        script = ("import os, sys; os.environ.pop('AUTOFLASH_EMU_NO_CLAIMS', None); from pathlib import Path; from tools.repo import emulator_instances as e; "
                  "e.LOCKS = Path(sys.argv[1]); print(e.claim('beken', [5]))")
        root = Path(__file__).resolve().parents[2]
        ended = subprocess.run([sys.executable, "-c", script, str(emulator_instances.LOCKS)], cwd=root,
                               capture_output=True, text=True, check=True, timeout=60)
        self.assertEqual(ended.stdout.strip(), "[]")
        self.assertEqual(emulator_instances.claim("beken", [5]), [])

    def test_the_tests_claim_nothing_on_the_computer(self):
        # Seen: the suites' own tests, Docker stubbed, took real locks under
        # the home folder, and tests run side by side refused each other.
        with mock.patch.dict(emulator_instances.os.environ, {"AUTOFLASH_EMU_NO_CLAIMS": "1"}):
            self.assertEqual(emulator_instances.claim("espressif", [1]), [])
        self.assertEqual(emulator_instances.HELD, {})
        self.assertFalse(any(emulator_instances.LOCKS.iterdir()))

    def test_the_claims_listed_say_whose_each_is(self):
        held_elsewhere(self, "beken", 81)
        emulator_instances.claim("espressif", [3])
        listed = emulator_instances.claims()
        self.assertIn("beken-81  4242 /home/someone/their-checkout 2026-11-19T01:00:00", listed)
        mine = next(line for line in listed if line.startswith("espressif-3 "))
        self.assertIn(f" {emulator_instances.os.getpid()} {emulator_instances.CHECKOUT} ", mine)
        emulator_instances.HELD.pop(("espressif", 3)).close()
        self.assertEqual([line for line in emulator_instances.claims() if line.startswith("espressif")], [])


if __name__ == "__main__":
    unittest.main()
