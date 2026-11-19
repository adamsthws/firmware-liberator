"""What prune_captures.py removes, and above all what it never does."""

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.repo import prune_captures as prune


class PruneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.captures = Path(temp.name) / "captures"
        mock.patch.object(prune, "CAPTURES", self.captures).start()
        mock.patch.object(prune, "running_states", return_value=set()).start()
        self.addCleanup(mock.patch.stopall)

    def make(self, relative, old=True):
        path = self.captures / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * 100, encoding="utf-8")
        if old:
            then = time.time() - 30 * 86400
            os.utime(path, (then, then))
        return path

    def run_it(self, *argv):
        with redirect_stdout(io.StringIO()) as out:
            prune.main(list(argv))
        return out.getvalue()

    def test_without_delete_nothing_goes(self):
        flash = self.make("emulation/state-3/flash.bin")
        self.assertIn("would go", self.run_it())
        self.assertTrue(flash.exists())

    def test_an_old_emulator_state_and_build_cache_go(self):
        state = self.make("emulation/state-3/work/tools/x.py")
        build = self.make("esphome-build/cache.o")
        self.run_it("--delete")
        self.assertFalse(state.exists())
        self.assertFalse(build.exists())

    def test_a_beken_emulators_old_state_and_earlier_tree_go(self):
        made = [self.make("emulation/beken/state/flash.bin"), self.make("emulation/beken/state-2/work/x.py"),
                self.make("emulation/beken/legacy/v2.3.0/README.md")]
        self.run_it("--delete")
        for path in made:
            self.assertFalse(path.exists(), path)

    def test_a_backup_is_kept_wherever_it_is(self):
        backup = self.make("emulation/state-3/work/captures/devices/12-34-56-ab-cd-ef/sonoff-backup/flash.bin")
        other = self.make("emulation/state-3/flash.bin")
        self.run_it("--delete")
        self.assertTrue(backup.exists())
        self.assertFalse(other.exists())

    def test_what_is_not_listed_is_never_touched(self):
        kept = [self.make("devices/12-34-56-ab-cd-ef/sonoff-backup/flash.bin"), self.make("emulation/saved/suite-esphome/flash.bin"),
                self.make("emulation/beken/firmwares/dump.bin"),
                self.make("emulation/beken/saved/openbeken/flash.bin"), self.make("settings.json"),
                self.make("emulation/public-dumps/d.bin")]
        self.run_it("--delete")
        for path in kept:
            self.assertTrue(path.exists(), path)

    def test_recently_used_stays(self):
        recent = self.make("emulation/state-4/flash.bin", old=False)
        self.run_it("--delete")
        self.assertTrue(recent.exists())

    def test_a_running_emulators_state_stays(self):
        state = self.make("emulation/state-2/flash.bin")
        with mock.patch.object(prune, "running_states", return_value={self.captures / "emulation/state-2"}):
            self.run_it("--delete")
        self.assertTrue(state.exists())

    def test_where_docker_cannot_say_no_emulator_state_goes(self):
        state = self.make("emulation/state-2/flash.bin")
        logs = self.make("emulation/beken/suite/logs/run.log")
        with mock.patch.object(prune, "running_states", return_value=None), \
                redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            prune.main(["--delete"])
        self.assertTrue(state.exists())
        self.assertFalse(logs.exists())

    def test_a_suites_results_stay_with_only_its_logs_gone(self):
        results = self.make("emulation/beken/suite/results.jsonl")
        log = self.make("emulation/beken/suite/logs/x/tool.log")
        self.run_it("--delete")
        self.assertTrue(results.exists())
        self.assertFalse(log.exists())

    def test_what_lies_inside_another_is_counted_once(self):
        self.make("emulation/state-6/logs/run.log")
        listed = [path for path, _what in prune.candidates(self.captures)]
        self.assertEqual(listed, [self.captures / "emulation/state-6"])

    def test_a_read_only_folder_is_no_bar(self):
        locked = self.make("emulation/state-5/work/.claude/worktrees/a/toolchain/gcc")
        locked.parent.chmod(0o555)
        self.addCleanup(lambda: locked.parent.chmod(0o755) if locked.parent.exists() else None)
        self.run_it("--delete")
        self.assertFalse(locked.exists())


