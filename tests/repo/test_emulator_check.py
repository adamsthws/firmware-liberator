"""Which devices a change checks, and what each device's check runs."""

import dataclasses
import hashlib
import io
import os
import re
import tempfile
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.autoflash import kinds
from tools.autoflash.routes import public_copies
from tools.repo import emulator_check as check

ROOT = Path(__file__).resolve().parents[2]


def keys(changed):
    return {job["device"] for job in check.jobs(changed)}


class WhichDevicesTests(unittest.TestCase):
    ALL = {kind.key for kind in kinds.READY} - check.NOTHING_TO_RUN
    ESPRESSIF = {key for key in ALL if check.family_of(kinds.find(key)) == "espressif"}

    def test_a_check_missing_a_package_says_what_to_install_before_it_starts(self):
        # Seen on a machine run with a Python without the NVS generator: the POW
        # Elite's check stopped part way through making its public dump, a traceback.
        pow_elite = next(kind for kind in kinds.READY if kind.key == "sonoff-pow-elite")
        with mock.patch("importlib.util.find_spec", return_value=None), \
                mock.patch.object(check, "check_device") as checked, \
                mock.patch("sys.stderr") as said:
            self.assertEqual(check.main(["sonoff-pow-elite"]), 2)
            self.assertIn("esp_idf_nvs_partition_gen", check.missing_packages(pow_elite))
        checked.assert_not_called()
        told = "".join(call.args[0] for call in said.write.call_args_list)
        self.assertIn("esp_idf_nvs_partition_gen", told)
        self.assertIn("requirements/ci.txt", told)
        s61 = next(kind for kind in kinds.READY if kind.key == "sonoff-s61")
        with mock.patch("importlib.util.find_spec", return_value=None):
            self.assertIsNone(check.missing_packages(s61), "a Beken device's check needs none of them")

    def test_every_device_with_no_change_named(self):
        self.assertEqual(keys(None), self.ALL)

    def test_a_device_that_is_ready_has_a_check_by_being_added(self):
        for kind in kinds.READY:
            self.assertIn(check.family_of(kind), check.EMULATORS, kind.key)

    def test_the_s61s_own_files_check_the_s61_alone(self):
        self.assertEqual(keys(["devices/sonoff-s61/esphome.yaml"]), {"sonoff-s61"})

    def test_the_basic_gen5_with_no_public_dump_has_a_check_of_its_own(self):
        self.assertIn("sonoff-basic-1gs", keys(None))
        self.assertEqual(keys(["devices/sonoff-basic-1gs/esphome.yaml"]), {"sonoff-basic-1gs"})

    def test_the_beken_tools_check_every_beken_device(self):
        # rebuild_dump.py among them, which makes the Basic Gen5's flash.
        for path in ("tools/beken/rbl.py", "tools/beken/rebuild_dump.py"):
            self.assertEqual(keys([path]), {"sonoff-s61", "sonoff-basic-1gs"}, path)

    def test_one_devices_config_checks_that_device(self):
        self.assertEqual(keys(["devices/sonoff-powct/esphome.yaml"]), {"sonoff-powct"})

    def test_the_espressif_tools_check_every_espressif_device(self):
        self.assertEqual(keys(["tools/espressif/flashmap.py"]), self.ESPRESSIF)

    def test_what_all_of_them_run_on_checks_all_of_them(self):
        for path in ("tools/autoflash/plans.py", "artifacts/firmware.json", "tools/ewelink/ota_push.py",
                     ".github/workflows/emulator-check.yml", "tools/repo/emulator_check.py",
                     "devices/shared-esphome/base.yaml"):
            self.assertEqual(keys([path]), self.ALL, path)

    def test_a_chips_shared_files_check_the_devices_on_it(self):
        for chip in ("esp32", "esp32c3"):
            found = keys([f"devices/{chip}/mover.yaml"])
            self.assertEqual(found, {kind.key for kind in kinds.READY if kind.chip == chip} - check.NOTHING_TO_RUN, chip)
        self.assertNotIn("sonoff-s60", keys(["devices/esp32/mover.yaml"]))

    def test_a_device_with_a_motor_mode_has_a_job_for_each_motor_copy_beside_its_own(self):
        from tools.espressif.emulator import public_dump
        found = check.jobs(["devices/sonoff-dualr3/esphome.yaml"])
        self.assertEqual([job.get("motor", "") for job in found], ["", *public_dump.MOTOR_COPIES["sonoff-dualr3"]])
        self.assertEqual([job["name"] for job in found], ["sonoff-dualr3", "sonoff-dualr3 motor",
                                                          "sonoff-dualr3 motor calibrated", "sonoff-dualr3 motor one-button"])
        # The workflow runs each as DEVICE --motor COPY: a copy --motor takes.
        with mock.patch.object(check, "missing_packages", return_value=None), \
                mock.patch.object(check, "check_public_dump", return_value=0) as ran:
            for job in found[1:]:
                self.assertEqual(check.main([job["device"], "--motor", job["motor"]]), 0)
                self.assertEqual(ran.call_args.args[2], job["motor"])
        every = check.jobs(None)
        self.assertEqual(len({job["name"] for job in every}), len(every), "each job's logs are kept under its name")
        self.assertFalse([job for job in every if job.get("motor") and not kinds.find(job["device"]).motor_mode])

    def test_the_workflow_runs_a_motor_job_as_its_motor_copy_under_its_own_name(self):
        workflow = (ROOT / ".github/workflows/emulator-check.yml").read_text(encoding="utf-8")
        self.assertIn('emulator_check.py "$DEVICE" ${MOTOR:+--motor "$MOTOR"}', workflow)
        self.assertIn("MOTOR: ${{ matrix.motor }}", workflow)
        self.assertIn("name: emulator (${{ matrix.name }})", workflow)
        self.assertIn("name: emulator-logs-${{ matrix.name }}", workflow)

    def test_nothing_a_conversion_runs_checks_nothing(self):
        self.assertEqual(keys(["docs/testing.md", "tests/repo/test_layout.py", "website/src/site.mjs"]), set())

    def test_a_commit_git_cannot_compare_with_checks_every_device(self):
        self.assertIsNone(check.changed_since("0" * 40))


