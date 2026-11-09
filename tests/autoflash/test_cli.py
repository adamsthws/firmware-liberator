"""The command line's own handling, before a run starts."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import cli, credentials, hostsys, interrupt, output, prompts, routes, runs
from tools.autoflash.routes.beken import images as beken_images
from tools.autoflash.routes.espressif import installer
from tools.autoflash.state import DONE, RunState


class BeforeTheRunTests(unittest.TestCase):
    def main(self, *, choosing, more=()):
        """cli.main() for `run`, the question of which run answered by `choosing`.
        What main() sets for the whole process is put back afterwards."""
        screen = io.StringIO()
        with mock.patch("sys.argv", ["cli.py", "run", *more]), mock.patch("sys.stdout", screen), \
                mock.patch.object(hostsys, "password_asker"), mock.patch.object(prompts, "UNATTENDED"), \
                mock.patch.object(installer, "SIMULATED_BACKUP_FAILURES"), \
                mock.patch.object(beken_images, "SPOIL_STEP"), mock.patch.object(beken_images, "SPOIL_TIMES"), \
                mock.patch.object(beken_images, "UNKNOWN_TABLE"), \
                mock.patch.object(cli.signal, "signal"), mock.patch.object(interrupt, "install"), \
                mock.patch.object(credentials, "load", return_value={}), \
                mock.patch.object(cli, "adopt_older_runs"), mock.patch.object(output, "start_at_top"), \
                mock.patch.object(prompts, "wait_for_taller_window"), \
                mock.patch.object(cli, "resolve_run_name", side_effect=choosing):
            return cli.main(), screen.getvalue()

    def test_ctrl_c_at_the_first_question_stops_without_a_traceback(self):
        # Seen: Ctrl-C at "A conversion did not finish last time. What now"
        # ended in a KeyboardInterrupt traceback.
        code, screen = self.main(choosing=KeyboardInterrupt)
        self.assertEqual(code, 130)
        self.assertIn("Stopped. Nothing was changed.", screen)

    def test_a_test_can_ask_for_a_beken_table_not_known(self):
        seen = []

        def choosing(*_args, **_kwargs):
            seen.append(beken_images.UNKNOWN_TABLE)
            raise KeyboardInterrupt
        self.main(choosing=choosing)
        self.main(choosing=choosing, more=["--simulate-unknown-table"])
        self.assertEqual(seen, [False, True])

    def test_a_test_can_ask_for_an_image_spoilt_a_number_of_times(self):
        seen = []

        def choosing(*_args, **_kwargs):
            seen.append((beken_images.SPOIL_STEP, beken_images.SPOIL_TIMES))
            raise KeyboardInterrupt
        self.main(choosing=choosing, more=["--simulate-bad-image", "O1:3"])
        self.main(choosing=choosing, more=["--simulate-bad-image", "J2"])
        self.main(choosing=choosing)
        self.assertEqual(seen, [("O1", 3), ("J2", 1), (None, 1)])

    def test_q_there_stops_the_same_way(self):
        code, screen = self.main(choosing=prompts.QuitRun("stopped at an operator prompt"))
        self.assertEqual(code, 130)
        self.assertIn("Stopped. Nothing was changed.", screen)


class AnotherDeviceTests(unittest.TestCase):
    """A conversion or a readout that finished asks what is next - another
    device converted, or one read out - and goes on to it in the same
    sitting: a new run, found afresh, the eWeLink sign-in kept.  Not after
    one that stopped, nor in an unattended one."""

    def main(self, *, codes, answers, command="run", more=()):
        from types import SimpleNamespace
        made, names = [], iter(f"run-{number}" for number in range(2, 9))

        def context(args):
            ctx = SimpleNamespace(args=args, run=args.run, plug_ip=args.plug_ip, dir=Path("unused"),
                                  state=mock.Mock(input=mock.Mock(return_value=None)), keep_sign_in=mock.Mock())
            made.append(ctx)
            return ctx
        codes, commands = iter(codes), []

        def ran(ctx, *_):
            commands.append(ctx.args.command)
            return next(codes)
        top, asked = mock.Mock(), mock.Mock(side_effect=list(answers))
        with contextlib.ExitStack() as patches:
            for patch in (
                    mock.patch("sys.argv", ["cli.py", command, "--plug-ip", "192.0.2.50", *more]),
                    mock.patch("sys.stdout", io.StringIO()),
                    mock.patch.object(hostsys, "password_asker"), mock.patch.object(prompts, "UNATTENDED"),
                    mock.patch.object(installer, "SIMULATED_BACKUP_FAILURES"),
                    mock.patch.object(beken_images, "SPOIL_STEP"), mock.patch.object(beken_images, "SPOIL_TIMES"),
                    mock.patch.object(beken_images, "UNKNOWN_TABLE"),
                    mock.patch.object(cli.signal, "signal"), mock.patch.object(interrupt, "install"),
                    mock.patch.object(credentials, "load", return_value={}),
                    mock.patch.object(cli, "adopt_older_runs"), mock.patch.object(output, "start_at_top", top),
                    mock.patch.object(prompts, "wait_for_taller_window"),
                    mock.patch.object(cli, "resolve_run_name", return_value="run-1"),
                    mock.patch.object(cli, "start_new_run", side_effect=lambda _: next(names)),
                    mock.patch.object(cli, "Context", side_effect=context),
                    mock.patch.object(routes, "apply_target"),
                    mock.patch.object(cli, "refused_for_another_run", return_value=False),
                    mock.patch.object(cli, "convert", ran), mock.patch.object(cli, "read_out", ran),
                    mock.patch.object(prompts, "ask_choice", asked)):
                patches.enter_context(patch)
            code = cli.main()
        self.commands = commands
        return code, made, asked, top

    CONVERT, READ_OUT = 0, 1   # the options' indexes

    def test_after_one_another_is_offered_until_q(self):
        code, made, asked, top = self.main(codes=[0, 0, 0], answers=[self.CONVERT, self.CONVERT,
                                                                     prompts.QuitRun("q")])
        self.assertEqual(code, 0)
        self.assertEqual([ctx.run for ctx in made], ["run-1", "run-2", "run-3"])
        # The address was the first device's: the next is found afresh.
        self.assertEqual([ctx.plug_ip for ctx in made], ["192.0.2.50", None, None])
        made[1].keep_sign_in.assert_called_once_with(made[0])
        made[2].keep_sign_in.assert_called_once_with(made[1])
        self.assertEqual(asked.call_args.args, ("What next", ["Convert another device", "Read out a device"]))
        self.assertEqual(top.call_count, 1 + 2)   # each conversion at the top of the window

    def test_a_conversion_and_a_readout_lead_to_each_other(self):
        code, made, asked, _ = self.main(codes=[0, 0, 0], answers=[self.READ_OUT, self.CONVERT, KeyboardInterrupt()])
        self.assertEqual((code, self.commands), (0, ["run", "readout", "run"]))
        self.assertEqual(asked.call_args_list[1].args[1], ["Convert a device", "Read out another device"])
        made[1].keep_sign_in.assert_called_once_with(made[0])

    def test_not_after_one_that_stopped(self):
        for command in ("run", "readout"):
            for code in (1, 2, 130):
                with self.subTest(command=command, code=code):
                    ended, made, asked, _ = self.main(codes=[code], answers=[], command=command)
                    self.assertEqual((ended, len(made)), (code, 1))
                    asked.assert_not_called()

    def test_not_asked_unattended_nor_after_a_readout_sent_again(self):
        for command, more in (("run", ["--unattended"]), ("readout", ["--send", "readout.tar"])):
            with self.subTest(command=command):
                code, made, asked, _ = self.main(codes=[0], answers=[], command=command, more=more)
                self.assertEqual((code, len(made)), (0, 1))
                asked.assert_not_called()


class StoppedReportTests(unittest.TestCase):
    """A run that did not finish asks for its report - but not one stopped
    before any stage began, which says nothing of how the tool does."""

    def ask(self, code, ending, begun, reported=False):
        from types import SimpleNamespace
        return cli.stopped_report(SimpleNamespace(reported=reported, stage_begun=begun), code, ending)

    def test_a_run_that_failed_in_a_stage_asks(self):
        self.assertEqual(self.ask(1, None, begun=True), "failed")

    def test_one_stopped_before_any_stage_does_not(self):
        # Seen: a wrong --plug-ip, stopped at once, still asked for its report.
        self.assertIsNone(self.ask(2, None, begun=False))

    def test_a_crash_asks_however_early(self):
        self.assertEqual(self.ask(1, "crashed", begun=False), "crashed")

    def test_nor_twice(self):
        self.assertIsNone(self.ask(1, None, begun=True, reported=True))


class PipeTests(unittest.TestCase):
    """Each in a process of its own: what it does to signals stays there."""

    @staticmethod
    def child(code, run=None, **kwargs):
        import subprocess
        from tools.autoflash.runs import ROOT
        return (run or subprocess.run)([sys.executable, "-c", "from tools.autoflash import cli\n" + code],
                                       cwd=ROOT, **kwargs)

    def test_a_device_hanging_up_on_a_write_raises_rather_than_ending_the_run(self):
        # A plug restarting under an update closes the connection a check of it
        # is writing to; ended there, the run cleans up nothing.
        done = self.child(
            "import socket\n"
            "cli.handle_signals()\n"
            "ours, plugs = socket.socketpair()\n"
            "plugs.close()\n"
            "try:\n"
            "    for _ in range(4):\n"
            "        ours.sendall(bytes(65536))\n"
            "except BrokenPipeError:\n"
            "    raise SystemExit(7)\n", capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 7, done.stderr)

    def test_piped_into_a_reader_that_stops_it_ends_quietly(self):
        # As head does: a row read, then the pipe closed.
        import subprocess
        child = self.child("import sys\n"
                           "cli._main = lambda: [print('a row of output') for _ in range(100_000)] and 0\n"
                           "sys.exit(cli.main())\n", run=subprocess.Popen,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child.stdout.readline()
        child.stdout.close()
        with child.stderr:
            said = child.stderr.read()
        self.assertEqual((child.wait(timeout=60), said), (1, b""))


class RunToLookAtTests(unittest.TestCase):
    """`status` and `cleanup` report on a run without starting one."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.current = None
        for patch in (
            mock.patch.object(runs, "run_directories",
                              lambda: sorted(p for p in self.root.iterdir() if p.is_dir())),
            mock.patch.object(runs, "read_current", lambda: self.current),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def run_at(self, name, plug_ip):
        RunState.open(self.root / name).set_input("plug_ip", plug_ip)
        return runs.run_name(self.root / name)

    def test_given_an_address_it_is_that_devices_latest_run(self):
        # Seen: status --plug-ip told of the current run, another device's.
        self.run_at("1-first", "10.0.0.51")
        latest = self.run_at("2-again", "10.0.0.51")
        self.current = self.run_at("3-other", "10.0.0.60")
        self.assertEqual(cli.run_to_look_at("10.0.0.51"), latest)

    def test_an_address_with_no_run_has_none(self):
        self.current = self.run_at("1-other", "10.0.0.60")
        self.assertIsNone(cli.run_to_look_at("10.0.0.51"))

    def test_without_an_address_it_is_the_current_run_else_the_latest(self):
        first = self.run_at("1-first", "10.0.0.51")
        latest = self.run_at("2-other", "10.0.0.60")
        self.assertEqual(cli.run_to_look_at(None), latest)
        self.current = first
        self.assertEqual(cli.run_to_look_at(None), first)


class RunChoiceTests(unittest.TestCase):
    """An unfinished conversion is offered back, never resumed or dropped silently."""

    def setUp(self):
        from unittest import mock
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.remembered = []
        for patch in (
            mock.patch.object(runs, "run_directories",
                              lambda: sorted(p for p in self.root.iterdir() if p.is_dir())),
            mock.patch.object(runs, "read_current", lambda: None),
            mock.patch.object(runs, "remember_current", self.remembered.append),
            mock.patch.object(output, "say"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def run_dir(self, name, done):
        state = RunState.open(self.root / name)
        state.set_input("plug_ip", "10.0.0.54")
        for step in done:
            state.mark(step, DONE)
        return name

    def choose(self, answer):
        from unittest import mock
        with mock.patch.object(prompts, "ask_choice", return_value=answer) as menu:
            name = cli.resolve_run_name(None, False, None, ask=True)
        return name, menu

    def test_with_nothing_unfinished_a_new_run_starts_without_asking(self):
        self.run_dir("done", [step for step, _, _ in routes.STEPS])
        name, menu = self.choose(0)
        menu.assert_not_called()
        self.assertNotEqual(name, "done")

    def test_continuing_picks_up_the_unfinished_run(self):
        self.run_dir("half", ["A0", "A5", "A1", "A2", "C2"])
        name, menu = self.choose(0)
        self.assertEqual(name, "half")
        options = menu.call_args.args[1]
        self.assertIn("carries on at stage 7, Backing up Sonoff firmware", options[0])
        self.assertIn("past the point of no return", options[0])

    def test_starting_a_new_plug_leaves_the_unfinished_run_alone(self):
        self.run_dir("half", ["A0", "A5"])
        name, menu = self.choose(1)
        self.assertNotEqual(name, "half")
        self.assertNotIn("past the point of no return", menu.call_args.args[1][0])
        self.assertTrue((self.root / "half").is_dir())

    def test_the_answer_reaches_the_new_runs_log(self):
        # The menu is gone from the screen once answered and comes before the
        # log opens: a part-converted plug left behind must still show in it.
        from unittest import mock
        self.run_dir("past", ["A0", "A5", "A1", "A2", "C2"])
        cli.RUN_CHOICE.clear()
        self.choose(1)
        with mock.patch.object(output, "say") as say:
            cli.log_run_choice()
        logged = "\n".join(call.args[0] for call in say.call_args_list)
        self.assertIn("a new conversion", logged)
        self.assertIn("carries on at stage 7, Backing up Sonoff firmware", logged)
        self.assertIn("past the point of no return", logged)
        self.assertEqual(cli.RUN_CHOICE, [])

    def plug_run(self, name, mac, done, **inputs):
        self.run_dir(name, done)
        state = RunState.open(self.root / name)
        for key, value in {"plug_mac": mac, **inputs}.items():
            state.set_input(key, value)

    def test_a_run_a_later_run_of_the_same_plug_wrote_over_is_not_offered(self):
        # Seen: a run stopped as it left Tasmota, carried on after a later run
        # had put ESPHome on - asking for the Wi-Fi to leave a Tasmota long gone.
        self.plug_run("1-stale", "12:34:56:ab:cd:ef", ["A0", "A5"])
        self.plug_run("2-later", "12:34:56:ab:cd:ef", ["A0", "A5", "L1"], source="tasmota", firmware="esphome")
        self.plug_run("3-other", "12:34:56:ab:cd:01", ["A0", "A5"])
        _, menu = self.choose(0)
        self.assertEqual(len(menu.call_args.args[1]), 4)   # 2-later, 3-other, a new one, remove one
        self.assertNotIn("1-stale", " ".join(menu.call_args.args[1]))

    def test_a_later_run_that_wrote_nothing_leaves_it_offered(self):
        self.plug_run("1-stale", "12:34:56:ab:cd:ef", ["A0", "A5"])
        self.plug_run("2-later", "12:34:56:ab:cd:ef", ["A0"])
        _, menu = self.choose(0)
        self.assertEqual(len(menu.call_args.args[1]), 4)

    def test_not_asked_for_one_device_only_its_run_is_carried_on(self):
        from unittest import mock
        self.run_dir("half", ["A0"])
        with mock.patch.object(prompts, "ask_choice") as menu:
            self.assertNotEqual(cli.resolve_run_name(None, False, "10.0.0.99"), "half")
            self.assertEqual(cli.resolve_run_name(None, False, "10.0.0.54"), "half")
        menu.assert_not_called()

    def test_other_commands_continue_without_asking(self):
        from unittest import mock
        self.run_dir("half", ["A0"])
        with mock.patch.object(prompts, "ask_choice") as menu:
            self.assertEqual(cli.resolve_run_name(None, False, None), "half")
        menu.assert_not_called()


class StageNumberTests(unittest.TestCase):
    """Where an unfinished run carries on, as the run-again menu says it
    (describe_run, from the run's record), is the stage its line showed as
    it ran (routes.begin_stage) - on every route, at every stage, chosen or
    not.  Seen: "Stopped at stage 10" for a run that carried on at 9."""

    KIND = {"espressif": "sonoff-s60", "beken": "sonoff-s61"}

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.addCleanup(routes.apply_target, "tasmota", "sonoff")

    def carries_on_at(self, name, inputs, done):
        state = RunState.open(self.root / name)
        for key, value in {"plug_ip": "192.0.2.50", **inputs}.items():
            state.set_input(key, value)
        for step in done:
            state.mark(step, DONE)
        said = cli.describe_run(self.root / name)
        return said.split("carries on at ", 1)[1].split(")", 1)[0].split(", past", 1)[0]

    def as_shown(self, firmware, source, family, step):
        """The stage line `step` begins with, as the run draws it."""
        begun = []
        routes.apply_target(firmware, source, family)
        with mock.patch.object(output, "stage_begin", side_effect=lambda *line: begun.append(line)), \
                mock.patch.object(output, "set_stage_width"):
            routes.begin_stage(step)
        return "stage {}, {}".format(*begun[0])

    def test_every_stage_of_every_route(self):
        from tools.autoflash.routes import espressif
        checked = 0
        for name, family in routes.FAMILIES.items():
            for source in family.SOURCES:
                for firmware in family.FIRMWARES:
                    try:
                        routes.steps_for(firmware, source, family)
                    except ValueError:
                        continue  # no such route
                    ways = [(source, {})]
                    if (source, firmware) == ("tasmota", "esphome"):
                        ways = [(source, {routes.LEAVES_DIRECT: False}),
                                (espressif.DIRECT_FROM_TASMOTA, {routes.LEAVES_DIRECT: True})]
                    for route_source, extra in ways:
                        steps = [step for step, _, _ in routes.steps_for(firmware, route_source, family)]
                        for index, step in enumerate(steps):
                            inputs = {"plug_kind": self.KIND[name], "source": source, "firmware": firmware, **extra}
                            with self.subTest(route=f"{name} {route_source}>{firmware}", step=step):
                                self.assertEqual(
                                    self.carries_on_at(f"{name}-{route_source}-{firmware}-{step}", inputs,
                                                       steps[:index]),
                                    self.as_shown(firmware, route_source, family, step))
                                checked += 1
        self.assertGreater(checked, 100)

    def test_a_run_stopped_before_its_firmware_was_chosen(self):
        # From Sonoff's firmware, found in eWeLink; from a converted plug, the
        # prerequisites read: both carry on at "Choosing the firmware".
        for name, family in routes.FAMILIES.items():
            for source, done in (("sonoff", ["A0", "A1"]), ("esphome", ["A0"])):
                with self.subTest(family=name, source=source):
                    said = self.carries_on_at(f"{name}-{source}", {"plug_kind": self.KIND[name], "source": source},
                                              done)
                    self.assertEqual(said, self.as_shown(None, source, family, "A3"))
                    self.assertTrue(said.endswith("Choosing the firmware"), said)


class RemoveRunTests(RunChoiceTests):
    """Unfinished runs can be removed from the menu, with care for half-converted plugs."""

    def menu(self, answers, confirm=True):
        from unittest import mock
        with mock.patch.object(prompts, "ask_choice", side_effect=answers) as menu, \
             mock.patch.object(prompts, "ask_yes_no", return_value=confirm), \
             mock.patch.object(prompts, "section") as section:
            name = cli.resolve_run_name(None, False, None, ask=True)
        return name, menu, section

    def test_removing_the_only_unfinished_run_then_starts_a_new_one(self):
        self.run_dir("half", ["A0", "A5"])
        name, _, section = self.menu([2])          # 1 continue, 2 new, 3 remove
        self.assertFalse((self.root / "half").exists())
        self.assertNotEqual(name, "half")
        section.assert_not_called()                # not past the point of no return

    def test_a_half_converted_plug_is_warned_about_before_removal(self):
        self.run_dir("past", ["A0", "A5", "A1", "A2", "C2"])
        _, _, section = self.menu([2])
        self.assertIn("part-converted", section.call_args.args[0])

    def test_declining_keeps_the_run_and_shows_the_menu_again(self):
        self.run_dir("half", ["A0"])
        name, menu, _ = self.menu([2, 0], confirm=False)
        self.assertTrue((self.root / "half").exists())
        self.assertEqual(name, "half")
        self.assertEqual(menu.call_count, 2)

    def test_with_several_runs_it_asks_which_to_remove(self):
        self.run_dir("first", ["A0"])
        self.run_dir("second", ["A0", "A5"])
        # 1-2 continue, 3 new, 4 remove; then pick the second; then start new
        name, menu, _ = self.menu([3, 1, 1])
        remaining = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(len(remaining), 1)
        self.assertEqual(menu.call_args_list[1].args[0], "Which run should be removed")
        self.assertNotIn(name, ("first", "second"))

    def remove(self, closes, current=False):
        """remove_run on a run that left two firewall rules, `closes` saying
        whether each could be removed: whether it was, what was told."""
        from unittest import mock
        rules = [hostsys.FirewallRule("ufw", "10.0.0.54", [8088]),
                 hostsys.FirewallRule("ufw", "10.0.0.54", [8089])]
        state = RunState.open(self.root / "half")
        state.set_input("firewall_rules", [rule.to_json() for rule in rules])
        pointer = self.root / ".autoflash-current"
        pointer.write_text("half\n", encoding="utf-8")
        with mock.patch.object(prompts, "ask_yes_no", return_value=True), \
             mock.patch.object(runs, "read_current", return_value="half" if current else "other"), \
             mock.patch.object(runs, "run_name", return_value="half"), \
             mock.patch.object(runs, "CURRENT_POINTER", pointer), \
             mock.patch.object(hostsys, "close_firewall", side_effect=closes) as close, \
             mock.patch.object(output, "tell") as told:
            self.assertTrue(cli.remove_run(self.root / "half"))
        self.assertFalse((self.root / "half").exists())
        self.assertEqual(close.call_args_list, [mock.call(rule) for rule in rules])
        return "\n".join(call.args[0] for call in told.call_args_list), pointer.exists()

    def test_the_firewall_rules_a_run_left_are_removed_with_it(self):
        told, _ = self.remove([True, True])
        self.assertEqual(told, "")

    def test_a_rule_that_cannot_be_removed_is_left_to_remove_by_hand(self):
        told, _ = self.remove([True, False])
        self.assertIn("Remove it yourself:", told)
        self.assertIn("8089", told)
        self.assertNotIn("8088", told)

    def test_removing_the_current_run_forgets_it(self):
        self.assertFalse(self.remove([True, True], current=True)[1])
        self.assertTrue(self.remove([True, True], current=False)[1])


class SudoPasswordTests(unittest.TestCase):
    def setUp(self):
        import tools.autoflash.cli as autoflash
        self.autoflash = autoflash
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("AUTOFLASH_SUDO_PASSWORD", "AUTOFLASH_EMULATOR_SUDO_PASSWORD"):
            os.environ.pop(key, None)

    def test_a_given_one_sudo_accepts_is_used(self):
        os.environ["AUTOFLASH_SUDO_PASSWORD"] = "right"
        with mock.patch.object(hostsys, "check_sudo_password", return_value=None), \
                mock.patch.object(prompts, "ask_admin_password") as asked:
            self.assertEqual(cli.admin_password(), "right")
        asked.assert_not_called()

    def test_unattended_without_one_says_what_to_give(self):
        with mock.patch.object(prompts, "UNATTENDED", True), \
                self.assertRaisesRegex(OSError, "AUTOFLASH_SUDO_PASSWORD"):
            cli.admin_password()


if __name__ == "__main__":
    unittest.main()