class TreeTests(unittest.TestCase):
    """Old checkouts beside this one, as --trees clears them."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        mock.patch.object(prune, "CAPTURES", self.folder / "ours/captures").start()
        mock.patch.object(prune, "running_states", return_value=set()).start()
        mock.patch.object(prune, "containers", return_value=[]).start()
        mock.patch.object(prune, "claimed_checkouts", return_value=set()).start()
        self.addCleanup(mock.patch.stopall)

    def make(self, relative, old=True):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * 100, encoding="utf-8")
        if old:
            then = time.time() - 30 * 86400
            os.utime(path, (then, then))
        return path

    def tree(self, name, old=True):
        self.make(f"{name}/{prune.CHECKOUT_MARK}", old)
        return self.folder / name

    def run_it(self, *argv, changed=None):
        # A folder's own time counts as its files' do: each made here is old,
        # but one `changed`.
        then = time.time() - 30 * 86400
        for directory, _dirs, _files in os.walk(self.folder):
            if Path(directory) != changed:
                os.utime(directory, (then, then))
        with redirect_stdout(io.StringIO()) as out, mock.patch("sys.stderr", io.StringIO()) as err:
            prune.main(["--trees", str(self.folder), *argv])
        return out.getvalue() + err.getvalue()

    def test_without_delete_nothing_goes(self):
        self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        self.assertIn("would go", self.run_it())
        self.assertTrue(state.exists())

    def test_an_old_checkout_is_cleared_to_what_cannot_be_made_again(self):
        self.tree("old")
        gone = [self.make("old/captures/emulation/state-3/flash.bin"), self.make("old/tools/x.py"),
                self.make("old/captures/emulation/attended/logs/run.log")]
        kept = [self.make("old/captures/emulation/state-3/work/captures/devices/12-34-56-ab-cd-ef/sonoff-backup/flash.bin"),
                self.make("old/captures/devices/12-34-56-ab-cd-ef/sonoff-backup/flash.bin"),
                self.make("old/captures/devices/12-34-56-ab-cd-ef/device.json"),
                self.make("old/captures/emulation/beken/suite/results.jsonl"),
                self.make("old/.git/HEAD"), self.make("old/.env")]
        self.run_it("--delete")
        for path in gone:
            self.assertFalse(path.exists(), path)
        for path in kept:
            self.assertTrue(path.exists(), path)
        self.assertEqual(prune.backups(self.folder / "old"), 2)

    def test_a_link_goes_and_what_it_points_to_stays(self):
        elsewhere = self.make("elsewhere/captures/emulation/saved/x/flash.bin")
        self.tree("old")
        link = self.folder / "old/captures"
        link.symlink_to(self.folder / "elsewhere/captures")
        then = time.time() - 30 * 86400
        os.utime(link, (then, then), follow_symlinks=False)
        self.run_it("--delete")
        self.assertFalse(link.is_symlink())
        self.assertTrue(elsewhere.exists())

    def test_a_recent_checkout_stays(self):
        self.tree("recent")
        state = self.make("recent/captures/emulation/state-3/flash.bin", old=False)
        self.run_it("--delete")
        self.assertTrue(state.exists())

    def test_a_folder_changed_lately_is_a_change(self):
        # A file just removed shows only in its folder's time.
        self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        self.make("old/captures/emulation/state-4/flash.bin").unlink()
        self.run_it("--delete", changed=self.folder / "old/captures/emulation/state-4")
        self.assertTrue(state.exists())

    def test_days_is_how_long_unchanged(self):
        self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        self.run_it("--delete", "--days", "40")
        self.assertTrue(state.exists())

    def test_this_checkout_is_never_one_of_them(self):
        ours = self.tree("ours")
        with mock.patch.object(prune, "ROOT", ours):
            self.assertEqual(prune.checkouts(self.folder), [])

    def test_what_is_not_a_checkout_is_never_touched(self):
        other = self.make("project/captures/emulation/state-3/flash.bin")
        self.run_it("--delete")
        self.assertTrue(other.exists())

    def test_a_checkout_an_emulator_runs_from_stays(self):
        tree = self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        running = [{"image": "sha256:a", "running": True, "mounts": [tree / "captures/emulation/state-3"]}]
        with mock.patch.object(prune, "containers", return_value=running):
            self.run_it("--delete")
        self.assertTrue(state.exists())

    def test_a_checkout_a_suite_claims_instances_for_stays(self):
        tree = self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        with mock.patch.object(prune, "claimed_checkouts", return_value={tree.resolve()}):
            self.run_it("--delete")
        self.assertTrue(state.exists())

    def test_where_docker_cannot_say_no_checkout_is_cleared(self):
        self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        with mock.patch.object(prune, "containers", return_value=None):
            self.assertIn("left alone", self.run_it("--delete"))
        self.assertTrue(state.exists())

    def test_a_checkout_with_changes_not_committed_stays(self):
        tree = self.tree("old")
        state = self.make("old/captures/emulation/state-3/flash.bin")
        with mock.patch.object(prune, "uncommitted", return_value=True):
            self.assertIn("not committed", self.run_it("--delete"))
        self.assertTrue(state.exists())
        self.assertTrue((tree / prune.CHECKOUT_MARK).exists())

    def test_a_git_worktree_is_never_cleared(self):
        # Its repository would still count it, with every file in it deleted.
        tree = self.tree("worktree")
        self.make("worktree/.git")   # a worktree's .git is a file
        state = self.make("worktree/captures/emulation/state-3/flash.bin")
        with mock.patch.object(prune, "uncommitted", return_value=False), \
                mock.patch.object(prune, "on_main", return_value=False):
            said = self.run_it("--delete")
        self.assertTrue(state.exists())
        self.assertTrue((tree / prune.CHECKOUT_MARK).exists())
        self.assertNotIn("git worktree remove", said)

    def test_a_finished_worktree_is_named_with_the_command_that_removes_it(self):
        self.tree("done")
        self.make("done/.git")
        self.make("done/tools/x.py")
        self.tree("kept")
        self.make("kept/.git")
        self.make("kept/captures/devices/12-34-56-ab-cd-ef/sonoff-backup/flash.bin")
        with mock.patch.object(prune, "uncommitted", return_value=False), \
                mock.patch.object(prune, "on_main", return_value=True):
            said = self.run_it("--delete")
        self.assertIn(f"git worktree remove {self.folder / 'done'}", said)
        self.assertNotIn(f"git worktree remove {self.folder / 'kept'}", said)
        self.assertTrue((self.folder / "done/tools/x.py").exists())

    def test_git_tells_a_commit_on_main(self):
        tree = self.folder / "repo"
        git = ["git", "-C", str(tree), "-c", "user.name=x", "-c", "user.email=x@example.com",
               "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(tree)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "one"], check=True)
        self.assertTrue(prune.on_main(tree))
        subprocess.run([*git, "checkout", "-q", "-b", "side"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "two"], check=True)
        self.assertFalse(prune.on_main(tree))

    def test_git_tells_changes_not_committed(self):
        tree = self.tree("old")
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        self.assertTrue(prune.uncommitted(tree))
        self.assertFalse(prune.uncommitted(self.folder / "not-git"))

    def test_once_enough_is_found_no_more_is_looked_at(self):
        # As a prep's suggestion needs: walking old checkouts can take minutes.
        for name in ("a", "b"):
            self.tree(name)
            self.make(f"{name}/captures/emulation/state-3/flash.bin")
        self.run_it()
        items, _notes = prune.plan(None, self.folder, False, enough=1)
        self.assertEqual([item.name for item in items], [str(self.folder / "a")])

    def test_a_cleared_checkout_is_not_listed_again(self):
        self.tree("old")
        self.make("old/captures/emulation/state-3/flash.bin")
        self.run_it("--delete")
        self.assertNotIn("would go", self.run_it())


class ImageTests(unittest.TestCase):
    """Old emulator images, as --images removes them."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        mock.patch.object(prune, "CAPTURES", Path(temp.name) / "captures").start()
        mock.patch.object(prune, "running_states", return_value=set()).start()
        mock.patch.object(prune, "claimed_checkouts", return_value=set()).start()
        mock.patch.object(prune, "current_tags", return_value={"current-espressif", "current-beken"}).start()
        self.addCleanup(mock.patch.stopall)
        self.images = {
            "sha256:now": (["autoflash-emu:latest"], "current-espressif"),
            "sha256:old": (["autoflash-emu-stub:latest", "ghcr.io/adamsthws/autoflash-emu:old"], "old"),
            "sha256:oldbeken": (["ghcr.io/adamsthws/autoflash-beken-emu:old2"], "old2"),
            "sha256:used": (["autoflash-beken-emu:x"], "old3"),
            "sha256:win": (["autoflash-win:latest"], "windows"),
            "sha256:other": (["python:3.10"], None),
            "sha256:dangling": ([], "old4"),
        }
        self.removed = []
        mock.patch.object(prune, "containers",
                          return_value=[{"image": "sha256:used", "running": False, "mounts": []}]).start()
        mock.patch.object(prune, "docker", side_effect=self.docker).start()

    def docker(self, *args):
        if args[0] == "images":
            return "\n".join(self.images)
        if args[:2] == ("image", "inspect"):
            return "\n".join("\t".join(json.dumps(v) for v in (image, 1000, names, {prune.LABEL: label} if label else None))
                             for image, (names, label) in self.images.items() if image in args)
        if args[0] == "rmi":
            self.removed.append(args[1])
            return ""
        raise AssertionError(args)

    def run_it(self, *argv):
        with redirect_stdout(io.StringIO()) as out, mock.patch("sys.stderr", io.StringIO()) as err:
            prune.main(["--images", *argv])
        return out.getvalue() + err.getvalue()

    def test_without_delete_nothing_goes(self):
        listed = self.run_it()
        self.assertIn("autoflash-emu-stub:latest", listed)
        self.assertEqual(self.removed, [])

    def test_only_old_emulator_images_no_container_uses_go(self):
        self.run_it("--delete")
        self.assertEqual(sorted(self.removed), ["autoflash-emu-stub:latest", "ghcr.io/adamsthws/autoflash-beken-emu:old2",
                                                "ghcr.io/adamsthws/autoflash-emu:old"])

    def test_where_this_checkouts_tag_is_unknown_none_goes(self):
        with mock.patch.object(prune, "current_tags", return_value=None):
            self.assertIn("left alone", self.run_it("--delete"))
        self.assertEqual(self.removed, [])

    def test_where_docker_cannot_say_none_goes(self):
        with mock.patch.object(prune, "containers", return_value=None):
            self.run_it("--delete")
        self.assertEqual(self.removed, [])

    def test_a_checkout_in_use_keeps_its_images(self):
        in_use = Path("/elsewhere")
        with mock.patch.object(prune, "claimed_checkouts", return_value={in_use}):
            self.run_it()
        self.assertIn(in_use, prune.current_tags.call_args.args[0])



class CurrentTagsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def tree(self, name, *tags):
        for script, tag in zip(prune.IMAGE_SH, tags):
            path = self.folder / name / script
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"#!/bin/sh\n[ \"$1\" = tag ] && echo {tag}\n", encoding="utf-8")
            path.chmod(0o755)
        return self.folder / name

    def test_each_familys_in_each_checkout(self):
        ours, other = self.tree("ours", "a", "b"), self.tree("other", "c", "d")
        with mock.patch.object(prune, "ROOT", ours):
            self.assertEqual(prune.current_tags([ours, other]), {"a", "b", "c", "d"})

    def test_unknown_where_this_checkouts_cannot_say(self):
        ours = self.tree("ours", "a")
        with mock.patch.object(prune, "ROOT", ours):
            self.assertIsNone(prune.current_tags([ours]))

    def test_another_checkout_that_cannot_say_adds_none(self):
        ours, other = self.tree("ours", "a", "b"), self.folder / "gone"
        with mock.patch.object(prune, "ROOT", ours):
            self.assertEqual(prune.current_tags([ours, other]), {"a", "b"})


class SuggestTests(unittest.TestCase):
    """The one line a suite's prep prints."""

    def items(self, size):
        return [prune.Item("x", "y", size, lambda: (0, 0))], []

    def test_much_to_free_is_said_with_the_command(self):
        with mock.patch.object(prune, "plan", return_value=self.items(prune.HINT_AT)), \
                redirect_stdout(io.StringIO()) as out:
            prune.suggest()
        self.assertIn(f"prune_captures.py --trees {prune.ROOT.parent} --images", out.getvalue())

    def test_little_is_not_mentioned(self):
        with mock.patch.object(prune, "plan", return_value=self.items(prune.HINT_AT - 1)), \
                redirect_stdout(io.StringIO()) as out:
            prune.suggest()
        self.assertEqual(out.getvalue(), "")
