"""A tree of this repository on another machine (tools/repo/remote_tree.py):
what is sent, the instances a command is given, what is fetched before a
tree is removed, and that a real device's backup outlives the tree. The scripts it runs there are run here, in a home folder
of the test's own."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import remote_tree


class NameTests(unittest.TestCase):
    def test_a_tree_is_one_folder_under_the_trees(self):
        self.assertEqual(remote_tree.tree("try-1"), "autoflash-trees/try-1")
        for name in ("", ".repo.git", "..", "a/b", "-x", " x"):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                remote_tree.tree(name)


class SendTests(unittest.TestCase):
    def test_the_newest_commits_are_asked_about_then_ever_further_back(self):
        commits = [f"c{n}" for n in range(20)]
        self.assertEqual(remote_tree.probes(commits), ["c0", "c1", "c3", "c7", "c15", "c19"])
        self.assertEqual(remote_tree.probes(["c0"]), ["c0"])
        self.assertEqual(remote_tree.probes([]), [])

    def test_only_what_the_other_machine_lacks_is_bundled(self):
        probed = ["c0", "c1", "c3", "c7"]
        self.assertEqual(remote_tree.bundle_range("HEAD", probed, {"c3", "c7"}), ["c3..HEAD"])
        self.assertEqual(remote_tree.bundle_range("HEAD", probed, set()), ["HEAD"])

    def test_a_commit_the_other_machine_has_is_bundled_from_one_it_had_before(self):
        # From the commit itself, the bundle held no commit, and so not the
        # ref: a second tree of the same commit could not be sent.
        probed = ["c0", "c1", "c3"]
        self.assertEqual(remote_tree.bundle_range("HEAD", probed, {"c0", "c1", "c3"}), ["c1..HEAD"])
        self.assertEqual(remote_tree.bundle_range("HEAD", ["c0"], {"c0"}), ["HEAD"])


@unittest.skipUnless(shutil.which("git") and shutil.which("sh"), "needs git and sh")
class SentTreeTests(unittest.TestCase):
    """send, to a home of the test's own, from a repository of its own."""

    def setUp(self):
        top = tempfile.TemporaryDirectory()
        self.addCleanup(top.cleanup)
        self.home = Path(top.name) / "home"
        self.home.mkdir()
        self.repo = Path(top.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.commit("one")
        self.git("tag", "-a", "-m", "release", "v1")
        self.commit("two")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                              capture_output=True, text=True, check=True).stdout

    def commit(self, text):
        (self.repo / "file.txt").write_text(text, encoding="utf-8")
        self.git("add", "file.txt")
        self.git("commit", "-q", "-m", text)

    def send(self, name="t", env=None):
        home = self.home
        env = {**os.environ, "HOME": str(home), **(env or {})}

        class Here(remote_tree.Remote):
            def run(self, script, *args, stdin=None, check=True, capture=True):
                done = subprocess.run(["sh", "-c", script, "-", *args], cwd=home, input=stdin, capture_output=True,
                                      env=env, timeout=60, check=False)
                if check and done.returncode:
                    raise AssertionError(done.stderr.decode(errors="replace"))
                return done

        with mock.patch.object(remote_tree, "ROOT", self.repo), mock.patch("builtins.print"):
            remote_tree.send(Here("here", None), name, "main")

    def archived(self, tag):
        return subprocess.run(["git", "-C", str(self.home / remote_tree.tree("t")), "archive", tag],
                              capture_output=True, check=False).returncode == 0

    def test_a_tree_holds_the_release_tags_its_suites_build_from(self):
        self.send()
        self.assertTrue(self.archived("v1"))
        self.git("tag", "-a", "-m", "release", "v2")
        self.commit("three")
        self.send()
        self.assertTrue(self.archived("v2"), "a tag made since the tree was sent")
        self.assertEqual((self.home / remote_tree.tree("t") / "file.txt").read_text(encoding="utf-8"), "three")

    def test_a_commit_sent_before_can_be_sent_as_another_tree(self):
        self.send()
        self.send("u")
        self.assertEqual((self.home / remote_tree.tree("u") / "file.txt").read_text(encoding="utf-8"), "two")

    def test_a_lightweight_tag_on_a_commit_sent_before_reaches_the_tree(self):
        # As this repository's release tags are: a bundle of what the other
        # machine lacks leaves out a tag on a commit it has, and
        # update_firmware.py then took every build there for one released.
        self.send()
        self.git("tag", "v2")
        self.commit("three")
        self.send()
        self.assertTrue(self.archived("v2"))

    def with_setup_dev(self, can_make_a_venv):
        """The repository given a setup-dev that notes each time it runs; the
        environment to send it with, its python3 able to make a venv or not."""
        (self.repo / "tools/repo").mkdir(parents=True)
        (self.repo / "requirements").mkdir()
        setup_dev = self.repo / "tools/repo/setup-dev"
        setup_dev.write_text('#!/bin/sh\nmkdir -p .venv/bin && echo ran >> "$HOME/setup-dev.log"\n',
                             encoding="utf-8")
        setup_dev.chmod(0o755)
        (self.repo / "requirements/ci.txt").write_text("one==1\n", encoding="utf-8")
        self.git("add", "tools/repo/setup-dev", "requirements/ci.txt")
        self.commit("with setup-dev")
        bin_dir = self.home.parent / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3").write_text(f"#!/bin/sh\nexit {0 if can_make_a_venv else 1}\n", encoding="utf-8")
        (bin_dir / "python3").chmod(0o755)
        return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def setup_dev_runs(self):
        log = self.home / "setup-dev.log"
        return len(log.read_text(encoding="utf-8").split()) if log.exists() else 0

    def test_a_tree_gets_its_venv_made_again_only_when_its_requirements_change(self):
        # Without it, the Espressif emulator check stopped in a tree for
        # want of the checkout's .venv, made by hand on each machine.
        env = self.with_setup_dev(can_make_a_venv=True)
        self.send(env=env)
        self.assertEqual(self.setup_dev_runs(), 1)
        self.assertTrue((self.home / remote_tree.tree("t") / ".venv/bin").is_dir())
        self.commit("three")
        self.send(env=env)
        self.assertEqual(self.setup_dev_runs(), 1, "made again with nothing it is made from changed")
        (self.repo / "requirements/ci.txt").write_text("one==2\n", encoding="utf-8")
        self.git("add", "requirements/ci.txt")
        self.commit("four")
        self.send(env=env)
        self.assertEqual(self.setup_dev_runs(), 2)

    def test_a_machine_whose_python_cannot_make_a_venv_is_told_what_to_install(self):
        env = self.with_setup_dev(can_make_a_venv=False)
        with self.assertRaisesRegex(AssertionError, "python3-venv"):
            self.send(env=env)
        self.assertEqual(self.setup_dev_runs(), 0)

    def refs(self):
        return subprocess.run(["git", "-C", str(self.home / remote_tree.REPO), "for-each-ref", "--format=%(refname)",
                               "refs/trees/"], capture_output=True, text=True, check=True).stdout.split()

    def test_removing_a_tree_removes_its_ref_and_no_other(self):
        # The ref left behind held every commit a tree was ever sent at.
        self.send()
        self.send("u")
        done = subprocess.run(["sh", "-c", remote_tree.REMOVE, "-", remote_tree.tree("t")], cwd=self.home,
                              capture_output=True, text=True, env={**os.environ, "HOME": str(self.home)},
                              timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.home / remote_tree.tree("t")).exists())
        self.assertEqual(self.refs(), ["refs/trees/u"])

    def test_removing_a_tree_already_gone_removes_its_ref(self):
        self.send()
        shutil.rmtree(self.home / remote_tree.tree("t"))
        done = subprocess.run(["sh", "-c", remote_tree.REMOVE, "-", remote_tree.tree("t")], cwd=self.home,
                              capture_output=True, text=True, env={**os.environ, "HOME": str(self.home)},
                              timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.refs(), [])

    def test_a_commit_without_setup_dev_is_sent_without_a_venv(self):
        self.send()
        self.assertFalse((self.home / remote_tree.tree("t") / ".venv").exists())


