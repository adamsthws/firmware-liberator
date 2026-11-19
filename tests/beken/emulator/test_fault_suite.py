"""The Beken fault suite: its scenarios, and how it tells a device converted."""

import io
import itertools
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import output
from tools.beken.emulator import fault_suite


def happened(*items):
    """A run's events (output.EVENTS) as the tool writes them: (name, fields)."""
    return [{"at": 0.0, "event": name, **fields} for name, fields in items]


def write_events(run_dir, *items):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / output.EVENTS).write_text("".join(json.dumps(item) + "\n" for item in happened(*items)),
                                         encoding="utf-8")


class ScenarioTests(unittest.TestCase):
    def test_every_direction_and_id_once(self):
        found = fault_suite.scenarios()
        ids = [sc["id"] for sc in found]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({sc["route"] for sc in found},
                         set(fault_suite.ROUTES) | {"sonoff>readout", "openbeken>readout", fault_suite.READOUT_ALL,
                                                    fault_suite.OWN_BUILD_ROUTE})

    def test_the_steps_that_write_are_also_faulted_early_and_late(self):
        ids = {sc["id"] for sc in fault_suite.scenarios()}
        self.assertIn("sonoff>openbeken:B2@10:deviceoff-1", ids)
        self.assertIn("openbeken>sonoff:O2@90:crash-1", ids)
        self.assertNotIn("sonoff>openbeken:K1@10:deviceoff-1", ids)   # a check step: 40% only

    def test_outages_are_as_long_as_the_espressif_suites(self):
        from tools.espressif.emulator import fault_suite as espressif
        lengths = lambda core: sorted(arg for fault, arg in core if fault == "outage")
        self.assertEqual(lengths(fault_suite.CORE), lengths(espressif.CORE))
        ids = {sc["id"] for sc in fault_suite.scenarios()}
        self.assertIn("sonoff>esphome:J1:outage-180", ids)

    def test_a_routine_run_has_the_outages_only_at_their_shortest_and_longest(self):
        from tools.espressif.emulator import fault_suite as espressif
        self.assertEqual(fault_suite.QUICK_OUTAGES, espressif.QUICK_OUTAGES)
        every = {sc["id"] for sc in fault_suite.scenarios()}
        quick = {sc["id"] for sc in fault_suite.scenarios(quick=True)}
        self.assertEqual(every - quick, {i for i in every if any(i.endswith(f":outage-{s}") for s in (15, 30, 120))
                                         and ">readout:" not in i})
        self.assertIn("sonoff>esphome:J1:outage-5", quick)
        self.assertIn("sonoff>esphome:K2:fallback-150", quick)
        self.assertIn("sonoff>readout:read:outage-30", quick)

    def test_found_says_what_each_fault_found_here_and_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            emulation = Path(directory)
            for folder, row in ((emulation / "beken" / "suite", {"id": "a>b:K2:outage-120", "result": "FAIL"}),
                                (emulation / "kept" / "second" / "beken" / "suite",
                                 {"id": "a>b:K2:outage-5", "result": "PASS"})):
                folder.mkdir(parents=True)
                (folder / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            with mock.patch.object(fault_suite.folders, "migrate", return_value=[]), \
                    mock.patch.object(fault_suite.folders, "EMU", emulation), \
                    mock.patch.object(fault_suite.folders, "BEKEN", emulation / "beken"), \
                    mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(fault_suite.main(["found"]), 0)
        self.assertEqual([line.split()[:4] for line in out.getvalue().splitlines()],
                         [["outage-5", "1", "run", "0"], ["outage-120", "1", "run", "1"]])

    def test_list_takes_only_what_it_lists(self):
        with mock.patch.object(fault_suite.folders, "migrate", return_value=[]):
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(fault_suite.main(["list", "quick"]), 0)
            self.assertEqual(out.getvalue().split(), [sc["id"] for sc in fault_suite.scenarios(quick=True)])
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(fault_suite.main(["list", "quik"]), 2)

    def test_where_esphome_raises_its_own_network_the_router_stays_off_for_it(self):
        found = [sc["id"] for sc in fault_suite.scenarios() if sc["fault"] == "fallback"]
        self.assertEqual(sorted(found), ["interim>esphome:K2:fallback-150", "openbeken>esphome:K2:fallback-150",
                                         "sonoff>esphome:K2:fallback-150"])

    def test_each_step_faulted_is_one_its_route_takes(self):
        for route, steps in fault_suite.ROUTES.items():
            self.assertLessEqual(set(steps), set(fault_suite.route_steps(route)), route)


class EndChecksTests(unittest.TestCase):
    """A converted device passes only if the report's end-of-run checks did."""

    def result(self, checks, screen=None, release=None, relay=None, kind=None, outcome=None, said="", kept=None,
               events=(), went=None):
        """The scenario's outcome; `kept`, a list given, gets what its folder kept."""
        from unittest import mock
        sc = {"id": "sonoff>openbeken:B2:deviceoff-1", "route": "sonoff>openbeken", "step": "B2",
              "fault": "deviceoff", "arg": 1, "at": 40}
        relay = relay or ("Switches (meter confirms)" if checks == "True" else "Did not switch")
        log = (f"[10:00:00] === run started, tool version 2.3.0 ===\n[10:00:01]     Relay: {relay}\n"
               f"[10:00:02]     Checks passed: {checks}\n[10:00:02]     Result: Converted\n"
               f"[10:00:03] === run ended ===\n")
        def step_scenario(instance, sc, target, tool_log, notes):
            if screen is not None:
                tool_log.write_text(screen, encoding="utf-8")   # what the run printed
            return {"result": "PASS", "runs": 1, **({"route": went} if went else {})}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp, "work/runs/1/run.log")
            write_events(run.parent, ("run", {}), ("began", {"step": "B2"}), *events)
            run.write_text(log, encoding="utf-8")
            (Path(tmp) / "flash.bin").write_bytes(b"\xff" * 0x400000)
            device = SimpleNamespace(reset=lambda start: None, account=lambda sc, folder: None, stop=lambda: None,
                                     start=lambda firmware: True, runs=lambda: [run],
                                     all_events=lambda: output.events(run.parent),
                                     records=lambda: run.parents[2],
                                   dhcp_names=lambda: ["openbeken-cdef"], last_log=lambda: log, state=Path(tmp),
                                   dhcp_log=lambda: "DHCPACK(br0) 198.18.62.51 12:34:56:ab:cd:ef openbeken-cdef\n",
                                   last_outcome=lambda since: outcome, run_texts=lambda: [said + log],
                                   last_state=lambda: {"inputs": {"device_kind": kind}} if kind else {})
            latest = fault_suite.LATEST["openbeken"]
            with mock.patch.object(fault_suite, "OUT", Path(tmp)), mock.patch.object(fault_suite, "MemoryWatch"), \
                    mock.patch.object(fault_suite, "step_scenario", side_effect=step_scenario), \
                    mock.patch.object(fault_suite, "release_of", return_value=release or latest):
                outcome = fault_suite.run_scenario(device, sc)
            if kept is not None:
                folder = Path(tmp, "logs/sonoff-openbeken_B2_deviceoff-1")
                kept.extend(sorted((path.name, path.read_text(encoding="utf-8")) for path in folder.iterdir()
                                   if path.name.startswith(("run-", "dhcp"))))
                kept.extend(("evidence", str(path.relative_to(folder))) for path in sorted(folder.rglob("*"))
                            if "evidence" in path.parts and path.is_file())
            return outcome

    def test_a_device_switched_to_the_other_firmware_is_judged_on_that(self):
        # cancel-switch: converted to ESPHome, it failed for not ending on OpenBeken.
        outcome = self.result("True", went="sonoff>esphome")
        self.assertIn("not esphome's", " ".join(outcome["notes"]["interim"]))

    def test_each_runs_log_and_events_and_the_routers_dhcp_log_are_kept(self):
        kept = []
        self.result("True", kept=kept)
        self.assertEqual([name for name, _ in kept], ["dhcp.log", "run-1.events.jsonl", "run-1.log"])
        self.assertIn("DHCPACK", kept[0][1])
        self.assertIn('"began"', kept[1][1])

    def test_the_explained_waits_its_runs_went_through_are_noted(self):
        # One the starting device's own run waited, before the scenario began, is not.
        waits = [("wait", {"wait": "nothing-asked"}), ("wait", {"wait": "fetch-refused", "at": time.time() + 60})]
        self.assertEqual(self.result("True", events=waits)["notes"]["waits"], ["fetch-refused"])

    def test_a_failure_keeps_its_run_records_whole_as_the_espressif_suites_does(self):
        kept = []
        self.assertEqual(self.result("False", kept=kept)["result"], "FAIL-CHECKS")
        self.assertIn(("evidence", "evidence/runs/1/run.log"), kept)
        passed = []
        self.result("True", kept=passed)
        self.assertNotIn("evidence", [name for name, _ in passed])

    def test_the_checks_are_read_from_the_runs_own_outcome_where_it_kept_one(self):
        # Its words in the log do not decide: the run judged its checks itself.
        said = {"result": "converted", "stopped": None,
                "checks": {"answer": "failed", "relay": "did not switch", "meter_confirms": False}}
        outcome = self.result("True", outcome=said)
        self.assertEqual((outcome["result"], outcome["notes"]["checks"]),
                         ("FAIL-CHECKS", "Checks passed: Fail; Relay: did not switch"))
        unconfirmed = {**said, "checks": {"answer": "passed", "relay": "switches (meter unchanged)",
                                          "meter_confirms": False}}
        self.assertEqual(self.result("True", outcome=unconfirmed)["result"], "FAIL-CHECKS")
        confirmed = {**said, "checks": {"answer": "passed", "relay": "switches (meter confirms)",
                                        "meter_confirms": True}}
        self.assertEqual(self.result("False", outcome=confirmed)["result"], "PASS")

    def test_a_run_that_met_an_error_nobody_foresaw_fails_though_the_next_converts(self):
        unforeseen = "[09:59:00] FAILED: something unexpected went wrong: OSError: the device fetched 1 of 2 bytes\n"
        self.assertEqual(self.result("True", said=unforeseen)["result"], "FAIL-UNEXPECTED")
        # Its own outcome says it too, whatever its words.
        from unittest import mock
        crashed = [{"at": 1.0, "result": "failed", "stopped": {"step": "J2", "error": "crash"}}]
        with mock.patch.object(fault_suite.fault_verdicts, "run_outcomes", return_value=crashed):
            self.assertEqual(self.result("True")["result"], "FAIL-UNEXPECTED")
        stopped = [{"at": 1.0, "result": "failed", "stopped": {"step": "J2", "error": "upload-cut"}}]
        with mock.patch.object(fault_suite.fault_verdicts, "run_outcomes", return_value=stopped):
            self.assertEqual(self.result("True")["result"], "PASS", "a stop to act on is no bug")

    def test_a_converted_device_whose_relay_switched_passes(self):
        self.assertEqual(self.result("True")["result"], "PASS")

    def test_a_stage_s_failed_try_left_on_the_screen_is_its_own_failure(self):
        rule = "=" * 72
        screen = (f"{rule}\nThe screen at the end of the run (100x40):\n{rule}\n"
                  "Stage 6 - Sending OpenBeken - Stopped\nStage 6 - Sending OpenBeken - Complete\n"
                  f"{rule}\nExit code 0 - finished\n")
        outcome = self.result("True", screen)
        self.assertEqual(outcome["result"], "FAIL-SCREEN")
        self.assertEqual(len(outcome["notes"]["screen"]), 1)
        clean = screen.replace("Stage 6 - Sending OpenBeken - Stopped\n", "")
        self.assertEqual(self.result("True", clean)["result"], "PASS")

    def test_a_converted_device_must_be_on_this_releases_firmware(self):
        self.assertEqual(self.result("True")["result"], "PASS")
        outcome = self.result("True", release="1.18.300-1")
        self.assertEqual((outcome["result"], outcome["notes"]["release"]), ("FAIL-NOT-LATEST", "openbeken 1.18.300-1"))

    def test_the_devices_release_is_what_its_firmware_says(self):
        from unittest import mock
        device = SimpleNamespace(lease=lambda: "192.0.2.50")
        with mock.patch.object(fault_suite.openbeken, "info", return_value={"build": "Build on ... version 1.18.320-2"}):
            self.assertEqual(fault_suite.release_of(device, "openbeken"), "1.18.320-2")
        ours = {"project_name": fault_suite.kinds.SONOFF_S61.esphome_project, "project_version": "2026.10.0-2"}
        with mock.patch.object(fault_suite.esphome, "api_device_info", return_value=ours):
            self.assertEqual(fault_suite.release_of(device, "esphome"), "2026.10.0-2")
        with mock.patch.object(fault_suite.esphome, "api_device_info", return_value=dict(ours, project_name="x.y")):
            self.assertIsNone(fault_suite.release_of(device, "esphome"))

    def test_one_whose_meter_did_not_confirm_the_switch_failed_its_checks(self):
        """The emulated device's meter measures a load while the relay is on
        (tools/beken/emulator/board.py), so a check that saw no change in it
        did not read it."""
        outcome = self.result("True", relay="Switches (meter unchanged)")
        self.assertEqual(outcome["result"], "FAIL-CHECKS")
        self.assertEqual(outcome["notes"]["checks"],
                         "the meter did not confirm the switch; Relay: Switches (meter unchanged)")

    def test_only_a_device_with_a_meter_needs_it_to_confirm_the_switch(self):
        """The Basic Gen5 has no meter: its check says the relay switched with
        the meter unchanged, and passes. The S61's must still confirm it."""
        unchanged = "Switches (meter unchanged)"
        self.assertEqual(self.result("True", relay=unchanged, kind="sonoff-basic-1gs")["result"], "PASS")
        self.assertEqual(self.result("True", relay=unchanged, kind="sonoff-s61")["result"], "FAIL-CHECKS")

    def test_the_meter_is_demanded_as_the_kind_run_says(self):
        for kind in fault_suite.kinds.ALL:
            self.assertEqual(fault_suite.metered({"inputs": {"device_kind": kind.key}}), kind.has_meter, kind.key)
        # A run that stopped before it knew the kind: held to the S61's meter.
        self.assertTrue(fault_suite.metered({}))
        self.assertTrue(fault_suite.metered({"inputs": {}}))

    def test_one_whose_checks_failed_is_its_own_failure_saying_what_they_found(self):
        outcome = self.result("False")
        self.assertEqual(outcome["result"], "FAIL-CHECKS")
        self.assertEqual(outcome["notes"]["checks"], "Checks passed: False; Relay: Did not switch")


