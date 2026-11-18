"""Every path into where the tool keeps what it records is made from
records.CAPTURES, the one place it is named: so AUTOFLASH_CAPTURES moves all
of it - the unit tests' to a folder of their own (tests/run.py), never your
captures/."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The checkout's captures/ named from its root, not records.CAPTURES.
NAMED = re.compile(r"""(?:\bROOT|\bREPO|\brepo|parents\[\d\])\s*/\s*["']captures["']""")
# Those that name it so, and why.
OWN = {
    "tools/autoflash/records.py": "where it is named, once",
    "tools/repo/leak_scan.py": "reads this checkout's own private records, to keep them out of its commits",
    "tools/ewelink/stub_cloud/make_ca.py": "run isolated (python -I) with no other module of the repository; "
                                           "emu.sh gives it --out-dir",
    "tools/espressif/safeboot/configure_recovery_safeboot.py": "the safeboot scripts also run from inside tools/, "
                                                                "where tools.autoflash is not a package; a default "
                                                                "output, for a person running one",
    "tools/espressif/safeboot/configure_imprintable_safeboot.py": "as configure_recovery_safeboot.py",
    "tools/espressif/safeboot/prepare_recovery_safeboot_migration.py": "as configure_recovery_safeboot.py",
    "tools/espressif/safeboot/prepare_imprinted_bundle.py": "as configure_recovery_safeboot.py",
}


class CapturesRootTests(unittest.TestCase):
    def test_every_path_into_it_is_made_from_records_captures(self):
        named = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tools").rglob("*.py")
                       if NAMED.search(path.read_text(encoding="utf-8")))
        self.assertEqual([path for path in named if path not in OWN], [],
                         "make it from records.CAPTURES, or say in OWN why not")
        self.assertEqual(sorted(set(OWN) - set(named)), [], "names it no longer: take it off OWN")


if __name__ == "__main__":
    unittest.main()
