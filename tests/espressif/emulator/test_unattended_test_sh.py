"""unattended-test.sh takes --instance=N as emu.sh does, 1-99; and --api keeps the API
Worker's own code in a container of its own beside the emulator."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "espressif" / "emulator" / "unattended-test.sh"


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class InstanceTests(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60)

    def test_up_to_99_is_taken(self):
        for instance in ("1", "30", "31", "99"):
            # --keep with --save is refused next, before anything is started.
            done = self.run_script(f"--instance={instance}", "--keep", "--save=x")
            self.assertEqual(done.returncode, 2, instance)
            self.assertIn("--save turns the plug off", done.stderr, instance)

    def test_others_are_refused(self):
        for instance in ("0", "05", "100", "x"):
            done = self.run_script(f"--instance={instance}", "--keep", "--save=x")
            self.assertEqual(done.returncode, 2, instance)
            self.assertIn("--instance=N", done.stdout, instance)  # the usage
            self.assertNotIn("--save turns the plug off", done.stderr, instance)


class ApiTests(unittest.TestCase):
    """Seen: run on this computer, under its Node, the API was out of reach of
    the emulated computer wherever a firewall turned the emulators' network
    away, and every readout-all scenario failed as if nothing had been sent."""

    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_the_api_runs_in_a_container_on_the_emulators_network(self):
        start = re.search(r'docker run -d --name "\$api_name" --network "\$network".*?\n(?:.*\\\n)*.*', self.text)
        self.assertIsNotNone(start, "the API's docker run")
        self.assertIn('"$API_IMAGE" node /website/api/local.mjs', start.group(0))

    def test_its_image_is_pinned_by_digest(self):
        self.assertRegex(self.text, r"(?m)^API_IMAGE=node:22\.\d+\.\d+-[a-z-]+@sha256:[0-9a-f]{64}$")

    def test_nothing_runs_node_on_this_computer(self):
        for line in self.text.splitlines():
            code = line.split("#", 1)[0]
            if re.search(r"(^|[\s\"'])(\$\{AUTOFLASH_NODE[^}]*\}|node)[\"']?\s+\S*local\.mjs", code):
                self.assertIn('"$API_IMAGE" node', code, line)


if __name__ == "__main__":
    unittest.main()