class EarlierAttemptTests(unittest.TestCase):
    def test_a_scenario_run_again_keeps_its_earlier_attempts_logs(self):
        # As the Espressif suite's: a second attempt wrote over the first's.
        from unittest import mock
        sc = {"id": "sonoff>openbeken:B2:deviceoff-1", "route": "sonoff>openbeken", "step": "B2"}
        device = mock.Mock(start=mock.Mock(return_value=False))   # a device that never starts: back at once
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite, "OUT", Path(tmp)), \
                mock.patch.object(fault_suite, "MemoryWatch"):
            folder = Path(tmp) / "logs" / "sonoff-openbeken_B2_deviceoff-1"
            for attempt in ("first", "second"):
                fault_suite.run_scenario(device, sc)
                (folder / "tool.log").write_text(attempt, encoding="utf-8")
            self.assertEqual((folder.with_name(folder.name + ".1") / "tool.log").read_text(encoding="utf-8"), "first")
            self.assertEqual((folder / "tool.log").read_text(encoding="utf-8"), "second")


class UpdateScenarioTests(unittest.TestCase):
    def test_each_firmware_built_here_has_an_update_with_its_own_faults(self):
        ids = {sc["id"] for sc in fault_suite.scenarios()}
        for wanted in ("outdated>openbeken:U3:deviceoff-1", "outdated>openbeken:U3@90:crash-1",
                       "outdated>openbeken:U4:outage-30", "outdated>openbeken:apply@50:deviceoff-1",
                       "outdated>esphome:U1:deviceoff-1", "outdated>esphome:U1@10:crash-1",
                       "outdated>esphome:U2:crash-1", "outdated>esphome:apply@90:deviceoff-1"):
            self.assertIn(wanted, ids)

    def test_an_update_starts_from_the_device_saved_for_its_firmware(self):
        self.assertEqual(fault_suite.start_of("outdated>openbeken"), "outdated-openbeken")
        self.assertEqual(fault_suite.start_of("outdated>esphome"), "outdated-esphome")
        self.assertEqual(fault_suite.start_of("esphome>openbeken"), "esphome")


class PrepUpdateTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(fault_suite.emulator_instances, "running", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prep_update_takes_the_first_instance_as_prep_and_batch_do(self):
        with mock.patch.object(fault_suite.folders, "migrate", return_value=[]), \
                mock.patch.object(fault_suite, "prep_update") as made:
            self.assertEqual(fault_suite.main(["prep-update", "--first-instance", "81"]), 0)
            self.assertEqual(fault_suite.main(["prep-update"]), 0)
        self.assertEqual([call.args for call in made.call_args_list], [(81,), (1,)])

    def test_prep_update_refuses_an_instance_in_use_before_it_makes_anything(self):
        with mock.patch.object(fault_suite.emulator_instances, "running", return_value={81}), \
                mock.patch.object(fault_suite.folders, "clear") as cleared, \
                mock.patch.object(fault_suite.subprocess, "run") as ran:
            with self.assertRaises(SystemExit) as refused:
                fault_suite.prep_update(81)
        self.assertIn("--first-instance 1", str(refused.exception))
        cleared.assert_not_called()
        ran.assert_not_called()

    def test_the_earlier_releases_prep_runs_on_the_instance_given(self):
        # That release's prep, as it chooses its instance (EARLIER_PREP_INSTANCE).
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp, "fault_suite.py")
            suite.write_text(f"def prep():\n    {fault_suite.EARLIER_PREP_INSTANCE}\n", encoding="utf-8")
            fault_suite.earlier_on(suite, 81)
            self.assertEqual(suite.read_text(encoding="utf-8"), "def prep():\n    instance = Instance(81)\n")
            # Not as expected: refused, never run on instance 1 beside another's.
            with self.assertRaises(SystemExit):
                fault_suite.earlier_on(suite, 82)


class BatchResultTests(unittest.TestCase):
    def setUp(self):
        # No other run's emulators here, whatever this computer runs.
        patcher = mock.patch.object(fault_suite.emulator_instances, "running", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_batch_has_passed_only_when_every_scenario_has(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            ids = Path(temp) / "ids"
            ids.write_text("a\nb\n", encoding="utf-8")
            with mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={"a": {"result": "PASS"}, "b": {"result": "MISTIMED"}}):
                self.assertFalse(fault_suite.all_passed(str(ids)))
            with mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={"a": {"result": "PASS"}, "b": {"result": "PASS"}}):
                self.assertTrue(fault_suite.all_passed(str(ids)))


    def test_share_writes_each_machines_share(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            ids = Path(directory) / "beken.txt"
            ids.write_text("a\nb\n", encoding="utf-8")
            emulation = Path(directory, "emulation")
            Path(emulation, "kept", "second").mkdir(parents=True)
            with mock.patch.object(fault_suite, "scenarios", return_value=[{"id": "a"}, {"id": "b"}]), \
                    mock.patch.object(fault_suite.folders, "migrate", return_value=[]), \
                    mock.patch.object(fault_suite.folders, "EMU", emulation), \
                    mock.patch.object(fault_suite.folders, "BEKEN", emulation / "beken"), \
                    mock.patch.object(fault_suite.scenario_times, "times", return_value={}) as took, \
                    mock.patch("builtins.print"):
                self.assertEqual(fault_suite.main(["share", str(ids), "1", "1"]), 0)
            # Its own folders, and those kept from another machine.
            self.assertEqual(took.call_args.args, (emulation / "beken", emulation / "kept" / "second" / "beken"))
            self.assertEqual([Path(f"{ids}.{n}").read_text(encoding="utf-8") for n in (1, 2)], ["a\n", "b\n"])

    def test_a_batch_starts_the_longest_first(self):
        from unittest import mock
        rows = [{"id": i, "route": "sonoff>openbeken", "step": "B2", "fault": "deviceoff", "arg": 1} for i in "abc"]
        ran = []

        def run(instance, sc):
            ran.append(sc["id"])
            return {"result": "PASS"}
        with mock.patch.object(fault_suite, "scenarios", return_value=rows), \
                mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={}), \
                mock.patch.object(fault_suite, "Instance"), mock.patch.object(fault_suite, "record"), \
                mock.patch.object(fault_suite, "run_scenario", side_effect=run), \
                mock.patch.dict(fault_suite.os.environ, {"AUTOFLASH_BEKEN_EMU_WORKERS": "1"}), \
                mock.patch.object(fault_suite.scenario_times, "times", return_value={"a": 60, "b": 1800, "c": 300}) as took, \
                mock.patch.object(fault_suite.cpu_load, "wait_for_room"), \
                mock.patch.object(fault_suite, "suite_roots", return_value=[Path("here"), Path("kept")]), \
                mock.patch("builtins.print"):
            fault_suite.batch()
        self.assertEqual(ran, ["b", "c", "a"])
        self.assertEqual(took.call_args.args, (Path("here"), Path("kept")))

    def test_a_batch_numbers_its_emulators_from_the_first_instance_given(self):
        from unittest import mock
        rows = [{"id": i, "route": "sonoff>openbeken", "step": "B2", "fault": "deviceoff", "arg": 1} for i in "abc"]
        with mock.patch.object(fault_suite, "scenarios", return_value=rows), \
                mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={}), \
                mock.patch.object(fault_suite, "Instance") as instance, mock.patch.object(fault_suite, "record"), \
                mock.patch.object(fault_suite, "run_scenario", return_value={"result": "PASS"}), \
                mock.patch.dict(fault_suite.os.environ, {"AUTOFLASH_BEKEN_EMU_WORKERS": "2"}), \
                mock.patch.object(fault_suite.scenario_times, "times", return_value={}), \
                mock.patch.object(fault_suite.cpu_load, "wait_for_room"), \
                mock.patch("builtins.print"):
            fault_suite.batch(first=81)
        self.assertEqual(sorted(call.args[0] for call in instance.call_args_list), [81, 82])

    def test_a_batch_refuses_instances_another_run_has(self):
        with mock.patch.object(fault_suite.emulator_instances, "running", return_value={2}), \
                mock.patch.object(fault_suite, "scenarios", return_value=[]), \
                mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={}), \
                mock.patch.object(fault_suite, "Instance") as instance, \
                mock.patch.dict(fault_suite.os.environ, {"AUTOFLASH_BEKEN_EMU_WORKERS": "2"}):
            with self.assertRaises(SystemExit) as refused:
                fault_suite.batch()
        self.assertIn("--first-instance 3", str(refused.exception))
        instance.assert_not_called()

    def test_a_scenario_waits_for_room_before_it_starts(self):
        from unittest import mock
        events = []
        with mock.patch.object(fault_suite, "scenarios", return_value=[{"id": "a", "arg": 1}]), \
                mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={}), \
                mock.patch.object(fault_suite, "Instance"), mock.patch.object(fault_suite, "record"), \
                mock.patch.object(fault_suite, "run_scenario",
                                  side_effect=lambda *a: events.append("run") or {"result": "PASS"}), \
                mock.patch.dict(fault_suite.os.environ, {"AUTOFLASH_BEKEN_EMU_WORKERS": "1"}), \
                mock.patch.object(fault_suite.scenario_times, "times", return_value={}), \
                mock.patch.object(fault_suite.cpu_load, "wait_for_room", side_effect=lambda *a: events.append("wait")), \
                mock.patch("builtins.print"):
            fault_suite.batch()
        self.assertEqual(events, ["wait", "run"])


    def test_a_scenario_that_raises_is_recorded_with_where(self):
        from unittest import mock

        def run(instance, sc):
            raise FileNotFoundError(2, "No such file or directory")
        with mock.patch.object(fault_suite, "scenarios", return_value=[{"id": "a", "arg": 1}]), \
                mock.patch.object(fault_suite.fault_verdicts, "latest", return_value={}), \
                mock.patch.object(fault_suite, "Instance"), mock.patch.object(fault_suite, "record") as record, \
                mock.patch.object(fault_suite, "run_scenario", side_effect=run), \
                mock.patch.dict(fault_suite.os.environ, {"AUTOFLASH_BEKEN_EMU_WORKERS": "1"}), \
                mock.patch.object(fault_suite.scenario_times, "times", return_value={}), \
                mock.patch.object(fault_suite.cpu_load, "wait_for_room"), \
                mock.patch("builtins.print"):
            fault_suite.batch()
        outcome = record.call_args.args[1]
        self.assertEqual(outcome["result"], "ERROR")
        self.assertIn("FileNotFoundError", outcome["notes"]["error"])
        self.assertIn("in run", outcome["notes"]["trace"])

