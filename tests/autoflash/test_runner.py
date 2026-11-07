"""Running a run's steps (tools/autoflash/runner.py): a step done is skipped
only as far as the plug agrees, one that may pass is tried again, and one
that fails says why."""
import contextlib
import errno
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import device, errors, firewall, join, output, prompts, report, routes, runner, runs
from tools.autoflash import safety_locks, unlock
from tools.autoflash.phases import PhaseError
from tools.autoflash.routes import espressif
from tools.autoflash.routes.espressif import bundle, evidence, tasmota
from tools.autoflash.state import ATTEMPTED, DONE, FAILED, RunState


class ConfirmSkipTests(unittest.TestCase):
    """A step recorded as done is skipped only as far as the plug agrees."""

    def skip(self, verifier):
        from types import SimpleNamespace
        verifiers = {} if verifier is None else {"D1": verifier}
        with mock.patch.dict(espressif.STEP_VERIFIERS, verifiers, clear=True), \
             mock.patch.object(prompts, "ask_yes_no") as asked, \
             mock.patch.object(output, "say") as said, mock.patch.object(output, "transient_begin"), \
             mock.patch.object(output, "transient_end"):
            skipped = runner.confirm_skip(SimpleNamespace(), "D1", "Install Tasmota")
        return skipped, asked, said.call_args.args[0] if said.called else ""

    def test_a_plug_that_disagrees_has_the_step_done_again_unasked(self):
        # Asked "run E3 again?", a person converting their plug could not
        # answer better than the step's own checks of the plug.
        skipped, asked, said = self.skip(lambda ctx: (False, "the plug says otherwise"))
        self.assertFalse(skipped)
        asked.assert_not_called()
        self.assertIn("the plug says otherwise: doing it again", said)

    def test_f2_is_doubted_only_on_the_recovery_firmware_where_doing_it_again_helps(self):
        # Anywhere else F2 would wait for a recovery firmware that never comes.
        from types import SimpleNamespace
        self.assertIs(tasmota.STEP_VERIFIERS["F2"], tasmota.verify_canonical)
        on = SimpleNamespace(device=SimpleNamespace(is_safeboot=lambda: True))
        off = SimpleNamespace(device=SimpleNamespace(is_safeboot=lambda: False))
        self.assertFalse(tasmota.verify_canonical(on)[0])
        self.assertEqual(tasmota.verify_canonical(off), (True, ""))

    def test_a_step_with_no_check_is_taken_at_its_record(self):
        skipped, asked, said = self.skip(None)
        self.assertTrue(skipped)
        asked.assert_not_called()
        self.assertIn("recorded as done", said)

    def test_a_step_the_plug_agrees_with_is_skipped(self):
        skipped, asked, said = self.skip(lambda ctx: (True, ""))
        self.assertTrue(skipped)
        asked.assert_not_called()
        self.assertIn("the device still agrees", said)

    def test_a_check_that_cannot_run_leaves_the_record_standing(self):
        for error in (device.DeviceError("refused"), OSError("unreachable"), ValueError("bad reply")):
            def check(ctx, error=error):
                raise error
            skipped, asked, said = self.skip(check)
            self.assertTrue(skipped, error)
            asked.assert_not_called()
            self.assertIn("could not re-check", said)


