"""Which emulator instances already run here (tools/repo/emulator_instances.py)."""

import subprocess
import unittest
from unittest import mock

from tools.repo import emulator_instances


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

    def test_the_first_free_run_of_numbers_is_found(self):
        self.assertEqual(emulator_instances.first_free(set(), 3), 1)
        self.assertEqual(emulator_instances.first_free({1, 4}, 2), 2)
        self.assertEqual(emulator_instances.first_free({2, 5}, 3), 6)


if __name__ == "__main__":
    unittest.main()
