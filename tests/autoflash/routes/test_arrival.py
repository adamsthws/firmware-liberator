"""arrival: a device looked for until it is back, and back on Sonoff's firmware."""

import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.autoflash.routes.beken.test_beken import a_context
from tools.autoflash import detect, errors, kinds, records
from tools.autoflash.routes import arrival

MAC = "12:34:56:ab:cd:ef"


class UntilBackTests(unittest.TestCase):
    def test_how_long_a_device_took_to_answer_once_back_on_is_kept_the_longest(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="openbeken")
            looks = iter([arrival.NotBack(), "found", arrival.NotBack(), "found"])

            def look(_restarted):
                found = next(looks)
                if isinstance(found, Exception):
                    raise found
                return found
            # Said back on at 100 s, found at 141; then at 200, found at 210.
            clock = iter([100.0, 141.0, 200.0, 210.0])
            with mock.patch.object(arrival.prompts, "section"), mock.patch.object(arrival.prompts, "ask_choice", return_value=0), \
                    mock.patch.object(arrival.output, "transient_begin"), mock.patch.object(arrival.output, "transient_end"), \
                    mock.patch.object(arrival.time, "monotonic", side_effect=lambda: next(clock)):
                self.assertEqual(arrival.until_back(ctx, look, "OpenBeken", "advice"), "found")
                self.assertEqual(arrival.until_back(ctx, look, "OpenBeken", "advice"), "found")
            self.assertEqual(ctx.state.input("back_after_power_cycle"), 41)
            self.assertEqual(ctx.state.input("power_cycles"), {"by hand": 2})

    def test_a_not_back_that_says_why_is_asked_and_stopped_with_its_own_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="openbeken")

            def look(_restarted):
                raise arrival.NotBack("its own reason", ("its own line",))
            with mock.patch.object(arrival.prompts, "UNATTENDED", True), \
                    self.assertRaisesRegex(errors.Abort, "^its own reason$"):
                arrival.until_back(ctx, look, "OpenBeken", "advice")
            with mock.patch.object(arrival.prompts, "section") as shown, \
                    mock.patch.object(arrival.prompts, "ask_choice", side_effect=arrival.prompts.QuitRun(130)), \
                    mock.patch.object(arrival.output, "transient_begin"), self.assertRaises(arrival.prompts.QuitRun):
                arrival.until_back(ctx, look, "OpenBeken", "advice")
            self.assertEqual(shown.call_args.args[1], ["its own line"])


class BackOnSonoffTests(unittest.TestCase):
    """R2 and K3 alike: on the Wi-Fi, and in eWeLink."""

    def setUp(self):
        for name in ("say", "tell", "progress", "progress_clear"):
            patcher = mock.patch.object(arrival.output, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def back(self, ctx, tmp, *, running=None, wait=50, devices=None, answers=True, **kwargs):
        ctx.account_devices = (lambda: devices) if not callable(devices) else devices
        ctx.ewelink = mock.sentinel.account
        with mock.patch.object(records, "DEVICES", Path(tmp) / "devices"), \
                mock.patch.object(detect, "identify", return_value=running), \
                mock.patch.object(arrival.ewelink_relay, "answers", return_value=answers), \
                mock.patch.object(arrival.time, "sleep"), \
                mock.patch.object(arrival.time, "monotonic", side_effect=iter(range(50_000, 60_000, 5))):
            if kwargs.get("ewelink_id", True):
                records.remember_device(MAC, ewelink_id="1001234abc")
            return arrival.back_on_sonoff(ctx, wait=wait, leaving=kwargs.get("leaving", ()))

    def espressif(self, tmp):
        ctx = a_context(tmp, device_ip="192.0.2.50")
        ctx.state.set_input("device_kind", kinds.SONOFF_S60.key)
        return ctx

    def test_on_the_wifi_but_never_in_ewelink_the_person_hears_of_the_internet(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self.espressif(tmp)
            offline = [{"deviceid": "1001234abc", "online": False}]
            with mock.patch.object(arrival.prompts, "UNATTENDED", True), \
                    self.assertRaisesRegex(errors.Abort, "on your Wi-Fi but has not reached eWeLink.*internet"):
                self.back(ctx, tmp, running="sonoff", devices=offline)
            with mock.patch.object(arrival.prompts, "section") as shown, \
                    mock.patch.object(arrival.prompts, "ask_choice", side_effect=arrival.prompts.QuitRun(130)), \
                    mock.patch.object(arrival.output, "transient_begin"), self.assertRaises(arrival.prompts.QuitRun):
                self.back(ctx, tmp, running="sonoff", devices=offline)
            self.assertEqual(shown.call_args.args[1], list(arrival.ON_WIFI_ONLY_LINES))

    def test_back_in_ewelink_it_says_when_it_was_on_the_wifi_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self.espressif(tmp)
            result = self.back(ctx, tmp, running="sonoff", devices=[{"deviceid": "1001234abc", "online": True}])
            self.assertEqual(result["confirmed_by"], "eWeLink")
            self.assertIn("on_wifi_after", result)

    def test_with_ewelink_out_of_reach_the_wifi_says(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self.espressif(tmp)
            self.assertEqual(self.back(ctx, tmp, running="sonoff", devices=None)["confirmed_by"], "your Wi-Fi")

    def test_a_device_with_no_lan_control_is_on_the_wifi_by_its_hardware_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, device_ip="192.0.2.50")   # an S61: no eWeLink discovery on the LAN
            ctx.finder = SimpleNamespace(poll=lambda: "192.0.2.50")
            self.assertFalse(kinds.of_run(ctx.state).uses_lan_control)
            result = self.back(ctx, tmp, devices=None)
            self.assertEqual(result["confirmed_by"], "your Wi-Fi")
            # Still the firmware it was to leave: not Sonoff's on the Wi-Fi.
            self.assertFalse(arrival.on_wifi(ctx, "openbeken"))

    def test_an_espressif_device_on_the_wifi_answers_as_sonoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self.espressif(tmp)
            ctx.finder = SimpleNamespace(poll=lambda: "192.0.2.50")
            self.assertTrue(arrival.on_wifi(ctx, "sonoff"))
            self.assertFalse(arrival.on_wifi(ctx, None))
            lan = dataclasses.replace(kinds.SONOFF_S61, uses_lan_control=True)
            with mock.patch.object(kinds, "of_run", return_value=lan):
                self.assertFalse(arrival.on_wifi(ctx, None))


if __name__ == "__main__":
    unittest.main()