class RunStepsTests(unittest.TestCase):
    def test_a_full_disk_is_said_plainly_and_any_other_error_is_unexpected(self):
        for code in (errno.ENOSPC, errno.EDQUOT, errno.EFBIG):
            with self.subTest(code=code):
                self.assertIn("free some disk space", runner.unexpected(OSError(code, "no room")))
        self.assertIn("something unexpected", runner.unexpected(OSError(errno.EACCES, "refused")))
        self.assertIn("something unexpected", runner.unexpected(KeyError("x")))

    """The run's loop: what it skips, carries on, asks about and stops at."""

    STEPS = [("S1", "First", None), ("S2", "Second", None), ("S3", "Third", None)]

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = RunState.open(self.root)

    def run_steps(self, *, codes=None, check=None, answer=0, unattended=False, skip=True, dry_run=False,
                  **overrides):
        """run_steps over STEPS, each step's code from `codes` (0 if not
        given), S2's evidence if interrupted, and the operator's answer:
        what it returned, the steps it ran, and the stand-ins, each of which
        `overrides` can replace."""
        from types import SimpleNamespace
        codes = codes or {}
        ran = []
        stand_ins = {
            "restore_left_locks": mock.Mock(), "hold_plug": mock.Mock(), "unlock_tasmota": mock.Mock(),
            "settle_route": mock.Mock(return_value=("tasmota", "sonoff")), "apply_target": mock.Mock(),
            "bundle_of_this_version": mock.Mock(), "wait_while_plug_busy": mock.Mock(),
            "begin_stage": mock.Mock(), "confirm_skip": mock.Mock(return_value=skip),
            "execute_with_retries": mock.Mock(side_effect=lambda ctx, step: ran.append(step) or codes.get(step, 0)),
            "stop_here_after_ctrl_c": mock.Mock(return_value=False), "close_firewall_rules": mock.Mock(),
            "tell_how_to_carry_on": mock.Mock(), "invite_report": mock.Mock(),
            "plug_page_url": mock.Mock(return_value=None), "rerun_on_tasmota": mock.Mock(return_value=0),
            "ask_choice": mock.Mock(return_value=answer),
            "say": mock.Mock(), "tell": mock.Mock(), "section": mock.Mock(),
            "output": mock.Mock(waited=mock.Mock(return_value=0.0)),
        } | overrides
        owners = {"restore_left_locks": safety_locks, "hold_plug": runs, "unlock_tasmota": unlock,
                  "settle_route": routes, "apply_target": routes, "begin_stage": routes,
                  "bundle_of_this_version": bundle, "wait_while_plug_busy": join,
                  "close_firewall_rules": firewall, "invite_report": report, "plug_page_url": report,
                  "ask_choice": prompts, "section": prompts, "say": output, "tell": output,
                  "run_source": runs}
        with contextlib.ExitStack() as patches:
            for name, stand_in in stand_ins.items():
                patches.enter_context(mock.patch.object(owners.get(name, runner), name, stand_in))
            patches.enter_context(mock.patch.object(routes, "STEPS", self.STEPS))
            patches.enter_context(mock.patch.dict(evidence.ATTEMPTED_EVIDENCE,
                                                  {} if check is None else {"S2": check}, clear=True))
            patches.enter_context(mock.patch.object(prompts, "UNATTENDED", unattended))
            patches.enter_context(mock.patch.object(prompts, "answered_unattended"))
            code = runner.run_steps(SimpleNamespace(state=self.state, research_bridge=False, dry_run=dry_run))
        return code, ran, stand_ins

    def test_every_step_runs_in_turn_and_the_rules_are_closed(self):
        code, ran, stand_ins = self.run_steps()
        self.assertEqual((code, ran), (0, ["S1", "S2", "S3"]))
        stand_ins["close_firewall_rules"].assert_called_once()
        stand_ins["invite_report"].assert_called_once()
        stand_ins["bundle_of_this_version"].assert_not_called()  # a new run's bundle is the one built

    def test_a_dry_run_ends_saying_nothing_was_written_and_asks_for_no_report(self):
        code, ran, stand_ins = self.run_steps(dry_run=True)
        self.assertEqual((code, ran), (0, ["S1", "S2", "S3"]))
        stand_ins["invite_report"].assert_not_called()
        shown = " ".join(str(call.args[0]) for call in stand_ins["output"].show.call_args_list)
        self.assertIn("Dry run complete: nothing was written", shown)
        self.assertNotIn("Conversion complete", shown)

    def test_a_ctrl_c_just_before_the_first_stage_still_counts(self):
        # Seen on the emulator: the first press forgotten here, so the second
        # only asked, at the end of its stage, instead of stopping the run.
        from tools.autoflash import interrupt

        def press(*_):
            interrupt._on_interrupt(None, None)
            return "tasmota", "sonoff"
        self.addCleanup(interrupt.clear)
        with mock.patch.object(interrupt, "_say"), self.assertRaises(KeyboardInterrupt):
            self.run_steps(settle_route=mock.Mock(side_effect=press), execute_with_retries=mock.Mock(side_effect=press))

    def test_a_route_refused_stops_before_anything_is_done(self):
        code, ran, stand_ins = self.run_steps(settle_route=mock.Mock(side_effect=errors.Abort("no plug")))
        self.assertEqual((code, ran), (2, []))
        stand_ins["apply_target"].assert_not_called()

    def test_nothing_to_do_on_tasmota_checks_its_meter(self):
        code, ran, stand_ins = self.run_steps(settle_route=mock.Mock(side_effect=routes.NothingToDo("on Tasmota")),
                                              run_source=mock.Mock(return_value="tasmota"))
        self.assertEqual((code, ran), (0, []))
        stand_ins["rerun_on_tasmota"].assert_called_once()

    def test_steps_done_and_agreed_with_are_skipped(self):
        self.state.mark("S1", DONE)
        code, ran, stand_ins = self.run_steps()
        self.assertEqual((code, ran), (0, ["S2", "S3"]))
        stand_ins["confirm_skip"].assert_called_once()
        stand_ins["bundle_of_this_version"].assert_called_once()  # a resumed run's bundle is checked

    def test_a_step_done_but_doubted_runs_again(self):
        self.state.mark("S1", DONE)
        _, ran, _ = self.run_steps(skip=False)
        self.assertEqual(ran, ["S1", "S2", "S3"])

    def test_a_resumed_run_whose_bundle_is_refused_stops_there(self):
        self.state.mark("S1", DONE)
        code, ran, _ = self.run_steps(bundle_of_this_version=mock.Mock(side_effect=errors.Abort("changed")))
        self.assertEqual((code, ran), (2, []))

    def test_an_interrupted_step_the_plug_shows_finished_is_not_run_again(self):
        self.state.mark("S2", ATTEMPTED)
        code, ran, stand_ins = self.run_steps(check=lambda ctx: (True, "it answers as Tasmota"))
        self.assertEqual((code, ran), (0, ["S1", "S3"]))
        self.assertTrue(RunState.open(self.root).is_done("S2"))
        stand_ins["ask_choice"].assert_not_called()

    def test_an_interrupted_step_shown_unfinished_is_done_again_without_asking(self):
        # Seen: asked, "it did finish" for a B2 that had not left the run
        # waiting for good on an interim firmware never sent.
        self.state.mark("S2", ATTEMPTED)
        _, ran, stand_ins = self.run_steps(check=lambda ctx: (False, "it still runs Sonoff"))
        self.assertEqual(ran, ["S1", "S2", "S3"])
        stand_ins["ask_choice"].assert_not_called()

    def test_an_interrupted_step_whose_check_fails_is_done_again_without_asking(self):
        self.state.mark("S2", ATTEMPTED)

        def check(ctx):
            raise device.DeviceError("no answer")
        _, ran, stand_ins = self.run_steps(check=check)
        self.assertEqual(ran, ["S1", "S2", "S3"])
        stand_ins["ask_choice"].assert_not_called()

    def test_an_interrupted_step_with_no_check_is_done_again_without_asking(self):
        self.state.mark("S2", ATTEMPTED)
        _, ran, stand_ins = self.run_steps()
        self.assertEqual(ran, ["S1", "S2", "S3"])
        stand_ins["ask_choice"].assert_not_called()

    def test_unattended_an_interrupted_step_is_done_again_without_asking(self):
        self.state.mark("S2", ATTEMPTED)
        _, ran, stand_ins = self.run_steps(unattended=True)
        self.assertEqual(ran, ["S1", "S2", "S3"])
        stand_ins["ask_choice"].assert_not_called()

    def test_a_step_sent_back_to_runs_again_and_the_run_carries_on_from_it(self):
        sent_back = []

        def execute(ctx, step):
            ran.append(step)
            if step == "S2" and not sent_back:
                sent_back.append(step)
                ctx.go_back = "S1"   # as runner._execute does on errors.GoBack
            return 0
        ran = []
        code, _, _ = self.run_steps(execute_with_retries=mock.Mock(side_effect=execute))
        self.assertEqual((code, ran), (0, ["S1", "S2", "S1", "S2", "S3"]))

    def test_a_failing_step_stops_the_run_and_says_how_to_carry_on(self):
        code, ran, stand_ins = self.run_steps(codes={"S2": 1})
        self.assertEqual((code, ran), (1, ["S1", "S2"]))
        stand_ins["close_firewall_rules"].assert_called_once()
        stand_ins["tell_how_to_carry_on"].assert_called_once()
        stand_ins["invite_report"].assert_not_called()

    def test_quitting_leaves_the_clean_up_to_main(self):
        code, ran, stand_ins = self.run_steps(codes={"S2": 130})
        self.assertEqual((code, ran), (130, ["S1", "S2"]))
        stand_ins["close_firewall_rules"].assert_not_called()

    def test_a_ctrl_c_confirmed_after_a_stage_stops_there(self):
        code, ran, stand_ins = self.run_steps(stop_here_after_ctrl_c=mock.Mock(side_effect=lambda step: step == "S1"))
        self.assertEqual((code, ran), (130, ["S1"]))
        stand_ins["close_firewall_rules"].assert_called_once()


