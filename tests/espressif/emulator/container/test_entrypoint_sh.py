"""entrypoint.sh, the emulator container's start: who may use sudo without a
password, and what of the stub reaches the readout API beside it."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tools.autoflash import firewall
from tools.ewelink.stub_cloud import stub_server

ENTRYPOINT = Path(__file__).resolve().parents[4] / "tools/espressif/emulator/container/entrypoint.sh"


class SudoTests(unittest.TestCase):
    """sudo asks for the password everywhere but the emulator's own Wi-Fi
    switching (wifi-root), as a desktop's Wi-Fi menu asks for nothing."""

    def test_the_wifi_rule_comes_after_the_rule_that_asks(self):
        """sudo takes the last rule that matches: with the ALL rule last,
        `wifi join` asked for the password."""
        text = ENTRYPOINT.read_text(encoding="utf-8")
        written = re.search(r"(?ms)^printf '%s\\n' (.*?)> /etc/sudoers\.d/autoflash-emu$", text)
        self.assertIsNotNone(written)
        rules = re.findall(r'"\$user ([^"]*)"', written.group(1))
        self.assertEqual(rules, ["ALL=(ALL) ALL", "ALL=(root) NOPASSWD: /opt/autoflash-emu/bin/wifi-root"])


class StubToTheApiTests(unittest.TestCase):
    """The stub serves its files in plaintext on the Docker network, where the
    readout API fetches an offered one (emu.sh's AUTOFLASH_LOCAL_OTA_BASE)."""

    def setUp(self):
        self.text = ENTRYPOINT.read_text(encoding="utf-8")

    def test_the_firewall_lets_the_api_reach_its_plaintext_port_with_the_stub_only(self):
        rule = re.search(r'(?m)^ *(.*)ufw allow in on eth0 to any port (\d+) proto tcp .*readout API', self.text)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.group(1), '[ "$STUB_CLOUD" = 1 ] && ')
        self.assertEqual(int(rule.group(2)), 80)
        self.assertNotIn("--plain-port", self.text, "the port the firewall allows is the stub's default")
        self.assertIn('"--plain-port", type=int, default=80)',
                      Path(stub_server.__file__).read_text(encoding="utf-8"))

    def test_its_port_is_none_the_tool_serves_files_on(self):
        self.assertNotIn(80, firewall.DELIVERY_PORTS + firewall.PHASE_PORTS)

    @unittest.skipIf(shutil.which("bash") is None, "no bash")
    def test_it_is_bound_to_the_docker_networks_address_alone(self):
        found = re.search(r'--plain-bind "\$\((ip -4 -o addr show dev eth0 \| (awk .*))\)"', self.text)
        self.assertIsNotNone(found)
        listed = "7: eth0@if8    inet 198.51.100.3/24 brd 198.51.100.255 scope global eth0\\       valid_lft forever\n"
        done = subprocess.run(["bash", "-c", found.group(2)], input=listed, capture_output=True, text=True,
                              timeout=60, check=True)
        self.assertEqual(done.stdout, "198.51.100.3\n")


if __name__ == "__main__":
    unittest.main()
