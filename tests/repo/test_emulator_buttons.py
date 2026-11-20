"""emulator_buttons.py: the buttons file both emulators' boards follow."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import emulator_buttons as buttons


class FileTests(unittest.TestCase):
    def test_a_pin_a_line_and_nothing_else(self):
        self.assertEqual(buttons.pins_of("9\n32\n\n33 x\n-1\n"), frozenset({9, 32, 33}))
        self.assertEqual(buttons.text_of({33, 9, 32}), "9\n32\n33\n")
        self.assertEqual(buttons.text_of(()), "")


class Kept:
    """A buttons file, as a check's read and write reach it, with each write kept."""
    def __init__(self, text=""):
        self.text, self.writes = text, []

    def read(self):
        return self.text

    def write(self, text):
        self.text = text
        self.writes.append(text)


class ButtonsTests(unittest.TestCase):
    def setUp(self):
        self.file = Kept("32\n")
        self.buttons = buttons.Buttons(self.file.read, self.file.write)

    def test_held_until_let_go_and_the_others_kept(self):
        self.buttons.hold(9)
        self.assertEqual(self.buttons.held(), {9, 32})
        self.buttons.release(32)
        self.assertEqual(self.file.text, "9\n")

    def test_a_press_is_held_long_enough_for_the_board_to_see_it(self):
        with mock.patch.object(buttons.time, "sleep") as slept:
            self.buttons.press(9, seconds=0.01)
        self.assertEqual(self.file.writes, ["9\n32\n", "32\n"])
        self.assertGreater(slept.call_args.args[0], buttons.POLL)


class FollowerTests(unittest.TestCase):
    def test_each_change_once_and_no_look_before_its_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / buttons.FILE
            follower = buttons.Follower(path)
            self.assertEqual(follower.changes(0.0), (frozenset(), frozenset()))
            path.write_text("9\n", encoding="utf-8")
            self.assertEqual(follower.changes(buttons.POLL / 2), (frozenset(), frozenset()))
            self.assertEqual(follower.changes(1.0), ({9}, frozenset()))
            self.assertEqual(follower.changes(2.0), (frozenset(), frozenset()))
            path.write_text("32\n", encoding="utf-8")
            self.assertEqual(follower.changes(3.0), ({32}, {9}))
            path.unlink()
            self.assertEqual(follower.changes(4.0), (frozenset(), {32}))

    def test_a_board_holds_each_pressed_then_releases_each_let_go(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / buttons.FILE
            follower, done = buttons.Follower(path), []
            follower.held = frozenset({4, 2})
            path.write_text("33\n9\n", encoding="utf-8")
            follower.follow(1.0, lambda pin: done.append(("hold", pin)), lambda pin: done.append(("release", pin)))
        self.assertEqual(done, [("hold", 9), ("hold", 33), ("release", 2), ("release", 4)])


if __name__ == "__main__":
    unittest.main()