class BackTests(unittest.TestCase):
    """Back to Sonoff's firmware: its own partitions behind its app slots as before."""

    def problem(self, flash: bytes, stock: bytes) -> str | None:
        from tools.espressif import layouts
        from tools.espressif.emulator import fault_suite
        with tempfile.TemporaryDirectory() as emu:
            (Path(emu) / "state-91").mkdir()
            (Path(emu) / "state-91" / "flash.bin").write_bytes(flash)
            with mock.patch.object(fault_suite, "EMU", Path(emu)):
                return check.back_problem(stock, 91, layouts.SONOFF_BASICR4)

    def test_fctry_as_the_test_copy_has_it_passes_and_written_over_fails(self):
        stock = bytearray(b"\xff" * 0x400000)
        stock[0x3F0000:0x3F6000] = b"\x5a" * 0x6000
        self.assertIsNone(self.problem(bytes(stock), bytes(stock)))
        over = bytearray(stock)
        over[0x3F0000:0x3F6000] = bytes([check.TASMOTA_WROTE]) * 0x6000
        self.assertEqual(self.problem(bytes(over), bytes(stock)), "fctry not as before")

    def test_only_a_device_with_a_public_dump(self):
        # Refused as a wrong argument, before the packages are looked for: on a
        # computer without them, as without the virtual environment.
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit), \
                mock.patch("sys.stderr", io.StringIO()), mock.patch.dict(os.environ), \
                mock.patch.object(check, "missing_packages", return_value="a package is missing"):
            check.main(["sonoff-s60", "--back"])


class WaysTests(unittest.TestCase):
    def test_each_final_firmware_then_the_other(self):
        self.assertEqual(check.ESPRESSIF_WAYS, (("tasmota", "esphome"), ("esphome", "tasmota")))

    def test_every_device_has_something_to_run_from_or_is_named_as_having_nothing(self):
        for kind in kinds.READY:
            self.assertTrue(kind.key in runs_from() or kind.key in check.NOTHING_TO_RUN, kind.key)

    def test_a_device_named_as_having_nothing_to_run_really_has_nothing(self):
        self.assertFalse(check.NOTHING_TO_RUN & runs_from())

    def test_only_a_beken_device_with_no_public_dump_runs_from_a_rebuilt_flash(self):
        self.assertTrue(check.rebuilt(kinds.find("sonoff-basic-1gs")))
        self.assertFalse(check.rebuilt(kinds.find("sonoff-s61")))


def runs_from():
    """Each device that has something for its check to start from."""
    from tools.espressif.emulator import public_dump
    return {*public_dump.DUMPS, *public_dump.UPDATES,
            "sonoff-s60",              # the published public devices
            check.REBUILT_FROM,        # upstream's public dump (beken-emu.sh)
            # a flash rebuilt from the S61's and its official file
            *(kind.key for kind in kinds.EVERY if kind.family == "beken")}


class CoresTests(unittest.TestCase):
    def test_a_check_whose_two_emulators_need_more_cores_than_the_computer_has_runs_saying_so(self):
        for key, cores, short in (("sonoff-dualr3", 3, True), ("sonoff-dualr3", 4, False), ("sonoff-s60", 2, False)):
            with self.subTest(key=key, cores=cores), redirect_stdout(io.StringIO()) as said, \
                    mock.patch.object(check.cpu_load.os, "cpu_count", return_value=cores), \
                    mock.patch.object(check, "check_public_dump", return_value=0) as ran, \
                    mock.patch.object(check.subprocess, "call", return_value=0), \
                    mock.patch.object(check, "ROOT", Path("/nowhere")):
                self.assertEqual(check.check_device(kinds.find(key), [3, 4]), 0)
            self.assertEqual("2 emulators at once need 4 cores" in said.getvalue(), short)
            self.assertEqual(ran.called, key == "sonoff-dualr3")


class BekenCheckTests(unittest.TestCase):
    """A Beken device's check: the fault suite's, on the emulators asked for,
    its logs where an Espressif device's go, from the S61's dump or a flash
    rebuilt for the device."""

    def run_check(self, key, environ=None):
        calls = []

        def call(command, env):
            calls.append((command, env))
            return 0
        flash = Path("/nowhere/sonoff-basic-1gs.bin")
        with mock.patch.dict(os.environ, environ or {}), mock.patch.object(check.subprocess, "run"), \
                mock.patch.object(check.subprocess, "call", side_effect=call), \
                mock.patch.object(check, "rebuilt_flash", return_value=flash) as rebuilt:
            self.assertEqual(check.check_device(kinds.find(key), [3, 4]), 0)
        ((command, env),) = calls
        return command, env, rebuilt

    def test_the_basic_gen5_runs_from_its_rebuilt_flash_on_the_emulators_asked_for(self):
        command, env, rebuilt = self.run_check("sonoff-basic-1gs")
        self.assertEqual(command[1:], ["check", "--instances", "3,4"])
        self.assertEqual(env["AUTOFLASH_BEKEN_EMU_FLASH"], "/nowhere/sonoff-basic-1gs.bin")
        self.assertEqual(env["AUTOFLASH_BEKEN_EMU_SUITE_DIR"], str(check.LOGS / "sonoff-basic-1gs"))
        rebuilt.assert_called_once()

    def test_the_s61_runs_from_its_public_dump_whatever_flash_was_named(self):
        command, env, rebuilt = self.run_check("sonoff-s61", {"AUTOFLASH_BEKEN_EMU_FLASH": "captures/other.bin"})
        self.assertEqual(command[1:], ["check", "--instances", "3,4"])
        self.assertNotIn("AUTOFLASH_BEKEN_EMU_FLASH", env)
        self.assertEqual(env["AUTOFLASH_BEKEN_EMU_SUITE_DIR"], str(check.LOGS / "sonoff-s61"))
        rebuilt.assert_not_called()


class WhereTheRebuiltFlashIsTests(unittest.TestCase):
    def test_in_the_beken_emulators_folders_in_captures_which_git_never_tracks(self):
        from tools.beken.emulator import folders
        self.assertEqual(folders.REBUILT, ROOT / "captures/emulation/beken/rebuilt")
        self.assertIn("captures/", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())


