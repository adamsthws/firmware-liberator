"""Unattended runs (tools/repo/unattended_run.py): the screen kept for review."""

import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import unattended_run

ROOT = Path(__file__).resolve().parents[2]


class ScreenTests(unittest.TestCase):
    def render(self, text, width=20, height=5, colour=True):
        screen = unattended_run.Screen(width, height)
        screen.feed(text)
        return screen.render(colour)

    def test_a_line_redrawn_in_place_shows_its_last_drawing(self):
        self.assertEqual(self.render("Stage 1  underway 3s\rStage 1  complete   \n", colour=False),
                         "Stage 1  complete\n")

    def test_rows_taken_away_are_gone(self):
        text = "kept\r\nnote\r\nquestion: y\r\n" + "\x1b[1F\x1b[2K" * 2 + "after\r\n"
        self.assertEqual(self.render(text, colour=False), "kept\nafter\n")

    def test_the_cursor_cannot_climb_into_the_scrollback(self):
        # Seven rows - six written, and the cursor's - in a window of five:
        # the window's top is row 2, and the cursor stops there.
        text = "".join(f"row {n}\r\n" for n in range(6)) + "\x1b[1F" * 9 + "X"
        self.assertEqual(self.render(text, colour=False).splitlines()[:3], ["row 0", "row 1", "Xow 2"])

    def test_long_lines_wrap_at_the_width(self):
        self.assertEqual(self.render("abcdefgh\r\n", width=5, colour=False), "abcde\nfgh\n")

    def test_colours_are_kept_and_links_left_as_text(self):
        text = ("\x1b[32mComplete!\x1b[0m \x1b]8;;http://x/\x1b\\http://x/\x1b]8;;\x1b\\\r\n")
        self.assertEqual(self.render(text, width=40), "\x1b[0m\x1b[32mComplete!\x1b[0m http://x/\n")


class StageProblemsTests(unittest.TestCase):
    """A stage that passed leaves one line; one that stopped, what it showed."""

    def problems(self, *rows):
        return unattended_run.stage_problems("".join(f"{row}\n" for row in rows))

    def test_stages_that_each_ended_once_leave_nothing(self):
        self.assertEqual(self.problems(
            "Stage 12 - Verifying memory layout - Complete",
            "Stage 13 - Staging startup firmware - Complete",
            "",
            "    The meter is left as it is: the meter did not report.",   # kept, under its line
            "Stage 14 - Committing memory layout - Underway",
            "",
            "    The device stopped part way",
            "Stage 14 - Committing memory layout - Stopped",
            "",
            "FAILED: the device did not answer"), [])

    def test_a_try_that_failed_left_on_the_screen_is_found(self):
        # As a run on 2026-11-08 left it: the retry drew the stage again
        # under the failed try's lines.
        self.assertEqual(self.problems(
            "Stage 13 - Staging startup firmware     - Underway",
            "",
            "    The device's console: 00:00:00.328 BRY: Berry initialized",
            "Stage 13 - Staging startup firmware     - Stopped",
            "",
            "FAILED: the device stopped the stage without reaching this computer",
            "    That may have been the network - trying again in 10s (attempt 2 of 3).",
            "Stage 13 - Staging startup firmware     - Complete",
            "Stage 14 - Committing memory layout     - Complete"),
            ["Stage 13 - Staging startup firmware: shown complete, after an earlier outcome - "
             "its first go was left on the screen"])

    def test_a_stage_never_ended_is_found(self):
        self.assertEqual(self.problems("Stage  9 - Preparing memory layout - Underway",
                                       "Stage 10 - Updating the bootloader - Complete"),
                         ["Stage 9 - Preparing memory layout: left underway when the next stage began"])

    def test_a_stage_gone_back_to_drawn_under_its_first_go_is_found(self):
        problems = self.problems("Stage 6 - Sending bridge firmware - Complete",
                                 "Stage 7 - Checking it arrived - Not installed",
                                 "",
                                 "The bridge was not installed. Doing B2 again.",
                                 "Stage 6 - Sending bridge firmware - Complete",
                                 "Stage 7 - Checking it arrived - Complete")
        self.assertEqual(len(problems), 2, problems)

    def test_a_stage_that_passed_with_its_notes_left_above_it_is_found(self):
        # As the fault suites' screens showed: instructions to join a network,
        # long since done with, above a stage that had passed.
        self.assertEqual(self.problems(
            "Stage  8 - Installing interim firmware  - Underway",
            "",
            "-- Back to your usual Wi-Fi",
            "    Your computer should rejoin your usual Wi-Fi by itself.",
            "Stage  8 - Installing interim firmware  - Complete"),
            ["Stage 8 - Installing interim firmware: passed, with what it showed while under way left above it"])

    def test_a_stage_left_retrying_is_found_as_one_left_underway(self):
        self.assertEqual(self.problems("Stage  9 - Preparing memory layout - Retrying",
                                       "Stage 10 - Updating the bootloader - Complete"),
                         ["Stage 9 - Preparing memory layout: left underway when the next stage began"])

    def test_two_blank_rows_together_are_found(self):
        self.assertEqual(self.problems("Stage 1 - Checking - Complete", "", "", "Done"),
                         ["two blank rows one after another"])


