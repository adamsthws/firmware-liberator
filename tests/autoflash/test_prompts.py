"""What the tool asks (tools/autoflash/prompts.py): a secret shown only as it
is typed, keys typed ahead thrown away, and a way to quit at every question."""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import prompts


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
        import os
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
        self.assertIn("Password (q to quit):", text)
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

    def test_menus_end_with_a_quit_option(self):
        from unittest import mock
        shown = []
        with mock.patch("builtins.input", return_value="3"), \
             mock.patch.object(prompts, "_emit", side_effect=shown.extend):
            with self.assertRaises(prompts.QuitRun):
                prompts.ask_choice("Which", ["One", "Two"])
        self.assertEqual(shown[-1], "3. Quit - stop here")

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


if __name__ == "__main__":
    unittest.main()
