"""device.py, the emulated device."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class DeviceTests(unittest.TestCase):
    """The emulated device's own decisions (tools/beken/emulator/device.py)."""

    def device(self, tmp):
        fakes = {"cloud_online": SimpleNamespace(SSID=b"net", PW=b"password"), "src": SimpleNamespace(),
                 "src.main": SimpleNamespace(build_emulator=None), "unicorn": SimpleNamespace(UC_HOOK_CODE=4),
                 "unicorn.arm_const": SimpleNamespace(UC_ARM_REG_LR=14, UC_ARM_REG_R0=0, UC_ARM_REG_R1=1,
                                                      UC_ARM_REG_R2=2, UC_ARM_REG_R3=3)}
        with mock.patch.dict(sys.modules, fakes):
            sys.modules.pop("device", None)
            import device
        self.addCleanup(sys.modules.pop, "device", None)
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
            self.assertIn("no help for this app", device.netlog[-1])

    def test_the_power_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.device(tmp)
            self.assertTrue(device.powered())                   # no switch yet: on
            (Path(tmp) / "power").write_text("off\n", encoding="utf-8")
            self.assertFalse(device.powered())
            (Path(tmp) / "power").write_text("on\n", encoding="utf-8")
            self.assertTrue(device.powered())


if __name__ == "__main__":
    unittest.main()
