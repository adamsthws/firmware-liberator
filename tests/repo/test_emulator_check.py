"""Which devices a change checks, and what each device's check runs."""

import unittest
from pathlib import Path

from tools.autoflash import kinds
from tools.repo import emulator_check as check

ROOT = Path(__file__).resolve().parents[2]


def keys(changed):
    return {job["device"] for job in check.jobs(changed)}


class WhichDevicesTests(unittest.TestCase):
    ALL = {kind.key for kind in kinds.READY} - check.NO_DUMP
    ESPRESSIF = {key for key in ALL if check.family_of(kinds.find(key)) == "espressif"}

    def test_every_device_with_no_change_named(self):
        self.assertEqual(keys(None), self.ALL)

    def test_a_device_that_is_ready_has_a_check_by_being_added(self):
        for kind in kinds.READY:
            self.assertIn(check.family_of(kind), check.EMULATORS, kind.key)

    def test_the_s61s_own_files_check_the_s61_alone(self):
        for path in ("devices/sonoff-s61/esphome.yaml", "tools/beken/rbl.py"):
            self.assertEqual(keys([path]), {"sonoff-s61"}, path)

    def test_one_devices_config_checks_that_device(self):
        self.assertEqual(keys(["devices/sonoff-powct/esphome.yaml"]), {"sonoff-powct"})

    def test_the_espressif_tools_check_every_espressif_device(self):
        self.assertEqual(keys(["tools/espressif/flashmap.py"]), self.ESPRESSIF)

    def test_what_all_of_them_run_on_checks_all_of_them(self):
        for path in ("tools/autoflash/plans.py", "artifacts/firmware.json", "tools/ewelink/ota_push.py",
                     ".github/workflows/emulator-check.yml", "tools/repo/emulator_check.py"):
            self.assertEqual(keys([path]), self.ALL, path)

    def test_a_chips_shared_files_check_the_devices_on_it(self):
        for chip in ("esp32", "esp32c3"):
            found = keys([f"devices/{chip}/mover.yaml"])
            self.assertEqual(found, {kind.key for kind in kinds.READY if kind.chip == chip} - check.NO_DUMP, chip)
        self.assertNotIn("sonoff-s60", keys(["devices/esp32/mover.yaml"]))

    def test_nothing_a_conversion_runs_checks_nothing(self):
        self.assertEqual(keys(["docs/testing.md", "tests/repo/test_layout.py", "website/src/site.mjs"]), set())

    def test_a_commit_git_cannot_compare_with_checks_every_device(self):
        self.assertIsNone(check.changed_since("0" * 40))


class WaysTests(unittest.TestCase):
    def test_each_final_firmware_then_the_other(self):
        self.assertEqual(check.ESPRESSIF_WAYS, (("tasmota", "esphome"), ("esphome", "tasmota")))

    def test_every_device_has_something_to_run_from_or_is_named_as_having_nothing(self):
        from tools.espressif.emulator import public_dump
        runs_from = {*public_dump.DUMPS, *public_dump.UPDATES,
                     "sonoff-s60",   # the published public plugs
                     "sonoff-s61"}   # upstream's public dump (bk-emu.sh)
        for kind in kinds.READY:
            self.assertTrue(kind.key in runs_from or kind.key in check.NO_DUMP, kind.key)

    def test_a_device_named_as_having_no_dump_really_has_none(self):
        from tools.espressif.emulator import public_dump
        self.assertFalse(check.NO_DUMP & {*public_dump.DUMPS, *public_dump.UPDATES})


class SideBySideTests(unittest.TestCase):
    def test_a_job_that_raises_has_failed_and_says_why(self):
        import io
        from contextlib import redirect_stdout
        from tools.espressif.emulator import public_plugs

        def broken():
            raise RuntimeError("the emulator did not start")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(public_plugs.side_by_side([lambda: 0, broken]), [0, 1])
        self.assertIn("the emulator did not start", out.getvalue())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/emulator-check.yml").read_text(encoding="utf-8")

    def test_the_required_check_keeps_its_name(self):
        from tools.repo import github_settings
        self.assertIn("emulator", github_settings.REQUIRED_CHECKS)
        self.assertRegex(self.workflow, r"(?m)^  emulator:$")

    def test_each_device_has_its_own_job(self):
        self.assertIn("emulator_check.py list", self.workflow)
        self.assertIn('emulator_check.py "$DEVICE"', self.workflow)

    def test_a_release_checks_every_device(self):
        self.assertIn("every_device", self.workflow)
