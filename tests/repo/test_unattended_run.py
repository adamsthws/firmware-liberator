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


if __name__ == "__main__":
    unittest.main()
