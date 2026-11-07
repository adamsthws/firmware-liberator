"""The workflows that keep things moving, as they are written: GitHub alone runs them."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


class DependabotMergeTests(unittest.TestCase):
    def setUp(self):
        self.text = workflow("dependabot-merge.yml")

    def test_one_behind_main_is_brought_up_to_date_and_checked_again(self):
        self.assertIn("update-branch", self.text)
        self.assertRegex(self.text, r'for workflow in emulator-check\.yml lint\.yml leak-scan\.yml; do\s+'
                                    r'gh workflow run -R "\$GITHUB_REPOSITORY" "\$workflow" --ref "\$branch"')

    def test_a_failure_on_an_old_main_is_not_held_against_it(self):
        failed = self.text[self.text.index("BLOCKED|UNSTABLE)"):]
        self.assertIn("brought_up_to_date", failed[:failed.index(";;")])

    def test_every_check_main_requires_is_run_again(self):
        from tools.repo import github_settings
        # Each required check is a job of one of the workflows started.
        started = "\n".join(workflow(name) for name in ("emulator-check.yml", "lint.yml", "leak-scan.yml"))
        for check in github_settings.REQUIRED_CHECKS:
            job = check.split(" (")[0]
            self.assertTrue(re.search(rf"(?m)^  {re.escape(job)}:$|name: {re.escape(check)}", started), check)


class MonthlyDryRunTests(unittest.TestCase):
    MONTHLY = "github.event.schedule == '0 6 1 * *'"

    def setUp(self):
        self.text = workflow("firmware-update.yml")

    def test_it_is_scheduled_and_is_a_dry_run(self):
        self.assertIn('cron: "0 6 1 * *"', self.text)
        self.assertIn(f"DRY_RUN: ${{{{ inputs.dry_run || {self.MONTHLY} }}}}", self.text)

    def test_it_takes_what_is_pinned_so_every_step_runs(self):
        self.assertIn('pinned_version(u.load_pins(), "esphome")', self.text)
        self.assertIn('pinned_version(u.load_pins(), "openbeken")', self.text)

    def test_it_merges_nothing_and_opens_no_issue(self):
        merge = self.text[self.text.index("\n  merge:"):]
        self.assertIn(self.MONTHLY.replace("==", "!="), merge.split("\n")[2])
        self.assertEqual(self.text.count(self.MONTHLY.replace("==", "!=")), 3)
