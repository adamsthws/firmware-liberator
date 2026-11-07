"""Each plug's records, under its MAC (tools/autoflash/plugs.py)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import plugs


def efuse_with_mac(mac):
    """eFuse registers whose base MAC is `mac`, as the chip stores it."""
    data = bytearray(1024)
    data[0x18:0x1E] = bytes.fromhex(mac.replace(":", ""))[::-1]
    return bytes(data)


class PlugLayoutTests(unittest.TestCase):
    """Each plug's records live under its MAC (tools/autoflash/plugs.py)."""

    def setUp(self):
        self.plugs = plugs
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patch = mock.patch.object(plugs, "PLUGS", Path(directory.name) / "plugs")
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_mac_names_a_folder_windows_can_hold(self):
        self.assertEqual(self.plugs.folder_name("AA:BB:CC:DD:EE:0F"), "aa-bb-cc-dd-ee-0f")
        self.assertEqual(self.plugs.folder_name("aa-bb-cc-dd-ee-0f"), "aa-bb-cc-dd-ee-0f")
        with self.assertRaises(ValueError):
            self.plugs.folder_name("../etc")

    def test_the_mac_is_read_from_the_chips_efuses(self):
        self.assertEqual(self.plugs.mac_from_efuse(efuse_with_mac("58:cf:79:01:02:03")),
                         "58:cf:79:01:02:03")

    def test_the_first_backup_is_kept_read_only_and_never_replaced(self):
        efuse = efuse_with_mac("58:cf:79:01:02:03")
        first, saved = self.plugs.save_backup(b"first", efuse, {}, "1.2.0")
        self.assertTrue(saved)
        self.assertEqual(first, self.plugs.backup_dir("58:cf:79:01:02:03"))
        again, saved = self.plugs.save_backup(b"second", efuse, {}, "1.3.0")
        self.assertFalse(saved)
        self.assertEqual((again / "flash.bin").read_bytes(), b"first")
        if sys.platform != "win32":
            self.assertEqual((first / "flash.bin").stat().st_mode & 0o777, 0o400)
        self.assertIn("58:cf:79:01:02:03", (first / "README.txt").read_text())

    def test_backed_up_plugs_are_listed_by_mac(self):
        self.plugs.save_backup(b"F", efuse_with_mac("58:cf:79:01:02:03"), {}, "1.2.0")
        self.plugs.remember_plug("58:cf:79:01:02:03", ewelink_name="Kettle")
        (found,) = self.plugs.backups()
        self.assertEqual(found.mac, "58:cf:79:01:02:03")
        self.assertTrue(found.describe().startswith("58:cf:79:01:02:03"))
        self.assertIn("Kettle", found.describe())
        self.assertIn("1.2.0", found.describe())

    def test_a_run_knows_its_plug_from_where_it_is_kept(self):
        run = self.plugs.runs_dir("58:cf:79:01:02:03") / "20261013-031500"
        self.assertEqual(self.plugs.mac_of_run(run), "58:cf:79:01:02:03")
        self.assertIsNone(self.plugs.mac_of_run(self.plugs.runs_dir(None) / "x"))


if __name__ == "__main__":
    unittest.main()
