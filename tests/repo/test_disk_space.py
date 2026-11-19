import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import disk_space


class FreeTests(unittest.TestCase):
    def test_a_folder_not_made_yet_is_measured_on_the_disk_it_will_be_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertGreater(disk_space.free(Path(tmp) / "not" / "yet"), 0)


class WaitTests(unittest.TestCase):
    def wait(self, frees, halted=False):
        clock = [0.0]
        said = []
        found = iter(frees)
        last = [frees[-1]]

        def free(path):
            last[0] = next(found, last[0])
            return last[0]

        class Halt:
            def is_set(self):
                return halted

            def wait(self, seconds):
                clock[0] += seconds
        with mock.patch.object(disk_space, "free", side_effect=free), \
                mock.patch.object(disk_space.time, "monotonic", side_effect=lambda: clock[0]):
            room = disk_space.wait_for_room(Path("results"), say=said.append, halt=Halt())
        return room, clock[0], said

    def test_a_disk_with_room_starts_at_once_saying_nothing(self):
        self.assertEqual(self.wait([disk_space.FLOOR]), (True, 0.0, []))

    def test_a_short_one_waits_for_room_saying_so(self):
        low = disk_space.FLOOR - 1
        room, waited, said = self.wait([low, low, low, disk_space.FLOOR * 2])
        self.assertEqual((room, waited), (True, 2 * disk_space.POLL))
        self.assertEqual(len(said), 2)
        self.assertIn("waits for room", said[0])

    def test_a_disk_that_stays_short_gives_up_so_the_batch_can_stop(self):
        room, waited, said = self.wait([0])
        self.assertEqual((room, waited), (False, disk_space.LONGEST))
        self.assertEqual(len(said), 1)

    def test_a_batch_already_stopping_waits_no_longer(self):
        self.assertEqual(self.wait([0], halted=True)[:2], (False, 0.0))

    def test_the_reason_says_what_to_do(self):
        with mock.patch.object(disk_space, "free", return_value=5 << 20):
            self.assertIn("free some space and run the batch again", disk_space.stop_reason(Path("r")))

    def test_a_real_event_is_waited_on(self):
        halt = threading.Event()
        halt.set()
        with mock.patch.object(disk_space, "free", return_value=0):
            self.assertFalse(disk_space.wait_for_room(Path("r"), say=lambda line: None, halt=halt))


if __name__ == "__main__":
    unittest.main()
