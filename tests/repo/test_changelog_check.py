"""tools/repo/changelog_check.py: a change to the tool with no changelog
change is warned of, unless its commit says it changes nothing seen."""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import changelog_check, leak_scan

GIT_ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")} | {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@unittest.skipIf(shutil.which("git") is None, "no git")
class ChangelogCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for patch in (mock.patch.dict(os.environ, GIT_ENV, clear=True), mock.patch.object(leak_scan, "ROOT", self.root)):
            patch.start()
            self.addCleanup(patch.stop)
        self.git("init", "-q")
        self.base = self.commit("start", "README.md")

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message, *files):
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(message, encoding="utf-8")
            self.git("add", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def since_base(self):
        return self.git("rev-list", f"{self.base}..HEAD").split()

    def test_a_change_to_the_tool_alone_is_named(self):
        tool = self.commit("Tool: a fix", "tools/autoflash/runner.py")
        self.commit("Tests: one more", "tests/autoflash/test_runner.py")
        self.assertEqual(changelog_check.unlisted(self.since_base()), [tool])

    def test_the_changelog_changed_anywhere_in_the_range_names_none(self):
        self.commit("Tool: a fix", "tools/autoflash/runner.py")
        self.commit("Changelog: the fix", "CHANGELOG.md")
        self.assertEqual(changelog_check.unlisted(self.since_base()), [])

    def test_a_commit_saying_it_changes_nothing_seen_is_not_named(self):
        self.commit("Tool: a rename\n\nChangelog: none", "tools/autoflash/runner.py")
        self.commit("Repo: a check", "tools/repo/lint.py")
        self.assertEqual(changelog_check.unlisted(self.since_base()), [])

    def test_a_push_is_warned_of_and_never_stopped(self):
        tool = self.commit("Tool: a fix", "tools/autoflash/runner.py")
        refs = f"refs/heads/main {tool} refs/heads/main {self.base}\n"
        warned = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(refs)), contextlib.redirect_stderr(warned):
            self.assertEqual(changelog_check.main(["push"]), 0)
        self.assertIn("Tool: a fix", warned.getvalue())
        self.assertIn("Changelog: none", warned.getvalue())


if __name__ == "__main__":
    unittest.main()
