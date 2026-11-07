"""The firmware bundle (tools/autoflash/routes/espressif/bundle.py): checked
at A5, and built again when this version cannot use it."""
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import hostsys, output, prompts, runs, safety_locks, state
from tools.autoflash.routes.espressif import bundle
from tools.autoflash.state import RunState
from tools.espressif.safeboot import prepare_imprinted_bundle


class StartChoiceTests(unittest.TestCase):
    """A5 asks nothing about Wi-Fi, and a run carried on keeps a bundle this
    version can use, or has one built again."""

    @staticmethod
    def locks_in_place():
        """A5 finding both safety locks in place, as this checkout's are - without
        looking in its captures, where the runs that lift them say so."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(safety_locks, "restore_left", return_value=[]))
        stack.enter_context(mock.patch.object(safety_locks, "in_place", return_value=True))
        return stack

    def ctx(self):
        from types import SimpleNamespace
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        return SimpleNamespace(state=RunState.open(root), evidence=root / "live",
                               manifest_path=(runs.ROOT / bundle.DEFAULT_MANIFEST).resolve(),
                               wifi=lambda: ("Home-2G", "password"))

    def test_a5_asks_nothing_about_wifi(self):
        # The details are read off the plug at E1, so A5 has nothing to ask.
        from unittest import mock
        with mock.patch.object(bundle, "validate_bundle", return_value={}), \
             mock.patch.object(hostsys, "check_sha256sums", return_value=[]), \
             mock.patch.object(prompts, "ask_choice") as menu, \
             mock.patch.object(bundle, "make_bundle") as rebuild, \
             self.locks_in_place(), mock.patch.object(output, "say"):
            bundle.step_check(self.ctx())
        menu.assert_not_called()
        rebuild.assert_not_called()

    def test_a_run_carried_on_has_a_bundle_this_version_cannot_use_built_again(self):
        # A run of 2.0.2 left part way, carried on after the firmware the tool
        # installs changed: its bundle names the old release, which every
        # phase server refuses (the fault suite's legacy-v2.0.2, at D5 and G1).
        from unittest import mock
        ctx = self.ctx()
        ctx.state.mark("A5", state.DONE, result={})
        stale = ValueError("manifest official-release identity differs from the reviewed frozen set")
        with mock.patch.object(bundle, "validate_bundle", side_effect=stale) as validate, \
             mock.patch.object(bundle, "make_bundle") as rebuild:
            bundle.bundle_of_this_version(ctx)
        rebuild.assert_called_once_with(ctx, problem=str(stale))
        self.assertTrue(validate.call_args.kwargs["current_only"])

    def test_a_run_carried_on_past_f2_keeps_a_bundle_of_a_release_pinned_before(self):
        # The plug runs the app that bundle holds: G1's Safeboot and H1's
        # check must be that release's too.
        from unittest import mock
        ctx = self.ctx()
        for step in ("A5", "F2"):
            ctx.state.mark(step, state.DONE, result={})
        with mock.patch.object(bundle, "validate_bundle", return_value={}) as validate, \
             mock.patch.object(bundle, "make_bundle") as rebuild:
            bundle.bundle_of_this_version(ctx)
        self.assertFalse(validate.call_args.kwargs["current_only"])
        rebuild.assert_not_called()

    def test_a_bundle_built_again_past_f2_is_of_the_release_on_the_plug(self):
        # 1.2.0's bundle, of the release pinned before, was made from another
        # startup firmware: built again of the release pinned now, H1 found
        # the plug's Safeboot and app differing (the fault suite's
        # legacy-v1.2.0 at H1).
        import json
        import tempfile
        from unittest import mock
        from tools.espressif.safeboot.safeboot_migration import FIRMWARE
        earlier = FIRMWARE["tasmota"]["earlier"][0]
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"schema": 1, "artifacts": {
                name: {"size": file["size"], "sha256": file["sha256"]}
                for name, file in earlier["files"].items()}}))
            ctx = self.ctx()
            ctx.manifest_path = manifest
            ctx.state.mark("A5", state.DONE, result={})
            self.assertIsNone(bundle.release_on_the_plug(ctx))
            ctx.state.mark("F2", state.DONE, result={})
            self.assertEqual(bundle.release_on_the_plug(ctx), earlier)
            with mock.patch.object(bundle, "build_bundle") as build, \
                 mock.patch.object(bundle, "validate_bundle"), mock.patch.object(output, "say"):
                bundle.make_bundle(ctx, problem="built from a different startup firmware")
            self.assertEqual(build.call_args.kwargs["release"], earlier)
            manifest.write_text(json.dumps({"schema": 1, "artifacts": {
                name: {"size": file["size"], "sha256": file["sha256"]}
                for name, file in FIRMWARE["tasmota"]["files"].items()}}))
            self.assertIsNone(bundle.release_on_the_plug(ctx))

    def test_a_run_carried_on_keeps_a_bundle_this_version_can_use(self):
        from unittest import mock
        ctx = self.ctx()
        ctx.state.mark("A5", state.DONE, result={})
        with mock.patch.object(bundle, "validate_bundle", return_value={}), \
             mock.patch.object(bundle, "make_bundle") as rebuild:
            bundle.bundle_of_this_version(ctx)
        rebuild.assert_not_called()

    def test_a5_builds_a_missing_bundle_without_asking_for_wifi(self):
        # A first run: the bundle is built with no Wi-Fi; E1 adds it, read off the plug.
        from unittest import mock
        with mock.patch.object(bundle, "validate_bundle",
                               side_effect=[ValueError("no manifest"), {}]), \
             mock.patch.object(bundle, "build_bundle") as build, \
             mock.patch.object(hostsys, "check_sha256sums", return_value=[]), \
             mock.patch.object(prepare_imprinted_bundle, "ask_credentials") as credentials, \
             mock.patch.object(prompts, "ask_secret") as secret, \
             self.locks_in_place(), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            bundle.step_check(self.ctx())
        build.assert_called_once()
        self.assertNotIn("ssid", build.call_args.kwargs)
        self.assertEqual(build.call_args.args, ())
        credentials.assert_not_called()
        secret.assert_not_called()


if __name__ == "__main__":
    unittest.main()
