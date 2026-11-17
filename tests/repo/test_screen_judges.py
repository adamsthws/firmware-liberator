"""Every suite judges a screen alike: through unattended_run.screen_problems,
the one judge, so a rule added there holds for each chip family, for
conversions and readouts, attended and in the fault suites - none calls its
parts (the stage rules, the end of a run's, a row said twice) on its own,
where a new rule would pass it by."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JUDGE = ROOT / "tools/repo/unattended_run.py"
# The judge's parts: called from the judge alone.
PARTS = {"stage_problems", "end_problems", "repeated_rows"}
# The suites that judge a screen, and what each calls to.
SUITES = {"tools/espressif/emulator/attended_suite.py": "screen_problems",
          "tools/espressif/emulator/fault_suite.py": "screen_problems",
          "tools/beken/emulator/fault_suite.py": "screen_problems",
          "tools/repo/fault_verdicts.py": "screen_problems"}


def called(path: Path) -> set[str]:
    """The names of what `path` calls, as name() or something.name()."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


class ScreenJudgeTests(unittest.TestCase):
    def test_the_judges_parts_are_called_from_the_judge_alone(self):
        for path in sorted((ROOT / "tools").rglob("*.py")):
            if path == JUDGE:
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertFalse(called(path) & PARTS,
                                 "call unattended_run.screen_problems, which every suite shares")

    def test_every_suite_judges_through_the_one_judge(self):
        for name, judge in SUITES.items():
            with self.subTest(suite=name):
                self.assertIn(judge, called(ROOT / name))


if __name__ == "__main__":
    unittest.main()
