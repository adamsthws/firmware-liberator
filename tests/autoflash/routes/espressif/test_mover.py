"""ESPHome and the way to and from it: its check (I2), the signal read before
leaving it (X1), ESPHome from the mover (M1), the identity carried off
Tasmota (L1), and an earlier release updated (U1, U2)."""
import contextlib
import io
import unittest
import time
from pathlib import Path
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tests.autoflash.routes.espressif.test_identity import IDENTITY, Fixture, on_tasmota
from tools.autoflash import cli, detect, errors, esphome, esphome_ota, handover
from tools.autoflash import join, kinds, prompts, report, routes, runs, unlock
from tools.autoflash.state import DONE
from tools.autoflash.routes import espressif, update
from tools.autoflash.routes.espressif import evidence, mover, source
from tools.espressif import nvs_image, sonoff_identity


class CheckStepTests(RouteTestCase):
    def test_it_recognises_esphome_itself(self):
        with mock.patch.object(esphome, "is_esphome", return_value=True):
            self.assertIn("seconds", mover.step_esphome_check(self.ctx(firmware="esphome")))

    def test_waiting_in_vain_points_at_the_wifi(self):
        with mock.patch.object(esphome, "is_esphome", return_value=False), \
                mock.patch.object(time, "sleep"):
            with self.assertRaisesRegex(errors.Abort, "esphome-xxxxxx"):
                mover.step_esphome_check(self.ctx(firmware="esphome"), wait=0.05)

    def test_on_its_own_network_it_is_set_up_through_that(self):
        with mock.patch.object(esphome, "is_esphome", return_value=False), \
                mock.patch.object(time, "sleep"), \
                mock.patch.object(join, "set_up_through_own_network", return_value=("Home", "pw")) as own:
            result = mover.step_esphome_check(self.ctx(firmware="esphome"), wait=0.05)
        self.assertEqual(result["set_up_through"], "its own network")
        self.assertEqual(own.call_args.args[1], "ESPHome")

    def test_joined_already_it_stops_waiting_on_the_home_wifi(self):
        with mock.patch.object(esphome, "is_esphome", return_value=False), \
                mock.patch.object(join, "on_a_plug_network", return_value=True), \
                mock.patch.object(time, "sleep") as slept, \
                mock.patch.object(join, "set_up_through_own_network", return_value=("Home", "pw")):
            mover.step_esphome_check(self.ctx(firmware="esphome"), wait=600)
        slept.assert_not_called()  # straight on, not three minutes first

    def test_a_dry_run_does_not_wait(self):
        ctx = self.ctx(firmware="esphome")
        ctx.dry_run = True
        with mock.patch.object(esphome, "is_esphome", side_effect=AssertionError("waited")):
            self.assertEqual(mover.step_esphome_check(ctx), {"dry_run": True})


class SignalBeforeLeavingESPHomeTests(RouteTestCase):
    """X1 reads the signal before anything is written, where ESPHome gives it."""

    def x1(self, signal, carry_on=True):
        ctx = self.ctx(source="esphome")
        with mock.patch.object(esphome, "signal", return_value=signal), \
                mock.patch.object(handover, "consent_to_leave") as consent, \
                mock.patch.object(detect, "identify", return_value="startup"), \
                mock.patch.object(prompts, "ask_yes_no", return_value=carry_on) as asked, \
                mock.patch.object(prompts, "section"):
            try:
                mover.step_startup_from_esphome(ctx)
            except errors.Abort as exc:
                return ctx, consent, asked, exc
        return ctx, consent, asked, None

    def test_a_weak_signal_is_asked_about_before_the_go_ahead(self):
        _, consent, asked, stopped = self.x1(-82, carry_on=False)
        self.assertRegex(str(stopped), "weak Wi-Fi signal")
        asked.assert_called_once()
        consent.assert_not_called()

    def test_a_good_signal_is_recorded(self):
        ctx, consent, asked, stopped = self.x1(-58)
        self.assertIsNone(stopped)
        self.assertEqual(ctx.state.data["captures"]["X1.signal_dbm"], -58)
        asked.assert_not_called()
        consent.assert_called_once()

    def test_an_esphome_without_the_sensor_goes_on_unasked(self):
        ctx, consent, asked, stopped = self.x1(None)
        self.assertIsNone(stopped)
        self.assertIsNone(ctx.state.data["captures"]["X1.signal_dbm"])
        asked.assert_not_called()
        consent.assert_called_once()