class RebuiltFlashTests(unittest.TestCase):
    """The Basic Gen5's flash, made from the S61's public dump and its official
    file, kept in the Beken emulator's folders under captures/."""

    def setUp(self):
        from tools.beken.emulator import folders
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dumps, self.rebuilt = Path(tmp.name, "firmwares"), Path(tmp.name, "rebuilt")
        self.dumps.mkdir()
        self.dump = b"the S61's dump"
        (self.dumps / check.S61_DUMP[0]).write_bytes(self.dump)
        for patch in (mock.patch.object(folders, "FIRMWARES", self.dumps), mock.patch.object(folders, "REBUILT", self.rebuilt),
                      mock.patch.object(check, "ROOT", Path(tmp.name)),
                      mock.patch.object(check, "S61_DUMP", (check.S61_DUMP[0], hashlib.sha256(self.dump).hexdigest()))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_made_from_the_s61s_dump_and_the_devices_official_file(self):
        import io
        from contextlib import redirect_stdout
        from tools.beken import rebuild_dump
        kind = kinds.find("sonoff-basic-1gs")
        with mock.patch.object(public_copies, "fetch", return_value=b"official") as official, \
                mock.patch.object(rebuild_dump, "rebuild", return_value=b"flash") as rebuild, \
                redirect_stdout(io.StringIO()):
            out = check.rebuilt_flash(kind)
        official.assert_called_once_with(kind)
        rebuild.assert_called_once_with(self.dump, b"official")
        self.assertEqual(out, self.rebuilt / "sonoff-basic-1gs.bin")
        self.assertEqual(out.read_bytes(), b"flash")
        self.assertEqual([path.name for path in self.rebuilt.iterdir()], ["sonoff-basic-1gs.bin"])

    def test_not_from_a_dump_other_than_the_one_pinned(self):
        (self.dumps / check.S61_DUMP[0]).write_bytes(b"another dump")
        with self.assertRaisesRegex(SystemExit, "not the public dump"):
            check.rebuilt_flash(kinds.find("sonoff-basic-1gs"))
        self.assertFalse(self.rebuilt.exists())

    def test_an_official_file_that_cannot_be_had_stops_the_check_saying_why(self):
        with mock.patch.object(public_copies, "fetch", side_effect=public_copies.Unavailable("could not be fetched")), \
                self.assertRaisesRegex(SystemExit, "could not be fetched"):
            check.rebuilt_flash(kinds.find("sonoff-basic-1gs"))


class MotorCheckTests(unittest.TestCase):
    """--motor: the test copy that drives a motor, each firmware then shown to
    drive it, its outputs never on together and off after a power cut."""

    def test_only_a_device_with_a_motor_mode_has_one(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            check.main(["sonoff-s60", "--motor"])
        with mock.patch.object(check, "missing_packages", return_value=None), \
                mock.patch.object(check, "check_public_dump", return_value=0) as ran:
            self.assertEqual(check.main(["sonoff-dualr3", "--motor"]), 0)
            self.assertEqual(ran.call_args.args[2], "motor")
            for copy in ("calibrated", "one-button"):
                self.assertEqual(check.main(["sonoff-dualr3", "--motor", copy]), 0)
                self.assertEqual(ran.call_args.args[2], copy)

    def test_every_motor_copy_can_be_checked(self):
        from tools.espressif.emulator import public_dump
        for copy in public_dump.MOTOR_COPIES["sonoff-dualr3"]:
            with self.subTest(copy), mock.patch.object(check, "missing_packages", return_value=None), \
                    mock.patch.object(check, "check_public_dump", return_value=0):
                self.assertEqual(check.main(["sonoff-dualr3", "--motor", copy]), 0)

    def test_each_ways_second_firmware_must_carry_what_its_first_was_set_to(self):
        from types import SimpleNamespace

        from tools.espressif.emulator import public_devices, public_dump
        calls = []

        def checked(instance, saved, firmware, stub, log, wanted, then=None, pins=None):
            calls.append((instance, firmware, wanted, then))
            self.assertEqual(pins, {"buttons": (0,), "switches": (32, 33)})   # pressed too
        copy = SimpleNamespace(mac="12:34:56:ab:cd:ef", settings='{"deviceMode":2}')
        with mock.patch.object(public_dump, "make"), mock.patch.object(public_dump, "recipe", return_value=copy), \
                mock.patch.object(public_devices, "run", return_value=(True, "Converted")), \
                mock.patch.object(public_devices, "side_by_side", lambda jobs: [job() for job in jobs]), \
                mock.patch.object(check, "emulator_stats"), mock.patch.object(check.subprocess, "run"), \
                mock.patch.object(check, "motor_problem", side_effect=checked), \
                mock.patch.object(check, "owner_rule_problem",
                                  side_effect=lambda instance, saved, stub, log, wanted, pins:
                                  calls.append((instance, "owner's rule", wanted))), \
                mock.patch("builtins.print"):
            self.assertEqual(check.check_public_dump(kinds.find("sonoff-dualr3"), [81, 82], "motor"), 0)
        default = check.motor_setup.DEFAULT
        moved = check.motor_setup.Setup(check.MOVED_TRAVEL, check.MOVED_TRAVEL, True, check.motor_setup.HOLD_TO_RUN,
                                        True)
        # Tasmota, first, then given the owner's rule in Rule1 and run again:
        # hold to run, with no rule of the tool's to write over, as it was.
        self.assertEqual(calls, [(81, "tasmota", default, moved), (81, "owner's rule", moved),
                                 (81, "esphome", moved, None),
                                 (82, "esphome", default, moved), (82, "tasmota", moved, None)])

    def test_the_owners_rule_over_the_tools_has_the_switches_from_the_backup_again(self):
        from types import SimpleNamespace

        from tools.espressif.emulator import public_devices, public_dump
        calls = []
        copy = SimpleNamespace(mac="12:34:56:ab:cd:ef", settings='{"deviceMode":2,"motorSwMode":3}')
        with mock.patch.object(public_dump, "make"), mock.patch.object(public_dump, "recipe", return_value=copy), \
                mock.patch.object(public_devices, "run", return_value=(True, "Converted")), \
                mock.patch.object(public_devices, "side_by_side", lambda jobs: [job() for job in jobs]), \
                mock.patch.object(check, "emulator_stats"), mock.patch.object(check.subprocess, "run"), \
                mock.patch.object(check, "motor_problem",
                                  side_effect=lambda instance, saved, firmware, stub, log, wanted, then=None, pins=None:
                                  calls.append((instance, firmware, wanted))), \
                mock.patch.object(check, "owner_rule_problem",
                                  side_effect=lambda instance, saved, stub, log, wanted, pins:
                                  calls.append((instance, "owner's rule", wanted))), \
                mock.patch("builtins.print"):
            self.assertEqual(check.check_public_dump(kinds.find("sonoff-dualr3"), [81, 82], "motor"), 0)
        # Moved from hold to run to one push-button, its rule written over: hold to run again.
        owned = check.motor_setup.Setup(check.MOVED_TRAVEL, check.MOVED_TRAVEL, True, check.motor_setup.HOLD_TO_RUN,
                                        True)
        self.assertEqual(calls[1:3], [(81, "owner's rule", owned), (81, "esphome", owned)])

    def test_what_a_first_firmware_is_set_to_is_unlike_the_copys(self):
        setup = check.motor_setup
        for carried in (setup.DEFAULT, setup.Setup(8.0, 8.0, True, setup.HOLD_TO_RUN, True),
                        setup.Setup(3.0, 3.0, False, setup.ONE_BUTTON, False)):
            moved = check.moved_setup(carried)
            for part in ("open", "reversed", "switches", "swapped"):
                self.assertNotEqual(getattr(moved, part), getattr(carried, part), part)
        # Across the copies, each mode is moved to once.
        copies = [setup.PUSH_BUTTONS, setup.HOLD_TO_RUN, setup.ONE_BUTTON]
        self.assertEqual(sorted(check.moved_setup(setup.Setup(switches=mode)).switches for mode in copies),
                         sorted(copies))

    def test_a_setting_the_firmware_does_not_keep_through_a_power_cut_fails(self):
        moved = check.moved_setup(check.motor_setup.DEFAULT)
        device = check.MotorDevice("127.0.0.1:8081", "esphome")
        device.get = lambda path, post=False: "{}"
        setups = iter([moved] + [check.motor_setup.DEFAULT] * 1000)
        device.setup = lambda known=None: next(setups)
        clock = iter(range(0, 10000, 10))
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", lambda: next(clock)):
            self.assertIn("after a power cut the motor is set to Setup(open=3.0", device.set_to(moved, lambda: None))

    def test_tasmota_is_set_through_its_template_travel_times_and_switches(self):
        moved = check.motor_setup.Setup(7.0, 7.0, True, check.motor_setup.ONE_BUTTON, True)
        device, sent = check.MotorDevice("127.0.0.1:8081", "tasmota"), []
        # Rule1 the owner's: the switches' rule where the tool would put it, in Rule2.
        rules = {name: {"State": "ON" if name == "Rule1" else "OFF", "Rules": "ON System#Boot DO Var1 1 ENDON"
                        if name == "Rule1" else ""} for name in check.motor_setup.TASMOTA_RULES}
        device.tasmota = lambda command: {command: rules[command]} if command in rules else sent.append(command) or {}
        device.setup = lambda known=None: moved
        device.answers = lambda within=None: sent.append("answers?") or True
        with mock.patch.object(check.time, "sleep"):
            self.assertIsNone(device.set_to(moved, lambda: None))
        # Its restart for the template waited for, which saves what it was given, before a power cut.
        self.assertEqual(sent[3], "answers?")
        # The template last: Tasmota restarts for it, and a Backlog after it was lost.
        self.assertEqual(sent[0], "Backlog ShutterOpenDuration1 7; ShutterCloseDuration1 7; "
                         + "; ".join(check.motor_setup.tasmota_switch_commands(check.motor_setup.ONE_BUTTON, rules)))
        self.assertIn(f"Rule2 {check.motor_setup.TASMOTA_SWITCHES[check.motor_setup.ONE_BUTTON][1]}", sent[0])
        self.assertEqual(json.loads(sent[1].split(" ", 1)[1])["GPIO"],
                         check.motor_setup.tasmota_gpio(kinds.find("sonoff-dualr3"), True, True))
        self.assertEqual(sent[2], "Module 0")
        # With no rule set free, nothing is set and the check says so.
        rules["Rule2"] = rules["Rule3"] = rules["Rule1"]
        sent.clear()
        self.assertIn("no rule set free", device.set_to(moved, lambda: None))
        self.assertEqual(sent, [])

    def test_esphome_is_set_through_its_settings(self):
        moved = check.motor_setup.Setup(7.0, 7.0, True, check.motor_setup.HOLD_TO_RUN, True)
        device, sent = check.MotorDevice("127.0.0.1:8081", "esphome"), []
        device.get = lambda path, post=False: sent.append(path) or "{}"
        device.setup = lambda known=None: moved
        self.assertIsNone(device.set_to(moved, lambda: None))
        self.assertIn("/select/Wall%20switches/set?option=Hold%20to%20run", sent)
        self.assertIn("/switch/Swap%20wall%20switches/turn_on", sent)
        self.assertIn("/switch/Reverse%20direction/turn_on", sent)

    def device(self, firmware, answers, setup=check.motor_setup.DEFAULT):
        device = check.MotorDevice("127.0.0.1:8081", firmware)
        readings = iter(answers)
        device.driving = lambda way: next(readings)
        device.settled = lambda: next(readings)
        device.answers = lambda within=None: True
        device.get = lambda path, post=False: '{"state": "OPEN"}'
        device.setup = lambda known=None: setup
        device.set_travel = lambda wanted: None
        settings = {"Interlock": "ON", "ShutterRelay1": 1, "ShutterMode": 1, "PowerOnState": 0}
        device.tasmota = lambda command: {command: settings[command]} if command in settings else {}
        return device

    def problem(self, firmware, answers, setup=check.motor_setup.DEFAULT, wanted=check.motor_setup.DEFAULT):
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            return self.device(firmware, answers, setup).problem(lambda: None, wanted)

    def test_a_motor_set_to_other_than_what_was_carried_fails_before_anything_moves(self):
        wanted = check.motor_setup.Setup(8.0, 8.0, True)
        self.assertIn("not Setup(open=8.0", self.problem("esphome", [], wanted=wanted))

    def test_a_reversed_motors_esphome_opens_with_output_2(self):
        reversed_motor = check.motor_setup.Setup(8.0, 8.0, True)
        self.assertIsNone(self.problem("esphome", [(False, True), (True, False), (False, False)],
                                       reversed_motor, reversed_motor))
        self.assertIn("asked to open", self.problem("esphome", [(True, False)], reversed_motor, reversed_motor))

    def test_the_setup_is_read_from_each_firmwares_settings(self):
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        template = json.loads(check.motor_setup.tasmota_template(kinds.find("sonoff-dualr3"), True))
        answers = {"ShutterOpenDuration1": {"ShutterOpenDuration1": 60.0},
                   "ShutterCloseDuration1": {"ShutterCloseDuration1": 60.0}, "Template": template}
        device.tasmota = lambda command: answers.get(command, {})
        # Its template says how its wall switches are wired, not how they are read.
        self.assertEqual(device.setup(), check.motor_setup.Setup(60.0, 60.0, True, None, False))

    def test_one_output_at_a_time_and_both_off_after_a_power_cut_passes(self):
        self.assertIsNone(self.problem("tasmota", [(True, False), (False, True), (False, True), (False, False)]))
        self.assertIsNone(self.problem("esphome", [(True, False), (False, True), (False, False)]))

    def test_both_on_together_fails(self):
        self.assertIn("asked to close", self.problem("esphome", [(True, False), (True, True)]))
        self.assertIn("both outputs came on together",
                      self.problem("tasmota", [(True, False), (False, True), (True, True)]))

    def test_outputs_not_read_yet_after_a_power_cut_are_read_again(self):
        # Seen on ESPHome under load: its meter read nothing yet, and the check failed.
        self.assertIsNone(self.problem("esphome", [(True, False), (False, True), None, None, (False, False)]))
        self.assertIn("after a power cut the outputs read None",
                      self.problem("esphome", [(True, False), (False, True), *[None] * 1000]))

    def test_an_output_on_after_a_power_cut_fails(self):
        self.assertIn("after a power cut",
                      self.problem("tasmota", [(True, False), (False, True), (False, True), (True, False)]))

    def test_a_tasmota_not_set_up_fails_before_anything_moves(self):
        device = self.device("tasmota", [])
        device.tasmota = lambda command: {"ShutterMode": 2} if command == "ShutterMode" else {command: None}
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertIn("not set up for its motor", device.problem(lambda: None))

    def test_a_setting_tasmota_does_not_answer_for_at_once_is_asked_again(self):
        # Seen just after a start: ShutterMode and PowerOnState unanswered for
        # 10 s, once Interlock and ShutterRelay1 had answered - "not set up".
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        settings = {"Interlock": "ON", "ShutterRelay1": 1, "ShutterMode": 1, "PowerOnState": 0}
        unanswered = {"ShutterMode": 2, "PowerOnState": 2}

        def answer(command):
            if unanswered.get(command):
                unanswered[command] -= 1
                return {}
            return {command: settings[command]}
        device.tasmota = answer
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertEqual(device.settings(*settings), settings)

    def test_a_setup_answered_a_setting_at_a_time_is_put_together(self):
        # Seen under load: Tasmota answering one request in three for
        # minutes, never all three settings in one round.
        setup = check.motor_setup.Setup(8.0, 8.0, True)
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        rounds = iter([check.motor_setup.Setup(8.0, None, None), check.motor_setup.Setup(None, 8.0, None),
                       check.motor_setup.Setup(None, None, True)])
        device.setup = lambda known=None: next(rounds, check.motor_setup.Setup())
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertEqual(device.until_set(setup), setup)

    def test_what_tasmota_has_answered_as_wanted_is_not_asked_again(self):
        # Seen: a slow emulated Tasmota answering a request a minute, its
        # settings set, but too few rounds of all of them to read each once.
        setup = check.motor_setup.Setup(7.0, 7.0, True, check.motor_setup.ONE_BUTTON, True)
        template = {"GPIO": check.motor_setup.tasmota_gpio(kinds.find("sonoff-dualr3"), True, True)}
        answers = {"ShutterOpenDuration1": {"ShutterOpenDuration1": 7.0}, "Template": template,
                   "SwitchMode1": {"SwitchMode1": 4}, "SwitchMode2": {"SwitchMode2": 4},
                   "Rule1": {"Rule1": {"State": "ON", "Rules": check.motor_setup.TASMOTA_SWITCHES[1][1]}},
                   "Rule2": {"Rule2": {"State": "OFF", "Rules": ""}}, "Rule3": {"Rule3": {"State": "OFF", "Rules": ""}}}
        device, asked = check.MotorDevice("127.0.0.1:8081", "tasmota"), []
        # Every setting but the close's travel answers, and only once.
        device.tasmota = lambda command: asked.append(command) or (answers.pop(command, {})
                                                                   if len(asked) < 10 else
                                                                   {"ShutterCloseDuration1": 7.0})
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertEqual(device.until_set(setup), setup)
        self.assertEqual(asked[8:], ["ShutterOpenDuration1", "ShutterCloseDuration1"])

    def test_travel_times_not_taken_at_once_are_set_again_until_seen(self):
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        # The rest of the setup as carried, its wall switches among it.
        carried = check.motor_setup.Setup(8.0, 8.0, True, check.motor_setup.HOLD_TO_RUN, True)
        travel = dataclasses.replace(carried, open=float(check.TRAVEL_OPEN), close=float(check.TRAVEL_CLOSE))
        sent = []
        device.tasmota = lambda command: sent.append(command) or {}
        device.setup = lambda known=None: travel if len(sent) > 1 else carried
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertIsNone(device.set_travel(carried))
        self.assertEqual(len(sent), 2)
        device.setup = lambda known=None: carried
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertIn("the motor is set to Setup(open=8.0", device.set_travel(carried))

    def test_a_setup_read_while_the_firmware_does_not_answer_is_read_again(self):
        device = self.device("esphome", [(True, False), (False, True), (False, False)])
        setups = iter([check.motor_setup.Setup(), check.motor_setup.DEFAULT])
        device.setup = lambda known=None: next(setups, check.motor_setup.DEFAULT)
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.time, "monotonic", ticking()):
            self.assertIsNone(device.problem(lambda: None))

    def test_tasmota_is_read_by_its_relays_states_and_both_on_once_is_both_on(self):
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        states = iter([{"POWER1": "ON", "POWER2": "OFF"}, {"POWER1": "ON", "POWER2": "ON"}])
        device.tasmota = lambda command: next(states, {"POWER1": "OFF", "POWER2": "ON"})
        with mock.patch.object(check.time, "sleep"):
            self.assertEqual(device.settled(), (True, True))

    def drive(self, shutter, way):
        """`way` asked of a Tasmota that is `shutter`, on its clock."""
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        device.tasmota = shutter.command
        with mock.patch.object(check.time, "sleep", shutter.run), \
                mock.patch.object(check.time, "monotonic", lambda: shutter.now):
            device.move(way)
            return device.driving(way)

    def test_a_close_back_from_near_closed_is_seen_while_it_runs(self):
        # Seen once in a --motor calibrated run: opened for about 5 s of 60, the
        # close back took as long, and a slow look put the check's last one
        # after the motor had stopped - "asked to close, output 1 is off and
        # output 2 off" from a Tasmota that had closed it as asked.
        shutter = FakeShutter()
        shutter.command(f"Backlog ShutterOpenDuration1 {check.TRAVEL_OPEN}; "
                        f"ShutterCloseDuration1 {check.TRAVEL_CLOSE}")
        self.assertEqual(self.drive(shutter, "open"), (True, False))
        shutter.slow_after = shutter.now + 2   # one look during the close takes 2 s
        self.assertEqual(self.drive(shutter, "close"), (False, True))
        self.assertEqual(shutter.direction, -1, "still closing after the last look")

    def test_a_close_that_ends_before_the_last_look_is_seen_while_it_ran(self):
        # The same both ways: the close ends within the looks, which count only
        # while Tasmota says it is closing.
        shutter = FakeShutter()
        self.assertEqual(self.drive(shutter, "open"), (True, False))
        shutter.slow_after = shutter.now + 2
        self.assertEqual(self.drive(shutter, "close"), (False, True))
        self.assertEqual(shutter.direction, 0, "closed before the last look")

    def test_a_tasmota_that_does_not_answer_for_a_while_is_waited_for(self):
        # Seen under load: Power1 ON taken, then Status 10 and Power0 each
        # unanswered for 5 s - "the motor did not start to open".
        shutter = FakeShutter()
        shutter.silent_for = 12
        self.assertEqual(self.drive(shutter, "open"), (True, False))

    def test_a_tasmota_that_never_answers_again_fails_saying_so(self):
        shutter = FakeShutter()
        shutter.silent_for = 10 * check.BACK_WITHIN
        self.assertEqual(self.drive(shutter, "open"), "the firmware stopped answering")

    def test_a_close_tasmota_never_starts_fails(self):
        shutter = FakeShutter(ignores_close=True)
        self.assertEqual(self.drive(shutter, "open"), (True, False))
        self.assertEqual(self.drive(shutter, "close"), "the motor did not start to close")

    def test_an_esphome_cover_that_stops_before_its_meter_shows_fails_saying_so(self):
        device = check.MotorDevice("127.0.0.1:8081", "esphome")
        covers = iter(['{"current_operation": "CLOSING"}', '{"current_operation": "IDLE"}'])
        device.get = lambda path, post=False: next(covers) if path == "/cover/Motor" else '{"value": 0}'
        with mock.patch.object(check.time, "sleep"):
            self.assertEqual(device.driving("close"), "the motor stopped before its outputs could be read")

    def test_each_request_names_the_device_as_a_browser_does(self):
        # Tasmota turns away /cm without it (seen through the emulator's browser port).
        device = check.MotorDevice("127.0.0.1:8081", "tasmota")
        with mock.patch.object(check.urllib.request.OpenerDirector, "open", side_effect=OSError) as opened:
            self.assertIsNone(device.get("/cm?cmnd=Power"))
        self.assertEqual(opened.call_args.args[0].get_header("Referer"), "http://127.0.0.1:8081/")



class FakeShutter:
    """Tasmota's shutter on relays 1 and 2 as the motor check asks it, on a
    clock of its own: Power1 ON opens and Power2 ON closes, the other relay
    off first and the motor started half a second later (ShutterMotorStop),
    both off at its end; each request takes LOOK seconds, or SLOW once after
    `slow_after`. Its travel is 60 s each way until a Backlog sets it."""

    LOOK, SLOW, MOTOR_STOP = 0.1, 2.0, 0.5

    def __init__(self, ignores_close=False):
        self.travel = {1: 60.0, -1: 60.0}
        self.ignores_close = ignores_close
        self.now, self.position, self.direction, self.starts = 0.0, 0.0, 0, 0.0
        self.relays = [False, False]
        self.slow_after = None
        # Seconds after a move it answers Status 10 and Power0 with nothing.
        self.silent_for, self.silent_until = 0.0, 0.0

    def run(self, seconds):
        until = self.now + seconds
        while self.now < until:
            step = min(0.05, until - self.now)
            self.now += step
            if self.direction and self.now >= self.starts:
                self.position += self.direction * step / self.travel[self.direction]
                if not 0 < self.position < 1:
                    self.position = min(max(self.position, 0.0), 1.0)
                    self.direction, self.relays = 0, [False, False]

    def command(self, text):
        slow = self.slow_after is not None and self.now >= self.slow_after
        if slow:
            self.slow_after = None
        self.run(self.SLOW if slow else self.LOOK)
        if text in ("Status 10", "Power0") and self.now < self.silent_until:
            self.run(5.0)   # no answer, after urllib's wait
            return {}
        if text.startswith("Backlog ShutterOpenDuration1"):
            opening, closing = re.findall(r"Duration1 (\d+)", text)
            self.travel = {1: float(opening), -1: float(closing)}
            return {}
        if text in ("Power1 ON", "Power2 ON"):
            way = 1 if text == "Power1 ON" else -1
            self.direction, self.relays = 0, [False, False]
            if way == -1 and self.ignores_close:
                return {}
            self.direction, self.starts, self.relays = way, self.now + self.MOTOR_STOP, [way == 1, way == -1]
            self.silent_until = self.now + self.silent_for
            return {text.split()[0].upper(): "ON"}
        if text == "Power0":
            return {"POWER1": "ON" if self.relays[0] else "OFF", "POWER2": "ON" if self.relays[1] else "OFF"}
        if text == "Status 10":
            return {"StatusSNS": {"Shutter1": {"Position": round(self.position * 100), "Direction": self.direction}}}
        return {}

class Board:
    """The board's buttons file as a check presses it, wired to a firmware
    that switches its relays as `react` says: (pin, now held) -> relay to
    toggle, or None."""
    def __init__(self, react, relays=2):
        self.held, self.react, self.relays = set(), react, [False] * relays
        self.buttons = check.emulator_buttons.Buttons(self.read, self.write)

    def read(self):
        return check.emulator_buttons.text_of(self.held)

    def write(self, text):
        now = check.emulator_buttons.pins_of(text)
        for pin in now ^ self.held:
            relay = self.react(pin, pin in now)
            if relay:
                self.relays[relay - 1] = not self.relays[relay - 1]
        self.held = set(now)


class InputsCheckTests(unittest.TestCase):
    """Each firmware's relays as its button and wall switches change them."""

    def device(self, firmware, board):
        device = check.Device("127.0.0.1:8081", firmware)
        if firmware == "esphome":
            def get(path, post=False):
                n = 2 if path.endswith("Relay%202") else 1
                return json.dumps({"value": board.relays[n - 1]})
            device.get = get
        else:
            device.tasmota = lambda command: {"POWER" if len(board.relays) == 1 else command.upper():
                                              "ON" if board.relays[int(command[-1]) - 1] else "OFF"}
        return device

    def problem(self, firmware, board, pins):
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check.emulator_buttons.time, "sleep"):
            return self.device(firmware, board).inputs_problem(board.buttons, pins)

    def test_the_button_toggles_the_relay_each_press(self):
        s60 = {"buttons": (9,), "switches": ()}
        for firmware in ("tasmota", "openbeken", "esphome"):
            with self.subTest(firmware=firmware):
                self.assertIsNone(self.problem(firmware, Board(lambda pin, held: 1 if held else None, 1), s60))

    def test_openbekens_press_is_held_past_its_debounce(self):
        held = []
        board = Board(lambda pin, now: 1 if now else None, 1)
        board.buttons.press = lambda pin, seconds=check.emulator_buttons.PRESS: held.append(seconds)
        device = self.device("openbeken", board)
        device.relay_becomes = lambda n, wanted=None: False if wanted is None else None
        with mock.patch.object(check.time, "sleep"):
            device.inputs_problem(board.buttons, {"buttons": (9,), "switches": ()})
        self.assertEqual(held, [check.HELD["openbeken"]])
        self.assertGreater(check.HELD["openbeken"], 0.075 + 0.025, "its debounce and a tick")
        held.clear()
        device = self.device("esphome", board)
        device.relay_becomes = lambda n, wanted=None: False if wanted is None else None
        with mock.patch.object(check.time, "sleep"):
            device.inputs_problem(board.buttons, {"buttons": (9,), "switches": ()})
        self.assertEqual(held, [check.emulator_buttons.PRESS])

    def test_a_button_that_switches_nothing_fails(self):
        found = self.problem("esphome", Board(lambda pin, held: None, 1), {"buttons": (9,), "switches": ()})
        self.assertEqual(found, "relay 1 stayed off with its button pressed (GPIO9)")

    def test_each_wall_switch_toggles_its_own_relay_each_change(self):
        dualr3 = {"buttons": (0,), "switches": (32, 33)}
        wired = {0: 1, 32: 1, 33: 2}
        self.assertIsNone(self.problem("tasmota", Board(lambda pin, held: wired[pin] if held or pin else None),
                                       dualr3))
        swapped = {0: 1, 32: 2, 33: 1}
        self.assertIn("wall switch 1 closed (GPIO32)",
                      self.problem("esphome", Board(lambda pin, held: swapped[pin] if held or pin else None), dualr3))
        self.assertIn("wall switch 1 opened (GPIO32)",
                      self.problem("esphome", Board(lambda pin, held: wired[pin] if held else None), dualr3))

    def test_a_relay_that_cannot_be_read_fails(self):
        device = check.Device("127.0.0.1:8081", "tasmota")
        device.tasmota = lambda command: {}
        with mock.patch.object(check.time, "sleep"):
            self.assertEqual(device.inputs_problem(Board(lambda pin, held: None).buttons,
                                                   {"buttons": (9,), "switches": ()}),
                             "relay 1 could not be read before its button pressed")

    def test_a_single_relay_is_power_and_more_are_numbered(self):
        device = check.Device("127.0.0.1:8081", "openbeken")
        device.tasmota = lambda command: {"POWER": "ON"}
        self.assertIs(device.relay(1), True)
        self.assertIsNone(device.relay(2))
        device.tasmota = lambda command: {"POWER2": "OFF"}
        self.assertIs(device.relay(2), False)


