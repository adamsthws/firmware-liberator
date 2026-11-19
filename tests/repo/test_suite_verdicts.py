"""Every verdict a fault suite can give is one docs/testing.md explains: the
Beken suite once said NO-STEP where the Espressif suite says NOT-FIRED, a
verdict no page named."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITES = ("tools/espressif/emulator/fault_suite.py", "tools/beken/emulator/fault_suite.py")
# A verdict as the suites write one: PASS, or upper-case words joined by hyphens.
VERDICT = re.compile(r'"((?:PASS|FAIL|ERROR|BRICK|LEFTOVER)|[A-Z]{2,}(?:-[A-Z]{2,})+)"')


def verdicts(path: Path) -> set[str]:
    return set(VERDICT.findall(path.read_text(encoding="utf-8")))


class VerdictsDocumentedTests(unittest.TestCase):
    def test_each_suites_verdicts_are_explained_in_the_testing_page(self):
        page = (ROOT / "docs/testing.md").read_text(encoding="utf-8")
        for suite in SUITES:
            found = verdicts(ROOT / suite)
            self.assertIn("NOT-FIRED", found, suite)
            self.assertEqual(sorted(v for v in found if not re.search(rf"\b{re.escape(v)}\b", page)), [],
                             f"{suite}: say what each means in docs/testing.md")

    def test_the_beken_suite_says_not_fired_as_the_espressif_one_does(self):
        self.assertNotIn("NO-STEP", verdicts(ROOT / SUITES[1]))


if __name__ == "__main__":
    unittest.main()