class ConfirmedOnlyAfterAMinuteTests(RouteTestCase):
    """ESPHome sent to the mover is confirmed only after a minute running."""

    def check(self, source, answers, probes=()):
        ctx = self.ctx(source=source, firmware="esphome")
        ctx.state.mark("M1", DONE)
        clock = iter(range(0, 100_000, 3))
        with mock.patch.object(time, "monotonic", side_effect=lambda: next(clock)), \
                mock.patch.object(time, "sleep"), \
                mock.patch.object(esphome, "is_esphome", side_effect=list(answers)), \
                mock.patch.object(esphome_ota, "probe", side_effect=list(probes) or None), \
                mock.patch.object(mover, "step_esphome_from_mover") as resent:
            return mover.step_esphome_check(ctx), resent

    def test_from_tasmota_it_is_only_done_once_esphome_has_confirmed_itself(self):
        result, _ = self.check("tasmota", [True] * 40)
        self.assertEqual(result["resent"], 0)
        # Asked again until 70 s after it first answered: 3 s a step.
        self.assertGreaterEqual(result["seconds"], 0)

    def test_back_on_the_mover_it_is_sent_esphome_again(self):
        result, resent = self.check("tasmota", [False] + [True] * 40, probes=[object()])
        resent.assert_called_once()
        self.assertEqual(result["resent"], 1)

    def test_from_tasmota_by_way_of_the_hub_nothing_is_waited_for(self):
        # Written there (I1), with the boot selection cleared: not an update.
        with mock.patch.object(esphome, "is_esphome", return_value=True) as asked:
            mover.step_esphome_check(self.ctx(source="tasmota", firmware="esphome"))
        self.assertEqual(asked.call_count, 1)

    def test_from_stock_nothing_is_waited_for(self):
        with mock.patch.object(esphome, "is_esphome", return_value=True) as asked:
            ctx = self.ctx(firmware="esphome")
            mover.step_esphome_check(ctx)
        self.assertEqual(asked.call_count, 1)


