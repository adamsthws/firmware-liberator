import unittest
from unittest import mock

from tools.repo import cpu_load


class OverloadTests(unittest.TestCase):
    def test_overloaded_is_well_above_one_task_a_core(self):
        # An emulator on each core keeps the load near one a core: no wait for that.
        for load, expected in ((6.0, False), (9.0, False), (9.5, True), (28.0, True)):
            with mock.patch.object(cpu_load.os, "getloadavg", return_value=(load, 0.0, 0.0)):
                self.assertIs(cpu_load.overloaded(cores=6), expected, load)

    def test_a_computer_that_keeps_no_load_is_never_held_back(self):
        with mock.patch.object(cpu_load.os, "getloadavg", side_effect=OSError):
            self.assertFalse(cpu_load.overloaded(cores=1))


class WaitTests(unittest.TestCase):
    def wait(self, loads):
        clock = [0.0]
        said = []
        found = iter(loads)
        last = [loads[-1]]

        def load():
            last[0] = next(found, last[0])
            return (last[0], 0.0, 0.0)

        def sleep(seconds):
            clock[0] += seconds
        with mock.patch.object(cpu_load.os, "getloadavg", side_effect=load), \
                mock.patch.object(cpu_load.os, "cpu_count", return_value=6), \
                mock.patch.object(cpu_load.time, "sleep", side_effect=sleep), \
                mock.patch.object(cpu_load.time, "monotonic", side_effect=lambda: clock[0]):
            return cpu_load.wait_for_room(say=said.append), said

    def test_a_machine_with_room_starts_at_once_saying_nothing(self):
        self.assertEqual(self.wait([4.0]), (0.0, []))

    def test_an_overloaded_one_waits_for_the_load_to_fall(self):
        waited, said = self.wait([28.0, 28.0, 20.0, 12.0, 6.0])
        self.assertEqual(waited, 3 * cpu_load.POLL)
        self.assertEqual(len(said), 1)

    def test_load_that_never_falls_holds_a_scenario_back_only_so_long(self):
        waited, said = self.wait([28.0])
        self.assertEqual(waited, cpu_load.LONGEST)
        self.assertIn("starting the next scenario anyway", said[-1])


class EmulatorCoresTests(unittest.TestCase):
    def test_a_classic_esp32_takes_two_cores_and_the_others_one(self):
        # QEMU runs a classic ESP32's two cores on a thread each.
        self.assertEqual([cpu_load.emulator_cores(chip) for chip in ("esp32", "esp32c3", "bk7238")], [2, 1, 1])

    def test_every_chip_counted_is_one_a_kind_has(self):
        from tools.autoflash import kinds
        self.assertLessEqual(set(cpu_load.EMULATOR_CORES), {kind.chip for kind in kinds.EVERY})

    def test_as_many_at_once_as_the_cores_hold_never_none_nor_more_than_asked(self):
        for chip, most, cores, expected in (("esp32", 6, 8, 4), ("esp32c3", 6, 8, 6), ("esp32", 3, 4, 2),
                                            ("esp32c3", 3, 4, 3), ("esp32", 3, 1, 1), ("bk7238", 2, 1, 1)):
            with self.subTest(chip=chip, most=most, cores=cores):
                self.assertEqual(cpu_load.emulators_at_once(chip, most, cores), expected)

    def test_this_computers_cores_where_none_are_given(self):
        with mock.patch.object(cpu_load.os, "cpu_count", return_value=4):
            self.assertEqual(cpu_load.emulators_at_once("esp32", 6), 2)
        with mock.patch.object(cpu_load.os, "cpu_count", return_value=None):
            self.assertEqual(cpu_load.emulators_at_once("esp32c3", 6), 1)

    def test_a_mix_is_short_where_its_cores_together_are_more_than_the_computers(self):
        self.assertIsNone(cpu_load.cores_short(["esp32"] * 4, cores=8))
        self.assertIsNone(cpu_load.cores_short(["esp32c3"] * 8, cores=8))
        short = cpu_load.cores_short(["esp32"] * 4 + ["bk7238"], cores=8)
        self.assertIn("5 emulators at once need 9 cores, 2 for each esp32, and this computer has 8", short)
        self.assertNotIn("for each bk7238", cpu_load.cores_short(["bk7238"] * 3, cores=2))


if __name__ == "__main__":
    unittest.main()