class StepTimingTests(unittest.TestCase):
    def test_a_step_is_seen_soon_enough_for_a_fault_late_in_the_shortest(self):
        shortest = min(seconds for steps in fault_suite.ROUTES.values() for seconds in steps.values())
        self.assertLess(fault_suite.STEP_POLL, shortest * 0.1 / 2)


class CheckTests(unittest.TestCase):
    def setUp(self):
        # No other run's emulators here, whatever this computer runs.
        patcher = mock.patch.object(fault_suite.emulator_instances, "running", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_final_firmware_is_installed_then_the_device_taken_to_the_other(self):
        for way in fault_suite.CHECK_WAYS:
            self.assertEqual(way[0].split(">")[0], "sonoff")
            self.assertEqual(way[1].split(">")[0], way[0].split(">")[1], "each starts from the device the one before made")
        self.assertEqual({way[0].split(">")[1] for way in fault_suite.CHECK_WAYS}, {"openbeken", "esphome"})

    def test_a_way_that_raises_has_failed_and_says_why(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        with mock.patch.object(fault_suite, "check_way", side_effect=RuntimeError("the device did not start")), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fault_suite.check(), 1)
        self.assertIn("FAIL", out.getvalue())
        self.assertIn("the device did not start", out.getvalue())

    def test_only_ways_that_got_to_their_end_pass(self):
        from unittest import mock
        with mock.patch.object(fault_suite, "check_way", return_value=0):
            self.assertEqual(fault_suite.check(), 0)

    def test_each_way_runs_on_the_emulator_asked_for(self):
        # So two devices' checks run side by side on one computer (emulator_check.py --instances).
        from unittest import mock
        with mock.patch.object(fault_suite.folders, "migrate", return_value=[]):
            with mock.patch.object(fault_suite, "check_way", return_value=0) as way:
                self.assertEqual(fault_suite.main(["check", "--instances", "3,4"]), 0)
            self.assertEqual(sorted(call.args for call in way.call_args_list),
                             sorted(zip((3, 4), fault_suite.CHECK_WAYS)))
            with mock.patch.object(fault_suite, "check_way", return_value=0) as way:
                self.assertEqual(fault_suite.main(["check"]), 0)
        self.assertEqual(sorted(call.args[0] for call in way.call_args_list), [1, 2])

    def test_one_emulator_for_two_ways_is_refused(self):
        import io
        from contextlib import redirect_stderr
        from unittest import mock
        with mock.patch.object(fault_suite.folders, "migrate", return_value=[]), \
                redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit):
            fault_suite.main(["check", "--instances", "3"])
        self.assertIn("--instances needs 2 numbers", said.getvalue())

    def test_a_way_whose_end_of_run_checks_failed_has_failed(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        device = mock.Mock(all_runs=list, last_state=dict, dhcp_names=lambda: ["openbeken-cdef"],
                         last_log=lambda: "[10:00:00] === run started ===\n[10:00:01]     Checks passed: False\n")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite, "OUT", Path(tmp)), \
                mock.patch.object(fault_suite, "Instance", return_value=device), \
                mock.patch.object(fault_suite, "converted", return_value=True), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fault_suite.check_way(1, ("sonoff>openbeken",)), 1)
        self.assertIn("FAIL  sonoff>openbeken: Checks passed: False", out.getvalue())

    def way_with_meter_unchanged(self, kind):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        log = ("[10:00:00] === run started ===\n[10:00:01]     Relay: Switches (meter unchanged)\n"
               "[10:00:02]     Checks passed: True\n")
        device = mock.Mock(all_runs=list, last_state=lambda: {"inputs": {"device_kind": kind}},
                           dhcp_names=lambda: ["openbeken-cdef"], last_log=lambda: log)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite, "OUT", Path(tmp)), \
                mock.patch.object(fault_suite, "Instance", return_value=device), \
                mock.patch.object(fault_suite, "converted", return_value=True), redirect_stdout(io.StringIO()) as out:
            return fault_suite.check_way(1, ("sonoff>openbeken",)), out.getvalue()

    def test_a_way_on_a_device_without_a_meter_passes_with_it_unchanged(self):
        self.assertEqual(self.way_with_meter_unchanged("sonoff-basic-1gs"), (0, "PASS  sonoff>openbeken\n"))
        status, said = self.way_with_meter_unchanged("sonoff-s61")
        self.assertEqual(status, 1)
        self.assertIn("FAIL  sonoff>openbeken: the meter did not confirm the switch", said)

    def test_each_of_its_routes_is_one_the_tool_has(self):
        for way in fault_suite.CHECK_WAYS:
            for route in way:
                self.assertTrue(fault_suite.route_steps(route), route)


class BadImageScenarioTests(unittest.TestCase):
    def test_each_step_that_sends_an_image_the_bootloader_installs_has_one_it_refuses(self):
        found = {sc["id"] for sc in fault_suite.scenarios() if sc["fault"] == "badimage" and sc["arg"] == "once"}
        self.assertEqual(found, {"sonoff>openbeken:B2:badimage-once", "sonoff>esphome:B2:badimage-once",
                                 "sonoff>esphome:J2:badimage-once", "openbeken>esphome:O1:badimage-once",
                                 "interim>esphome:J2:badimage-once", "sonoff>readout:start:badimage-once"})

    def outcome(self, arg, *events):
        sc = {"id": "x", "route": "sonoff>openbeken", "step": "B2", "fault": "badimage", "arg": arg, "at": 40}
        instance = SimpleNamespace(tool=lambda *a, **k: None, last_state=dict, all_events=lambda: happened(*events))
        with mock.patch.object(fault_suite, "finish"), mock.patch.object(fault_suite, "converted", return_value=True):
            return fault_suite.bad_image_scenario(instance, sc, "openbeken", Path("tool.log"), {})["result"]

    def test_it_hears_the_run_send_the_image_again(self):
        # The run's own word that it went back to the step (runner.execute, output.EVENTS).
        self.assertEqual(self.outcome("once", ("began", {"step": "B2"})), "FAIL")
        self.assertEqual(self.outcome("once", ("again", {"step": "B4", "restarted": False})), "FAIL")
        self.assertEqual(self.outcome("once", ("again", {"step": "B2", "restarted": False})), "PASS")
        # Restarted by the run itself, where that is what is tested.
        self.assertEqual(self.outcome("restart", ("again", {"step": "B2", "restarted": False})), "FAIL")
        self.assertEqual(self.outcome("restart", ("again", {"step": "B2", "restarted": False}),
                                      ("again", {"step": "B2", "restarted": True})), "PASS")


class SafeModeScenarioTests(unittest.TestCase):
    """safemode-ten: ESPHome cut at each start until it is in safe mode, then
    the tool - which must find it so and wait it out, as on an S60."""

    def test_each_route_from_esphome_has_one_as_the_espressif_suites(self):
        found = {sc["id"] for sc in fault_suite.scenarios() if sc["fault"] == "safemode"}
        self.assertEqual(found, {"esphome>openbeken:start:safemode-ten", "esphome>sonoff:start:safemode-ten"})

    def outcome(self, starts, said, converted=True):
        """`starts`: the ports open at each start's looks; `said`: the first run's words."""
        looks = iter(starts)
        cycled = []
        instance = SimpleNamespace(power=cycled.append, lease=lambda: "192.0.2.60", tool=lambda *a, **k: None,
                                   last_state=dict, run_texts=lambda: [said])
        with mock.patch.object(fault_suite, "ports_open", side_effect=lambda host, *ports: next(looks) & set(ports)), \
                mock.patch.object(fault_suite.time, "sleep"), mock.patch.object(fault_suite, "finish"), \
                mock.patch.object(fault_suite, "recover"), \
                mock.patch.object(fault_suite, "converted", return_value=converted):
            notes = {}
            result = fault_suite.safe_mode_scenario(
                instance, {"id": "x", "route": "esphome>openbeken", "step": "start", "fault": "safemode",
                           "arg": "ten", "at": 40}, "openbeken", Path("tool.log"), notes)["result"]
        return result, len(cycled), notes

    # Each start: its update service answers, then a look for its page.
    NORMAL = [{8892}, {80, 8892}]
    SAFE = [{8892}, {8892}, {8892}]

    def test_safe_mode_seen_and_waited_out_passes(self):
        result, cuts, notes = self.outcome(self.NORMAL * 2 + self.SAFE, "ESPHome in safe mode, after starting")
        self.assertEqual((result, cuts, notes["safemode_cuts"]), ("PASS", 3, 3))

    def test_a_run_that_took_it_for_sonoffs_firmware_fails(self):
        self.assertEqual(self.outcome(self.SAFE, "on Sonoff's firmware: asking eWeLink")[0], "FAIL-TAKEN-FOR-SONOFF")

    def test_a_run_that_found_esphome_itself_again_came_too_late(self):
        # The emulator's safe mode lasts about a minute: its clock runs ahead.
        said = "This run is for the device at 192.0.2.60 (esphome-7a1ec8), running ESPHome."
        self.assertEqual(self.outcome(self.SAFE, said)[0], "MISTIMED")

    def test_never_in_safe_mode_is_not_fired(self):
        self.assertEqual(self.outcome(self.NORMAL * fault_suite.SAFE_MODE_CUTS, "")[0], "NOT-FIRED")


