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

    def test_a_suite_takes_its_numbers_from_the_first_given(self):
        with docker_ps("autoflash-beken-emu-1"):
            self.assertEqual(list(emulator_instances.take("beken", 81, 2)), [81, 82])
            self.assertIn(("beken", 82), emulator_instances.HELD)
            with self.assertRaises(SystemExit) as refused:
                emulator_instances.take("beken", 1)
        self.assertIn("--first-instance 2", str(refused.exception))

    def test_every_suite_takes_the_first_instance_alike(self):
        import argparse
        parser = argparse.ArgumentParser()
        emulator_instances.add_first_instance(parser, commands="prep, batch")
        self.assertEqual(parser.parse_args([]).first_instance, 1)
        self.assertEqual(parser.parse_args(["--first-instance", "81"]).first_instance, 81)
        self.assertIn("prep, batch: the first emulator instance", parser.format_help())
        attended = argparse.ArgumentParser()
        emulator_instances.add_first_instance(attended, default=61)
        self.assertEqual(attended.parse_args([]).first_instance, 61)
        # Each family's fault suite gives it through this one, so they cannot drift apart.
        root = Path(__file__).resolve().parents[2]
        for suite in ("tools/espressif/emulator/fault_suite.py", "tools/beken/emulator/fault_suite.py"):
            text = (root / suite).read_text(encoding="utf-8")
            self.assertIn("emulator_instances.add_first_instance(parser", text, suite)
            self.assertNotIn('add_argument("--first-instance"', text, suite)

    def test_the_first_free_run_of_numbers_is_found(self):
        self.assertEqual(emulator_instances.first_free(set(), 3), 1)
        self.assertEqual(emulator_instances.first_free({1, 4}, 2), 2)
        self.assertEqual(emulator_instances.first_free({2, 5}, 3), 6)



class ClaimTests(unittest.TestCase):
    def setUp(self):
        own_locks(self)

    def test_the_tests_read_no_claim_another_suite_holds(self):
        # Tests run beside other suites' emulators: their claims must not change what a test is given.
        held_elsewhere(self, "espressif", 2)
        with mock.patch.dict(emulator_instances.os.environ, {"AUTOFLASH_EMU_NO_CLAIMS": "1"}):
            self.assertEqual(emulator_instances.claims(), [])
            self.assertEqual(emulator_instances.claimed_elsewhere("espressif"), set())

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


class FreeTests(unittest.TestCase):
    """What a device's check runs on when given none (emulator_check.py):
    the first instances free here, claimed."""

    def setUp(self):
        own_locks(self)

    def test_one_running_or_claimed_by_another_is_passed_over(self):
        held_elsewhere(self, "espressif", 2)
        with docker_ps("autoflash-emu-1", "autoflash-beken-emu-3"):
            self.assertEqual(emulator_instances.take_free("espressif", 2), [3, 4])
        self.assertEqual(sorted(emulator_instances.HELD), [("espressif", 3), ("espressif", 4)])

    def test_one_claimed_between_the_look_and_the_claim_is_passed_over(self):
        # Two checks started at once both saw 1 free; the one that claimed it second takes the next.
        held_elsewhere(self, "espressif", 1)
        with docker_ps(), mock.patch.object(emulator_instances, "claimed_elsewhere", return_value=set()):
            self.assertEqual(emulator_instances.take_free("espressif", 2), [2, 3])

    def test_none_free_is_refused(self):
        with docker_ps(*(f"autoflash-emu-{n}" for n in range(2, emulator_instances.LAST + 1))), \
                self.assertRaises(SystemExit) as refused:
            emulator_instances.take_free("espressif", 2)
        self.assertIn("fewer than 2 espressif emulator instances are free here", str(refused.exception))
        self.assertEqual(emulator_instances.HELD, {})

    def test_two_checks_started_at_once_get_different_ones(self):
        # Each holds its claim until told to end, as a check does while it runs.
        script = ("import os, sys; from pathlib import Path; from tools.repo import emulator_instances as e; "
                  "e.LOCKS = Path(sys.argv[1]); e.running = lambda family: set(); "
                  "print(e.take_free('espressif', 2), flush=True); sys.stdin.read()")
        root = Path(__file__).resolve().parents[2]
        env = {**emulator_instances.os.environ, "AUTOFLASH_EMU_NO_CLAIMS": "0"}
        checks = [subprocess.Popen([sys.executable, "-c", script, str(emulator_instances.LOCKS)], cwd=root, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            taken = [check.stdout.readline().strip() for check in checks]
        finally:
            for check in checks:
                check.communicate("", timeout=60)
        self.assertEqual(sorted(taken), ["[1, 2]", "[3, 4]"])

    def test_those_given_are_refused_where_another_runs_one_naming_free_ones(self):
        with docker_ps("autoflash-emu-1"), self.assertRaises(SystemExit) as refused:
            emulator_instances.chosen("espressif", [1, 2], 2)
        self.assertIn("espressif emulator instance 1 already running here: give --instances 2,3 to keep clear "
                      "of it, or leave out --instances for free ones, or stop it", str(refused.exception))
        held_elsewhere(self, "espressif", 5)
        with docker_ps(), self.assertRaises(SystemExit) as refused:
            emulator_instances.chosen("espressif", [5, 6], 2)
        self.assertIn("5 claimed by another suite here", str(refused.exception))
        self.assertIn("give --instances 1,2", str(refused.exception))
        with docker_ps():
            self.assertEqual(emulator_instances.chosen("espressif", [7, 8], 2), [7, 8])
        self.assertIn(("espressif", 8), emulator_instances.HELD)

    def test_those_chosen_are_said(self):
        import io
        from contextlib import redirect_stdout
        with docker_ps("autoflash-emu-1"), redirect_stdout(io.StringIO()) as said:
            self.assertEqual(emulator_instances.chosen("espressif", None, 2), [2, 3])
        self.assertEqual(said.getvalue(), "Emulator instances 2, 3: the first free here.\n")


if __name__ == "__main__":
    unittest.main()
