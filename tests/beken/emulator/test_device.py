"""device.py, the emulated device."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class DeviceTests(unittest.TestCase):
    """The emulated device's own decisions (tools/beken/emulator/device.py)."""

    def module(self):
        fakes = {"cloud_online": SimpleNamespace(SSID=b"net", PW=b"password"), "src": SimpleNamespace(),
                 "src.emulator": SimpleNamespace(SLOW_TICK_INSNS=10000, SLOW_TICK_MS=2),
                 "src.main": SimpleNamespace(build_emulator=None), "unicorn": SimpleNamespace(UC_HOOK_CODE=4),
                 "unicorn.arm_const": SimpleNamespace(UC_ARM_REG_LR=14, UC_ARM_REG_R0=0, UC_ARM_REG_R1=1,
                                                      UC_ARM_REG_R2=2, UC_ARM_REG_R3=3)}
        with mock.patch.dict(sys.modules, fakes):
            sys.modules.pop("device", None)
            import device
        self.addCleanup(sys.modules.pop, "device", None)
        return device

    def device(self, tmp):
        device = self.module()
        self.stock_points = device.stock_points
        return device.Device(Path(tmp), "tap0")

    def test_sonoffs_app_is_the_one_the_points_are_found_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            with mock.patch.object(self.stock_points, "app_of_flash", return_value=b"sonoff"), \
                    mock.patch.object(self.stock_points, "find", return_value="its points") as find:
                self.assertEqual(device.stock_points(b"flash"), "its points")
            find.assert_called_once_with(b"sonoff")

    def test_any_other_app_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            with mock.patch.object(self.stock_points, "app_of_flash", return_value=bytes(0x1000)):
                self.assertIsNone(device.stock_points(b"flash"))
            self.assertIn("no help for this app", (Path(tmp) / "net.log").read_text(encoding="utf-8"))

    def test_the_router_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            self.assertEqual(device.router(), (True, "password"))   # no switch yet: on, as provisioned
            for written, read in (("off\n", (False, "password")), ("on\n", (True, "password")),
                                  ("on new-pass-1\n", (True, "new-pass-1"))):
                (Path(tmp) / "router").write_text(written, encoding="utf-8")
                self.assertEqual(device.router(), read)

    def test_the_access_point_follows_the_router_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            device.air.mkdir()
            switched = []
            device.home = SimpleNamespace(passphrase="password", set_on_air=switched.append)
            device.follow_router()
            self.assertEqual((switched, (device.air / "home").read_text(encoding="utf-8")), ([True], "net\n"))
            # A new password restarts it: every device on it joins again.
            (Path(tmp) / "router").write_text("on new-pass-1\n", encoding="utf-8")
            device.follow_router()
            self.assertEqual((switched[1:], device.home.passphrase), ([False, True], "new-pass-1"))
            (Path(tmp) / "router").write_text("off\n", encoding="utf-8")
            device.follow_router()
            self.assertEqual(switched[-1], False)
            self.assertFalse((device.air / "home").exists())

    def test_the_devices_own_network_is_on_the_air_while_the_station_hears_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            device.air.mkdir()
            device.station = SimpleNamespace(bss={"ssid": "esphome-abcdef"}, set_joining=lambda on: None)
            device.follow_air()
            self.assertEqual((device.air / "network").read_text(encoding="utf-8"), "esphome-abcdef\n")
            device.station.bss = None
            device.follow_air()
            self.assertFalse((device.air / "network").exists())

    def test_the_station_is_on_the_devices_network_only_while_the_computer_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            device.air.mkdir()
            resets, joining = [], []
            device.station = SimpleNamespace(bss=None, device_reset=lambda: resets.append(1),
                                             set_joining=joining.append)
            (Path(tmp) / "joins").write_text("", encoding="utf-8")
            device.follow_air()
            device.follow_air()
            self.assertEqual((resets, joining), ([], [False]), "the computer at home: it only hears the network")
            (Path(tmp) / "joins").write_text("join 1.0\n", encoding="utf-8")
            device.follow_air()
            device.follow_air()
            self.assertEqual((resets, joining), ([1], [False, True]), "joined afresh, once a join")
            (Path(tmp) / "joins").write_text("join 2.0\n", encoding="utf-8")
            device.follow_air()
            self.assertEqual(resets, [1, 1])
            (Path(tmp) / "joins").write_text("home 3.0\n", encoding="utf-8")
            device.follow_air()
            self.assertEqual((resets, joining[-1]), ([1, 1], False), "home: it leaves")

    def test_the_power_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            self.assertTrue(device.powered())                   # no switch yet: on
            (Path(tmp) / "power").write_text("off\n", encoding="utf-8")
            self.assertFalse(device.powered())
            (Path(tmp) / "power").write_text("on\n", encoding="utf-8")
            self.assertTrue(device.powered())


class WallClockTests(unittest.TestCase):
    """The device's time held to the wall's (device.hold_to_wall)."""

    def held(self, hold=True):
        """A run of chunks whose device time and wall time the test sets."""
        device = DeviceTests.module(self)
        self.wall, self.slept, self.lines = 100.0, [], []
        emu = SimpleNamespace(_pacing=lambda state: None)

        def sleep(seconds):
            self.slept.append(round(seconds, 3))
            self.wall += seconds
        device.hold_to_wall(emu, 0.001, self.lines.append, hold, clock=lambda: self.wall, sleep=sleep)

        def chunk(device_seconds, wall_seconds):
            self.wall += wall_seconds
            emu._pacing(SimpleNamespace(insn_count=round(device_seconds * 1000)))
        return device, chunk

    def test_a_device_ahead_of_the_wall_waits_for_it(self):
        _, chunk = self.held()
        chunk(0, 0)
        chunk(0.5, 0.1)     # idle: half a second of the device's in a tenth of the wall's
        self.assertEqual(self.slept, [0.4])
        chunk(0.505, 0.001)   # under HOLD_AT_LEAST ahead: not worth a sleep
        self.assertEqual(self.slept, [0.4])

    def test_free_it_runs_ahead(self):
        _, chunk = self.held(hold=False)
        chunk(0, 0)
        chunk(0.5, 0.1)
        self.assertEqual(self.slept, [])

    def test_behind_it_catches_up_no_more_than_catch_up(self):
        device, chunk = self.held()
        chunk(0, 0)
        chunk(1, 10)        # busy: a second of the device's in ten of the wall's
        chunk(1 + device.CATCH_UP + 3, 0)   # idle again: it may run CATCH_UP ahead of where it fell to
        self.assertEqual(self.slept, [3.0])

    def test_both_times_are_logged_now_and_then(self):
        device, chunk = self.held(hold=False)
        chunk(0, 0)
        chunk(30, device.CLOCK_LOG_EVERY)
        self.assertEqual(self.lines, ["[clock] the device's time 30.0 s, the wall's %.1f s" % device.CLOCK_LOG_EVERY])


if __name__ == "__main__":
    unittest.main()