class OwnBuildScenarioTests(unittest.TestCase):
    """ownbuild-replaced: a build of its own put on the device, as a builder
    that adopted it would; then the tool, as the Espressif suite has it."""

    def test_one_from_esphome_as_the_espressif_suites(self):
        self.assertEqual([sc["id"] for sc in fault_suite.scenarios() if sc["fault"] == "ownbuild"],
                         ["esphome>esphome:start:ownbuild-replaced"])

    def outcome(self, builds, said="It is a build of its own, adopted into an ESPHome builder", converted=True):
        """`builds`: what the device says it was built at, each time it is asked."""
        published = fault_suite.beken_images.pin(fault_suite.DEVICE, "esphome")["built"]
        own = "2000-01-01 01:00:00 +0000"
        builds = iter([published, *(own if build == "own" else published for build in builds)])
        runs = []
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "tool.log")

            def tool(target, log_path, new=False, more=()):
                runs.append(more)
                with open(log_path, "a", encoding="utf-8") as out:
                    out.write(said if not more else "Update complete!")
            instance = SimpleNamespace(tool=tool, lease=lambda: "192.0.2.60", last_state=dict)
            with mock.patch.object(fault_suite.own_build, "beken", return_value=(b"uf2", own)), \
                    mock.patch.object(fault_suite.esphome_ota, "upload") as upload, \
                    mock.patch.object(fault_suite, "esphome_now",
                                      side_effect=lambda _: (next(builds), "stock store")), \
                    mock.patch.object(fault_suite.time, "sleep"), mock.patch.object(fault_suite, "finish"), \
                    mock.patch.object(fault_suite.time, "monotonic", side_effect=itertools.count(0, 10)), \
                    mock.patch.object(fault_suite, "recover"), \
                    mock.patch.object(fault_suite, "converted", return_value=converted):
                notes = {}
                outcome = fault_suite.own_build_scenario(instance, log, notes)
        return outcome, runs, notes, upload

    def test_left_be_then_replaced_passes_judged_as_an_update(self):
        outcome, runs, notes, upload = self.outcome(["own", "own", "published"])
        self.assertEqual((outcome["result"], outcome["route"]), ("PASS", "outdated>esphome"))
        self.assertEqual(runs, [(), ("--replace-own-build",)])
        self.assertEqual(upload.call_args.kwargs["port"], fault_suite.esphome_ota.LIBRETINY_PORT)
        self.assertTrue(notes["refused"])

    def test_not_said_to_be_its_own_fails(self):
        outcome = self.outcome(["own", "own", "published"], said="nothing to convert.")[0]
        self.assertEqual(outcome["result"], "FAIL-NOT-REFUSED")

    def test_still_its_own_after_the_replace_fails(self):
        self.assertEqual(self.outcome(["own", "own", "own"])[0]["result"], "FAIL-NOT-REPLACED")

    def test_a_build_of_its_own_that_never_started_is_not_fired(self):
        outcome, runs, _, _ = self.outcome(["published"] * 200)
        self.assertEqual((outcome["result"], runs), ("NOT-FIRED", []))


class DoubleCutTests(unittest.TestCase):
    """A double's second cut as the run under way sends its command again."""

    def cut(self, looks_at, ended_after=None, arg="deviceoff-1"):
        """`looks_at`: the run's events at each look, as (name, fields) lists."""
        from unittest import mock
        seen = iter(looks_at)
        last = [[]]

        def last_events():
            last[0] = next(seen, last[0])
            return happened(*last[0])
        looks = [0]

        def poll():
            looks[0] += 1
            return 0 if ended_after is not None and looks[0] > ended_after else None
        instance = SimpleNamespace(last_events=last_events)
        notes = {}
        sc = {"route": "sonoff>openbeken", "step": "B2", "at": 40, "fault": "double", "arg": arg}
        with mock.patch.object(fault_suite, "inject") as injected, mock.patch.object(fault_suite.time, "sleep"):
            done = fault_suite.cut_as_sent_again(instance, SimpleNamespace(poll=poll), sc, notes)
        return done, notes, injected

    def test_the_second_cut_comes_as_the_same_run_sends_again(self):
        # Seen: once the sender sent a lost command again itself, the first run
        # converted, no run again came, and every double was NOT-FIRED.
        sent = [("began", {"step": "B2"}), ("phase", {"phase": "serving"})]
        again = sent + [("resent", {"attempt": 2})]
        done, notes, injected = self.cut([sent, sent, again])
        self.assertTrue(done)
        self.assertTrue(notes["second_cut"])
        injected.assert_called_once()

    def test_one_ridden_out_with_nothing_sent_again_gets_the_second_as_the_step_goes_on(self):
        # Seen: an outage at B2, the device fetching on - no command sent again,
        # the run converted, and the double was NOT-FIRED.
        from unittest import mock
        with mock.patch.object(fault_suite, "RESENT_WITHIN", 0.2):
            done, notes, injected = self.cut([[("began", {"step": "B2"})]])
        self.assertTrue(done)
        self.assertTrue(notes["second_cut"])
        injected.assert_called_once()

    def test_an_outage_twice_running_needs_nothing_sent_again(self):
        done, notes, injected = self.cut([[("began", {"step": "B2"})]], arg="outage-30")
        self.assertTrue(done)
        injected.assert_called_once()
        self.assertEqual(injected.call_args.args[1:3], ("outage", 30))
        done, _, injected = self.cut([[("began", {"step": "B2"}), ("done", {"step": "B2"})]], arg="outage-30")
        self.assertFalse(done)   # the step over before it: left to a run that tries it again
        injected.assert_not_called()

    def test_a_run_that_ends_without_sending_again_leaves_it_to_the_next(self):
        done, notes, injected = self.cut([[("began", {"step": "B2"})]], ended_after=3)
        self.assertFalse(done)
        self.assertNotIn("second_cut", notes)
        injected.assert_not_called()


class LeaseTests(unittest.TestCase):
    """Where the device is: its router's lease, or - released while it goes on
    using the address, as ESPHome does on rejoining after its network went -
    the last address the router gave it."""

    def lease(self, leases, log):
        instance = fault_suite.Instance.__new__(fault_suite.Instance)
        instance.device = "a-device"
        answers = {"leases": SimpleNamespace(stdout=leases), "dnsmasq.log": SimpleNamespace(stdout=log)}
        with mock.patch.object(fault_suite.subprocess, "run",
                               side_effect=lambda cmd, **_: answers[cmd[-1].rsplit("/", 1)[-1]]):
            return instance.lease()

    def test_the_lease_where_there_is_one(self):
        self.assertEqual(self.lease("1 12:34:56:ab:cd:ef 198.18.61.52 esphome-abcdef *\n", ""), "198.18.61.52")

    def test_the_last_address_given_where_it_was_released(self):
        log = ("dnsmasq-dhcp[33]: 1 DHCPACK(br0) 198.18.61.50 12:34:56:ab:cd:ef openbeken-abcdef\n"
               "dnsmasq-dhcp[33]: 2 DHCPACK(br0) 198.18.61.51 12:34:56:ab:cd:ef esphome-abcdef\n"
               "dnsmasq-dhcp[33]: 3 DHCPRELEASE(br0) 198.18.61.51 12:34:56:ab:cd:ef\n")
        self.assertEqual(self.lease("", log), "198.18.61.51")
        self.assertIsNone(self.lease("", ""))


