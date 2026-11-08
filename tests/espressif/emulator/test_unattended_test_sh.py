"""unattended-test.sh takes --instance=N as emu.sh does, 1-99."""

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


if __name__ == "__main__":
    unittest.main()
