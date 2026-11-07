"""A run's record on disk (tools/autoflash/runs.py): filed under its plug's
MAC, and what it ran from."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import cli, context, credentials, errors, output, plugs, report, routes, runs
from tools.autoflash.routes.espressif import bundle
from tools.autoflash.state import RunState


class FromTasmotasSafebootTests(unittest.TestCase):
    """A run from Tasmota's safeboot is a run from Tasmota once T1 has put it
    back (seen on the emulator: its report stopped at KeyError: 'safeboot')."""

    def state(self, done=()):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        state.set_input("source", "safeboot")
        for step in done:
            state.mark(step, "done")
        return state

    def test_every_starting_point_is_named_in_the_report(self):
        self.assertEqual(set(report.CONVERTED_FROM), {s for f in routes.FAMILIES.values() for s in f.SOURCES})

    def test_tasmota_runs_from_t1_until_it_is_left(self):
        self.assertFalse(runs.plug_runs_tasmota(self.state()))           # its safeboot
        self.assertTrue(runs.plug_runs_tasmota(self.state(["T1"])))      # Tasmota back
        self.assertFalse(runs.plug_runs_tasmota(self.state(["T1", "L1"])))  # on the mover


class RunFilingTests(unittest.TestCase):
    """A run moves under its plug's MAC once found; older runs are adopted."""

    def setUp(self):
        self.plugs = plugs
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.captures = Path(directory.name)
        for patch in (mock.patch.object(plugs, "CAPTURES", self.captures),
                      mock.patch.object(plugs, "PLUGS", self.captures / "plugs"),
                      mock.patch.object(runs, "CURRENT_POINTER", self.captures / ".current"),
                      mock.patch.object(output, "say")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_new_run_waits_unidentified_then_moves_under_the_mac(self):
        from types import SimpleNamespace
        name = runs.new_run_name(None)
        self.assertTrue(name.startswith("plugs/unidentified/runs/"))
        ctx = context.Context(SimpleNamespace(
            run=name, manifest=bundle.DEFAULT_MANIFEST, dry_run=False, plug_ip=None,
            email=None, web_user=None, web_password=None, esphome_ota_password=None,
            esphome_web_user=None, esphome_web_password=None, esphome_api_key=None))
        ctx.state.set_input("plug_ip", "10.0.0.5")
        ctx.file_under_plug("58:CF:79:01:02:03")
        self.assertEqual(ctx.dir.parent, self.captures / "plugs/58-cf-79-01-02-03/runs")
        self.assertEqual(ctx.state.input("plug_ip"), "10.0.0.5")
        ctx.state.set_input("plug_mac", "58:cf:79:01:02:03")  # saved where it now is
        self.assertTrue((ctx.dir / "autoflash-state.json").is_file())
        self.assertFalse((self.captures / name).exists())
        self.assertEqual(runs.read_current(), ctx.plug)

    def test_firmware_passwords_come_from_the_flags_then_the_environment(self):
        from types import SimpleNamespace
        name = runs.new_run_name(None)

        def args(**over):
            base = dict(run=name, manifest=bundle.DEFAULT_MANIFEST, dry_run=False,
                        plug_ip=None, email=None, web_user=None, web_password=None,
                        esphome_ota_password=None, esphome_web_user=None, esphome_web_password=None,
                        esphome_api_key=None)
            base.update(over)
            return SimpleNamespace(**base)
        key = base64.b64encode(bytes(range(32))).decode()
        # The flags win, each kept out of the log under its label.
        private = mock.Mock()
        with mock.patch.object(output, "private", private):
            ctx = context.Context(args(web_password="tas-pw", esphome_ota_password="esp-pw",
                                              esphome_web_user="admin", esphome_web_password="web-pw",
                                              esphome_api_key=key))
        self.assertEqual((ctx.web_password, ctx.esphome_password), ("tas-pw", "esp-pw"))
        self.assertEqual((ctx.esphome_login, ctx.esphome_key), (("admin", "web-pw"), bytes(range(32))))
        private.assert_any_call("tas-pw", "tasmota-password")
        private.assert_any_call("esp-pw", "esphome-password")
        private.assert_any_call("web-pw", "esphome-web-password")
        private.assert_any_call(key, "esphome-api-key")
        # With no flag, the environment (credentials) is the fallback.
        with mock.patch.object(credentials, "get",
                               side_effect={"AUTOFLASH_TASMOTA_PASSWORD": "env-tas",
                                            "AUTOFLASH_ESPHOME_OTA_PASSWORD": "env-esp",
                                            "AUTOFLASH_ESPHOME_WEB_USER": "env-admin",
                                            "AUTOFLASH_ESPHOME_WEB_PASSWORD": "env-web",
                                            "AUTOFLASH_ESPHOME_API_KEY": key}.get):
            ctx = context.Context(args())
        self.assertEqual((ctx.web_password, ctx.esphome_password), ("env-tas", "env-esp"))
        self.assertEqual((ctx.esphome_login, ctx.esphome_key), (("env-admin", "env-web"), bytes(range(32))))
        # A web login is both, or none; a key is one, or the run is refused at once.
        self.assertIsNone(context.Context(args(esphome_web_password="web-pw")).esphome_login)
        with self.assertRaisesRegex(errors.Abort, "API encryption key given.*is not one.*base64"):
            context.Context(args(esphome_api_key="not-a-key"))

    def test_older_runs_with_a_mac_are_adopted(self):
        old = RunState.open(self.captures / "conversion-20261011-1431")
        old.set_input("plug_mac", "58:cf:79:01:02:03")
        RunState.open(self.captures / "conversion-unknown").save()
        runs.remember_current("conversion-20261011-1431")
        cli.adopt_older_runs()
        moved = self.captures / "plugs/58-cf-79-01-02-03/runs/conversion-20261011-1431"
        self.assertTrue((moved / "autoflash-state.json").is_file())
        self.assertTrue((self.captures / "conversion-unknown").is_dir())
        self.assertEqual(runs.read_current(), runs.run_name(moved))
        self.assertEqual(len(runs.run_directories()), 2)


if __name__ == "__main__":
    unittest.main()