class PersonTests(unittest.TestCase):
    """The person at the computer joins the device's own network when the run
    says to, again while it still waits, and goes home when told."""

    def watch(self, looks_at, joins_take=True):
        """What the person did, looking once at the run's events as each of
        `looks_at` has them, 20 s apart."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "run.log")
            notes, did, clock = {}, [], [100.0]
            person = fault_suite.Person(SimpleNamespace(runs=lambda: [log], computer="a-computer"), notes)
            person.wifi = lambda *args: did.append(" ".join(args)) or joins_take or args[0] != "join"
            with mock.patch.object(fault_suite.time, "monotonic", side_effect=lambda: clock[0]):
                for events in looks_at:
                    write_events(log.parent, *events)
                    person.look()
                    clock[0] += 20
            return did, notes

    ASKED = [("run", {}), ("join", {"network": "esphome-abcdef"})]
    JOINED = ASKED + [("joined", {"network": "esphome-abcdef"})]

    def test_joined_when_asked_and_home_when_told(self):
        did, notes = self.watch([self.ASKED, self.JOINED, self.JOINED + [("home", {})]])
        self.assertEqual(did, ["join", "home"])
        self.assertEqual(notes, {"joined_own_network": 1})

    def test_a_join_that_did_not_take_is_tried_again_while_the_run_asks(self):
        did, notes = self.watch([self.ASKED, self.ASKED, self.JOINED], joins_take=False)
        self.assertEqual(did, ["join", "join"])
        self.assertNotIn("joined_own_network", notes)

    def test_asked_again_after_a_join_it_is_joined_again(self):
        did, _ = self.watch([self.JOINED, self.JOINED + self.ASKED[1:]])
        self.assertEqual(did, ["join"])

    def test_nothing_is_done_unasked(self):
        did, _ = self.watch([[("run", {}), ("began", {"step": "K2"})]])
        self.assertEqual(did, [])


class NewScenarioTests(unittest.TestCase):
    def test_every_route_that_writes_has_its_apply_cut_and_the_writing_steps_their_double_and_readdress(self):
        ids = {sc["id"] for sc in fault_suite.scenarios()}
        for route, steps in fault_suite.ROUTES.items():
            if not set(steps) & fault_suite.beken.DESTRUCTIVE_STEPS:
                continue
            for at in (10, 50, 90):
                self.assertIn(f"{route}:apply@{at}:deviceoff-1", ids)
            for step in set(steps) & fault_suite.beken.DESTRUCTIVE_STEPS:
                self.assertIn(f"{route}:{step}:double-deviceoff-1", ids)
                self.assertIn(f"{route}:{step}:readdress-new", ids)


class ApplyCutTests(unittest.TestCase):
    """The cut while the bootloader writes the image in, timed off its output."""

    def watch(self, lines, at, *, reads=None):
        """What the cut did as `lines` came out one at a time - or, with
        `reads`, in those groups of lines, each taken in by one read."""
        with tempfile.TemporaryDirectory() as tmp:
            switched = []
            serial = Path(tmp, "serial.log")
            serial.write_bytes(b"")

            def power(how):
                switched.append(how)
                if how == "off":
                    with serial.open("ab") as out:
                        out.write(fault_suite.SWITCHED_OFF + b"; its flash is kept\n")
            instance = SimpleNamespace(state=Path(tmp), power=power)
            cut = fault_suite.ApplyCut(instance, at)
            cut.start()
            for group in reads or [[line] for line in lines]:
                with serial.open("ab") as out:
                    out.write(b"".join(line + b"\n" for line in group))
                time.sleep(0.5 if reads else 0.05)
            deadline = time.monotonic() + 3
            while cut.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            cut.done.set()
            cut.join(2)
            return cut.fired, switched

    def write(self, *percents):
        return [b"[I/OTA] OTA Write: [" + b"=" * p + b">] %d%%" % p for p in percents]

    def test_cut_at_its_share_of_the_bootloaders_write(self):
        lines = [b"[I/OTA] OTA firmware(app) upgrade(1.0.2->1.0.0) startup."] + self.write(5, 30, 49, 52, 80)
        self.assertEqual(self.watch(lines, 50), (52, ["off", "on"]))

    def test_progress_before_an_apply_begins_is_not_it(self):
        # The firmware's own download prints no such line; a stray one is ignored.
        self.assertEqual(self.watch(self.write(60, 70), 50), (None, []))

    def test_an_apply_over_before_its_moment_waits_for_the_next(self):
        lines = ([b"OTA firmware(app) upgrade(a->b) startup."] + self.write(10, 20)
                 + [b"[E/OTA] (ota_erase_dl_rbl:32) ota_erase_dl_rbl erase:286000"]
                 + [b"OTA firmware(app) upgrade(a->b) startup."] + self.write(95))
        self.assertEqual(self.watch(lines, 90), (95, ["off", "on"]))
        # One read taking in the first's end and the next's start, as a busy computer's may.
        self.assertEqual(self.watch(lines, 90, reads=[lines[:-1], lines[-1:]]), (95, ["off", "on"]))


class FaultAppliedTests(unittest.TestCase):
    """A fault that never happened is not a pass (fault_suite.not_fired)."""

    def instance(self, tmp, says_off=True):
        serial = Path(tmp, "serial.log")
        serial.write_bytes(b"[device] switched off; an earlier cut\n")

        def power(how):
            self.switched.append(how)
            if how == "off" and says_off:
                with serial.open("ab") as out:
                    out.write(fault_suite.SWITCHED_OFF + b"; its flash is kept\n")
        self.switched = []
        self.order = []   # the router switched, and Docker asked, in turn
        return SimpleNamespace(state=Path(tmp), power=power, network="a-network", computer="a-computer",
                               device="a-device", lease=lambda: "198.18.62.51",
                               router=lambda how: self.order.append(f"router {how}"))

    def test_a_device_seen_to_stop_is_a_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = {}
            fault_suite.inject(self.instance(tmp), "deviceoff", 0, notes)
        self.assertEqual(self.switched, ["off", "on"])
        self.assertNotIn("not_applied", notes)

    def test_a_device_that_never_says_it_stopped_is_not(self):
        # Only a line since the switch counts: an earlier cut's is in the log already.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fault_suite.cut_power(self.instance(tmp, says_off=False), 0, wait=0.5))
        self.assertEqual(self.switched, ["off", "on"])

    def test_the_apply_cut_does_not_fire_on_a_device_that_stayed_on(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            serial = Path(tmp, "serial.log")
            instance = self.instance(tmp)
            serial.write_bytes(b"OTA firmware(app) upgrade(a->b) startup.\nOTA Write: [=>] 60%\n")
            cut = fault_suite.ApplyCut(instance, 50)
            with mock.patch.object(fault_suite, "cut_power", return_value=False):
                cut.start()
                cut.join(3)
        self.assertIsNone(cut.fired)

    def docker(self, fault, arg, codes):
        from unittest import mock
        notes = {}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite.subprocess, "run",
                                  side_effect=[SimpleNamespace(returncode=c, stdout="198.18.62.2\n") for c in codes]):
            if fault == "readdress":
                fault_suite.readdress(self.instance(tmp), notes)
            else:
                fault_suite.inject(self.instance(tmp), fault, arg, notes)
        return notes

    def test_each_docker_command_that_fails_is_noted(self):
        for fault, arg, codes in (("outage", 0, [0, 1, 0]), ("crash", 1, [1]),
                                  ("ctrlc", "once", [1]), ("readdress", None, [1])):
            with self.subTest(fault=fault, codes=codes):
                self.assertTrue(self.docker(fault, arg, codes).get("not_applied"))

    def test_each_docker_command_that_works_is_not(self):
        for fault, arg, codes in (("outage", 0, [0, 0, 0]), ("crash", 1, [0]), ("ctrlc", "once", [0]),
                                  ("readdress", None, [0])):
            with self.subTest(fault=fault):
                self.assertNotIn("not_applied", self.docker(fault, arg, codes))

    def outage_ending(self, computer_running):
        from unittest import mock
        answers = [SimpleNamespace(returncode=0, stdout="198.18.62.2\n"), SimpleNamespace(returncode=0, stdout=""),
                   SimpleNamespace(returncode=1, stdout=""),
                   SimpleNamespace(returncode=0, stdout="true\n" if computer_running else "false\n")]
        notes = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite.subprocess, "run", side_effect=answers):
            fault_suite.inject(self.instance(tmp), "outage", 0, notes)
        return notes

    def test_an_outage_is_the_router_off_for_the_device_and_the_computer(self):
        # As the Espressif suite's: the network gone, not only this computer off it.
        from unittest import mock

        def docker(command, **_):
            self.order.append(" ".join(command[1:3]))
            return SimpleNamespace(returncode=0, stdout="198.18.62.2\n")
        notes = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite.subprocess, "run", side_effect=docker):
            fault_suite.inject(self.instance(tmp), "outage", 30, notes)
        self.assertEqual(self.order, ["inspect -f", "router off", "network disconnect", "router on", "network connect"])
        self.assertNotIn("not_applied", notes)

    def test_a_router_that_would_not_switch_is_noted(self):
        from unittest import mock
        notes = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite.subprocess, "run",
                                  return_value=SimpleNamespace(returncode=0, stdout="198.18.62.2\n")):
            instance = self.instance(tmp)
            instance.router = mock.Mock(side_effect=fault_suite.subprocess.CalledProcessError(1, "router"))
            fault_suite.inject(instance, "outage", 30, notes)
        self.assertEqual(notes["not_applied"], ["the router switched off", "the router switched on again"])

    def test_an_outage_outlasting_the_run_still_happened(self):
        self.assertNotIn("not_applied", self.outage_ending(computer_running=False))

    def test_a_computer_still_running_and_not_brought_back_is_noted(self):
        self.assertTrue(self.outage_ending(computer_running=True)["not_applied"])

    def test_an_outage_with_no_address_to_come_back_to_is_not_started(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(fault_suite.subprocess, "run",
                                  return_value=SimpleNamespace(returncode=0, stdout="\n")) as run:
            notes = {}
            fault_suite.inject(self.instance(tmp), "outage", 0, notes)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(notes["not_applied"])

    def test_a_fallback_is_the_router_alone_off_long_enough(self):
        from unittest import mock
        notes = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fault_suite.time, "sleep") as slept, \
                mock.patch.object(fault_suite.subprocess, "run") as docker:
            fault_suite.inject(self.instance(tmp), "fallback", 150, notes)
        self.assertEqual(self.order, ["router off", "router on"])
        slept.assert_called_once_with(150)
        docker.assert_not_called()   # the computer stays on the network
        self.assertNotIn("not_applied", notes)

    def test_a_fallback_the_person_never_joined_for_proves_nothing(self):
        self.assertTrue(fault_suite.not_fired("fallback", {}))
        self.assertFalse(fault_suite.not_fired("fallback", {"joined_own_network": 1}))

    def test_what_never_happened_proves_nothing(self):
        self.assertFalse(fault_suite.not_fired("deviceoff", {}))
        self.assertTrue(fault_suite.not_fired("deviceoff", {"not_applied": ["x"]}))
        self.assertTrue(fault_suite.not_fired("double", {}))
        self.assertFalse(fault_suite.not_fired("double", {"second_cut": True}))
        self.assertTrue(fault_suite.not_fired("readdress", {"moved_from": "a", "moved_to": "a"}))
        self.assertTrue(fault_suite.not_fired("readdress", {"moved_from": "", "moved_to": "a"}))
        self.assertFalse(fault_suite.not_fired("readdress", {"moved_from": "a", "moved_to": "b"}))
        self.assertTrue(fault_suite.not_fired("ctrlc", {"ctrlc_seen": False}))
        self.assertFalse(fault_suite.not_fired("ctrlc", {"ctrlc_seen": True}))

    def test_a_device_that_stayed_on_is_noted(self):
        from unittest import mock
        notes = {}
        with mock.patch.object(fault_suite, "cut_power", return_value=False):
            fault_suite.inject(None, "deviceoff", 1, notes)
        self.assertTrue(notes["not_applied"])

    def step_result(self, *, is_converted):
        from unittest import mock
        sc = next(sc for sc in fault_suite.scenarios() if sc["fault"] == "ctrlc")
        instance = SimpleNamespace(tool=lambda *a, **k: None, last_state=lambda: {}, last_log=lambda: "",
                                   runs=lambda: [], run_texts=list, last_events=list)
        with mock.patch.object(fault_suite, "wait_for_step", return_value=True), \
                mock.patch.object(fault_suite, "inject"), mock.patch.object(fault_suite, "finish"), \
                mock.patch.object(fault_suite, "recover"), mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite, "converted", return_value=is_converted):
            return fault_suite.step_scenario(instance, sc, "esphome", Path("unused"), {})["result"]

    def test_converted_without_its_fault_is_not_fired_and_a_failure_stands(self):
        self.assertEqual(self.step_result(is_converted=True), "NOT-FIRED")
        self.assertEqual(self.step_result(is_converted=False), "FAIL")


class RecoverTests(unittest.TestCase):
    def test_the_device_is_switched_off_and_on_only_where_the_run_said_to(self):
        # As it stopped: advice in a step the run went on from is not for the person now.
        for events, cycles in (([("began", {"step": "B2"}), ("switch", {})], 1),
                               ([("began", {"step": "B2"})], 0),
                               ([("switch", {}), ("began", {"step": "B4"})], 0),
                               ([("began", {"step": "B4"}), ("switch", {"now": True})], 0)):
            powered = []
            instance = SimpleNamespace(last_events=lambda events=events: happened(*events), power=powered.append,
                                       ready=lambda firmware, wait=0: True)
            notes = {}
            fault_suite.recover(instance, "esphome", notes)
            self.assertEqual((powered, notes.get("power_cycles", 0)), (["cycle"] * cycles, cycles), events)

    def test_a_step_is_seen_starting_from_the_runs_own_event(self):
        events = [("began", {"step": "O3"})]
        instance = SimpleNamespace(last_events=lambda: happened(*events))
        run = SimpleNamespace(poll=lambda: None)
        threading.Timer(0.3, lambda: events.append(("began", {"step": "O1"}))).start()
        self.assertTrue(fault_suite.wait_for_step(instance, "O1", run, limit=5))
        self.assertFalse(fault_suite.wait_for_step(SimpleNamespace(last_events=lambda: happened(events[0])), "O1",
                                                   SimpleNamespace(poll=lambda: None), limit=0.6))

    def test_the_newest_runs_events_are_from_its_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = fault_suite.Instance(3)
            older, newer = Path(tmp, "1/run.log"), Path(tmp, "2/run.log")
            write_events(older.parent, ("run", {}), ("began", {"step": "B2"}))
            write_events(newer.parent, ("run", {}), ("began", {"step": "B2"}), ("ended", {}), ("run", {}),
                         ("began", {"step": "B4"}))
            with mock.patch.object(fault_suite.Instance, "runs", return_value=[older, newer]):
                self.assertEqual([e.get("step") for e in instance.last_events()], [None, "B4"])
                self.assertEqual(len(instance.all_events()), 7)


class InterimTests(unittest.TestCase):
    CLEAN = bytes([0xFF]) * 0x400000

    def test_a_new_openbeken_is_checked_with_scenarios_the_suite_has(self):
        self.assertLessEqual(set(fault_suite.OPENBEKEN_CHECK), {sc["id"] for sc in fault_suite.scenarios()})

    def test_every_way_from_the_interim_firmware(self):
        self.assertLessEqual({"interim>esphome", "interim>openbeken", "interim>sonoff"}, set(fault_suite.ROUTES))

    def test_kept_as_openbeken_nothing_is_written_and_j3_waits_for_its_restart(self):
        # J3 sends no image, so there is no bootloader write to cut; and it
        # waits for the restart itself, so K1 finds the device answering at once.
        ids = {sc["id"] for sc in fault_suite.scenarios() if sc["route"] == "interim>openbeken"}
        self.assertFalse({i for i in ids if ":apply@" in i or ":K1:" in i})

    def test_the_names_a_device_asked_dhcp_for(self):
        log = ("Nov  3 10:00:01 dnsmasq-dhcp[7]: 3126 DHCPACK(br0) 198.18.0.50 12:34:56:ab:cd:ef\n"
               "Nov  3 10:01:02 dnsmasq-dhcp[7]: 3127 DHCPACK(br0) 198.18.0.50 12:34:56:ab:cd:ef "
               "autoflash-recovery-CDEF\n")
        self.assertEqual([m.group(1) for m in fault_suite.DHCP_NAME.finditer(log)], ["autoflash-recovery-CDEF"])

    def test_through_the_interim_firmware_by_its_own_name_and_left_clean(self):
        self.assertEqual(fault_suite.interim_problems(
            "sonoff>esphome", ["autoflash-recovery-CDEF", "esphome-abcdef"], self.CLEAN), [])

    def test_the_interim_firmware_by_openbekens_name(self):
        problems = fault_suite.interim_problems(
            "sonoff>esphome", ["openbeken-abcdef", "esphome-abcdef"], self.CLEAN)
        self.assertEqual(len(problems), 2)   # never its own name, and OpenBeken's

    def test_its_settings_left_behind(self):
        flash = bytearray(self.CLEAN)
        flash[0x1E5000 + 0x10] = 0
        problems = fault_suite.interim_problems(
            "interim>esphome", ["autoflash-recovery-CDEF", "esphome-abcdef"], bytes(flash))
        self.assertEqual(problems, ["the interim firmware's settings at 0x1e5000 were not erased"])

    def test_kept_as_openbeken_by_openbekens_name(self):
        self.assertEqual(fault_suite.interim_problems(
            "interim>openbeken", ["autoflash-recovery-CDEF", "openbeken-abcdef"], None), [])
        self.assertTrue(fault_suite.interim_problems("interim>openbeken", ["autoflash-recovery-CDEF"], None))

    def test_routes_that_never_run_it_ask_nothing_of_it(self):
        self.assertEqual(fault_suite.interim_problems("sonoff>openbeken", ["openbeken-abcdef"], None), [])
        self.assertEqual(fault_suite.interim_problems("openbeken>sonoff", ["openbeken-abcdef"], None), [])


class ConvertedTests(unittest.TestCase):
    def state(self, **steps):
        return {"steps": {step: {"status": status} for step, status in steps.items()}}

    def test_converted_when_every_step_of_the_route_is_done(self):
        self.assertTrue(fault_suite.converted(self.state(B1="done", B2="done", K1="done", B4="done"),
                                              "sonoff>openbeken"))

    def test_not_while_a_step_is_left(self):
        self.assertFalse(fault_suite.converted(self.state(B1="done", B2="done", K1="failed"),
                                               "sonoff>openbeken"))
        self.assertFalse(fault_suite.converted({}, "esphome>sonoff"))


class SavingTests(unittest.TestCase):
    def test_a_saved_device_keeps_its_flash_and_its_runs_captures_only(self):
        self.assertEqual(fault_suite.keep_for_saving("/x/work", ["captures", "tools", "README.md"]),
                         ["tools", "README.md"])
        self.assertEqual(fault_suite.keep_for_saving("/x/state-1",
                                                     ["flash.bin", "serial.log", "power", "ca.crt"]),
                         ["serial.log", "power", "ca.crt"])
        self.assertEqual(fault_suite.keep_for_saving("/x/devices/12-34-56-ab-cd-ef", ["device.json", "sonoff-backup"]),
                         ["sonoff-backup"])


class ReadoutAllTests(unittest.TestCase):
    """A readout of every device in each made-up account, as the Espressif
    suite's: the same scenarios, the API's side judged, run again until one
    finishes."""

    def test_the_same_scenarios_as_the_espressif_suites(self):
        from tools.espressif.emulator import fault_suite as espressif
        mine = {sc["id"] for sc in fault_suite.scenarios() if sc["route"] == fault_suite.READOUT_ALL}
        theirs = {sc["id"] for sc in espressif.scenarios() if sc["route"] == espressif.READOUT_ALL}
        self.assertEqual(mine, theirs)
        self.assertEqual(fault_suite.READOUT_ALL, espressif.READOUT_ALL)

    def test_one_that_passed_meets_no_conversions_checks(self):
        # Seen in the suite's first run of them: every one an ERROR, looking up
        # the steps of a conversion to "readout-all" once it had passed.
        from types import SimpleNamespace
        from unittest import mock
        sc = {"id": "sonoff>readout-all:start:none-mixed", "route": fault_suite.READOUT_ALL, "step": "start",
              "fault": "none", "arg": "mixed", "at": 40, "account": "mixed"}
        with tempfile.TemporaryDirectory() as tmp:
            device = SimpleNamespace(reset=lambda start: None, account=lambda sc, folder: None,
                                     start=lambda firmware: True, runs=lambda: [], state=Path(tmp),
                                     all_events=list, run_texts=lambda: [], dhcp_log=lambda: "",
                                     dhcp_names=mock.Mock(side_effect=AssertionError("a conversion's check")))
            with mock.patch.object(fault_suite, "OUT", Path(tmp)), mock.patch.object(fault_suite, "MemoryWatch"), \
                    mock.patch.object(fault_suite, "Person"), \
                    mock.patch.object(fault_suite, "readout_all_scenario", return_value={"result": "PASS", "runs": 1}):
                outcome = fault_suite.run_scenario(device, sc)
        self.assertEqual(outcome["result"], "PASS")

    def test_each_account_reaches_the_stub_and_the_api_and_none_lingers(self):
        from tools.ewelink.stub_cloud import accounts
        instance = fault_suite.Instance(3)
        with tempfile.TemporaryDirectory() as tmp:
            instance.account({"account": "many"}, Path(tmp))
            self.assertEqual(json.loads(Path(instance.env["AUTOFLASH_EMU_STUB_OTHERS"]).read_text(encoding="utf-8")),
                             accounts.ACCOUNTS["many"])
            self.assertEqual(instance.env["AUTOFLASH_LOCAL_READOUT_LIMIT"], "1")
            instance.account({"account": "enough"}, Path(tmp))
            self.assertNotIn("AUTOFLASH_LOCAL_READOUT_LIMIT", instance.env)
            self.assertIn(accounts.ACCOUNTS["enough"][0]["productModel"], instance.env["AUTOFLASH_API_ENOUGH"])
            instance.account({}, Path(tmp))
            self.assertFalse({"AUTOFLASH_EMU_STUB_OTHERS", "AUTOFLASH_API_ENOUGH"} & set(instance.env))

    WANTED = '2026-11-17T10:00:01.000Z POST /v1/wanted 140 -> 200 {"wanted":true,"held":[]}'

    def outcome(self, codes, requests, stored=True):
        """readout_all_scenario with no fault, each run ending with `codes` in
        turn, the API asked `requests` (lines) by each."""
        from types import SimpleNamespace
        from unittest import mock
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp, "state")
            (state / "api").mkdir(parents=True)
            run = state / "work/runs/1/run.log"
            run.parent.mkdir(parents=True)
            run.write_text("[10:00:00] === run started ===\n" + "".join(
                f"[10:00:0{n}] Reading out 'Device {n}' (X) ({n} of 3)...\n" for n in (1, 2, 3)), encoding="utf-8")
            if stored:
                (state / f"api/readouts__2026-11-17__{digest[:24]}.tar").write_bytes(b"tar")
            pending = list(zip(codes, requests))

            def tool(target, log, **options):
                code, lines = pending.pop(0)
                with open(state / "api/requests.log", "a", encoding="utf-8") as out:
                    out.write("".join(line + "\n" for line in lines))
                return SimpleNamespace(returncode=code, wait=lambda timeout=None: code)
            instance = SimpleNamespace(state=state, tool=tool, runs=lambda: [run])
            sc = {"id": "x", "route": fault_suite.READOUT_ALL, "step": "start", "fault": "none", "arg": "mixed",
                  "at": 40, "account": "mixed"}
            with mock.patch.object(fault_suite, "finish"), mock.patch.object(fault_suite, "recover"), \
                    mock.patch.object(fault_suite, "sonoff_works", return_value=True), \
                    mock.patch.object(fault_suite.fault_verdicts, "readout_problems", return_value=[]):
                found = fault_suite.readout_all_scenario(instance, sc, Path(tmp, "tool.log"), notes := {})
            # Each run's own part of the API's log, kept beside the scenario's.
            kept = [Path(tmp, f"tool-api-{n}/requests.log").read_text(encoding="utf-8") for n in
                    range(1, len(notes["runs"]) + 1)]
        return found, kept

    def taken(self, digest="a" * 64):
        return f'2026-11-17T10:00:10.000Z POST /v1/readout 9000 -> 201 {{"id":"2026-11-17-{digest[:12]}"}}'

    def test_every_device_asked_and_the_device_taken_whole_passes(self):
        held = '2026-11-17T10:00:21.000Z POST /v1/wanted 140 -> 200 {"wanted":false,"why":"held","held":[]}'
        found, _ = self.outcome([0], [[held, held, self.WANTED, self.taken()]])
        self.assertEqual(found["result"], "PASS")

    def test_run_again_until_one_finishes_each_judged_by_its_own_requests(self):
        held = '2026-11-17T10:00:21.000Z POST /v1/wanted 140 -> 200 {"wanted":false,"why":"held","held":[]}'
        found, kept = self.outcome([1, 0], [[held, held, self.WANTED], [held, held, self.WANTED, self.taken()]])
        self.assertEqual((found["result"], found["runs"]), ("PASS", 2))
        self.assertEqual(kept[1].count("/v1/wanted"), 3)   # the second run's alone

    def test_one_the_api_did_not_keep_fails_as_it_says(self):
        held = '2026-11-17T10:00:21.000Z POST /v1/wanted 140 -> 200 {"wanted":false,"why":"held","held":[]}'
        found, _ = self.outcome([0], [[held, held, self.WANTED, self.taken()]], stored=False)
        self.assertEqual(found["result"], "FAIL-NOT-STORED")


class InstanceTests(unittest.TestCase):
    def test_each_instance_is_beken_emu_shs_own(self):
        instance = fault_suite.Instance(3)
        self.assertEqual(instance.command("power", "on"),
                         [str(fault_suite.BK_EMU), "--instance", "3", "power", "on"])
        self.assertEqual((instance.device, instance.network, instance.computer),
                         ("autoflash-beken-emu-3", "autoflash-beken-emu-3", "autoflash-beken-emu-computer-3"))
        self.assertEqual(instance.state, fault_suite.REPO / "captures/emulation/beken/state-3")

    def test_stopping_takes_the_api_beside_it_away_too(self):
        # Else its watcher takes it away only after the device has gone, and the
        # next scenario's computer could find it, keeping what it is sent in a
        # state reset() has cleared.
        from unittest import mock
        with mock.patch.object(fault_suite.subprocess, "run",
                               return_value=SimpleNamespace(stdout="")) as run:
            fault_suite.Instance(3).stop()
        self.assertIn(mock.call(["docker", "rm", "-f", "autoflash-beken-emu-3-api"], capture_output=True),
                      run.call_args_list)



class EarlierRecordsTests(unittest.TestCase):
    def test_runs_a_saved_device_came_with_are_not_this_scenarios_once_their_folder_is_renamed(self):
        """A device saved by a release before 3.0.0 has its runs under
        captures/plugs/; this checkout's tool renames that as it starts."""
        with tempfile.TemporaryDirectory() as tmp:
            instance = fault_suite.Instance(3)
            instance.state = Path(tmp)
            earlier = instance.state / "work/captures/plugs/12-34-56-ab-cd-ef/runs/20261013-031500"
            earlier.mkdir(parents=True)
            (earlier / "run.log").write_text("converted\n", encoding="utf-8")
            instance.before = {fault_suite.fault_verdicts.filed_as(path) for path in instance.all_runs()}
            self.assertEqual(len(instance.before), 1)
            (instance.state / "work/captures/plugs").rename(instance.state / "work/captures/devices")
            self.assertEqual(instance.runs(), [])
            new = instance.state / "work/captures/devices/12-34-56-ab-cd-ef/runs/20261110-120000"
            new.mkdir(parents=True)
            (new / "run.log").write_text("updated\n", encoding="utf-8")
            self.assertEqual(instance.runs(), [new / "run.log"])

    def test_a_run_filed_away_as_its_runs_are_listed_is_left_out(self):
        # Seen in a whole run (ERROR): the tool moved a run from unidentified/
        # to its device's folder between the listing and the look at its time.
        with tempfile.TemporaryDirectory() as tmp:
            instance = fault_suite.Instance(3)
            instance.state = Path(tmp)
            kept = instance.state / "work/captures/devices/12-34-56-ab-cd-ef/runs/20261114-094916/run.log"
            kept.parent.mkdir(parents=True)
            kept.write_text("converting\n", encoding="utf-8")
            moved = instance.state / "work/captures/devices/unidentified/runs/20261114-094916/run.log"
            with mock.patch.object(Path, "glob", return_value=iter([moved, kept])):
                self.assertEqual(instance.all_runs(), [kept])
            with mock.patch.object(fault_suite.Instance, "runs", return_value=[moved]):
                self.assertEqual(instance.last_log(), "")