class InstanceTests(unittest.TestCase):
    def test_first_is_the_first_free_instance(self):
        self.assertEqual(remote_tree.first_instance(["suite", "--first-instance", "{first}"], 31),
                         ["suite", "--first-instance", "31"])

    def test_first_named_with_none_chosen_is_refused(self):
        with self.assertRaises(SystemExit):
            remote_tree.first_instance(["suite", "--first-instance", "{first}"], None)
        self.assertEqual(remote_tree.first_instance(["python3", "tests/run.py"], None), ["python3", "tests/run.py"])

    def test_run_takes_its_options_before_the_command_and_the_command_whole(self):
        with mock.patch.object(remote_tree, "free", return_value=7) as free, \
                mock.patch.object(remote_tree, "run", return_value=0) as run:
            self.assertEqual(remote_tree.main(["host", "run", "t", "--family", "beken", "--emulators", "2", "--",
                                               "suite", "--first-instance", "{first}", "--detach"]), 0)
        self.assertEqual(free.call_args.args[1:], ("t", "beken", 2))
        self.assertEqual(run.call_args.args[1:], ("t", "suite --first-instance 7 --detach", False))

    def test_each_family_has_a_prep(self):
        for family, command in remote_tree.PREP.items():
            with self.subTest(family=family):
                self.assertIn("{first}", command)
                self.assertIn(family, command)
                self.assertIn(family, remote_tree.PREP_EMULATORS)


