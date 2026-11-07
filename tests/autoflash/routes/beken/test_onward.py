"""From one converted firmware to the next: each fetched or sent as an update
of the firmware's own kind, from the interim firmware too, and an earlier
release of OpenBeken updated."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.autoflash.test_openbeken import FakeOpenBeken
from tests.autoflash.routes.beken.test_beken import a_context
from tools.autoflash import detect, errors, handover, openbeken, report, routes, state
from tools.autoflash.routes import beken, update
from tools.autoflash.routes.beken import evidence, images, onward
from tools.beken import uf2
from tools.ewelink import ota_push


class OnwardTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeOpenBeken()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        for patch in (mock.patch.object(onward.firewall, "choose_port", return_value=8088),
                      mock.patch.object(onward.firewall, "ensure_firewall"),
                      mock.patch.object(onward.output, "say"), mock.patch.object(onward.output, "progress"),
                      mock.patch.object(onward.output, "progress_clear")):
            patch.start()
            self.addCleanup(patch.stop)

    def served(self, ctx, step, fetched):
        """Run `step` with the firmware server stood in for: what the plug was told to fetch."""
        class Server:
            def __init__(self, address, image, device_ip, say=print, names=("user1.bin", "user2.bin"), live=None):
                self.image = image
                fetched["image"] = image
                fetched["names"] = names
                fetched["live"] = live

            def serve_forever(self):
                pass

            def unique_bytes_served(self):
                return len(self.image)

            def shutdown(self):
                pass

            def server_close(self):
                pass

        with mock.patch.object(ota_push, "FirmwareServer", Server):
            return step(ctx)

    def test_every_direction_checks_the_wifi_signal_before_the_go_ahead(self):
        # OpenBeken's as Tasmota gives it, ESPHome's from its sensor: a weak one asked about first.
        order = []
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip="192.0.2.50", firmware="esphome", source="openbeken")
            ctx.device = SimpleNamespace(command=lambda cmd: {"StatusSTS": {"Wifi": {"Signal": -85}}})
            with mock.patch.object(onward.join, "warn_if_weak_signal",
                                   side_effect=lambda ctx, signal, then: order.append(("weak?", signal))), \
                    mock.patch.object(onward.handover, "consent_to_leave",
                                      side_effect=lambda ctx, what: order.append(("go-ahead", what))), \
                    mock.patch.object(onward, "serve_to_plug", return_value=1.0), \
                    mock.patch.object(onward.openbeken, "info", return_value=None), \
                    mock.patch.object(onward.join.output, "say"):
                self.assertEqual(onward.step_openbeken_to_esphome(ctx)["signal_dbm"], -85)
                with mock.patch.object(onward.esphome, "signal", return_value=-70), \
                        mock.patch.object(onward.handover, "send_as_esphome_update", return_value=1.0):
                    self.assertEqual(onward.step_esphome_to_openbeken(ctx)["signal_dbm"], -70)
        self.assertEqual(order, [("weak?", -85), ("go-ahead", "OpenBeken"), ("weak?", -70), ("go-ahead", "ESPHome")])

    def test_openbeken_fetches_esphome_by_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="esphome", source="openbeken")
            fetched = {}
            result = self.served(ctx, onward.step_openbeken_to_esphome, fetched)
            image, version = images.image(ctx, "esphome")
            self.assertEqual(fetched["image"], image)
            self.assertEqual(result["version"], version)
            self.assertEqual(self.server.commands, ["ota_http http://192.0.2.2:8088/firmware.rbl"])
            # Served under the name the plug is sent to fetch (it was once 404).
            self.assertIn("firmware.rbl", fetched["names"])
            # Its progress logged, never drawn over the stage line.
            self.assertIs(fetched["live"], False)

    def test_an_openbeken_that_asks_for_nothing_is_restarted_and_asked_again(self):
        # Its last download cut off - this computer stopped mid-way - OpenBeken
        # refuses another until it restarts (seen in the fault suite).
        asked = []

        class Server:
            def __init__(self, address, image, device_ip, say=print, names=(), live=None):
                self.image = image

            def serve_forever(self):
                pass

            def unique_bytes_served(self):
                return len(self.image) if len(asked) > 1 else 0

            def shutdown(self):
                pass

            def server_close(self):
                pass

        restarts = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ota_push, "FirmwareServer", Server), \
                mock.patch.object(onward, "FIRST_BYTE_WAIT", 0.0), mock.patch.object(onward.time, "sleep"):
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="esphome", source="openbeken")
            onward.serve_to_plug(ctx, b"image", "ESPHome", asked.append,
                                 unstick=lambda: restarts.append(1) or True)
        self.assertEqual((len(asked), len(restarts)), (2, 1))

    def test_openbeken_fetches_sonoffs_firmware_through_the_modules_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="sonoff", source="openbeken")
            fetched = {}
            with mock.patch.object(images, "stock_rbl", return_value=(b"RBL stock", "1.0.2")):
                result = self.served(ctx, onward.step_openbeken_to_sonoff, fetched)
            self.assertEqual((fetched["image"], result["version"]), (b"RBL stock", "1.0.2"))
            self.assertEqual(self.server.commands, ["AutoflashRevertToStock http://192.0.2.2:8088/firmware.rbl"])
            self.assertIn("firmware.rbl", fetched["names"])
            # An OpenBeken without the module cannot check where the image would go: refused.
            self.server.unknown.add("AutoflashRevertToStock")
            with mock.patch.object(images, "stock_rbl", return_value=(b"RBL stock", "1.0.2")):
                with self.assertRaisesRegex(errors.Abort, "no AutoflashRevertToStock"):
                    self.served(ctx, onward.step_openbeken_to_sonoff, fetched)

    def test_esphome_is_sent_a_uf2_on_libretinys_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="openbeken", source="esphome")
            with mock.patch.object(handover, "send_as_esphome_update", return_value=4.0) as sent:
                result = onward.step_esphome_to_openbeken(ctx)
            image, version = images.image(ctx, "openbeken")
            data, what = sent.call_args.args[1], sent.call_args.args[2]
            self.assertEqual(uf2.image_in(data), ("download", image))
            self.assertEqual(sent.call_args.kwargs["port"], 8892)
            self.assertEqual((result["seconds"], result["version"]), (4.0, version))
            self.assertIn(version, what)
            with mock.patch.object(images, "stock_rbl", return_value=(b"RBL stock", "1.0.2")), \
                    mock.patch.object(handover, "send_as_esphome_update", return_value=4.0) as sent:
                onward.step_esphome_to_sonoff(ctx)
            self.assertEqual(uf2.image_in(sent.call_args.args[1]), ("download", b"RBL stock"))

    def test_an_earlier_esphome_is_sent_this_release_the_same_way(self):
        # No go-ahead: an ESPHome update, not a conversion.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="esphome", source="outdated")
            with mock.patch.object(handover, "send_as_esphome_update", return_value=4.0) as sent, \
                    mock.patch.object(handover, "consent_to_leave", side_effect=AssertionError("asked")):
                result = onward.step_esphome_update(ctx)
            image, version = images.image(ctx, "esphome")
            self.assertEqual(uf2.image_in(sent.call_args.args[1]), ("download", image))
            self.assertEqual(sent.call_args.kwargs["port"], 8892)
            self.assertEqual(result["version"], version)

    def test_the_go_ahead_comes_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="esphome", source="openbeken")
            ctx.consented = False
            with mock.patch.object(handover.prompts, "warning_box"), \
                    mock.patch.object(handover.prompts, "require_word", return_value=False):
                with self.assertRaises(errors.NothingWritten):
                    onward.step_openbeken_to_esphome(ctx)
            self.assertEqual(self.server.commands, [])


    def test_the_interim_firmware_fetches_esphome_without_asking_again(self):
        # The go-ahead was given at B2, before Sonoff's firmware was replaced.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="esphome", source="sonoff")
            ctx.consented = False
            fetched = {}
            with mock.patch.object(onward.handover, "consent_to_leave") as asked, \
                    mock.patch.object(onward.join, "note_signal") as signal:
                result = self.served(ctx, onward.step_interim_to_esphome, fetched)
            self.assertFalse(asked.called or signal.called)
            image, version = images.image(ctx, "esphome")
            self.assertEqual((fetched["image"], result["version"]), (image, version))
            self.assertEqual(self.server.commands, ["ota_http http://192.0.2.2:8088/firmware.rbl"])


class FromTheInterimFirmwareTests(unittest.TestCase):
    """A plug left on the interim firmware, found by a run with no record of
    the one that left it there."""

    def setUp(self):
        self.server = FakeOpenBeken()
        self.server.interim, self.server.short_name = True, "autoflash-recovery-CDEF"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        for patch in (mock.patch.object(onward.firewall, "choose_port", return_value=8088),
                      mock.patch.object(onward.firewall, "ensure_firewall"),
                      mock.patch.object(onward.output, "say"), mock.patch.object(onward.output, "progress"),
                      mock.patch.object(onward.output, "progress_clear"), mock.patch.object(openbeken.time, "sleep"),
                      mock.patch.object(handover.output, "transient_begin"),
                      mock.patch.object(handover.output, "transient_end")):
            patch.start()
            self.addCleanup(patch.stop)

    def going_ahead(self, ctx, step, image=b"RBL image"):
        """`step` run with the firmware server stood in for, the go-ahead given:
        what the go-ahead said was replaced."""
        boxes = []

        class Server:
            def __init__(self, address, image, device_ip, say=print, names=(), live=None):
                self.image = image

            def serve_forever(self):
                pass

            def unique_bytes_served(self):
                return len(self.image)

            def shutdown(self):
                pass

            def server_close(self):
                pass

        with mock.patch.object(ota_push, "FirmwareServer", Server), \
                mock.patch.object(images, "stock_rbl", return_value=(image, "1.0.2")), \
                mock.patch.object(handover.prompts, "warning_box", side_effect=lambda title, lines: boxes.append(lines)), \
                mock.patch.object(handover.prompts, "require_word", return_value=True):
            step(ctx)
        return " ".join(boxes[0]) if boxes else None

    def test_to_esphome_it_fetches_esphome_once_the_go_ahead_is_given(self):
        # This run gave none before (no B2 of its own): asked, of the interim firmware.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="esphome", source="interim")
            ctx.consented = False
            said = self.going_ahead(ctx, onward.step_interim_to_esphome)
            self.assertIn("the device's interim firmware is replaced", said)
            self.assertNotIn("openbeken", said.lower())
            self.assertEqual(self.server.commands[-1], "ota_http http://192.0.2.2:8088/firmware.rbl")

    def test_to_sonoffs_firmware_it_fetches_it_as_openbeken_would(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="sonoff", source="interim")
            ctx.consented = False
            said = self.going_ahead(ctx, onward.step_interim_to_sonoff)
            self.assertIn("the device's interim firmware is replaced", said)
            self.assertEqual(self.server.commands[-1], "AutoflashRevertToStock http://192.0.2.2:8088/firmware.rbl")
            # Still on it afterwards: the image was not applied, and is sent again.
            with mock.patch.object(detect, "identify", return_value="interim"):
                self.assertEqual(evidence.evidence_installed(ctx),
                                 (False, "The device still answers as the interim firmware: the image was not "
                                         "applied."))

    def test_to_openbeken_it_is_kept_as_the_plugs_own_and_restarted(self):
        # Nothing sent: it is the OpenBeken the run would install.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="openbeken", source="interim")
            with mock.patch.object(onward.output, "say") as said:
                self.assertEqual(onward.step_interim_to_openbeken(ctx), {"restarted": True})
            self.assertEqual(self.server.commands, ["AutoflashInterim off", "reboot"])
            self.assertFalse(self.server.interim)
            self.assertIn("so your network knows it by its new name", said.call_args_list[-1].args[0])
            ctx.dry_run = True
            self.assertEqual(onward.step_interim_to_openbeken(ctx), {"dry_run": True})
            self.assertEqual(len(self.server.commands), 2)

    def test_one_that_will_not_be_renamed_stops_the_run(self):
        self.server.kind = None   # a module without the command
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, plug_ip=self.server.host, firmware="openbeken", source="interim")
            with self.assertRaisesRegex(errors.Abort, "would not take its new name"):
                onward.step_interim_to_openbeken(ctx)

    def test_the_person_is_told_it_was_left_part_way(self):
        with mock.patch.object(routes.prompts, "section") as section, \
                mock.patch.object(routes.prompts, "ask_choice", return_value=1), \
                mock.patch.object(routes.output, "transient_begin"), mock.patch.object(routes.output, "transient_end"):
            self.assertEqual(routes.ask_firmware("interim", ["openbeken", "esphome", "sonoff"]), "esphome")
        self.assertIn("left part way through a conversion", " ".join(section.call_args.args[1]))

    def test_with_plug_ip_it_is_found_with_its_kind(self):
        # As the search finds it: the plug's kind is its import module's to say
        # (an OpenBeken at --plug-ip was taken for Sonoff's firmware before).
        from tools.autoflash.routes import source
        for interim, running in ((True, "interim"), (False, "openbeken")):
            with self.subTest(running=running), tempfile.TemporaryDirectory() as tmp:
                self.server.interim = interim
                run = state.RunState.open(Path(tmp) / "run")
                run.set_input("plug_ip", self.server.host)
                ctx = SimpleNamespace(state=run, web_password=None, esphome_login=None,
                                      set_plug_ip=lambda ip: run.set_input("plug_ip", ip))
                with mock.patch.object(source.unlock, "identify", return_value=running), \
                        mock.patch.object(source.hostsys, "mac_of", return_value=None), \
                        mock.patch.object(source.output, "say"):
                    self.assertEqual(source.find_current_firmware(ctx), running)
                self.assertEqual(run.input("plug_kind"), "sonoff-s61")
        # Answering otherwise the second time: never guessed at.
        with tempfile.TemporaryDirectory() as tmp:
            run = state.RunState.open(Path(tmp) / "run")
            run.set_input("plug_ip", self.server.host)
            ctx = SimpleNamespace(state=run, web_password=None, esphome_login=None, set_plug_ip=lambda ip: None)
            with mock.patch.object(source.unlock, "identify", return_value="interim"), \
                    mock.patch.object(source.hostsys, "mac_of", return_value=None):
                with self.assertRaisesRegex(errors.Abort, "did not say twice alike"):
                    source.find_current_firmware(ctx)


class OpenBekenUpdateTests(unittest.TestCase):
    """This tool's OpenBeken, an earlier release, is offered this one as an
    update, fetched as any OpenBeken update is; any other OpenBeken is not."""

    def setUp(self):
        self.server = FakeOpenBeken()
        self.server.version = "1.0.9"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = directory.name
        self.published = images.PINS["sonoff-s61"]["openbeken"]["version"]

    def ctx(self, run="run", **inputs):
        ctx = a_context(Path(self.tmp) / run, plug_ip=self.server.host, **inputs)
        ctx.firmware_given, ctx.replace_own_build = inputs.get("given"), False
        return ctx

    def settle(self, *, given=None, answer=0, run="run"):
        ctx = self.ctx(run)
        ctx.firmware_given = given
        with mock.patch.object(routes.source, "find_current_firmware", return_value="openbeken"), \
                mock.patch.object(routes, "sonoff_backup_exists", return_value=True), \
                mock.patch.object(routes.output, "say"), \
                mock.patch.object(routes.prompts, "section"), \
                mock.patch.object(routes.prompts, "ask_choice", return_value=answer) as asked:
            route = routes.settle_route(ctx)
        self.addCleanup(routes.apply_target, "tasmota", "sonoff")
        return ctx, route, (asked.call_args.args[1] if asked.called else None)

    def test_an_earlier_release_is_offered_the_update(self):
        ctx, route, offered = self.settle(answer=0)
        self.assertEqual(offered, [f"OpenBeken, updated to {self.published}", "ESPHome",
                                   "Sonoff's own firmware (back to eWeLink, from its backup)"])
        self.assertEqual(route, ("openbeken", "outdated"))
        self.assertEqual([step for step, _, _ in routes.steps_of(ctx.state)], ["A0", "U3", "U4"])
        self.assertEqual(update.versions(ctx), ("1.0.9", self.published))

    def test_asked_for_on_the_command_line_it_is_updated_unasked(self):
        _, route, offered = self.settle(given="openbeken")
        self.assertEqual((route, offered), (("openbeken", "outdated"), None))

    def test_leaving_it_is_leaving_any_openbeken(self):
        ctx, route, _ = self.settle(answer=1)
        self.assertEqual(route, ("esphome", "openbeken"))
        self.assertEqual(routes.steps_of(ctx.state), routes.steps_for("esphome", "openbeken", beken))

    def test_any_other_openbeken_is_not_offered_it(self):
        release, build = self.published.split("-")
        later = f"{release}-{int(build) + 1}"
        for why, version, kind in (("this release", self.published, "sonoff-s61"), ("a later one", later, "sonoff-s61"),
                                   ("not one of our releases", "1.18.320-dev", "sonoff-s61"),
                                   ("another kind's", "1.0.9", "sonoff-s99")):
            with self.subTest(why):
                self.server.version, self.server.kind = version, kind
                with self.assertRaisesRegex(routes.NothingToDo, "already runs OpenBeken"):
                    self.settle(given="openbeken", run=why)

    def test_it_is_fetched_by_the_earlier_release_unasked(self):
        # No go-ahead and no backup: an OpenBeken update, not a conversion.
        ctx = self.ctx(firmware="openbeken", source="outdated")
        with mock.patch.object(onward, "serve_to_plug", return_value=4.0) as served, \
                mock.patch.object(handover, "consent_to_leave", side_effect=AssertionError("asked")):
            result = onward.step_openbeken_update(ctx)
        image, version = images.image(ctx, "openbeken")
        self.assertEqual(served.call_args.args[1], image)
        served.call_args.args[3]("http://192.0.2.2:8088/firmware.rbl")
        self.assertEqual(self.server.commands[-1], "ota_http http://192.0.2.2:8088/firmware.rbl")
        self.assertEqual((result["seconds"], result["version"]), (4.0, version))

    def test_the_check_waits_for_the_new_release(self):
        ctx = self.ctx(firmware="openbeken", source="outdated", update_from="1.0.9", update_to=self.published)
        answers = iter(["1.0.9", self.published])

        def sleep(_seconds):
            self.server.version = next(answers)
        with mock.patch.object(time, "sleep", side_effect=sleep), mock.patch.object(update.output, "say"):
            self.server.version = "1.0.9"
            result = update.wait_for_update(ctx, wait=60.0)
        self.assertEqual((result["from"], result["to"]), ("1.0.9", self.published))

    def test_a_send_cut_off_is_judged_by_the_release_it_runs(self):
        ctx = self.ctx(firmware="openbeken", source="outdated", update_from="1.0.9", update_to=self.published)
        for version, finished in ((self.published, True), ("1.0.9", False)):
            self.server.version = version
            self.assertIs(beken.ATTEMPTED_EVIDENCE["U3"](ctx)[0], finished, version)
            self.assertIs(beken.STEP_VERIFIERS["U3"](ctx)[0], finished, version)
        self.server.kind = None   # an OpenBeken not ours, or none: cannot say
        self.assertIsNone(beken.ATTEMPTED_EVIDENCE["U3"](ctx)[0])

    def test_its_report_says_it_was_updated_from_an_earlier_openbeken(self):
        ctx = self.ctx(firmware="openbeken", source="outdated")
        with mock.patch.object(routes, "STEPS", []), mock.patch.object(routes, "ROUTE", ("openbeken", "outdated")):
            fields = dict(report.report_fields(ctx, 60.0, False, result=report.run_result(ctx, "converted")))
        self.assertEqual((fields["Result"], fields["Converted from"]), ("Updated", "OpenBeken, an earlier release"))


if __name__ == "__main__":
    unittest.main()