class ReadoutScenarioTests(unittest.TestCase):
    """Readouts (cli.py readout): of a stock device, faulted where it matters
    most, and of a device on OpenBeken, which must be refused."""

    def test_the_readouts_listed(self):
        self.assertEqual([sc["id"] for sc in fault_suite.scenarios() if sc["route"].endswith(">readout")], [
            "sonoff>readout:start:none-unattended", "sonoff>readout:read:deviceoff-1", "sonoff>readout:read:outage-30",
            "sonoff>readout:read:crash-1", "sonoff>readout:back@10:deviceoff-1", "sonoff>readout:back@10:outage-30",
            # In the read, as a conversion's steps that write have them.
            *(f"sonoff>readout:read:{fault}-{arg}" for fault, arg in
              fault_suite.READOUT_HANDLING + fault_suite.NETWORK + [("ctrlc", "once"), ("token", "login"),
                                                               ("readdress", "new")]),
            "sonoff>readout:read:diskfull-once",
            "sonoff>readout:start:badimage-once", "openbeken>readout:start:none-first-stage"])

    def test_a_readout_is_run_as_one_found_by_itself(self):
        from unittest import mock
        with mock.patch.object(fault_suite.subprocess, "Popen") as popen, tempfile.TemporaryDirectory() as tmp:
            fault_suite.Instance(3).tool("readout", Path(tmp, "tool.log"))
            fault_suite.Instance(3).tool("sonoff", Path(tmp, "tool.log"), new=True)
        readout, conversion = (c.args[0][3:] for c in popen.call_args_list)
        # No --device-ip: a Beken device answers no discovery, and is found by its hardware address (a9f1a13).
        # On a terminal of its own, its screen kept (unattended_run.py adds --unattended); the
        # readout kept unsent, for readout_problems, not sent to the API beside the device.
        self.assertEqual(readout, ["--no-api", "computer", "tools/repo/unattended_run.py", "--command", "readout",
                                   "--", "--env-file", "captures/tool.env"])
        self.assertEqual(conversion[:2], ["--no-api", "computer"])
        self.assertEqual(conversion[2:4] + conversion[-3:],
                         ["tools/repo/unattended_run.py", "--", "--firmware", "sonoff", "--new"])

    def test_each_phase_is_timed_from_what_the_readout_does(self):
        from unittest import mock
        from tools.autoflash import readout_beken
        said = []
        with mock.patch.object(output, "event", side_effect=lambda name, **f: said.append(f.get("phase"))), \
                mock.patch.object(output, "say"), mock.patch.object(output, "progress"), \
                mock.patch.object(output, "progress_clear"), \
                mock.patch.object(readout_beken.openbeken, "read_flash", return_value=b"\xff" * readout_beken.WHOLE_FLASH), \
                mock.patch.object(readout_beken.onward, "serve_to_device"), \
                mock.patch("tools.ewelink.relay.answers", return_value=True):
            readout_beken.read_whole_flash(mock.Mock(), "192.0.2.50")
            readout_beken.revert_to_sonoff(mock.Mock(), "192.0.2.50", mock.Mock())
            ctx = mock.Mock(account_devices=lambda: [{"deviceid": "1000abcdef", "online": True}],
                            ewelink_env=lambda: {"EWELINK_PASSWORD": "made-up"})
            readout_beken.wait_for_sonoff(ctx, "1000abcdef")
        for phase, (begins, ends, _) in fault_suite.READOUT_PHASES.items():
            self.assertLess(said.index(begins), said.index(ends), phase)
            self.assertLessEqual({begins, ends}, output.PHASES)

    def instance(self, tmp, kept=None, answering=False):
        """A device whose runs kept `kept` (a readout.tar's bytes, or None)."""
        run = Path(tmp, "runs/1/run.log")
        run.parent.mkdir(parents=True, exist_ok=True)
        run.write_text("[10:00:00] === run started ===\n", encoding="utf-8")
        if kept is not None:
            (run.parent / fault_suite.READOUT).write_bytes(kept)
        return SimpleNamespace(runs=lambda: [run], answering=lambda: answering, last_log=lambda: "",
                               last_events=list, stub_said=lambda: b"", run_texts=list,
                               tool=lambda target, log, **options: SimpleNamespace(target=target))

    def outcome(self, sc, instance, kept_after=None, works=True):
        from unittest import mock
        notes = {}
        tools = []

        def run(target, log, **options):
            tools.append(target)
            if kept_after is not None and len(tools) == kept_after:
                (instance.runs()[0].parent / fault_suite.READOUT).write_bytes(b"whole")
            return SimpleNamespace(wait=lambda timeout=None: 0)
        instance.tool = run
        with mock.patch.object(fault_suite, "finish"), mock.patch.object(fault_suite, "wait_for", return_value=True), \
                mock.patch.object(fault_suite, "inject"), mock.patch.object(fault_suite, "recover"), \
                mock.patch.object(fault_suite, "settle"), mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite, "sonoff_works", return_value=works), \
                mock.patch.object(fault_suite.fault_verdicts, "readout_problems",
                                  side_effect=lambda data, family: [] if data == b"whole" else ["no info.txt"]):
            return fault_suite.readout_scenario(instance, sc, Path("tool.log"), notes), tools, notes

    SC = {"id": "x", "route": "sonoff>readout", "step": "read", "fault": "crash", "arg": 1, "at": 40}

    def test_a_whole_readout_kept_and_sonoff_working_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome, tools, _ = self.outcome(self.SC, self.instance(tmp), kept_after=1)
        self.assertEqual((outcome["result"], tools), ("PASS", ["readout"]))

    def test_a_device_left_on_openbeken_is_put_back_on_sonoff_then_read_out_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp, answering=True)
            outcome, tools, notes = self.outcome(self.SC, instance, kept_after=3)
        # Still answering as OpenBeken after the conversion, in this stand-in: sent to Sonoff again.
        self.assertEqual(tools[:2], ["readout", "sonoff"])
        self.assertEqual(notes["then"][0], "sonoff")
        self.assertEqual(outcome["result"], "PASS")

    def test_none_kept_whole_or_sonoff_not_working_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome, tools, notes = self.outcome(self.SC, self.instance(tmp, kept=b"part"))
        self.assertEqual(outcome["result"], "FAIL-READOUT")
        self.assertEqual(len(tools), 1 + fault_suite.RECOVERY_RUNS)
        self.assertEqual(notes["readout"], ["no info.txt"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.outcome(self.SC, self.instance(tmp), kept_after=1, works=False)[0]["result"],
                             "FAIL-NOT-SONOFF")

    def test_a_fault_after_its_phase_is_mistimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self.instance(tmp)
            instance.last_events = lambda: happened(("phase", {"phase": "read-done"}))
            self.assertEqual(self.outcome(self.SC, instance, kept_after=1)[0]["result"], "MISTIMED")

    PLAIN = {"id": "x", "route": "sonoff>readout", "step": "start", "fault": "none", "arg": "unattended", "at": 0}
    DIGEST = "a" * 64

    def sent(self, *, stored=True, whole=True):
        """The readout with no fault, sent to the API beside the device: its
        verdict, and how the tool was run."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp, "state/work/runs/1/run.log")
            run.parent.mkdir(parents=True)
            run.write_text("[10:00:00] === run started ===\n", encoding="utf-8")
            api = Path(tmp, "state/api")
            api.mkdir()
            (api / "requests.log").write_text(
                '2026-11-17T10:00:01.000Z POST /v1/wanted 140 -> 200 {"wanted":true,"held":[]}\n'
                f'2026-11-17T10:00:10.000Z POST /v1/readout 9000 -> 201 {{"id":"2026-11-17-{self.DIGEST[:12]}"}}\n',
                encoding="utf-8")
            if stored:
                (api / f"readouts__2026-11-17__{self.DIGEST[:24]}.tar").write_bytes(b"tar")
            runs = []
            instance = SimpleNamespace(state=Path(tmp, "state"), runs=lambda: [run], answering=lambda: False,
                                       last_log=lambda: "", last_events=list, stub_said=lambda: b"", run_texts=list,
                                       tool=lambda target, log, **options: runs.append((target, options)))
            with mock.patch.object(fault_suite, "finish"), mock.patch.object(fault_suite, "recover"), \
                    mock.patch.object(fault_suite, "settle"), \
                    mock.patch.object(fault_suite, "sonoff_works", return_value=True), \
                    mock.patch.object(fault_suite.fault_verdicts, "readout_problems",
                                      return_value=[] if whole else ["no info.txt"]):
                outcome = fault_suite.readout_scenario(instance, self.PLAIN, Path(tmp, "x.log"), {})
            self.assertTrue(Path(tmp, "x-api/requests.log").exists())   # what the API was asked, kept
        return outcome["result"], runs

    def test_the_readout_with_no_fault_is_sent_to_the_api_and_judged_there(self):
        result, runs = self.sent()
        self.assertEqual((result, runs), ("PASS", [("readout", {"api": True})]))
        self.assertTrue(fault_suite.to_api(self.PLAIN))
        self.assertFalse(fault_suite.to_api(self.SC))

    def test_one_the_api_did_not_keep_whole_fails_as_the_api_says(self):
        self.assertEqual(self.sent(stored=False)[0], "FAIL-NOT-STORED")
        self.assertEqual(self.sent(whole=False)[0], "FAIL-READOUT")

    def test_a_readout_sent_to_the_api_runs_the_computer_with_it(self):
        from unittest import mock
        with mock.patch.object(fault_suite.subprocess, "Popen") as popen, tempfile.TemporaryDirectory() as tmp:
            fault_suite.Instance(3).tool("readout", Path(tmp, "tool.log"), api=True)
        self.assertEqual(popen.call_args.args[0][3:5], ["computer", "tools/repo/unattended_run.py"])

    def converted(self, code, said="", answering=True, kept=b"tar", events=()):
        from unittest import mock
        sc = {"id": "x", "route": "openbeken>readout", "step": "start", "fault": "none", "arg": "first-stage", "at": 40}
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "runs/1/run.log")
            log.parent.mkdir(parents=True)
            if kept is not None:
                (log.parent / fault_suite.READOUT).write_bytes(kept)
            instance = SimpleNamespace(tool=lambda target, log, **options: SimpleNamespace(returncode=code),
                                       last_log=lambda: said, answering=lambda: answering, runs=lambda: [log],
                                       last_events=lambda: happened(*events))
            with mock.patch.object(fault_suite, "finish"), \
                    mock.patch.object(fault_suite.fault_verdicts, "first_stage_problems",
                                      side_effect=lambda data, runs: [] if (data, runs) == (b"tar", "openbeken")
                                      else ["wrong"]):
                return fault_suite.readout_scenario(instance, sc, Path("tool.log"), {})["result"]

    def test_a_readout_of_a_device_on_openbeken_reads_its_first_stage_alone_leaving_it_as_it_was(self):
        self.assertEqual(self.converted(0), "PASS")
        self.assertEqual(self.converted(1, "[10:00:00] FAILED: Device is offline in eWeLink"), "FAIL-EXIT")
        self.assertEqual(self.converted(0, kept=None), "FAIL-READOUT")
        self.assertEqual(self.converted(0, kept=b"a whole one"), "FAIL-READOUT")
        self.assertEqual(self.converted(0, events=[("phase", {"phase": "serving"})]), "FAIL-SENT")
        self.assertEqual(self.converted(0, answering=False), "FAIL-DISTURBED")

    def test_sonoff_online_is_told_from_the_stub_since_a_moment(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = fault_suite.Instance(3)
            instance.state = Path(tmp)
            (Path(tmp) / "stub.log").write_bytes(b'{"TZ":{"x":1}}\nlater\n')
            self.assertTrue(instance.online())
            self.assertFalse(instance.online(since=10))



class RestartImageTests(unittest.TestCase):
    """badimage-restart: an image refused past the times a run sends it again,
    so the run restarts the device (on OpenBeken) and sends it whole."""

    def test_spoilt_once_past_the_runs_limit(self):
        from tools.autoflash import runner
        self.assertEqual(fault_suite.SPOILT_PAST_LIMIT, runner.GO_BACK_LIMIT + 1)

    def test_listed_where_the_device_refusing_it_is_on_openbeken(self):
        ids = {sc["id"] for sc in fault_suite.scenarios() if sc["arg"] == "restart"}
        self.assertEqual(ids, {"openbeken>esphome:O1:badimage-restart", "interim>esphome:J2:badimage-restart",
                               "sonoff>esphome:J2:badimage-restart"})



class HandlingScenarioTests(unittest.TestCase):
    """The tool's own handling and the network spoilt, as the Espressif suite tries them."""

    def test_each_step_that_writes_has_them_and_the_readouts_read(self):
        ids = {sc["id"] for sc in fault_suite.scenarios()}
        for fault, arg in fault_suite.OWN_HANDLING + fault_suite.NETWORK:
            with self.subTest(fault=fault, arg=arg):
                self.assertIn(f"sonoff>openbeken:B2:{fault}-{arg}", ids)
                self.assertIn(f"openbeken>sonoff:O2:{fault}-{arg}", ids)
                self.assertNotIn(f"sonoff>openbeken:K1:{fault}-{arg}", ids)   # a check step
                if (fault, arg) != ("ctrlc", "countdown"):   # a readout counts down to no question
                    self.assertIn(f"sonoff>readout:read:{fault}-{arg}", ids)

    def test_named_as_the_espressif_suite_names_them(self):
        from tools.espressif.emulator import fault_suite as espressif
        theirs = {(sc["fault"], sc["arg"] if isinstance(sc["arg"], str) else 0) for sc in espressif.scenarios()}
        for fault, arg in fault_suite.OWN_HANDLING + fault_suite.NETWORK + [("cancel", "switch"), ("token", "login"),
                                                                           ("diskfull", "once")]:
            with self.subTest(fault=fault, arg=arg):
                self.assertIn((fault, arg if isinstance(arg, str) else 0), theirs)

    def test_switched_only_where_there_is_another_firmware_and_signed_in_again_where_ewelink_sends(self):
        ids = {sc["id"] for sc in fault_suite.scenarios()}
        self.assertIn("sonoff>esphome:J2:cancel-switch", ids)
        self.assertNotIn("openbeken>sonoff:O2:cancel-switch", ids)
        self.assertEqual({i for i in ids if i.endswith(":token-login") and ">readout" not in i},
                         {"sonoff>openbeken:B2:token-login", "sonoff>esphome:B2:token-login"})
        self.assertIn("sonoff>openbeken:B4:diskfull-once", ids)


class HandlingFaultTests(unittest.TestCase):
    """What each fault does to the computer, as Docker is asked it."""

    def inject(self, fault, arg, outputs=None, log=""):
        from unittest import mock
        asked, notes = [], {}

        def run(command, **_):
            asked.append(command)
            return SimpleNamespace(returncode=0, stdout=(outputs or {}).get(command[-1], ""), stderr="")
        instance = SimpleNamespace(network="net", computer="pc", last_log=lambda: log)
        with mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite.subprocess, "run", side_effect=run):
            fault_suite.inject(instance, fault, arg, notes, target="openbeken", length=30)
        return asked, notes

    def test_ctrl_c_and_the_window_closed_reach_the_tool_itself(self):
        # Not the recorder around it: a second Ctrl-C there would end the recorder.
        asked, notes = self.inject("cancel", "continue")
        self.assertEqual(asked, [["docker", "exec", "pc", "pkill", "-INT", "-f", fault_suite.TOOL]] * 2)
        self.assertNotIn("not_applied", notes)
        asked, _ = self.inject("hangup", "continue")
        self.assertEqual(asked, [["docker", "exec", "pc", "pkill", "-HUP", "-f", fault_suite.TOOL]])

    def test_the_second_ctrl_c_lands_in_the_countdown_the_first_asks(self):
        asked, notes = self.inject("ctrlc", "countdown", log="-- You pressed Ctrl-C\n")
        self.assertEqual([command[4] for command in asked], ["-INT", "-INT"])
        self.assertTrue(notes["countdown_pressed"])

    def test_asleep_the_computer_is_off_the_network_and_stopped_then_all_back(self):
        asked, notes = self.inject("sleep", 600, outputs={"pc": "198.18.62.2\n"})
        self.assertEqual([list(command[1:3]) for command in asked],
                         [["inspect", "-f"], ["network", "disconnect"], ["pause", "pc"], ["unpause", "pc"],
                          ["network", "connect"]])
        asked, _ = self.inject("pcoff", 30, outputs={"pc": "198.18.62.2\n"})
        self.assertNotIn(["pause", "pc"], [list(command[1:3]) for command in asked])

    def test_a_weak_network_is_lossy_both_ways_and_then_taken_off(self):
        asked, _ = self.inject("weak", 60)
        self.assertEqual([command[-1] for command in asked], [fault_suite.WEAK_ON, fault_suite.WEAK_OFF])
        self.assertTrue(all(command[:4] == ["docker", "exec", "-u", "0"] for command in asked))

    def test_packets_are_changed_until_one_has_been(self):
        shown = "filter pref 10 ... action order 2: pedit ... Sent 1200 bytes 3 pkt\n"
        asked, notes = self.inject("corrupt", "once", outputs={
            "tc -s filter show dev eth0 egress; tc -s filter show dev eth0 ingress": shown})
        self.assertEqual(notes["corrupted_packets"], 3)
        self.assertEqual(asked[-1][-1], "tc qdisc del dev eth0 clsact")

    def test_a_second_run_beside_it_must_be_refused(self):
        from unittest import mock
        refused = "Stopped: another run is converting this device (12:34:56:ab:cd:ef) right now"
        for target, said, wanted in (("openbeken", refused, True), ("openbeken", "Converted", False),
                                     ("readout", refused, True)):
            with self.subTest(target=target, said=said), mock.patch.object(
                    fault_suite, "as_you", return_value=SimpleNamespace(returncode=1, stdout=said, stderr="")) as beside:
                notes = {}
                fault_suite.second_run(SimpleNamespace(), target, notes)
                self.assertEqual(notes["second_refused"], wanted)
                self.assertIn("readout" if target == "readout" else "--firmware", beside.call_args.args)

    def test_the_disk_fills_for_the_tools_own_files_then_is_freed(self):
        asked, notes = self.inject("diskfull", "once", outputs={fault_suite.TOOL: "321\n"})
        limits = [command[-1] for command in asked if "prlimit" in command]
        self.assertEqual(limits, [f"--fsize={fault_suite.DISKFULL_LIMIT}:", "--fsize=unlimited:"])
        self.assertEqual(notes["diskfull_pid"], "321")

    def test_no_run_to_fill_the_disk_for_is_not_applied(self):
        _, notes = self.inject("diskfull", "once")
        self.assertTrue(notes["not_applied"])


