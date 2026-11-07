"""Official Tasmota of an earlier release, updated from its safeboot (U5, U6)."""
import unittest
import time
from types import SimpleNamespace
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tools.autoflash import errors, prompts, routes, runs
from tools.autoflash.routes import espressif, update
from tools.autoflash.routes.espressif import evidence, source, tasmota


class TasmotaUpdateTests(RouteTestCase):
    """Official Tasmota of an earlier release than the one this tool installs
    is offered that one, uploaded to its safeboot as F2 uploads it; any
    other Tasmota is not."""

    def settle(self, release, *, given=None, answer=0, run="run"):
        ctx = self.ctx(given=given, run=run, plug_kind="sonoff-s60")
        with mock.patch.object(source, "find_current_firmware", return_value="tasmota"), \
                mock.patch.dict(update.RELEASE, tasmota=lambda ctx: release), \
                mock.patch.object(routes, "sonoff_backup_exists", return_value=True), \
                mock.patch.object(source, "leaves_tasmota_direct", return_value=False), \
                mock.patch.object(prompts, "ask_choice", return_value=answer) as asked:
            route = routes.settle_route(ctx)
        self.addCleanup(routes.apply_target, "tasmota", "sonoff")
        return ctx, route, (asked.call_args.args[1] if asked.called else None)

    def test_an_earlier_release_is_offered_the_one_this_tool_installs(self):
        ctx, route, offered = self.settle("15.6.0")
        self.assertEqual(offered, [f"Tasmota, updated to {tasmota.OFFICIAL_TASMOTA_VERSION}", "ESPHome",
                                   "Sonoff's own firmware (back to eWeLink, from its backup)"])
        self.assertEqual(route, ("tasmota", "outdated"))
        self.assertEqual([step for step, _, _ in routes.steps_of(ctx.state)], ["A0", "A5", "U5", "U6"])
        self.assertEqual(update.versions(ctx), ("15.6.0", tasmota.OFFICIAL_TASMOTA_VERSION))
        self.assertTrue(runs.plug_runs_tasmota(ctx.state))

    def test_asked_for_on_the_command_line_it_is_updated_unasked(self):
        _, route, offered = self.settle("15.6.0", given="tasmota")
        self.assertEqual((route, offered), (("tasmota", "outdated"), None))

    def test_leaving_it_is_leaving_any_tasmota(self):
        _, route, _ = self.settle("15.6.0", answer=1)
        self.assertEqual(route, ("esphome", "tasmota"))

    def test_this_release_or_anyones_own_build_is_kept_as_it_is(self):
        # Its meter checked instead (rerun_on_tasmota), as ever.
        for why, release in (("this release", tasmota.OFFICIAL_TASMOTA_VERSION), ("not official", None)):
            with self.subTest(why):
                with self.assertRaisesRegex(routes.NothingToDo, "already runs Tasmota"):
                    self.settle(release, given="tasmota", run=why)

    def test_its_release_is_read_from_an_official_builds_version_alone(self):
        for version, release in (("15.6.0(release-tasmota32)", "15.6.0"), ("16.0.0 (release-tasmota32)", "16.0.0"),
                                 ("15.6.0.1(f5b34a2-bluetooth)", None), ("16.0.0(release-safeboot)", None),
                                 ("14.0.0(tasmota32c3-mine)", None), ("", None)):
            ctx = SimpleNamespace(device=SimpleNamespace(firmware_version=lambda version=version: version))
            self.assertEqual(update.tasmota_release(ctx), release, version)

    def plug(self, safeboot_after=1):
        """A Tasmota that goes to its safeboot on Restart 3, after `safeboot_after` asks."""
        commands = []
        asks = iter([False] * safeboot_after + [True] * 100)
        device = SimpleNamespace(is_safeboot=lambda: next(asks),
                                 command=lambda text, timeout=None: commands.append(text) or {})
        ctx = self.ctx(source="outdated", firmware="tasmota", update_from="15.6.0",
                       update_to=tasmota.OFFICIAL_TASMOTA_VERSION)
        ctx.device = device
        return ctx, commands

    def test_it_is_switched_to_its_safeboot_and_given_this_release_there(self):
        ctx, commands = self.plug()
        with mock.patch.object(tasmota, "install_official", return_value=("uploaded", "rejoined the network")) \
                as installed, mock.patch.object(tasmota.time, "sleep"):
            result = tasmota.step_tasmota_update(ctx)
        self.assertEqual(commands, ["Restart 3"])
        self.assertEqual(installed.call_args.args[1].name, "tasmota32c3.bin")
        self.assertEqual(result["outcome"], "rejoined the network")

    def test_left_on_its_safeboot_it_is_given_it_from_there(self):
        ctx, commands = self.plug(safeboot_after=0)
        with mock.patch.object(tasmota, "install_official", return_value=("uploaded", "rejoined the network")):
            tasmota.step_tasmota_update(ctx)
        self.assertEqual(commands, [])

    def test_one_that_lost_its_settings_is_left_to_the_person(self):
        ctx, _ = self.plug()
        with mock.patch.object(tasmota, "install_official", return_value=("uploaded", "raised its own network")), \
                mock.patch.object(tasmota.time, "sleep"), self.assertRaisesRegex(errors.Abort, "lost its settings"):
            tasmota.step_tasmota_update(ctx)

    def test_the_check_waits_for_the_new_release_then_checks_the_meter(self):
        ctx, _ = self.plug()
        answers = iter(["15.6.0", None, tasmota.OFFICIAL_TASMOTA_VERSION])
        with mock.patch.dict(update.RELEASE, tasmota=lambda ctx: next(answers)), \
                mock.patch.object(tasmota, "calibrate_tasmota_meter", return_value={"changed": False}) as meter, \
                mock.patch.object(time, "sleep"):
            result = tasmota.step_tasmota_updated(ctx)
        self.assertEqual((result["to"], result["meter"]), (tasmota.OFFICIAL_TASMOTA_VERSION, {"changed": False}))
        meter.assert_called_once()

    def test_a_try_cut_off_is_judged_by_the_release_it_runs(self):
        ctx, _ = self.plug()
        for release, finished in ((tasmota.OFFICIAL_TASMOTA_VERSION, True), ("15.6.0", False), (None, None)):
            with mock.patch.dict(update.RELEASE, tasmota=lambda ctx, release=release: release):
                self.assertIs(evidence.ATTEMPTED_EVIDENCE["U5"](ctx)[0], finished, release)
                self.assertIs(espressif.STEP_VERIFIERS["U5"](ctx)[0], finished is not False, release)


if __name__ == "__main__":
    unittest.main()