class PublicDumpInputsTests(unittest.TestCase):
    """A public dump's check presses the inputs of the firmware each way ends on."""

    def check(self, problem):
        from types import SimpleNamespace
        from tools.espressif.emulator import public_devices, public_dump
        runs = []

        def run(instance, saved, firmware, keep, log, kind, extra):
            runs.append((instance, firmware, keep, "--keep" in extra))
            return True, "Converted"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(check, "LOGS", Path(tmp)), \
                mock.patch.object(public_dump, "make"), \
                mock.patch.object(public_dump, "recipe", return_value=SimpleNamespace(mac="12:34:56:ab:cd:ef")), \
                mock.patch.object(public_devices, "run", side_effect=run), \
                mock.patch.object(check, "espressif_inputs_problem", return_value=problem) as pressed, \
                mock.patch.object(check.emulator_stats, "send", return_value=""), \
                mock.patch.object(check.subprocess, "run"), redirect_stdout(io.StringIO()) as out:
            code = check.check_public_dump(kinds.find("sonoff-dualr3"), [1, 2])
        return code, sorted(runs), sorted(call.args[1:] for call in pressed.call_args_list), out.getvalue()

    def test_each_way_ends_left_running_and_pressed(self):
        code, runs, pressed, said = self.check(None)
        self.assertEqual(code, 0)
        self.assertEqual(runs, [(1, "esphome", None, True), (1, "tasmota", "check-sonoff-dualr3-1-tasmota", False),
                                (2, "esphome", "check-sonoff-dualr3-2-esphome", False), (2, "tasmota", None, True)])
        self.assertEqual(pressed, [(1, "esphome"), (2, "tasmota")])
        self.assertIn("PASS  sonoff-dualr3 on tasmota follows its button and wall switches: each press and each "
                      "change switched its relay", said)

    def test_a_firmware_that_does_not_follow_them_fails(self):
        code, _, _, said = self.check("relay 2 stayed off with wall switch 2 closed (GPIO33)")
        self.assertEqual(code, 2)
        self.assertIn("FAIL  sonoff-dualr3 on esphome follows its button and wall switches: relay 2 stayed off", said)


