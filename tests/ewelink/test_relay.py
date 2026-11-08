import unittest
from unittest import mock

from tools.ewelink import relay


class Plug:
    """The cloud's socket as the app sees it, with a plug behind it that
    reports each switch it is sent - or, `obeys` False, none."""

    def __init__(self, obeys=True, error=0, reports=False, knows_readings=True):
        self.sent, self.obeys, self.error, self.on, self.reports = [], obeys, error, False, reports
        self.knows_readings = knows_readings

    def send_json(self, value):
        self.sent.append(value)

    def receive_json(self, timeout=None):
        last = self.sent[-1]
        if last.get("action") == "userOnline":
            return {"error": 0}
        if self.error:
            return {"error": self.error, "sequence": last["sequence"]}
        if not self.obeys:
            raise relay.socket.timeout
        if "uiActive" in last["params"]:   # its readings asked for: answered, then sent
            if not self.knows_readings:    # a plug with no meter says nothing
                raise relay.socket.timeout
            return {"error": 0, "deviceid": "1000abcdef", "sequence": last["sequence"]}
        if "sledOnline" in last["params"]:
            return {"error": 0, "deviceid": "1000abcdef", "sequence": last["sequence"]}
        self.on = last["params"]["switches"][0]["switch"] == "on"
        if self.reports:   # its new state, as some plugs say it
            return {"action": "update", "deviceid": "1000abcdef",
                    "params": {"switches": [{"outlet": 0, "switch": "on" if self.on else "off"}]}}
        return {"error": 0, "deviceid": "1000abcdef", "sequence": last["sequence"]}   # the S61's answer

    def close(self):
        pass


DEVICE = {"deviceid": "1000abcdef", "apikey": "made-up", "devicekey": "made-up", "online": True,
          "params": {"switches": [{"outlet": 0, "switch": "off"}]}}


