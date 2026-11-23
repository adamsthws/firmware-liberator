"""arrival: a device looked for until it is back, and back on Sonoff's firmware."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.autoflash.routes.beken.test_beken import a_context
from tools.autoflash import detect, errors, kinds, records
from tools.autoflash.routes import arrival
from tools.ewelink import get_device_key
from tests.ewelink.test_get_device_key import queued_turns, taking_turns

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


class NotBackFromSonoffTests(unittest.TestCase):
    """An image Sonoff's firmware sent, not back: whether Sonoff's firmware
    still answers is looked for before the person is asked."""

    def not_back(self, ctx, told, *, sent_by="B2", from_sonoff=True, unattended=False):
        """until_back with a look never finding it, still_on_sonoff saying
        `told`, and q at the question; the lines it was asked with, if it was."""
        def look(_restarted):
            raise arrival.NotBack
        with mock.patch.object(arrival, "still_on_sonoff", return_value=told) as self.checked, \
                mock.patch.object(arrival.prompts, "UNATTENDED", unattended), \
                mock.patch.object(arrival.prompts, "section") as shown, \
                mock.patch.object(arrival.prompts, "ask_choice", side_effect=arrival.prompts.QuitRun(130)), \
                mock.patch.object(arrival.output, "transient_begin"):
            try:
                arrival.until_back(ctx, look, "OpenBeken", "advice", sent_by, from_sonoff=from_sonoff)
            except arrival.prompts.QuitRun:
                return shown.call_args.args[1]
        raise AssertionError("not asked")

    def test_sonoffs_firmware_answering_sends_it_again_unasked(self):
        for unattended in (False, True):
            with self.subTest(unattended=unattended), tempfile.TemporaryDirectory() as tmp:
                ctx = a_context(tmp, firmware="openbeken")
                with self.assertRaises(errors.GoBack) as back:
                    self.not_back(ctx, True, unattended=unattended)
                self.assertEqual((back.exception.step, back.exception.reason, back.exception.outcome),
                                 ("B2", arrival.not_installed("OpenBeken"), "not installed"))
                self.assertIsNone(ctx.state.input("restart_asked"))

    def test_with_no_step_to_go_back_to_it_stops_saying_to_try_again(self):
        # A readout: run again, it sends the readout firmware again.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="openbeken")
            with self.assertRaisesRegex(errors.Abort, "^OpenBeken was not installed: .*try again, and it is sent "
                                                      "again$"):
                self.not_back(ctx, True, sent_by=None)

    def test_neither_answering_the_person_hears_the_device_most_likely_has_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.not_back(a_context(tmp, firmware="openbeken"), False), list(arrival.NEITHER_LINES))

    def test_where_it_cannot_be_told_the_person_is_asked_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.not_back(a_context(tmp, firmware="openbeken"), None), list(arrival.NOT_BACK_LINES))

    def test_unattended_neither_or_cannot_tell_stops_with_the_advice(self):
        for told in (False, None):
            with self.subTest(told=told), tempfile.TemporaryDirectory() as tmp, \
                    self.assertRaisesRegex(errors.Abort, "^advice$"):
                self.not_back(a_context(tmp, firmware="openbeken"), told, unattended=True)

    def test_an_image_another_firmware_sent_is_not_checked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.not_back(a_context(tmp, firmware="openbeken"), True, sent_by="J2",
                                           from_sonoff=False), list(arrival.NOT_BACK_LINES))
            self.checked.assert_not_called()


class StillOnSonoffTests(unittest.TestCase):
    def told(self, tmp, *, running=None, held=True, devices=(), relayed=True, known=True):
        ctx = a_context(tmp, device_ip="192.0.2.50")
        ctx.ewelink = SimpleNamespace(held=("eu", {}) if held else None)
        ctx.account_devices = lambda: None if devices is None else list(devices)
        with mock.patch.object(records, "DEVICES", Path(tmp) / "devices"), \
                mock.patch.object(detect, "identify", return_value=running), \
                mock.patch.object(arrival.output, "log_only") as self.logged, \
                mock.patch.object(arrival.ewelink_relay, "answers", return_value=relayed) as self.relay:
            if known:
                records.remember_device(MAC, ewelink_id="1001234abc")
            found = arrival.still_on_sonoff(ctx)
        self.assertEqual(ctx.state.input(arrival.STOCK_CHECK), arrival.STOCK_CHECKS[found])
        return found

    def test_its_lan_port_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIs(self.told(tmp, running="sonoff"), True)
            self.relay.assert_not_called()

    def test_answering_through_ewelink_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIs(self.told(tmp, devices=[{"deviceid": "1001234abc", "online": True}]), True)

    def test_ewelink_reached_and_the_device_not_answering_there_says_neither(self):
        # Online in eWeLink is not enough: it keeps a silent device online for minutes.
        for devices, relayed in (([{"deviceid": "1001234abc", "online": False}], True),
                                 ([{"deviceid": "1001234abc", "online": True}], False)):
            with self.subTest(devices=devices, relayed=relayed), tempfile.TemporaryDirectory() as tmp:
                self.assertIs(self.told(tmp, devices=devices, relayed=relayed), False)

    def test_cannot_be_told_without_ewelink_or_the_devices_id_there(self):
        # Not signed in to in this run: never a sign-in asked for before the question.
        for case in ({"held": False}, {"devices": None}, {"known": False}):
            with self.subTest(case), tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(self.told(tmp, **case))
                self.relay.assert_not_called()
        self.assertIn("cannot-tell", self.logged.call_args.args[0])


class WaitingSaidTests(unittest.TestCase):
    def test_the_wait_for_the_new_firmware_says_what_it_is(self):
        # Seen in an attended S61 run: minutes of the stage's spinner alone
        # while OpenBeken was waited for, before the run asked for the device
        # to be switched off and on. In either chip family's route.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="openbeken")
            said = []
            with mock.patch.object(arrival.output, "working",
                                   side_effect=lambda text: said.append(text) or mock.MagicMock()):
                self.assertEqual(arrival.until_back(ctx, lambda _restarted: "found", "OpenBeken", "advice"),
                                 "found")
        self.assertEqual(said, ["Waiting for OpenBeken to start on the device and answer - it can take a few "
                                "minutes"])


class PowerCycleTests(unittest.TestCase):
    def test_asked_and_counted_by_whom_the_emulators_switch_used_where_there_is_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="openbeken")
            switch = Path(tmp) / "power"
            with mock.patch.object(arrival.prompts, "section"), \
                    mock.patch.object(arrival.prompts, "ask_choice", side_effect=[0, 1]) as asked, \
                    mock.patch.object(arrival.output, "transient_begin"), mock.patch.object(arrival.output, "transient_end"), \
                    mock.patch.object(arrival.output, "say"), mock.patch.object(arrival.time, "sleep"), \
                    mock.patch.dict(arrival.os.environ, {arrival.EMULATED_POWER: str(switch)}):
                arrival.power_cycle(ctx, "Title", ("a line",), "send it again")
                arrival.power_cycle(ctx, "Title", ("a line",), "send it again")
            self.assertEqual(asked.call_args.args[1], ["Send it again - the device is back on",
                                                       "Power-cycle the emulated device, then send it again"])
            self.assertEqual(ctx.state.input("power_cycles"), {"by hand": 1, "by the tool": 1})
            self.assertEqual(switch.read_text(encoding="utf-8"), "on\n")


class OnlineInEwelinkTests(unittest.TestCase):
    def test_waits_for_the_device_online_and_gives_up_at_once_without_ewelink(self):
        answers = iter([[{"deviceid": "1001234abc", "online": False}], [{"deviceid": "1001234abc", "online": True}]])
        ctx = SimpleNamespace(account_devices=lambda: next(answers))
        with mock.patch.object(arrival.time, "sleep"), mock.patch.object(arrival.output, "progress"), \
                mock.patch.object(arrival.output, "progress_clear"):
            self.assertTrue(arrival.online_in_ewelink(ctx, "1001234abc"))
            self.assertFalse(arrival.online_in_ewelink(SimpleNamespace(account_devices=lambda: None), "1001234abc"))


class AnsweringTests(unittest.TestCase):
    def test_ewelinks_word_and_the_devices_answer_come_in_one_turn_in_the_fault_suite(self):
        # The device reaches eWeLink there only in its run's turn: listed online
        # outside it, it is another run's device, which is what would answer.
        within = []

        def devices():
            within.append(get_device_key.in_turn())
            return [{"deviceid": "1001234abc", "online": True}]

        def answers(**_kwargs):
            within.append(get_device_key.in_turn())
            return True
        ctx = SimpleNamespace(account_devices=devices, ewelink=mock.sentinel.account)
        with taking_turns() as asked, mock.patch.object(arrival.ewelink_relay, "answers", side_effect=answers):
            self.assertEqual(arrival.answering(ctx, "1001234abc")[1:], (True, True))
        self.assertEqual((asked, within), (["command"], [True, True]))


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
                    self.assertRaisesRegex(errors.Abort, "on your Wi-Fi but not in eWeLink.*internet"):
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

    def test_time_queued_for_turns_is_not_taken_from_the_wait(self):
        # Seen against the real cloud: R2 runs queued 50-280 s for turns,
        # behind other runs', all of it out of SONOFF_WAIT.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self.espressif(tmp)
            ctx.account_devices = lambda: [{"deviceid": "1001234abc", "online": True}]
            ctx.ewelink = mock.sentinel.account
            with mock.patch.object(records, "DEVICES", Path(tmp) / "devices"), \
                    mock.patch.object(detect, "identify", return_value="sonoff"), \
                    mock.patch.object(arrival.ewelink_relay, "answers", side_effect=[False, False, False, True]), \
                    mock.patch.object(arrival.prompts, "UNATTENDED", True), queued_turns(100.0):
                records.remember_device(MAC, ewelink_id="1001234abc")
                result = arrival.back_on_sonoff(ctx, wait=60)
            self.assertEqual((result["confirmed_by"], result["seconds"]), ("eWeLink", 30.0))

    def test_with_ewelink_out_of_reach_the_wifi_says(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self.espressif(tmp)
            self.assertEqual(self.back(ctx, tmp, running="sonoff", devices=None)["confirmed_by"], "your Wi-Fi")

    def test_every_family_answering_nothing_known_is_on_the_wifi_by_its_hardware_address(self):
        # Sonoff's firmware opens eWeLink's LAN port only with LAN Control on.
        for kind in (kinds.SONOFF_S60, kinds.SONOFF_S61):
            with self.subTest(kind.key), tempfile.TemporaryDirectory() as tmp:
                ctx = a_context(tmp, device_ip="192.0.2.50")
                ctx.state.set_input("device_kind", kind.key)
                ctx.finder = SimpleNamespace(poll=lambda: "192.0.2.50")
                self.assertEqual(self.back(ctx, tmp, devices=None)["confirmed_by"], "your Wi-Fi")
                self.assertTrue(arrival.on_wifi(ctx, None))
                # Or by eWeLink's LAN port, with LAN Control on.
                self.assertTrue(arrival.on_wifi(ctx, "sonoff"))
                # Still the firmware it was to leave: not Sonoff's on the Wi-Fi.
                self.assertFalse(arrival.on_wifi(ctx, "openbeken"))
                ctx.finder = SimpleNamespace(poll=lambda: None)
                self.assertFalse(arrival.on_wifi(ctx, None), "not on the Wi-Fi at all")


class SonoffAnswersTests(unittest.TestCase):
    """A wait that must not take another firmware starting for Sonoff's: its
    LAN port, or the device at its hardware address answering through eWeLink."""

    def answers(self, tmp, *, running=None, seen=MAC, online=True, relayed=True):
        ctx = a_context(tmp, device_ip="192.0.2.50")
        ctx.state.set_input("device_kind", kinds.SONOFF_S60.key)
        ctx.account_devices = lambda: [{"deviceid": "1001234abc", "online": online}]
        ctx.ewelink = mock.sentinel.account
        with mock.patch.object(records, "DEVICES", Path(tmp) / "devices"), \
                mock.patch.object(detect, "identify", return_value=running), \
                mock.patch.object(arrival.hostsys, "mac_of", return_value=seen), \
                mock.patch.object(arrival.ewelink_relay, "answers", return_value=relayed) as self.relay:
            records.remember_device(MAC, ewelink_id="1001234abc")
            return arrival.sonoff_answers(ctx, "192.0.2.50")

    def test_its_lan_port_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(self.answers(tmp, running="sonoff", relayed=False))
            self.relay.assert_not_called()

    def test_with_no_lan_control_the_device_answering_through_ewelink_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(self.answers(tmp))
            self.relay.assert_called_once_with(account=mock.sentinel.account, device_id="1001234abc")

    def test_nothing_less_will_do(self):
        for case in ({"running": "installer"}, {"seen": None}, {"seen": "12:34:56:ab:cd:00"},
                     {"online": False}, {"relayed": False}, {"relayed": None}):
            with self.subTest(case), tempfile.TemporaryDirectory() as tmp:
                self.assertFalse(self.answers(tmp, **case))


if __name__ == "__main__":
    unittest.main()