class RetryTests(unittest.TestCase):
    """Safe steps that failed for a reason that may pass are run again."""

    def run_with(self, step, errors):
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        ctx = SimpleNamespace(state=state, dry_run=False)
        outcomes = iter(errors)

        def fake_execute(_, name):
            error = next(outcomes)
            if error is None:
                state.mark(name, DONE)
                return 0
            state.mark(name, FAILED, error=error)
            return 1
        with mock.patch.object(runner, "execute", side_effect=fake_execute) as ran, \
             mock.patch.object(time, "sleep"), mock.patch.object(output, "tell"):
            code = runner.execute_with_retries(ctx, step)
        return code, ran.call_count

    def test_a_network_failure_is_tried_again(self):
        code, runs = self.run_with("E2", ["stage did not pass: the device says: POST /capture/staged/22 "
                                          "returned HTTP -1 after 21 attempts", None])
        self.assertEqual((code, runs), (0, 2))

    def test_a_wifi_drop_is_tried_again(self):
        # The plug now gives up after 20s, and this computer waits out the rest.
        code, runs = self.run_with("E2", ["stage did not pass: the device says: migration_refused: "
                                          "the plug's Wi-Fi went down and was not back within "
                                          "20 seconds", None])
        self.assertEqual((code, runs), (0, 2))

    def test_three_attempts_at_most(self):
        code, runs = self.run_with("E1", ["timed out"] * 5)
        self.assertEqual((code, runs), (1, 3))

    def test_a_refusal_is_not_tried_again(self):
        code, runs = self.run_with("E2", ["stage did not pass: the device says: "
                                          "migration_refused: NVS changed since preflight"])
        self.assertEqual((code, runs), (1, 1))

    def test_the_commit_is_never_tried_again(self):
        code, runs = self.run_with("E3", ["timed out", None])
        self.assertEqual((code, runs), (1, 1))

    def test_a_dry_run_records_no_step_it_only_said_it_would_do(self):
        # Carried on for real, the run must do them: nothing was written.
        from types import SimpleNamespace
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        for step, result, expected in (("E3", {"dry_run": True}, "pending"), ("E1", {"read": 1}, "done")):
            title, _ = routes.STEP_INDEX[step]
            with mock.patch.dict(routes.STEP_INDEX, {step: (title, mock.Mock(return_value=dict(result)))}), \
                    mock.patch.object(routes, "begin_stage"), mock.patch.object(output, "stage_end"), \
                    mock.patch.object(output, "say"), \
                    mock.patch.object(state, "mark", wraps=state.mark) as marked:
                self.assertEqual(runner.execute(SimpleNamespace(state=state, dry_run=True), step), 0)
            self.assertEqual(state.status(step), expected, step)
            self.assertNotIn("attempted", [call.args[1] for call in marked.call_args_list], step)

    def test_a_phase_with_no_result_keeps_the_commit_attempted(self):
        from types import SimpleNamespace
        from unittest import mock

        for step, expected in (("E3", "attempted"), ("E2", FAILED)):
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            state = RunState.open(Path(directory.name))
            title, _ = routes.STEP_INDEX[step]
            failing = mock.Mock(side_effect=PhaseError("the plug stopped the commit without reaching this computer"))
            with mock.patch.dict(routes.STEP_INDEX, {step: (title, failing)}), \
                 mock.patch.object(routes, "begin_stage"), \
                 mock.patch.object(output, "stage_end"), \
                 mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
                runner.execute(SimpleNamespace(state=state, dry_run=False), step)
            self.assertEqual(state.status(step), expected, step)


