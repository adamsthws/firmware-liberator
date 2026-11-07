"""device_identity.py, the plug's identity the stub reads from its dump."""

import subprocess
import sys
import unittest
from pathlib import Path

STUB = Path(__file__).resolve().parents[3] / "tools" / "ewelink" / "stub_cloud"


class RunIsolatedTests(unittest.TestCase):
    """Run as the emulator runs it: in isolated mode (-I), where only its own
    path puts the repository on sys.path."""

    def test_it_imports_the_repository(self):
        done = subprocess.run([sys.executable, "-I", str(STUB / "device_identity.py"), "--help"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("usage:", done.stdout)


if __name__ == "__main__":
    unittest.main()