class SwitchedMotor:
    """A firmware's motor as its wall switches S1 (GPIO32) and S2 (GPIO33)
    move it, in `mode`, one of Sonoff's (motor_setup.SWITCH_MODES), S1
    opening unless `swapped`, as Sonoff's firmware was seen to; a push-button
    acting as it is pressed where `on_press`, and never stopped where
    `latched`. Its button (GPIO0) starts it one way and stops it."""

    def __init__(self, mode, swapped=False, on_press=False, latched=False, button=True):
        self.mode, self.swapped, self.on_press, self.latched, self.button = mode, swapped, on_press, latched, button
        self.moving, self.last, self.held = None, "close", set()

    def react(self, pin, held):
        (self.held.add if held else self.held.discard)(pin)
        if pin == 0:
            if held and self.button:
                self.moving = "open" if self.moving is None else None
            return None
        opener = 33 if self.swapped else 32
        if self.mode == check.motor_setup.HOLD_TO_RUN:
            opening, closing = opener in self.held, (65 - opener) in self.held
            self.moving = None if opening == closing else "open" if opening else "close"
        elif held != self.on_press:
            return None
        elif self.moving:
            self.moving = self.moving if self.latched else None
        elif self.mode == check.motor_setup.PUSH_BUTTONS:
            self.moving = "open" if pin == opener else "close"
        else:
            self.moving = "open" if self.last == "close" else "close"
        self.last = self.moving or self.last
        return None


