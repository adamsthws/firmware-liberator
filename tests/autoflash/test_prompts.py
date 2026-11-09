"""What the tool asks (tools/autoflash/prompts.py): a secret shown only as it
is typed, keys typed ahead thrown away, a way to quit at every question, the
answers of an unattended run, and the plug's setup network looked for."""
import io
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.autoflash.test_names import SetupNetworkTestCase
from tools.autoflash import names, output, prompts


class TypedAheadTests(unittest.TestCase):
    """A key pressed while nothing was asking - during the look round the
    network, with nothing on screen - is not the next question's answer (seen
    in the emulator: an Enter taken as an empty answer to "Which plug")."""

    def test_dropped_on_a_terminal(self):
        with mock.patch.object(prompts.sys.stdin, "isatty", return_value=True), \
             mock.patch.object(prompts.sys.stdin, "fileno", return_value=0), \
             mock.patch.object(prompts.hostsys, "IS_WINDOWS", False), \
             mock.patch("termios.tcflush") as flush:
            prompts.discard_typed_ahead()
        import termios
        flush.assert_called_once_with(0, termios.TCIFLUSH)

    def test_piped_answers_are_left_alone(self):
        with mock.patch.object(prompts.sys.stdin, "isatty", return_value=False), \
             mock.patch("termios.tcflush") as flush:
            prompts.discard_typed_ahead()
        flush.assert_not_called()

    def test_every_question_drops_it_first(self):
        order = []
        with mock.patch.object(prompts, "discard_typed_ahead", side_effect=lambda: order.append("dropped")), \
             mock.patch("builtins.input", side_effect=lambda _: order.append("asked") or "2"), \
             mock.patch.object(prompts, "UNATTENDED", False), mock.patch.object(prompts, "_emit"):
            self.assertEqual(prompts.ask_choice("Which plug is this run for", ["a", "b"]), 1)
        self.assertEqual(order, ["dropped", "asked"])


class SecretPromptTests(unittest.TestCase):
    """Only the character just typed is ever readable, and never after Enter."""

    def test_only_the_last_character_is_shown(self):
        from tools.autoflash.prompts import SecretLine
        line = SecretLine()
        for char in "pass":
            self.assertTrue(line.feed(char))
        self.assertEqual(line.display(reveal_last=True), "***s")
        self.assertEqual(line.display(reveal_last=False), "****")
        self.assertEqual(line.value, "pass")

    def test_backspace_and_ctrl_u_edit_the_value(self):
        from tools.autoflash.prompts import SecretLine
        line = SecretLine()
        for char in "pasz\x7fs":
            line.feed(char)
        self.assertEqual(line.value, "pass")
        line.feed("\x15")
        self.assertEqual((line.value, line.display(True)), ("", ""))

    def test_control_characters_are_not_stored(self):
        from tools.autoflash.prompts import SecretLine
        line = SecretLine()
        for char in "a\tb\x01c":
            line.feed(char)
        self.assertEqual(line.value, "abc")

    @unittest.skipIf(sys.platform == "win32", "needs a POSIX pseudo-terminal")
    def test_a_real_terminal_never_keeps_a_readable_character(self):
        import pty
        import subprocess
        import sys

        master, slave = pty.openpty()
        code = (
            "from tools.autoflash.prompts import ask_secret\n"
            "value = ask_secret('Password: ')\n"
            "print('LEN', len(value), value == 'Secret-9x')\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code], stdin=slave, stdout=slave, stderr=slave,
            cwd=Path(__file__).resolve().parents[2],
        )
        os.close(slave)
        output = b""

        def drain(seconds, until=None):
            nonlocal output
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not (until and until in output):
                import select
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        output += os.read(master, 4096)
                    except OSError:
                        return

        # Typed only once the prompt is up: a busy machine can take more than
        # a second to start the child, and keys typed before then are lost.
        drain(15.0, until=b"> ")
        for char in "Secret-9x":
            os.write(master, char.encode())
            drain(0.05)
        os.write(master, b"\r")
        drain(1.0)
        process.wait(timeout=5)
        os.close(master)

        text = output.decode(errors="replace")
        self.assertIn("LEN 9 True", text)
        # The prompt, with its quit hint, is too long to share the input row in
        # an 80-column terminal, so it sits on its own line above "> ".
        self.assertIn("Password (q to quit):", re.sub(r"\x1b\[[0-9;]*m", "", text))   # in grey
        # What is left on the input row after its final redraw: asterisks only.
        input_rows = [row for row in text.split("\n") if "> " in row]
        final = input_rows[-1].rstrip("\r").split("\r")[-1].replace("\x1b[K", "")
        self.assertEqual(final.strip(), "> *********")
        # While typing, each character appeared alone at the end, never with the rest.
        self.assertIn("> ********x", text)
        self.assertNotIn("Secret", text)