@unittest.skipUnless(hasattr(os, "fork"), "a pseudo-terminal needs Linux or macOS")
class RecordingTests(unittest.TestCase):
    def test_the_screen_a_person_would_see_is_rebuilt(self):
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from tools.autoflash import output, prompts
            output.start(None, verbose=False)
            output.stage_begin(1, "Checking the files")
            output.transient_begin()
            prompts.warning_box("Read this first", ["taken away once read"])
            prompts.UNATTENDED = True
            prompts.wait_for_key()
            output.transient_end()
            output.stage_end("complete")
            output.show("Conversion complete!", output.GREEN)
            output.finish()
        """)
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):  # the run gets colour regardless
            code, written = unattended_run.record([sys.executable, "-c", script],
                                                  (80, 24), live=False, timeout=60)
        self.assertEqual(code, 0, written)
        screen = unattended_run.Screen(80, 24)
        screen.feed(written.decode("utf-8", errors="replace"))
        plain = screen.render(colour=False)
        self.assertIn("Checking the files", plain)
        self.assertIn("complete", plain)
        self.assertNotIn("Read this first", plain)
        self.assertIn("\x1b[32mConversion complete!", screen.render())



@unittest.skipUnless(hasattr(os, "fork"), "a pseudo-terminal needs Linux or macOS")
class AnswerTests(unittest.TestCase):
    def test_each_question_is_answered_as_it_shows(self):
        script = textwrap.dedent("""
            name = input("Your name: ")
            word = input("Type PROCEED to go ahead: ")
            print(f"got {name} and {word}")
        """)
        answers = [unattended_run.Answer(r"Type PROCEED", "PROCEED\r"), unattended_run.Answer(r"Your name", "Ada\r")]
        code, written = unattended_run.record([sys.executable, "-c", script], (80, 24), live=False,
                                              timeout=60, answers=answers)
        self.assertEqual(code, 0, written)
        self.assertIn(b"got Ada and PROCEED", written)
        self.assertEqual([answer.left for answer in answers], [0, 0])   # each once, as told
        name, word = answers[1].typed_at[0], answers[0].typed_at[0]
        self.assertLess(name, word)   # where each was typed, in the bytes written

    def test_an_answer_waits_for_its_question_not_a_note_that_says_its_words(self):
        # Seen: q typed at the firmware question, whose note said "your eWeLink
        # account", for the sign-in's "eWeLink account email" asked later.
        script = textwrap.dedent("""
            print("Its note: going back uses your eWeLink account email record.")
            print("A second line, and a third,")
            print("then the question itself.")
            first = input("Which firmware would you like: ")
            account = input("eWeLink account email: ")
            print(f"got {first} and {account}")
        """)
        answers = [unattended_run.Answer(r"eWeLink account email", "you@example.com\r"),
                   unattended_run.Answer(r"Which firmware", "1\r")]
        code, written = unattended_run.record([sys.executable, "-c", script], (80, 24), live=False,
                                              timeout=60, answers=answers)
        self.assertIn(b"got 1 and you@example.com", written)

    def test_a_question_is_its_last_two_rows_on_the_screen(self):
        screen = unattended_run.Screen(80, 24)
        screen.feed("a note\r\nmore\r\n Wi-Fi password (q to quit):\r\n > ")
        self.assertEqual(unattended_run.question(screen), " Wi-Fi password (q to quit):\n >")

    def test_a_question_waits_until_its_prompt_ends_the_row(self):
        for asked, expected in ((" Which firmware would you like (q to quit):", True),
                                (" Wi-Fi password (q to quit):\n >", True),
                                (" Press any key to continue (q to quit)", True),
                                (" Wi-Fi password (q to quit):\n > ***", False),
                                (" Which firmware would you like (q to quit): 1", False)):
            with self.subTest(asked=asked):
                self.assertEqual(unattended_run.waiting(asked), expected)

    def test_answers_are_read_from_a_file(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "answers.json"
            path.write_text(json.dumps([{"when": "Wi-Fi password", "type": "secret\r", "times": 2}]))
            loaded = unattended_run.Answer.load(path)
        self.assertEqual((loaded[0].type, loaded[0].left), ("secret\r", 2))

    def test_an_attended_run_is_not_told_it_is_unattended(self):
        self.assertNotIn("--unattended", unattended_run.tool_command("run", ["--new"], attended=True))


class CommandTests(unittest.TestCase):
    def test_a_conversion_unless_told_otherwise(self):
        self.assertEqual(unattended_run.tool_command("run", ["--new"])[2:], ["run", "--unattended", "--new"])

    def test_a_readout_unattended_too(self):
        # The fault suites' readout scenarios (docs/testing.md).
        self.assertEqual(unattended_run.tool_command("readout", ["--plug-ip", "192.0.2.50"])[2:],
                         ["readout", "--unattended", "--plug-ip", "192.0.2.50"])
        self.assertIn("readout", unattended_run.COMMANDS)


if __name__ == "__main__":
    unittest.main()