class MotorInputsTests(unittest.TestCase):
    """A motor's wall switches and button, as MotorDevice.inputs_problem moves
    it: a firmware that moves the motor as `motor` (a SwitchedMotor) does,
    each way driving the outputs `drives` gives."""

    OUTPUTS = {"open": (True, False), "close": (False, True)}
    PUSH, HOLD, ONE = check.motor_setup.PUSH_BUTTONS, check.motor_setup.HOLD_TO_RUN, check.motor_setup.ONE_BUTTON

    def problem(self, motor, drives=None, wanted=check.motor_setup.DEFAULT, firmware="esphome"):
        device = check.MotorDevice("127.0.0.1:8081", firmware)
        drives = drives or self.OUTPUTS
        device.moving = lambda: motor.moving
        device.driving = lambda way: drives[way] if motor.moving == way else f"the motor did not start to {way}"
        device.settled = lambda: (False, False) if motor.moving is None else drives[motor.moving]
        board = Board(motor.react)
        with mock.patch.object(check.time, "sleep"), mock.patch.object(check, "STARTS_WITHIN", 0):
            return device.inputs_problem(board.buttons, {"buttons": (0,), "switches": (32, 33)}, wanted)

    def setup(self, mode, swapped=False, reversed=False):
        return dataclasses.replace(check.motor_setup.DEFAULT, switches=mode, swapped=swapped, reversed=reversed)

    def test_each_mode_its_switches_move_it_and_the_button_moves_and_stops_it(self):
        for mode in check.motor_setup.SWITCH_MODES:
            for firmware in ("esphome", "tasmota"):
                for swapped in (False, True):
                    with self.subTest(mode=mode, firmware=firmware, swapped=swapped):
                        self.assertIsNone(self.problem(SwitchedMotor(mode, swapped),
                                                       wanted=self.setup(mode, swapped), firmware=firmware))

    def test_switches_swapped_or_not_as_wanted_fail(self):
        for mode in (self.PUSH, self.HOLD):
            with self.subTest(mode):
                self.assertIn("to open: the motor did not start to open",
                              self.problem(SwitchedMotor(mode, swapped=True), wanted=self.setup(mode)))
                self.assertIn("S2 (GPIO33)",
                              self.problem(SwitchedMotor(mode), wanted=self.setup(mode, swapped=True)))

    def test_a_reversed_motors_esphome_opens_with_output_2(self):
        reversed_motor = self.setup(self.HOLD, reversed=True)
        self.assertIsNone(self.problem(SwitchedMotor(self.HOLD), drives={"open": (False, True), "close": (True, False)},
                                       wanted=reversed_motor))
        self.assertIn("S1 (GPIO32) held to open", self.problem(SwitchedMotor(self.HOLD), wanted=reversed_motor))

    def test_push_buttons_acting_as_they_are_pressed_fail(self):
        self.assertEqual(self.problem(SwitchedMotor(self.PUSH, on_press=True), wanted=self.setup(self.PUSH)),
                         "S1 (GPIO32) pressed: the motor started to open while it was held, not as it was let go")

    def test_hold_to_run_where_push_buttons_are_wanted_fails(self):
        self.assertIn("S1 (GPIO32) pressed", self.problem(SwitchedMotor(self.HOLD), wanted=self.setup(self.PUSH)))

    def test_push_buttons_where_hold_to_run_is_wanted_fail(self):
        self.assertEqual(self.problem(SwitchedMotor(self.PUSH), wanted=self.setup(self.HOLD)),
                         "S1 (GPIO32) held to open: the motor did not start to open")

    def test_a_switch_that_does_not_stop_it_fails(self):
        self.assertEqual(self.problem(SwitchedMotor(self.PUSH, latched=True), wanted=self.setup(self.PUSH)),
                         "S2 (GPIO33) pressed: the motor did not stop")

    def test_one_push_button_that_does_not_go_the_other_way_fails(self):
        self.assertEqual(self.problem(SwitchedMotor(self.PUSH), wanted=self.setup(self.ONE)),
                         "S1 (GPIO32) pressed: the motor started to open again, not the other way")

    def test_a_button_that_does_nothing_fails(self):
        self.assertEqual(self.problem(SwitchedMotor(self.PUSH, button=False), wanted=self.setup(self.PUSH)),
                         "its button (GPIO0) pressed: the motor did not start")

    def test_they_are_pressed_once_the_motor_checks_have_passed(self):
        # The travel the motor checks set first, for their reads while it moves.
        order = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(check.subprocess, "run"), \
                mock.patch.object(check.MotorDevice, "answers", return_value=True), \
                mock.patch.object(check.MotorDevice, "problem", side_effect=lambda *_: order.append("motor")), \
                mock.patch.object(check.MotorDevice, "inputs_problem",
                                  side_effect=lambda *_: order.append("inputs") or "stayed off"):
            found = check.motor_problem(1, "kept", "esphome", (), Path(tmp) / "motor.log",
                                        pins={"buttons": (0,), "switches": (32, 33)})
        self.assertEqual((order, found), (["motor", "inputs"], "stayed off"))

    def test_the_owners_rule_must_be_left_as_given_and_running(self):
        setup = check.motor_setup
        wanted = setup.Setup(7.0, 7.0, False, setup.ONE_BUTTON, False)
        theirs = {"State": "ON", "Once": "OFF", "StopOnError": "OFF", "Rules": check.OWNER_RULE}
        ours = {"State": "ON", "Once": "OFF", "StopOnError": "OFF", "Rules": setup.TASMOTA_SWITCHES[setup.ONE_BUTTON][1]}
        for rule1, var1, problem in ((theirs, "1", None), (ours, "1", "the owner's rule in Rule1 is now"),
                                     (dict(theirs, State="OFF"), "1", "the owner's rule in Rule1 is now"),
                                     (theirs, "", "the owner's rule did not run")):
            with self.subTest(rule1=rule1, var1=var1), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(check.subprocess, "run"), mock.patch.object(check, "keep_as"), \
                    mock.patch.object(check.time, "sleep"), \
                    mock.patch.object(check.MotorDevice, "answers", return_value=True), \
                    mock.patch.object(check.MotorDevice, "until_set", return_value=wanted), \
                    mock.patch.object(check.MotorDevice, "set_travel", return_value=None), \
                    mock.patch.object(check.MotorDevice, "inputs_problem", return_value=None), \
                    mock.patch.object(check.MotorDevice, "settings",
                                      return_value={"Rule1": rule1, "Rule2": ours, "Rule3": {"Rules": ""}}), \
                    mock.patch.object(check.MotorDevice, "tasmota", return_value={"Var1": var1}), \
                    mock.patch("tools.espressif.emulator.board.buttons"):
                found = check.owner_rule_problem(1, "kept", (), Path(tmp) / "owner.log", wanted,
                                                 {"buttons": (0,), "switches": (32, 33)})
                if problem is None:
                    self.assertIsNone(found)
                else:
                    self.assertIn(problem, found)