class StageTimingTests(unittest.TestCase):
    """Every stage's time is kept for the report, however it ends."""

    def test_a_stage_that_fails_has_its_time_and_the_plugs_busy_time_kept(self):
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        ctx = SimpleNamespace(state=state, dry_run=False)

        def stage(context):
            context.last_runner = SimpleNamespace(busy_seconds=41.5)
            raise PhaseError("the plug stopped the stage without reaching this computer")
        title, _ = routes.STEP_INDEX["E2"]
        with mock.patch.dict(routes.STEP_INDEX, {"E2": (title, stage)}), \
             mock.patch.object(routes, "begin_stage"), \
             mock.patch.object(output, "stage_end"), \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(runner.execute(ctx, "E2"), 1)
        captures = state.data["captures"]
        self.assertIsInstance(captures["E2.seconds"], int)
        self.assertEqual(captures["E2.plug_busy_seconds"], 41.5)
        self.assertIn("without reaching", ctx.last_failure)

        # A quicker try after it keeps the longest: how near the plug came to stopping.
        def quicker(context):
            context.last_runner = SimpleNamespace(busy_seconds=3.0)
            raise PhaseError("the plug did not fetch the stage instructions")
        with mock.patch.dict(routes.STEP_INDEX, {"E2": (title, quicker)}), \
             mock.patch.object(routes, "begin_stage"), \
             mock.patch.object(output, "stage_end"), \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            runner.execute(ctx, "E2")
        self.assertEqual(state.data["captures"]["E2.plug_busy_seconds"], 41.5)


