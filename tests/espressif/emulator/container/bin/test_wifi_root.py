"""wifi-root, the root half of the emulated computer's Wi-Fi: here its
readdress, the router giving the device another address (the fault suite's
readdress), with the commands it runs stubbed."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WIFI_ROOT = Path(__file__).resolve().parents[5] / "tools/espressif/emulator/container/bin/wifi-root"
MAC = "12:34:56:ab:cd:ef"


class ReaddressTests(unittest.TestCase):
    def run_root(self, *args, pid=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run, stubs = Path(tmp.name, "run"), Path(tmp.name, "bin")
        run.mkdir()
        stubs.mkdir()
        (run / "env").write_text(f"LAN=eth9\nWINDOWS=0\nDEVICE_MAC={MAC}\nCLOUD=0\n", encoding="utf-8")
        (run / "tap-mac").write_text("02:00:00:00:00:01\n", encoding="utf-8")
        (run / "leases").write_text(f"1 {MAC} 192.168.60.50 sonoff *\n", encoding="utf-8")
        if pid:   # a router running
            (run / "dnsmasq.pid").write_text(f"{pid}\n", encoding="utf-8")
        calls = Path(tmp.name, "calls")
        # iptables has no rule to take away: its -D loops end at once.
        for name, code in (("ip", 0), ("iptables", 1), ("pkill", 0), ("tc", 0), ("dnsmasq", 0)):
            (stubs / name).write_text(f'#!/bin/sh\necho "{name} $*" >> {calls}\nexit {code}\n', encoding="utf-8")
            (stubs / name).chmod(0o755)
        done = subprocess.run(["bash", str(WIFI_ROOT), *args], capture_output=True, text=True, timeout=30,
                              env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}",
                                   "AUTOFLASH_EMU_RUN": str(run)})
        return done, run, calls.read_text(encoding="utf-8") if calls.exists() else ""

    def test_the_router_gives_the_device_the_new_address_and_lets_its_arp_through(self):
        done, run, calls = self.run_root("readdress", "192.168.60.55")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((run / "device-ip").read_text(encoding="utf-8").strip(), "192.168.60.55")
        self.assertEqual((run / "leases").read_text(encoding="utf-8"), "")   # the old one forgotten
        self.assertIn(f"--dhcp-host={MAC},192.168.60.55", calls)
        self.assertIn("tc filter del dev tap0 egress pref 1", calls)
        self.assertIn("arp_tip 192.168.60.55 action pass", calls)
        self.assertEqual((run / "browser-target").read_text(encoding="utf-8").strip(), "192.168.60.55:80")
        # Its own address kept: taken off, the tool's servers on it were cut off.
        self.assertNotIn("ip address flush", calls)

    def test_a_router_running_is_started_again_with_the_new_address(self):
        # Its old dnsmasq is left a zombie no one reaps, which still answers
        # kill -0: seen, no router started again, and the device given no address.
        zombie = subprocess.Popen(["sleep", "30"])
        self.addCleanup(zombie.kill)
        done, run, calls = self.run_root("readdress", "192.168.60.55", pid=zombie.pid)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"--dhcp-host={MAC},192.168.60.55", calls)

    def test_the_home_network_gives_it_50_until_then(self):
        done, _, calls = self.run_root("home")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"--dhcp-host={MAC},192.168.60.50", calls)

    def test_an_address_off_the_network_or_in_its_pool_is_turned_down(self):
        for address in ("10.0.0.5", "192.168.60.150", "192.168.60.0"):
            with self.subTest(address=address):
                done, run, calls = self.run_root("readdress", address)
                self.assertEqual(done.returncode, 2)
                self.assertFalse((run / "device-ip").exists())
                self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
