import subprocess
import unittest
from pathlib import Path

WIFI = Path(__file__).resolve().parents[5] / "tools/espressif/emulator/container/bin/wifi"


class WifiUsageTests(unittest.TestCase):
    def test_usage_is_the_header_and_no_code(self):
        result = subprocess.run(["bash", str(WIFI), "no-such-command"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stdout.startswith("This computer's Wi-Fi, emulated."))
        self.assertTrue(result.stdout.endswith("wifi home            back to the home network\n"))


if __name__ == "__main__":
    unittest.main()