class HandlingVerdictTests(unittest.TestCase):
    def verdict(self, fault, arg, notes=None, ok=True, runs=1, texts=()):
        return fault_suite.handling_verdict({"fault": fault, "arg": arg}, notes or {}, ok, runs, lambda: list(texts))

    def test_as_the_espressif_suite_judges_them(self):
        self.assertEqual(self.verdict("ctrlc", "once", runs=2), "FAIL-STOPPED")
        self.assertEqual(self.verdict("ctrlc", "apart", runs=2), "FAIL-STOPPED")
        self.assertIsNone(self.verdict("ctrlc", "apart"))
        self.assertEqual(self.verdict("ctrlc", "countdown"), "NOT-FIRED")
        self.assertEqual(self.verdict("ctrlc", "countdown", {"countdown_pressed": True}), "FAIL-NOT-STOPPED")
        self.assertIsNone(self.verdict("ctrlc", "countdown", {"countdown_pressed": True}, runs=2))
        self.assertEqual(self.verdict("hangup", "continue"), "FAIL-NOT-STOPPED")
        self.assertEqual(self.verdict("hangup", "continue", runs=2), "FAIL-NO-NOTE")
        self.assertIsNone(self.verdict("hangup", "continue", runs=2, texts=["it stopped when its window\n was closed"]))
        # A readout is never carried on, so the next says nothing of the last.
        self.assertIsNone(fault_suite.handling_verdict({"fault": "hangup", "arg": "continue",
                                                        "route": "sonoff>readout"}, {}, True, 2, list))
        self.assertEqual(self.verdict("second", "run"), "FAIL-NOT-REFUSED")
        self.assertEqual(self.verdict("second", "run", {"second_refused": True}, runs=2), "FAIL-DISTURBED")
        self.assertIsNone(self.verdict("second", "run", {"second_refused": True}))
        self.assertEqual(self.verdict("corrupt", "once"), "NO-TRAFFIC")
        self.assertIsNone(self.verdict("corrupt", "once", {"corrupted_packets": 2}))

    def test_a_backup_is_whole_when_every_file_is_as_its_manifest_says(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp, "work/captures/devices/12-34-56-ab-cd-ef/sonoff-backup")
            backup.mkdir(parents=True)
            instance = SimpleNamespace(state=Path(tmp))
            self.assertFalse(fault_suite.backup_whole(instance))   # none taken
            (backup / "config.bin").write_bytes(b"data")
            (backup / "backup.json").write_text(json.dumps({"regions": {"config.bin": {
                "sha256": hashlib.sha256(b"data").hexdigest()}}}), encoding="utf-8")
            self.assertTrue(fault_suite.backup_whole(instance))
            (backup / "config.bin").write_bytes(b"da")
            self.assertFalse(fault_suite.backup_whole(instance))


