"""The command line's own handling, before a run starts."""
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
        self.assertIn("stopped at stage 7, Backing up Sonoff firmware", options[0])
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
        self.assertIn("stopped at stage 7, Backing up Sonoff firmware", logged)
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