class RelayTests(unittest.TestCase):
    def check(self, plug, device=DEVICE, ask=relay.check_relay, sockets=None, **more):
        """Asked of `plug`, or of `sockets` one connection at a time."""
        if ask is relay.check_relay:
            more.setdefault("say", lambda *a: None)
        connect = {"side_effect": sockets} if sockets else {"return_value": plug}
        with mock.patch.object(relay, "login_session", return_value=("eu", {"at": "t", "user": {"apikey": "a"}})), \
                mock.patch.object(relay, "get_devices", return_value=[device]), \
                mock.patch.object(relay, "api_json", return_value={"domain": "192.0.2.1", "port": 443}), \
                mock.patch.object(relay.WebSocket, "connect", **connect):
            return ask(email="someone@example.invalid", password="made-up-password",
                       country_code="+44", region="eu", device_id="1000abcdef", **more)

    def test_a_plug_that_answers_is_there_and_nothing_on_it_changes(self):
        plug = Plug()
        self.assertIs(self.check(plug, ask=relay.answers), True)
        self.assertEqual([m["params"] for m in plug.sent if m.get("action") == "update"], [{"uiActive": 60}])
        self.assertFalse(plug.on)

    def test_a_plug_with_no_meter_is_asked_to_keep_its_leds_setting(self):
        plug = Plug(knows_readings=False)
        device = {**DEVICE, "params": {**DEVICE["params"], "sledOnline": "off"}}
        self.assertIs(self.check(plug, device, ask=relay.answers), True)
        self.assertEqual([m["params"] for m in plug.sent if m.get("action") == "update"],
                         [{"uiActive": 60}, {"sledOnline": "off"}])
        self.assertFalse(plug.on)

    def test_with_no_led_setting_reported_nothing_more_is_sent(self):
        plug = Plug(knows_readings=False)
        self.assertIs(self.check(plug, ask=relay.answers), False)
        self.assertEqual([m["params"] for m in plug.sent if m.get("action") == "update"], [{"uiActive": 60}])

    def test_a_plug_online_still_from_before_does_not_answer(self):
        # eWeLink keeps a plug that restarted online until its heartbeats are
        # missed: what is sent to it meanwhile goes to no one.
        self.assertIs(self.check(Plug(obeys=False), ask=relay.answers), False)
        self.assertIs(self.check(Plug(error=504), ask=relay.answers), False)

    def test_whether_it_answers_is_not_known_without_ewelink(self):
        with mock.patch.object(relay, "login_session", side_effect=relay.EwelinkError("no route")):
            self.assertIsNone(relay.answers(email="someone@example.invalid", password="made-up-password",
                                            country_code="+44", region="eu", device_id="1000abcdef"))

    def test_the_relay_is_switched_over_and_back(self):
        plug = Plug()
        self.assertEqual(self.check(plug), (True, True))
        switched = [m["params"] for m in plug.sent if m.get("action") == "update"]
        self.assertEqual(switched, [{"switches": [{"switch": "on", "outlet": 0}]},
                                    {"switches": [{"switch": "off", "outlet": 0}]}])
        self.assertFalse(plug.on)   # as it was

    def test_a_report_of_the_new_state_confirms_a_switch_too(self):
        self.assertEqual(self.check(Plug(reports=True)), (True, True))

    def test_a_query_from_the_plug_passed_on_meanwhile_is_not_its_report(self):
        # Seen in the emulator: as Sonoff's firmware came back it asked the cloud
        # for its settings, a query whose params are a list of their names.
        class Asking(Plug):
            def receive_json(self, timeout=None):
                if self.sent[-1].get("action") == "update" and not getattr(self, "asked", False):
                    self.asked = True
                    return {"action": "query", "deviceid": "1000abcdef", "params": ["TZ", "timers"]}
                return super().receive_json(timeout)
        self.assertEqual(self.check(Asking(reports=True)), (True, True))

    def test_a_switch_the_plug_never_reports_is_not_confirmed(self):
        with mock.patch.object(relay, "SWITCH_WAIT", 0.2), mock.patch.object(relay, "RESTORE_WAIT", 0.5), \
                mock.patch.object(relay.time, "sleep"):
            self.assertEqual(self.check(Plug(obeys=False)), (False, False))

    def test_a_plug_that_drops_off_as_it_switches_is_switched_back_once_it_returns(self):
        # Seen in the emulator: the plug confirmed the first switch, closed its
        # connection, and the switch back reached no one; the relay was left over.
        class Dropping(Plug):
            def receive_json(self, timeout=None):
                if self.sent[-1].get("action") == "update" and len(self.sent) > 2:
                    raise relay.socket.timeout       # the switch back, sent as it went
                return super().receive_json(timeout)
        first, again = Dropping(), Plug()
        again.on = True
        with mock.patch.object(relay.time, "sleep"), mock.patch.object(relay, "SWITCH_WAIT", 0.2):
            self.assertEqual(self.check(first, sockets=[first, again]), (True, True))
        self.assertFalse(again.on)   # as it was

    def test_a_plug_that_never_returns_is_given_up_on(self):
        with mock.patch.object(relay, "SWITCH_WAIT", 0.2), mock.patch.object(relay, "RESTORE_WAIT", 0.5), \
                mock.patch.object(relay.time, "sleep"):
            self.assertEqual(self.check(Plug(obeys=False)), (False, False))

    def test_a_switch_turned_down_is_not_confirmed(self):
        with mock.patch.object(relay, "RESTORE_WAIT", 0.5), mock.patch.object(relay.time, "sleep"):
            self.assertEqual(self.check(Plug(error=504)), (False, False))

    def test_an_offline_plug_is_not_tried(self):
        plug = Plug()
        self.assertIsNone(self.check(plug, {**DEVICE, "online": False}))
        self.assertEqual(plug.sent, [])

    def test_a_single_switch_is_read_and_sent_as_one(self):
        self.assertIs(relay.relay_state({"switch": "on"}), True)
        self.assertEqual(relay.switch_params({"switch": "on"}, False), {"switch": "off"})
        self.assertIsNone(relay.relay_state({"fwVersion": "1.3.0"}))


if __name__ == "__main__":
    unittest.main()