class GoBackTests(unittest.TestCase):
    """A step that finds an earlier one did not take on the plug sends the run
    back to it, unasked, a few times at most."""

    def setUp(self):
        from types import SimpleNamespace
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = RunState.open(Path(directory.name))
        self.ctx = SimpleNamespace(state=self.state, dry_run=False, go_back=None)

    def execute(self):
        def stage(context):
            raise errors.GoBack("E1", "ESPHome was not installed: the device restarted on the firmware it had",
                                "not installed")
        title, _ = routes.STEP_INDEX["E2"]
        with mock.patch.dict(routes.STEP_INDEX, {"E2": (title, stage)}), \
             mock.patch.object(routes, "begin_stage"), mock.patch.object(output, "stage_end") as ended, \
             mock.patch.object(output, "tell") as told, mock.patch.object(output, "say"):
            return runner.execute(self.ctx, "E2"), ended, told

    def test_the_run_goes_back_to_the_step_not_taken(self):
        self.state.mark("E1", DONE)
        code, ended, told = self.execute()
        self.assertEqual((code, self.ctx.go_back), (0, "E1"))
        self.assertEqual((self.state.status("E1"), self.state.status("E2")), ("pending", "pending"))
        ended.assert_called_once_with("not installed")   # not "stopped": the run goes on
        self.assertIn("Doing E1 again", told.call_args.args[0])

    def test_one_that_still_does_not_take_stops_the_run(self):
        self.state.capture("E1.sent_again", runner.GO_BACK_LIMIT)
        code, ended, _ = self.execute()
        self.assertEqual((code, self.ctx.go_back), (1, None))
        self.assertEqual(self.state.status("E2"), "failed")
        ended.assert_called_once_with("stopped")
        self.assertIn(f"after it was sent {runner.GO_BACK_LIMIT + 1} times", self.ctx.last_failure)


