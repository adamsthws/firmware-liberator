"""plug.py, the emulated plug."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class PlugTests(unittest.TestCase):
    """The emulated plug's own decisions (tools/beken/emulator/plug.py)."""

    def plug(self, tmp):
        fakes = {"cloud_online": SimpleNamespace(SSID=b"net", PW=b"password"), "src": SimpleNamespace(),
                 "src.main": SimpleNamespace(build_emulator=None), "unicorn": SimpleNamespace(UC_HOOK_CODE=4),
                 "unicorn.arm_const": SimpleNamespace(UC_ARM_REG_LR=14, UC_ARM_REG_R0=0, UC_ARM_REG_R1=1,
                                                      UC_ARM_REG_R2=2, UC_ARM_REG_R3=3)}
        with mock.patch.dict(sys.modules, fakes):
            sys.modules.pop("plug", None)
            import plug
        self.addCleanup(sys.modules.pop, "plug", None)
        self.stock_points = plug.stock_points
        return plug.Plug(Path(tmp), "tap0")

    def test_sonoffs_app_is_the_one_the_points_are_found_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            plug = self.plug(tmp)
            with mock.patch.object(self.stock_points, "app_of_flash", return_value=b"sonoff"), \
                    mock.patch.object(self.stock_points, "find", return_value="its points") as find:
                self.assertEqual(plug.stock_points(b"flash"), "its points")
            find.assert_called_once_with(b"sonoff")

    def test_any_other_app_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            plug = self.plug(tmp)
            with mock.patch.object(self.stock_points, "app_of_flash", return_value=bytes(0x1000)):
                self.assertIsNone(plug.stock_points(b"flash"))
            self.assertIn("no help for this app", plug.netlog[-1])

    def test_the_power_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            plug = self.plug(tmp)
            self.assertTrue(plug.powered())                   # no switch yet: on
            (Path(tmp) / "power").write_text("off\n", encoding="utf-8")
            self.assertFalse(plug.powered())
            (Path(tmp) / "power").write_text("on\n", encoding="utf-8")
            self.assertTrue(plug.powered())


if __name__ == "__main__":
    unittest.main()
