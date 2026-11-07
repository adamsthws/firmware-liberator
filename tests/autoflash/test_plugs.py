"""Each plug's records, under its MAC (tools/autoflash/plugs.py), and one run on
a plug at a time - a run that dies lets go of it."""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import cli, errors, output, plugs, runs


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


MAC = "12:34:56:ab:cd:ef"


class PlugLockTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = mock.patch.object(plugs, "PLUGS", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        held = mock.patch.dict(plugs._HELD, {}, clear=True)
        held.start()
        self.addCleanup(held.stop)

    def other_run(self, script):
        """A second process holding the plug, as another run would."""
        code = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
            from tools.autoflash import plugs
            from pathlib import Path
            plugs.PLUGS = Path({str(self.root)!r})
            plugs.hold({MAC!r}, "plugs/the-other-run")
            print("held", flush=True)
            {script}
        """)
        process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(process.stdout.readline().strip(), "held")
        return process

    def test_a_second_run_is_refused_and_told_which_holds_it(self):
        other = self.other_run("time.sleep(30)")
        try:
            with self.assertRaises(plugs.PlugBusy) as busy:
                plugs.hold(MAC, "plugs/this-run")
            self.assertIn("the-other-run", str(busy.exception))
        finally:
            other.kill()
            other.wait()

    def test_a_run_that_died_leaves_no_lock_behind(self):
        other = self.other_run("import os; os._exit(9)")  # gone without a word, as in a crash
        other.wait()
        plugs.hold(MAC, "plugs/this-run")  # not refused
        self.assertIn(MAC, plugs._HELD)

    def test_the_run_refused_is_told_plainly(self):
        from types import SimpleNamespace
        with mock.patch.object(plugs, "hold", side_effect=plugs.PlugBusy("run plugs/x, process 7")):
            with self.assertRaisesRegex(errors.Abort, "another run is converting this device"):
                runs.hold_plug(SimpleNamespace(), MAC)

    def test_busy_names_the_plugs_others_hold(self):
        self.assertEqual(plugs.busy(), [])
        other = self.other_run("time.sleep(30)")
        try:
            busy = plugs.busy()
            self.assertEqual(len(busy), 1)
            self.assertIn(MAC, busy[0])
            self.assertIn("the-other-run", busy[0])
        finally:
            other.kill()
            other.wait()
        self.assertEqual(plugs.busy(), [])  # let go with the process

    def ctx(self, mac=MAC):
        from types import SimpleNamespace
        return SimpleNamespace(state=SimpleNamespace(input=lambda key: mac if key == "plug_mac" else None),
                               dir=self.root / "runs" / "this-run")

    def test_a_carried_on_run_is_refused_before_it_touches_anything(self):
        # Its log, report, firewall rule and safety locks are the other run's
        # too: the run's main stops before any of them (seen in the fault
        # suite: a refused run took the other's firewall rule away).
        other = self.other_run("time.sleep(30)")
        try:
            with mock.patch.object(output, "tell") as told:
                self.assertTrue(cli.refused_for_another_run("run", self.ctx()))
            self.assertIn("another run is converting this device", told.call_args.args[0])
        finally:
            other.kill()
            other.wait()
        self.assertFalse(cli.refused_for_another_run("run", self.ctx()))  # then it is this run's

    def test_cleanup_waits_for_every_run_to_finish(self):
        other = self.other_run("time.sleep(30)")
        try:
            with mock.patch.object(output, "tell") as told:
                self.assertTrue(cli.refused_for_another_run("cleanup", self.ctx(mac=None)))
            self.assertIn("Not cleaning up", told.call_args.args[0])
        finally:
            other.kill()
            other.wait()
        self.assertFalse(cli.refused_for_another_run("cleanup", self.ctx(mac=None)))

    def test_looking_is_never_refused(self):
        other = self.other_run("time.sleep(30)")
        try:
            for command in ("status", "list"):
                self.assertFalse(cli.refused_for_another_run(command, self.ctx()))
        finally:
            other.kill()
            other.wait()


if __name__ == "__main__":
    unittest.main()