class PlugBackTests(unittest.TestCase):
    """Before a step is tried again, a plug that is not answering is waited for."""

    def wait(self, answers, d1_done=True, step=None):
        from types import SimpleNamespace
        from unittest import mock
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        state.set_input("plug_ip", "10.0.0.63")
        if d1_done:
            state.mark("D1", DONE)
        device = mock.Mock(answers=mock.Mock(side_effect=list(answers)))
        clock = iter(range(0, 100_000, 3))
        with mock.patch.object(time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(time, "sleep"), \
             mock.patch.object(output, "tell") as told, \
             mock.patch.object(output, "progress"), \
             mock.patch.object(output, "progress_clear"):
            runner.wait_for_plug_back(SimpleNamespace(state=state, device=device), step)
        return device, told

    def test_an_answering_plug_is_tried_again_at_once(self):
        device, told = self.wait([True])
        told.assert_not_called()

    def test_a_plug_back_after_a_router_restart_is_tried_again_then(self):
        device, told = self.wait([False] * 30 + [True])
        self.assertEqual(device.answers.call_count, 31)
        self.assertIn("answering again", told.call_args.args[0])

    def test_a_long_wait_says_the_plug_has_no_network_of_its_own_now(self):
        said = lambda told: " ".join(call.args[0] for call in told.call_args_list)
        device, told = self.wait([False] * 30 + [True], step="E2")
        self.assertIn("no setup network of its own", said(told))
        device, told = self.wait([False] * 5 + [True], step="E2")
        self.assertNotIn("no setup network", said(told))  # back before it was worth saying
        # F4's official Tasmota may not have the Wi-Fi yet, so may have one.
        device, told = self.wait([False] * 30 + [True], step="F4")
        self.assertNotIn("no setup network", said(told))

    def test_one_that_never_answers_is_tried_anyway_after_five_minutes(self):
        device, told = self.wait([False] * 1000)
        self.assertIn("trying anyway", told.call_args.args[0])
        self.assertLess(device.answers.call_count, 1000)

    def test_before_tasmota_nothing_is_waited_for(self):
        device, _ = self.wait([], d1_done=False)
        device.answers.assert_not_called()


class UnexpectedErrorTests(unittest.TestCase):
    """An error nothing anticipated is recorded and explained, not a traceback."""

    def run_step(self, step, error):
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        ctx = SimpleNamespace(state=state, dry_run=False)
        title, _ = routes.STEP_INDEX[step]
        failing = mock.Mock(side_effect=error)
        with mock.patch.dict(routes.STEP_INDEX, {step: (title, failing)}), \
             mock.patch.object(routes, "begin_stage"), \
             mock.patch.object(output, "stage_end") as ended, \
             mock.patch.object(output, "log_only") as logged, \
             mock.patch.object(output, "tell") as told, \
             mock.patch.object(output, "say"):
            code = runner.execute(ctx, step)
        return code, state, ended, logged, told

    def test_it_stops_the_step_with_a_message_and_the_traceback_in_the_log(self):
        code, state, ended, logged, told = self.run_step("E1", KeyError("x"))
        self.assertEqual(code, 1)
        self.assertEqual(state.status("E1"), FAILED)
        ended.assert_called_once_with("stopped")
        self.assertIn("Traceback", logged.call_args.args[0])
        self.assertIn("something unexpected went wrong: KeyError", told.call_args.args[0])

    def test_the_look_for_a_plug_that_lost_its_settings_stops_the_step_not_the_run(self):
        # Seen in the fault suite: not joined to the plug's network in time,
        # its Abort crashed the run, put as "something unexpected went wrong".
        from unittest import mock
        gave_up = errors.Abort("this computer did not join tasmota-ABCDEF-1234 within 15 minutes")
        with mock.patch.object(runner, "set_up_if_erased", side_effect=gave_up):
            code, state, ended, _, told = self.run_step("D4", gave_up)
        self.assertEqual(code, 1)
        ended.assert_called_once_with("stopped")
        self.assertEqual(told.call_args.args[0], f"FAILED: {gave_up}")

    def test_a_request_the_network_dropped_is_no_surprise_and_is_tried_again(self):
        # Seen in the fault suite: an outage reset H1's read of a region part
        # way, and the run said "something unexpected went wrong" - then
        # converted the plug on the next try.
        import http.client
        for error in (ConnectionResetError(104, "Connection reset by peer"), http.client.IncompleteRead(b""),
                      http.client.RemoteDisconnected("Remote end closed connection without response"),
                      TimeoutError("timed out")):
            code, state, ended, logged, told = self.run_step("H1", error)
            self.assertEqual(code, 1)
            self.assertEqual(state.status("H1"), FAILED)
            self.assertIn("Traceback", logged.call_args.args[0])
            message = told.call_args.args[0]
            self.assertIn("the connection to the device was lost", message)
            self.assertNotIn("unexpected", message)
            self.assertRegex(message, errors.TRANSIENT_RE)

    def test_a_step_that_writes_stays_attempted(self):
        # Whether the commit got as far as writing is for the next run to find
        # out from the plug - so it must not be recorded as failed.
        code, state, *_ = self.run_step("E3", RuntimeError("boom"))
        self.assertEqual(code, 1)
        self.assertEqual(state.status("E3"), "attempted")

    def test_ctrl_c_is_not_swallowed(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_step("E1", KeyboardInterrupt())


class ErasedTasmotaTests(unittest.TestCase):
    """A Tasmota that lost its settings part way is set up through its own
    network again - and set up again (F4), when it had been already."""

    def run_it(self, step="G1", answers=False, network="tasmota-ABCDEF-1234", f4_done=True, in_place=False):
        from types import SimpleNamespace
        from unittest import mock
        done = {"D1", "F4"} if f4_done else {"D1"}
        inputs = {"plug_mac": "12:34:56:ab:cd:ef", "plug_ip": "192.0.2.51", "product_model": "S60TPG"}
        # Answers in turn, the last for every ask after; a network in turn likewise.
        replies = list(answers) if isinstance(answers, list) else [answers]
        device = SimpleNamespace(answers=lambda timeout=None: replies.pop(0) if len(replies) > 1 else replies[0],
                                 wait_reachable=lambda timeout=None: None)
        ctx = SimpleNamespace(device=device, state=SimpleNamespace(
            input=inputs.get, is_done=lambda s: s in done, capture=lambda *a: None))
        clock = iter(range(0, 10_000, 5))
        with mock.patch.object(prompts, "find_ssid", side_effect=network if isinstance(network, list)
                               else lambda *a, **k: network) as looked, \
             mock.patch.object(join, "configure_plug_wifi") as by_hand, \
             mock.patch.object(tasmota, "template_in_place", return_value=in_place), \
             mock.patch.object(tasmota, "step_configure") as configured, \
             mock.patch.object(time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(time, "sleep"), \
             mock.patch.object(output, "tell"), mock.patch.object(output, "progress"), \
             mock.patch.object(output, "progress_clear"):
            result = runner.set_up_if_erased(ctx, step)
        return result, looked, by_hand, configured

    def test_one_just_starting_up_is_left_alone(self):
        # Seen in the fault suite: D1 had just restarted the plug onto the
        # Wi-Fi, and the network it showed for a moment as it started
        # (ESP_ and its access-point MAC) was taken for its setup network.
        result, looked, by_hand, _ = self.run_it(answers=[False, False, False, True], network="ESP_ABCDF0")
        self.assertFalse(result)
        by_hand.assert_not_called()

    def test_one_whose_network_went_as_it_started_is_left_alone(self):
        result, looked, by_hand, _ = self.run_it(network=["ESP_ABCDF0", None])
        self.assertFalse(result)
        self.assertEqual(looked.call_count, 2)
        by_hand.assert_not_called()

    def test_it_is_set_up_through_the_network_it_settled_on(self):
        _, _, by_hand, _ = self.run_it(network=["ESP_ABCDF0", "tasmota-ABCDEF-1234"])
        self.assertEqual(by_hand.call_args.args[1], "tasmota-ABCDEF-1234")

    def test_one_answering_is_left_alone(self):
        result, looked, by_hand, _ = self.run_it(answers=True)
        self.assertFalse(result)
        looked.assert_not_called()

    def test_on_its_own_network_it_is_set_up_through_it_and_f4_again(self):
        # Seen in the fault suite: switched off and on 7 times during G1, the
        # run asked for Wi-Fi details for a plug it could not reach, run after run.
        result, _, by_hand, configured = self.run_it()
        self.assertTrue(result)
        by_hand.assert_called_once()
        configured.assert_called_once()

    def test_its_template_still_there_needs_no_f4(self):
        _, _, by_hand, configured = self.run_it(in_place=True)
        by_hand.assert_called_once()
        configured.assert_not_called()

    def test_before_f4_only_the_wifi(self):
        _, _, by_hand, configured = self.run_it(step="D4", f4_done=False)
        by_hand.assert_called_once()
        configured.assert_not_called()

    def test_no_network_of_its_own_is_left_to_the_step(self):
        result, _, by_hand, _ = self.run_it(network=None)
        self.assertFalse(result)
        by_hand.assert_not_called()

    def test_on_a_route_with_no_d1_as_well(self):
        # From ESPHome, Tasmota comes by the mover and the startup firmware:
        # seen in the fault suite, G1 there was never helped.
        from types import SimpleNamespace
        from unittest import mock
        inputs = {"plug_mac": "12:34:56:ab:cd:ef", "plug_ip": "192.0.2.51", "product_model": "S60TPG"}
        ctx = SimpleNamespace(device=SimpleNamespace(answers=lambda timeout=None: False,
                                                     wait_reachable=lambda timeout=None: None),
                              state=SimpleNamespace(input=inputs.get, is_done=lambda s: s == "F4",
                                                    capture=lambda *a: None))
        with mock.patch.object(prompts, "find_ssid", return_value="tasmota-ABCDEF-1234"), \
             mock.patch.object(join, "configure_plug_wifi") as by_hand, \
             mock.patch.object(tasmota, "template_in_place", return_value=True), \
             mock.patch.object(runner, "ERASED_SETTLE", 0.0), \
             mock.patch.object(output, "tell"), mock.patch.object(output, "progress_clear"):
            self.assertTrue(runner.set_up_if_erased(ctx, "G1"))
        by_hand.assert_called_once()

    def test_not_at_a_step_of_a_tasmota_with_the_wifi(self):
        for step in ("F2", "F4", "L1", "X1"):
            result, looked, _, _ = self.run_it(step=step)
            self.assertFalse(result, step)
            looked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