class SideBySideTests(unittest.TestCase):
    def test_a_job_that_raises_has_failed_and_says_why(self):
        import io
        from contextlib import redirect_stdout
        from tools.espressif.emulator import public_devices

        def broken():
            raise RuntimeError("the emulator did not start")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(public_devices.side_by_side([lambda: 0, broken]), [0, 1])
        self.assertIn("the emulator did not start", out.getvalue())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/emulator-check.yml").read_text(encoding="utf-8")

    def test_the_required_check_keeps_its_name(self):
        from tools.repo import github_settings
        self.assertIn("emulator", github_settings.REQUIRED_CHECKS)
        self.assertRegex(self.workflow, r"(?m)^  emulator:$")

    def test_only_a_run_on_main_is_given_the_emulator_stats_token(self):
        from tools.repo import emulator_stats
        for name in (emulator_stats.CLIENT_ID, emulator_stats.CLIENT_SECRET):
            uses = re.findall(rf"{name}: (.*)", self.workflow)
            self.assertEqual(uses, [f"${{{{ github.event_name != 'pull_request' && github.ref == 'refs/heads/main' "
                                    f"&& secrets.{name} || '' }}}}"], name)

    def test_each_device_has_its_own_job(self):
        self.assertIn("emulator_check.py list", self.workflow)
        self.assertIn('emulator_check.py "$DEVICE"', self.workflow)

    def test_every_familys_runs_are_kept_for_reading(self):
        # Each family's emulator keeps a device's computer's runs under its own state folders.
        from tools.beken.emulator import folders as beken_folders
        beken = beken_folders.state(1).parent.relative_to(ROOT).as_posix()
        for family in ("captures/emulation", beken):
            with self.subTest(family):
                self.assertIn(f"            {family}/state-*/work/captures/devices/*/runs/*/run.log\n", self.workflow)

    def test_a_release_checks_every_device(self):
        self.assertIn("every_device", self.workflow)


def ticking(step=1.0):
    """A clock for time.monotonic that moves on `step` seconds each time it is read."""
    now = [0.0]

    def tick():
        now[0] += step
        return now[0]
    return tick