class QuitOptionTests(unittest.TestCase):
    """Every kind of prompt offers a way to quit, and quitting stops cleanly."""

    def answer(self, function, *args, reply, **kwargs):
        from unittest import mock
        with mock.patch("builtins.input", return_value=reply), \
             mock.patch.object(prompts, "_emit"):
            return function(*args, **kwargs)

    def test_a_menu_numbers_only_its_options_and_q_quits(self):
        # q quits at every question, said in grey after it: never a number.
        from unittest import mock
        shown, asked = [], []
        with mock.patch("builtins.input", side_effect=lambda prompt: asked.append(prompt) or "q"), \
             mock.patch.object(prompts, "_emit", side_effect=shown.extend):
            with self.assertRaises(prompts.QuitRun):
                prompts.ask_choice("Which", ["One", "Two"])
        self.assertEqual(shown, ["1. One", "2. Two"])
        self.assertIn("(q to quit)", asked[0])

    def test_a_number_past_the_options_is_asked_again(self):
        from unittest import mock
        replies = iter(["3", "2"])
        with mock.patch("builtins.input", side_effect=lambda prompt: next(replies)), \
             mock.patch.object(prompts, "_emit"), mock.patch.object(prompts, "_retry") as retried:
            self.assertEqual(prompts.ask_choice("Which", ["One", "Two"]), 1)
        self.assertIn("between 1 and 2", retried.call_args.args[1][0])

    def test_a_menu_choice_still_works(self):
        self.assertEqual(self.answer(prompts.ask_choice, "Which", ["One", "Two"], reply="2"), 1)

    def test_q_quits_yes_no_and_typed_questions(self):
        for function, args in ((prompts.ask_yes_no, ("Go on",)), (prompts.ask_text, ("Name",))):
            with self.subTest(function=function.__name__):
                with self.assertRaises(prompts.QuitRun):
                    self.answer(function, *args, reply="q")

    def test_a_lone_q_at_a_password_prompt_quits(self):
        from unittest import mock
        with mock.patch.object(prompts, "_read_secret", return_value="q"):
            with self.assertRaises(prompts.QuitRun):
                prompts.ask_secret("Password: ")
        with mock.patch.object(prompts, "_read_secret", return_value="quietly-long-password"):
            self.assertEqual(prompts.ask_secret("Password: "), "quietly-long-password")

    def test_quitting_a_standalone_tool_is_a_clean_exit(self):
        self.assertTrue(issubclass(prompts.QuitRun, SystemExit))


class SearchTests(SetupNetworkTestCase):
    def find(self, visible, ctx, *later):
        scans = iter([visible, *later])
        with mock.patch.object(prompts, "scan_wifi_ssids", side_effect=lambda **_: next(scans, visible)), \
             mock.patch.object(prompts.time, "sleep"):
            return prompts.find_ssid(names.setup_networks(ctx))

    def test_the_plugs_network_is_found_by_either_name(self):
        self.assertEqual(self.find(["Home", "tasmota-ABCDEF-1234"], self.ctx()), "tasmota-ABCDEF-1234")
        self.assertEqual(self.find(["Home", "ESP_ABCDF0"], self.ctx()), "ESP_ABCDF0")

    def test_its_real_name_over_the_one_it_had_for_a_moment(self):
        # Tasmota turns its setup network on, then names it: for that moment
        # it is ESP-IDF's ESP_ABCDF0, which a computer lists for a while after,
        # perhaps first. Asked to join it, a person would find it gone.
        self.assertEqual(self.find(["ESP_ABCDF0", "Home", "tasmota-ABCDEF-1234"], self.ctx()),
                         "tasmota-ABCDEF-1234")

    def test_caught_in_that_moment_it_is_looked_at_again(self):
        self.assertEqual(self.find(["Home", "ESP_ABCDF0"], self.ctx(), ["Home", "tasmota-abcdef"]),
                         "tasmota-abcdef")

    def test_the_name_asked_for_first_wins_over_scan_order(self):
        self.assertEqual(self.find(["tasmota-ABCDEF-1234", "tasmota-abcdef"], self.ctx()), "tasmota-abcdef")

    def test_a_neighbours_tasmota_or_esp_is_not(self):
        self.assertIsNone(self.find(["tasmota-112233-0001", "ESP_112234", "ESP_ABCDEF"], self.ctx()))

    def test_the_installers_network_is_not_taken_for_tasmotas(self):
        # Seen in the fault suite: sent through the installer's own network,
        # Tasmota went on, and the installer's network - still listed for a
        # moment as it restarted - was taken for Tasmota's, never to be joined.
        # A Tasmota the run named has the Wi-Fi, and raises no network by itself.
        self.assertIsNone(self.find(["Home", "autoflash-recovery-CDEF"], self.ctx()))
        # It is the installer's, and found as that.
        with mock.patch.object(prompts, "scan_wifi_ssids", return_value=["Home", "autoflash-recovery-CDEF"]):
            self.assertEqual(prompts.find_ssid(names.bridge_networks(self.ctx())), "autoflash-recovery-CDEF")

    def test_only_tasmotas_own_name_is_kept_as_the_plugs(self):
        ctx = self.ctx()
        names.note_setup_network(ctx, "ESP_ABCDF0")  # not what its router shows
        names.note_setup_network(ctx, "autoflash-recovery-CDEF")  # only until it is set up
        self.assertIsNone(ctx.state.input("device_name"))
        names.note_setup_network(ctx, "tasmota-ABCDEF-1234")
        self.assertEqual(ctx.state.input("device_name"), "tasmota-ABCDEF-1234")


class UnattendedPromptTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(prompts, "UNATTENDED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.screen = io.StringIO()
        stdout = mock.patch("sys.stdout", self.screen)
        stdout.start()
        self.addCleanup(stdout.stop)
        self.addCleanup(output.finish)

    def test_no_key_is_waited_for(self):
        with mock.patch("builtins.input", side_effect=AssertionError("read the keyboard")):
            prompts.wait_for_key()

    def test_a_yes_no_question_takes_its_cautious_default(self):
        self.assertFalse(prompts.ask_yes_no("Carry on anyway", default=False))
        self.assertTrue(prompts.ask_yes_no("Enter it now", default=True))
        self.assertIn("Carry on anyway (y/N): n   (unattended)", self.screen.getvalue())

    def test_any_other_question_stops_the_run_and_names_it(self):
        for ask in (lambda: prompts.ask_choice("Which plug", ["one", "two"]),
                    lambda: prompts.ask_yes_no("No default"),
                    lambda: prompts.ask_text("eWeLink account email"),
                    lambda: prompts.ask_secret("Wi-Fi password: ")):
            with self.assertRaises(prompts.NoOneToAnswer):  # a QuitRun: the run stops cleanly
                ask()
        self.assertIn("eWeLink account email", self.screen.getvalue())

    def test_proceed_is_taken_as_typed(self):
        with mock.patch("builtins.input", side_effect=AssertionError("read the keyboard")):
            self.assertTrue(prompts.require_word("proceed"))
        self.assertIn("PROCEED   (unattended)", self.screen.getvalue())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(hasattr(os, "fork"), "a pseudo-terminal needs Linux or macOS")
class CountdownKeyTests(unittest.TestCase):
    """The countdown after a Ctrl-C carries on by itself, or at once with
    Enter; q stops, as at every question - taken as it is pressed, on a real
    terminal.  Its first redraw once readied the terminal for a typed answer,
    so a key waited for an Enter, whose row was left blank."""

    def ask(self, typed):
        import codecs
        import textwrap
        from tools.repo import unattended_run
        root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(root)!r})
            from tools.autoflash import output, prompts
            output.start(None, verbose=False)
            output.transient_begin()
            carry_on = prompts.countdown_carry_on("The run carries on to the next stage", seconds=20)
            output.transient_end()
            output.show(f"carry on {{carry_on}}")
            output.finish()
        """)
        code, written = unattended_run.record([sys.executable, "-c", script], (80, 24), live=False, timeout=30,
                                              answers=[unattended_run.Answer(r"press Enter now", typed)])
        screen = unattended_run.Screen(80, 24)
        screen.feed(codecs.decode(written, "utf-8", errors="replace"))
        self.written = written
        return code, screen.render(colour=False).splitlines()

    def test_q_stops_at_once(self):
        started = time.monotonic()
        code, rows = self.ask("q")
        self.assertEqual((code, rows[-1].strip()), (0, "carry on False"))
        self.assertLess(time.monotonic() - started, 10)   # not the countdown's 20 s

    def test_enter_carries_on_at_once_leaving_no_row(self):
        for typed, said in (("\r", "carry on True"), ("q\r", "carry on False")):
            with self.subTest(typed=typed):
                code, rows = self.ask(typed)
                self.assertEqual(rows[-1].strip(), said)
                self.assertNotEqual(rows[-2:-1], [""], rows)   # no blank row left above it

    def test_how_to_quit_is_said_as_at_every_question(self):
        self.ask("\r")
        self.assertIn(b"\x1b[2m(q to quit)\x1b[0m", self.written)   # in grey, never a number


class AdminPasswordTests(unittest.TestCase):
    def test_a_wrong_password_is_replaced_not_piled_up(self):
        # As every question's wrong answer is: only why, and the question again.
        from tests.autoflash.test_unlock import drawn
        asked = iter(["wrong-one", "wrong-two", "right-one"])

        def body(look):
            def secret(prompt, reveal_seconds=1.0):
                look()
                value = next(asked)
                output.show(f"{prompt}{'*' * len(value)}")
                return value
            with mock.patch.object(prompts, "ask_secret", side_effect=secret), \
                 mock.patch.object(prompts.hostsys, "check_sudo_password",
                                   side_effect=lambda value: None if value == "right-one" else "refused"):
                prompts.ask_admin_password()
        screens, _ = drawn(body)
        third = screens[2]
        self.assertEqual(third.count("That password was not accepted"), 1, third)
        self.assertNotIn("*********", third)   # neither wrong try left


class SecretFieldTests(unittest.TestCase):
    def test_a_password_is_typed_on_its_own_row_however_short_its_question(self):
        # The same for every password, whatever its question's length.
        for prompt in ("PIN (q to quit): ", "This computer's password (for sudo) (q to quit): "):
            self.assertTrue(prompts._secret_field_below(prompt), prompt)