class CancelRunsTests(unittest.TestCase):
    def runs(self, arg, target="openbeken", said=("",), converts_after=1):
        from unittest import mock
        sc = {"route": f"sonoff>{target}", "step": "B2", "fault": "cancel", "arg": arg, "at": 40}
        made, routes = [], []
        done = iter([False] * converts_after + [True])
        words = iter(said)
        instance = SimpleNamespace(tool=lambda target, log, **options: made.append((target, options)),
                                   last_state=lambda: {}, run_texts=lambda: [output.CTRL_C_LOGGED], last_events=list,
                                   last_log=lambda: next(words, ""))

        def converted(state, route):
            routes.append(route)
            return next(done, True)
        with mock.patch.object(fault_suite, "wait_for_step", return_value=True), \
                mock.patch.object(fault_suite, "inject"), mock.patch.object(fault_suite, "finish"), \
                mock.patch.object(fault_suite, "recover"), mock.patch.object(fault_suite.time, "sleep"), \
                mock.patch.object(fault_suite, "happened", return_value=False), \
                mock.patch.object(fault_suite, "converted", side_effect=converted):
            outcome = fault_suite.step_scenario(instance, sc, target, Path("unused"), {})
        self.went = outcome["route"]   # what run_scenario's end checks judge the device on
        return outcome["result"], made, routes

    def test_after_ctrl_c_twice_a_person_carries_on_starts_afresh_or_switches(self):
        result, made, _ = self.runs("continue")
        self.assertEqual(result, "PASS")
        self.assertEqual(made[1], ("openbeken", {"new": False}))
        _, made, _ = self.runs("new")
        self.assertEqual(made[1], ("openbeken", {"new": True}))
        _, made, routes = self.runs("switch")
        self.assertEqual(made[1], ("esphome", {"new": False}))
        self.assertEqual(routes[-1], "sonoff>esphome")   # converted to the other firmware
        self.assertEqual(self.went, "sonoff>esphome")

    def test_a_switch_refused_part_way_is_started_again_with_new_as_the_run_says(self):
        refused = f"The device {fault_suite.SWITCH_REFUSED} ESPHome part way. Start again with --new."
        _, made, routes = self.runs("switch", said=(refused,), converts_after=2)
        self.assertEqual(made[1:], [("esphome", {"new": False}), ("esphome", {"new": True})])
        self.assertEqual(routes[-1], "sonoff>esphome")


if __name__ == "__main__":
    unittest.main()