@unittest.skipUnless(shutil.which("rsync") and shutil.which("sh"), "needs sh and rsync")
class BackupTests(unittest.TestCase):
    """The scripts run as the other machine would, in a home of the test's own."""

    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        self.source = self.home / "other/captures/devices/12-34-56-ab-cd-ef"
        (self.source / "sonoff-backup").mkdir(parents=True)
        (self.source / "sonoff-backup/flash.bin").write_bytes(b"stock")
        (self.source / "device.json").write_text("{}", encoding="utf-8")

    def there(self, script, *args):
        return subprocess.run(["sh", "-c", script, "-", *args], cwd=self.home, capture_output=True, text=True,
                              env={**os.environ, "HOME": str(self.home)}, timeout=30, check=False)

    def copied(self):
        tree = remote_tree.tree("t")
        done = self.there(remote_tree.DEVICES, "other/captures/devices", tree, "12-34-56-ab-cd-ef")
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.home / tree / "captures/devices/12-34-56-ab-cd-ef"

    def test_a_devices_backup_is_linked_and_the_rest_copied(self):
        copy = self.copied()
        self.assertTrue((copy / "sonoff-backup/flash.bin").samefile(self.source / "sonoff-backup/flash.bin"))
        self.assertFalse((copy / "device.json").samefile(self.source / "device.json"))
        self.assertEqual(sorted(p.name for p in self.source.iterdir()), ["device.json", "sonoff-backup"],
                         "nothing is written where it is copied from")

    def test_a_device_already_in_the_tree_is_left_as_it_is(self):
        copy = self.copied()
        (copy / "device.json").write_text("mine", encoding="utf-8")
        self.copied()
        self.assertEqual((copy / "device.json").read_text(encoding="utf-8"), "mine")

    def test_removing_the_tree_keeps_a_backup_it_holds_the_only_copy_of(self):
        copy = self.copied()
        made_there = copy.parent / "12-34-56-ab-cd-01/sonoff-backup/flash.bin"
        made_there.parent.mkdir(parents=True)
        made_there.write_bytes(b"made in the tree")
        (copy.parents[2] / "run.log").write_text("output", encoding="utf-8")
        done = self.there(remote_tree.REMOVE, remote_tree.tree("t"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(made_there.exists())
        self.assertIn(str(made_there.relative_to(self.home)), done.stdout)
        self.assertFalse((copy / "sonoff-backup").exists(), "a linked backup's link goes")
        self.assertEqual((self.source / "sonoff-backup/flash.bin").read_bytes(), b"stock")
        self.assertFalse((copy.parents[2] / "run.log").exists())

    def test_removing_the_tree_deletes_an_emulated_devices_backup(self):
        # Those, each the only copy, were left on the machines for good: the
        # backups the tool took in an emulator, the saved starts', and the
        # test devices made from public dumps.
        self.copied()
        captures = self.home / remote_tree.tree("t") / "captures"
        emulated = [captures / "emulation/state-81/work/captures/devices/12-34-56-e3-2d-01/sonoff-backup/flash.bin",
                    captures / "emulation/saved/start/captures/devices/12-34-56-e3-2d-01/sonoff-backup/flash.bin",
                    captures / "emulation/beken/state-1/work/captures/devices/12-34-56-e3-2d-09/sonoff-backup/a.rbl",
                    captures / "devices/12-34-56-e3-2d-01/sonoff-backup/flash.bin"]
        real = captures / "devices/12-34-56-ab-cd-01/sonoff-backup/flash.bin"
        for path in (*emulated, real):
            path.parent.mkdir(parents=True)
            path.write_bytes(b"a backup")
            path.chmod(0o400)
        (captures / "devices/12-34-56-e3-2d-01/device.json").write_text(
            '{\n  "mac": "12:34:56:e3:2d:01",\n  "test_copy_of": "https://example.com/dump.bin"\n}\n', encoding="utf-8")
        (real.parents[1] / "device.json").write_text('{"mac": "12:34:56:ab:cd:01", "test_copy_of": ""}',
                                                     encoding="utf-8")
        done = self.there(remote_tree.REMOVE, remote_tree.tree("t"))
        self.assertEqual(done.returncode, 0, done.stderr)
        for path in emulated:
            with self.subTest(path=path):
                self.assertFalse(path.exists())
        self.assertTrue(real.exists(), "a backup not an emulated device's is kept")
        self.assertEqual(done.stdout.count("sonoff-backup/"), 1, done.stdout)
        self.assertEqual(sorted(p.name for p in (self.home / remote_tree.tree("t")).rglob("*") if p.is_file()),
                         ["flash.bin"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root deletes what it cannot write")
    def test_a_tree_not_wholly_deleted_says_so(self):
        copy = self.copied()
        stuck = copy / "stuck"
        stuck.mkdir()
        (stuck / "file").write_text("x", encoding="utf-8")
        stuck.chmod(0o500)
        self.addCleanup(stuck.chmod, 0o700)
        done = self.there(remote_tree.REMOVE, remote_tree.tree("t"))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("stuck/file", done.stderr)

    def test_a_tree_still_running_is_not_removed(self):
        self.copied()
        runs = self.home / "autoflash-trees/t.runs"
        runs.mkdir()
        (runs / "1.log.pid").write_text(str(os.getpid()), encoding="utf-8")
        done = self.there(remote_tree.REMOVE, remote_tree.tree("t"))
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue((self.home / "autoflash-trees/t/captures/devices/12-34-56-ab-cd-ef/device.json").exists())

    def test_two_runs_started_together_keep_their_own_log_and_exit(self):
        self.copied()
        started = [subprocess.Popen(["sh", "-c", remote_tree.START, "-", remote_tree.tree("t"), command],
                                    cwd=self.home, stdout=subprocess.PIPE, text=True,
                                    env={**os.environ, "HOME": str(self.home)})
                   for command in ("echo one; (exit 3)", "echo two")]
        logs = [Path(run.communicate(timeout=30)[0].strip()) for run in started]
        self.assertEqual(len(set(logs)), 2, "two runs in the same second, one log")
        for log, said, code in zip(logs, ("one", "two"), ("3", "0")):
            exit_file = Path(f"{log}.exit")
            for _ in range(300):
                if exit_file.exists() and exit_file.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.1)
            self.assertEqual(log.read_text(encoding="utf-8").strip(), said)
            self.assertEqual(exit_file.read_text(encoding="utf-8").strip(), code)


@unittest.skipUnless(shutil.which("rsync"), "needs rsync")
class FetchTests(unittest.TestCase):
    """What a fetch brings back of a tree's captures/emulation/ (FETCHED)."""

    KEPT = ("suite/results.jsonl", "suite/logs/a_K1_crash-1/run-1.log", "suite/logs/a_K1_crash-1/run-1.raw",
            "beken/suite/results.jsonl", "beken/suite/logs/b/serial.log", "device-check/sonoff-s60/1-tasmota.log",
            "attended/20261120-120000/s/api/api.sqlite", "state-81/logs/uart.log", "state-81/stub-reported.json",
            "state-81/work/captures/devices/12-34-56-ab-cd-ef/runs/20261120-120000/run.log",
            "state-81/work/captures/devices/12-34-56-ab-cd-ef/runs/20261120-120000/events.jsonl",
            "state-81/work/captures/unattended/20261120-120000/screen.txt", "beken/state-2/serial.log",
            "beken/state-2/work/captures/devices/12-34-56-ab-cd-ef/runs/20261120-120000/outcomes.jsonl")
    LEFT = ("state-81/flash.bin", "state-81/work/tools/autoflash/__init__.py",
            "state-81/work/captures/devices/12-34-56-ab-cd-ef/sonoff-backup/flash.bin",
            "state-81/work/captures/safeboot-recovery-migration/tasmota32c3.bin",
            "state-81/work/captures/esphome-build/x/firmware.elf", "beken/state-2/flash.bin",
            "beken/state-2/leaf.key", "beken/state-2/offered.fota", "beken/state-2/work/README.md",
            "saved/stub-suite-tasmota/flash.bin", "saved/stub-suite-tasmota/notes.txt", "legacy/v1/README.md",
            "public-dumps/Sonoff-Powr316D.bin", "beken/saved/esphome/serial.log", "beken/firmwares/x.txt",
            "beken/rebuilt/s.bin", "stub-cloud/ca.crt", "cache/x.json", "kept/other/suite/results.jsonl",
            "suite/logs/r-api/readouts__2026-11-17__6f8f.tar")

    def test_what_is_worth_keeping_comes_back_and_what_is_made_again_does_not(self):
        top = tempfile.TemporaryDirectory()
        self.addCleanup(top.cleanup)
        source, copy = Path(top.name, "emulation"), Path(top.name, "kept")
        for name in (*self.KEPT, *self.LEFT):
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            (source / name).write_text(name, encoding="utf-8")
        subprocess.run(["rsync", "-a", "--prune-empty-dirs", *remote_tree.FETCHED, f"{source}/", f"{copy}/"],
                       check=True)
        self.assertEqual(sorted(str(p.relative_to(copy)) for p in copy.rglob("*") if p.is_file()), sorted(self.KEPT))

    def fetch_from(self, files):
        """fetch, of tree t holding `files` in a home of the test's own, the
        other machine's ssh a script that runs rsync's other end there."""
        top = tempfile.TemporaryDirectory()
        self.addCleanup(top.cleanup)
        home, captures = Path(top.name, "home"), Path(top.name, "captures")
        home.mkdir()
        for name in files:
            (home / name).parent.mkdir(parents=True, exist_ok=True)
            (home / name).write_text(name, encoding="utf-8")
        ssh = Path(top.name, "ssh")
        ssh.write_text(f'#!/bin/sh\nshift\ncd "{home}" && exec sh -c "$*"\n', encoding="utf-8")
        ssh.chmod(0o755)

        class There(remote_tree.Remote):
            def text(self, script, *args):
                return subprocess.run(["sh", "-c", script, "-", *args], cwd=home, capture_output=True, text=True,
                                      timeout=30, check=True).stdout

        remote = There("host", None)
        remote.rsh = str(ssh)
        with mock.patch.object(remote_tree, "captures", return_value=captures), mock.patch("builtins.print"):
            folder = remote_tree.fetch(remote, "t")
        return folder, captures / "emulation" / remote_tree.scenario_times.KEPT

    def test_a_fetch_brings_back_the_results_and_the_commands_output(self):
        folder, kept = self.fetch_from(["autoflash-trees/t/captures/emulation/suite/results.jsonl",
                                        "autoflash-trees/t/captures/emulation/state-81/flash.bin",
                                        "autoflash-trees/t.runs/1.log"])
        self.assertEqual(folder, kept / "host-t")
        self.assertEqual(sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()),
                         ["runs/1.log", "suite/results.jsonl"])

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_fetch_from_a_worktree_keeps_it_in_the_main_checkouts_captures(self):
        # The worktree's own captures/ went with it, and what was fetched too.
        top = tempfile.TemporaryDirectory()
        self.addCleanup(top.cleanup)
        main, linked = Path(top.name, "main"), Path(top.name, "linked")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q", str(main)], check=True)
        subprocess.run([*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "one"], check=True)
        subprocess.run([*git, "-C", str(main), "worktree", "add", "-q", "--detach", str(linked)], check=True)
        env = {name: value for name, value in os.environ.items() if name != "AUTOFLASH_CAPTURES"}
        for root in (main, linked):
            with self.subTest(root=root.name), mock.patch.object(remote_tree, "ROOT", root), \
                    mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(remote_tree.captures(), main.resolve() / "captures")
        with mock.patch.dict(os.environ, {"AUTOFLASH_CAPTURES": str(top.name)}):
            self.assertEqual(remote_tree.captures(), remote_tree.records.CAPTURES)

    def test_a_fetch_with_nothing_worth_keeping_leaves_no_folder(self):
        folder, kept = self.fetch_from(["autoflash-trees/t/captures/emulation/state-81/flash.bin"])
        self.assertIsNone(folder)
        self.assertEqual(list(kept.iterdir()), [])
        self.assertIsNone(self.fetch_from([])[0], "no tree")


class RemoveTests(unittest.TestCase):
    """remove: fetched first, then deleted, what was fetched set apart."""

    def setUp(self):
        captures = tempfile.TemporaryDirectory()
        self.addCleanup(captures.cleanup)
        patch = mock.patch.object(remote_tree, "captures", return_value=Path(captures.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.steps = []
        steps = self.steps

        class There(remote_tree.Remote):
            def text(self, script, *args):
                steps.append("remove" if script == remote_tree.REMOVE else script)
                return "removed"

        self.remote = There("host", None)

    def fetched(self, remote, name):
        self.steps.append("fetch")
        folder = remote_tree.kept(remote.host, name)
        folder.mkdir(parents=True)
        (folder / "results.jsonl").write_text("{}", encoding="utf-8")
        return folder

    def test_a_tree_is_fetched_before_it_is_removed(self):
        # Nothing made anyone fetch first, and trees were removed with their
        # logs never fetched.
        with mock.patch.object(remote_tree, "fetch", side_effect=self.fetched), mock.patch("builtins.print"):
            remote_tree.remove(self.remote, "t", fetch_first=True)
        self.assertEqual(self.steps, ["fetch", "remove"])
        self.assertFalse(remote_tree.kept("host", "t").exists())
        sealed = list(remote_tree.kept("host", "t").parent.glob("host-t-*/results.jsonl"))
        self.assertEqual(len(sealed), 1, "what was fetched is kept apart from a later tree of the same name")

    def test_a_fetch_that_fails_leaves_the_tree(self):
        with mock.patch.object(remote_tree, "fetch", side_effect=SystemExit("rsync failed")), \
                self.assertRaises(SystemExit):
            remote_tree.remove(self.remote, "t", fetch_first=True)
        self.assertEqual(self.steps, [])

    def test_no_fetch_removes_without(self):
        with mock.patch.object(remote_tree, "remove") as remove:
            remote_tree.main(["host", "remove", "t", "--no-fetch"])
        self.assertFalse(remove.call_args.args[2])
        with mock.patch.object(remote_tree, "fetch") as fetch, mock.patch("builtins.print"):
            remote_tree.remove(self.remote, "t", fetch_first=False)
        fetch.assert_not_called()
        self.assertEqual(self.steps, ["remove"])


class UsageTests(unittest.TestCase):
    def test_each_option_the_usage_names_is_one_its_command_takes(self):
        import contextlib
        import io
        import re
        lines = re.findall(r"(?m)^\s+tools/repo/remote_tree\.py HOST (\w+) (.*)$", remote_tree.__doc__)
        self.assertEqual(len(lines), 6)
        for command, rest in lines:
            out = io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                remote_tree.main(["host", command, "--help"])
            for option in re.findall(r"--[a-z][a-z-]*", rest):
                self.assertIn(option, out.getvalue(), f"{command} {option}")


if __name__ == "__main__":
    unittest.main()