class ESPHomeUpdateTests(RouteTestCase):
    """This tool's ESPHome, an earlier release, is offered this one as an
    update; any other ESPHome is not."""

    def settle(self, info, *, given=None, answer=0, run="run", **inputs):
        ctx = self.ctx(given=given, run=run, plug_kind="sonoff-s60", **inputs)
        with mock.patch.object(source, "find_current_firmware", return_value="esphome"), \
                mock.patch.object(esphome, "api_device_info", return_value=info), \
                mock.patch.object(espressif, "published_version", return_value="1.6.0"), \
                mock.patch.object(routes, "sonoff_backup_exists", return_value=True), \
                mock.patch.object(source, "leaves_tasmota_direct", return_value=False), \
                mock.patch.object(prompts, "ask_choice", return_value=answer) as asked:
            route = routes.settle_route(ctx)
        self.addCleanup(routes.apply_target, "tasmota", "sonoff")
        return ctx, route, (asked.call_args.args[1] if asked.called else None)

    @staticmethod
    def ours(release="1.4.0", **changed):
        return {"name": "esphome-abcdef", "esphome_version": "2026.10.0", "project_name": "adamsthws.sonoff-s60",
                "project_version": release, "built": esphome.PINS["image"]["built"], "encrypted": False, **changed}

    def test_an_earlier_release_is_offered_the_update(self):
        ctx, route, offered = self.settle(self.ours(), answer=1)
        self.assertEqual(offered, ["Tasmota", "ESPHome, updated to 1.6.0",
                                   "Sonoff's own firmware (back to eWeLink, from its backup)"])
        self.assertEqual(route, ("esphome", "outdated"))
        self.assertEqual([step for step, _, _ in routes.steps_of(ctx.state)], ["A0", "A5", "U1", "U2"])
        self.assertEqual(update.versions(ctx), ("1.4.0", "1.6.0"))

    def test_asked_for_on_the_command_line_it_is_updated_unasked(self):
        _, route, offered = self.settle(self.ours(), given="esphome")
        self.assertEqual((route, offered), (("esphome", "outdated"), None))

    def test_leaving_it_is_leaving_any_esphome(self):
        ctx, route, _ = self.settle(self.ours(), answer=0)
        self.assertEqual(route, ("tasmota", "esphome"))
        self.assertEqual(routes.steps_of(ctx.state), routes.steps_for("tasmota", "esphome"))

    def test_any_other_esphome_is_not_offered_it(self):
        for why, info in (("this release", self.ours("1.6.0")), ("a later one", self.ours("1.10.0")),
                          ("another project", self.ours(project_name="someone.else")),
                          ("another kind's", self.ours(project_name="adamsthws.sonoff-s61")),
                          ("adopted, its API given a key", self.ours(encrypted=True)),
                          ("adopted without a key: not a build published",
                           self.ours(built="2026-11-04 21:19:12 +0000")),
                          ("not one of our releases", self.ours("dev")), ("not saying", None)):
            with self.subTest(why):
                with self.assertRaisesRegex(routes.NothingToDo, "already runs ESPHome"):
                    self.settle(info, given="esphome", run=why)

    def test_not_with_an_esphome_built_by_hand(self):
        ctx = self.ctx(given="esphome", plug_kind="sonoff-s60")
        ctx.esphome_firmware = Path("own.bin")
        with mock.patch.object(esphome, "api_device_info", return_value=self.ours()):
            self.assertFalse(update.outdated(ctx, "esphome", "1.6.0"))

    def test_status_says_an_update_is_available_and_records_nothing(self):
        ctx = self.ctx(plug_kind="sonoff-s60", firmware="esphome", source="sonoff")
        with mock.patch.object(esphome, "api_device_info", return_value=self.ours()), \
                mock.patch.object(espressif, "published_version", return_value="1.6.0"):
            self.assertEqual(routes.update_available(ctx),
                             "ESPHome 1.4.0 is on the device, and 1.6.0 is available: run the tool again")
        self.assertIsNone(ctx.state.input("update_from"))

    def test_status_shows_it_with_the_run(self):
        ctx = self.ctx(plug_kind="sonoff-s60", firmware="esphome", source="sonoff")
        ctx.plug, ctx.manifest_path = "12:34:56:ab:cd:ef", "manifest.json"
        with mock.patch.object(routes, "update_available", return_value="1.6.0 is available"), \
                contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(cli.cmd_status(ctx), 0)
        self.assertIn("Update:    1.6.0 is available", shown.getvalue())

    def test_status_says_nothing_where_there_is_no_update_to_offer(self):
        for why, firmware, info in (("this release", "esphome", self.ours("1.6.0")),
                                    ("adopted", "esphome", self.ours(encrypted=True)),
                                    ("not answering", "esphome", None),
                                    ("no firmware chosen", None, self.ours()),
                                    ("back on Sonoff's", "sonoff", self.ours())):
            with self.subTest(why):
                ctx = self.ctx(run=why, plug_kind="sonoff-s60", firmware=firmware, source="sonoff")
                with mock.patch.object(esphome, "api_device_info", return_value=info), \
                        mock.patch.object(espressif, "published_version", return_value="1.6.0"):
                    self.assertIsNone(routes.update_available(ctx))

    def test_status_survives_a_plug_that_fails_when_asked(self):
        ctx = self.ctx(plug_kind="sonoff-s60", firmware="esphome", source="sonoff")
        with mock.patch.object(esphome, "api_device_info", side_effect=OSError("no route")), \
                mock.patch.object(espressif, "published_version", return_value="1.6.0"):
            self.assertIsNone(routes.update_available(ctx))

    OWN = "2026-11-04 21:19:12 +0000"

    def replacing(self, info, *, word=True, **inputs):
        ctx = self.ctx(given="esphome", run=str(id(info)) + str(word), plug_kind="sonoff-s60", **inputs)
        ctx.replace_own_build = True
        with mock.patch.object(source, "find_current_firmware", return_value="esphome"), \
                mock.patch.object(esphome, "api_device_info", **(
                    {"side_effect": info} if isinstance(info, BaseException) else {"return_value": info})), \
                mock.patch.object(espressif, "published_version", return_value="1.6.0"), \
                mock.patch.object(routes, "sonoff_backup_exists", return_value=True), \
                mock.patch.object(prompts, "warning_box") as warned, \
                mock.patch.object(prompts, "require_word", return_value=word):
            self.addCleanup(routes.apply_target, "tasmota", "sonoff")
            return ctx, routes.settle_route(ctx), warned

    def test_replacing_an_own_build_without_a_key_of_the_same_version(self):
        ctx, route, warned = self.replacing(self.ours("1.6.0", built=self.OWN))
        self.assertEqual(route, ("esphome", "outdated"))
        self.assertEqual(update.versions(ctx), (update.OWN_BUILD, "1.6.0"))
        self.assertEqual([step for step, _, _ in routes.steps_of(ctx.state)], ["A0", "A5", "U1", "U2"])
        said = " ".join(warned.call_args.args[1])
        self.assertIn("You lose any changes", said)
        self.assertIn("If you renamed its relay", said)

    def test_replacing_an_own_build_with_a_key_given(self):
        ctx, route, _ = self.replacing(self.ours("1.6.0", built=self.OWN, encrypted=True))
        self.assertEqual(route, ("esphome", "outdated"))

    def test_with_no_key_to_give_nothing_is_replaced(self):
        with mock.patch.object(unlock, "ask_esphome_key", return_value=False):
            with self.assertRaisesRegex(errors.Abort, "API encryption key, and none was given"):
                self.replacing(esphome.ApiLocked())
            with self.assertRaisesRegex(errors.Abort, "API encryption key, and the one given is not it"):
                self.replacing(esphome.ApiLocked(wrong=True))

    def test_not_agreeing_stops_before_anything_is_recorded(self):
        with self.assertRaisesRegex(errors.Abort, "not confirmed"):
            self.replacing(self.ours("1.6.0", built=self.OWN), word=False)

    def test_another_projects_esphome_is_never_replaced(self):
        for info in (self.ours(project_name="someone.else"), self.ours(project_name="adamsthws.sonoff-s61"), None):
            with self.subTest(info=info), self.assertRaisesRegex(errors.Abort, "only replaces this project's"):
                self.replacing(info)

    def test_a_published_build_has_nothing_to_replace(self):
        with self.assertRaisesRegex(routes.NothingToDo, "nothing to convert\\.$"):
            self.replacing(self.ours("1.6.0"))

    def test_a_published_earlier_release_is_just_updated(self):
        ctx, route, warned = self.replacing(self.ours())
        self.assertEqual(update.versions(ctx), ("1.4.0", "1.6.0"))
        warned.assert_not_called()

    def test_any_other_firmware_is_not_replaced(self):
        ctx = self.ctx(given="esphome", plug_kind="sonoff-s60")
        with self.assertRaisesRegex(errors.Abort, "does not answer as that: it runs Tasmota"):
            update.replace_own_build(ctx, "tasmota", "16.0.0")

    def test_the_update_is_only_done_once_the_published_build_answers(self):
        # Often the same version: an own build that kept running, or a plug that
        # went back to it, must not pass for the release sent.
        ctx = self.ctx(plug_kind="sonoff-s60", firmware="esphome", replaced_build=self.OWN)
        with mock.patch.object(esphome, "api_device_info", return_value=self.ours("1.6.0", built=self.OWN)):
            self.assertEqual(update.esphome_release(ctx), update.OWN_BUILD)
        with mock.patch.object(esphome, "api_device_info", return_value=self.ours("1.6.0")):
            self.assertEqual(update.esphome_release(ctx), "1.6.0")

    def test_a_refused_update_points_to_the_option_for_an_own_build_alone(self):
        for info, says in ((self.ours("1.6.0", built=self.OWN), True), (self.ours("1.6.0"), False),
                           (esphome.ApiLocked(), True), (None, False)):
            ctx = self.ctx(given="esphome", run=f"hint{id(info)}", plug_kind="sonoff-s60")
            with self.subTest(info=info), mock.patch.object(esphome, "api_device_info", **(
                    {"side_effect": info} if isinstance(info, BaseException) else {"return_value": info})):
                with self.assertRaises(routes.NothingToDo) as stopped:
                    routes.nothing_to_convert(ctx, "esphome")
                self.assertEqual("--replace-own-build" in str(stopped.exception), says)

    def test_a_build_published_for_an_earlier_release_is_offered_it(self):
        ctx, route, _ = self.settle(self.ours(built=esphome.PINS["image"]["earlier_builds"][0]), answer=1)
        self.assertEqual(route, ("esphome", "outdated"))

    def test_published_builds_are_each_kinds_own(self):
        s60, s61 = (esphome.published_builds(kinds.by_key(key)) for key in ("sonoff-s60", "sonoff-s61"))
        self.assertIn(esphome.PINS["image"]["built"], s60)
        self.assertIn(esphome.FIRMWARE["beken"]["sonoff-s61"]["esphome"]["built"], s61)
        self.assertEqual(s60 & s61, set())

    def test_releases_are_compared_as_numbers(self):
        self.assertTrue(update.older("1.9.0", "1.10.0"))
        self.assertFalse(update.older("1.10.0", "1.9.0"))
        self.assertFalse(update.older("1.6.0", "1.6.0"))
        self.assertFalse(update.older("", "1.6.0"))

    def test_openbekens_builds_of_a_release_are_compared_too(self):
        self.assertTrue(update.older("1.1.0", "1.18.320-1"))
        self.assertTrue(update.older("1.18.320-1", "1.18.320-2"))
        self.assertTrue(update.older("1.18.320-2", "1.18.321-1"))
        self.assertFalse(update.older("1.18.320-2", "1.18.320-2"))
        self.assertFalse(update.older("16.0.0(release-tasmota32)", "16.0.0"))

    def check(self, versions, confirm=0.0):
        ctx = self.ctx(source="outdated", firmware="esphome", update_from="1.4.0", update_to="1.6.0",
                       plug_kind="sonoff-s60")
        answers = iter(self.ours(version) if version else None for version in versions)
        with mock.patch.object(esphome, "api_device_info", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(time, "sleep"):
            return update.wait_for_update(ctx, wait=60.0, confirm=confirm)

    def test_the_check_waits_for_the_new_release(self):
        result = self.check([None, "1.4.0", "1.6.0"])
        self.assertEqual((result["from"], result["to"]), ("1.4.0", "1.6.0"))

    def test_one_gone_back_to_its_old_release_is_not_done(self):
        # The S60's ESPHome takes an update back if it restarts before confirming it.
        with self.assertRaisesRegex(errors.Abort, "went back to ESPHome 1.4.0"):
            self.check(["1.6.0", "1.4.0"], confirm=70.0)
        self.assertEqual(self.check(["1.6.0", "1.6.0"], confirm=70.0)["to"], "1.6.0")

    def test_a_send_cut_off_is_judged_by_the_release_it_runs(self):
        ctx = self.ctx(source="outdated", firmware="esphome", update_from="1.4.0", update_to="1.6.0",
                       plug_kind="sonoff-s60")
        for version, finished in (("1.6.0", True), ("1.4.0", False), (None, None)):
            with mock.patch.object(esphome, "api_device_info", return_value=self.ours(version) if version else None):
                self.assertIs(evidence.ATTEMPTED_EVIDENCE["U1"](ctx)[0], finished, version)
                self.assertIs(espressif.STEP_VERIFIERS["U1"](ctx)[0], finished is not False, version)

    def test_its_report_says_it_was_updated(self):
        for running, firmware, result in (("outdated", "esphome", "Updated"), ("esphome", "tasmota", "Converted")):
            ctx = self.ctx(source=running, firmware=firmware, run=running)
            with mock.patch.object(report, "check_esphome_hardware", return_value={}), \
                    mock.patch.object(report, "check_hardware", return_value={}), \
                    mock.patch.object(report, "offer_report") as offered:
                report.invite_report(ctx, 60.0, False)
            self.assertEqual(report.answer(dict(offered.call_args.args[1])["Result"]), result, running)

    def test_the_wifi_is_read_from_esphome_not_tasmota(self):
        ctx = self.ctx(source="outdated", firmware="esphome")
        self.assertFalse(runs.plug_runs_tasmota(ctx.state))


class CarriedOffTasmotaTests(Fixture):
    """L1: the settings of the next firmware hold the identity."""

    def test_the_kept_identity_goes_into_sonoffs_settings(self):
        ctx = self.ctx(on_tasmota(sector=sonoff_identity.to_sector(IDENTITY)))
        settings = mover.settings_to_carry(ctx, esphome.layout_of(kinds.SONOFF_S60))
        self.assertEqual(sonoff_identity.from_nvs(settings + b"\xff" * (0x10000 - len(settings))), IDENTITY)
        self.assertNotIn(b"Home-2G", settings)  # nothing of the Wi-Fi
        self.assertEqual(len(settings) % nvs_image.PAGE, 0)

    def test_a_plug_converted_before_carries_its_backups(self):
        self.back_up()
        settings = mover.settings_to_carry(self.ctx(on_tasmota()), esphome.layout_of(kinds.SONOFF_S60))
        self.assertEqual(sonoff_identity.from_nvs(settings + b"\xff" * (0x10000 - len(settings))), IDENTITY)

    def test_with_neither_the_region_is_cleared_as_before(self):
        self.assertIsNone(mover.settings_to_carry(self.ctx(on_tasmota()), esphome.layout_of(kinds.SONOFF_S60)))


if __name__ == "__main__":
    unittest.main()
