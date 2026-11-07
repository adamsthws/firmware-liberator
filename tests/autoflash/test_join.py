"""The plug onto the Wi-Fi and found there (tools/autoflash/join.py)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import device, errors, hostsys, join, output, prompts, routes, setup_network
from tools.autoflash.state import DONE, RunState


class NetworkCheckTests(unittest.TestCase):
    """The plug's live network must be the one the bundle's firmware will join."""

    def setUp(self):
        self.patches = [
            mock.patch.object(join, "bundle_ssid", return_value="Home-2G"),
            mock.patch.object(output, "say"),
            mock.patch.object(prompts, "section"),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in self.patches])

    def ctx(self, ssid, signal=-50):
        from types import SimpleNamespace
        wifi = {"SSId": ssid, "Signal": signal} if ssid else {}
        device = SimpleNamespace(command=lambda _: {"StatusSTS": {"Wifi": wifi}})
        state = SimpleNamespace(input=lambda key: None, set_input=lambda key, value: None)
        return SimpleNamespace(device=device, manifest_path=Path("unused"), state=state)

    def test_a_matching_network_passes(self):
        result = join.check_plug_network(self.ctx("Home-2G"))
        self.assertEqual(result, {"network": "Home-2G", "signal_dbm": -50})

    def test_a_different_network_stops_when_not_rebuilt(self):
        with mock.patch.object(prompts, "ask_yes_no", return_value=False):
            with self.assertRaisesRegex(errors.Abort, "'Guest' but the bundle is set up for 'Home-2G'"):
                join.check_plug_network(self.ctx("Guest"))

    def test_a_different_network_can_be_fixed_with_its_password(self):
        ctx = self.ctx("Guest")
        ctx.adopt_wifi = mock.Mock()
        with mock.patch.object(prompts, "ask_yes_no", return_value=True), \
             mock.patch.object(join, "ask_password", return_value="guest pass") as asked, \
             mock.patch.object(join, "bundle_ssid", side_effect=["Home-2G", "Guest"]):
            result = join.check_plug_network(ctx)
        asked.assert_called_once_with("Guest")  # the name is known: only the password is asked
        ctx.adopt_wifi.assert_called_once_with("Guest", "guest pass")
        self.assertEqual(result["network"], "Guest")

    def test_names_differing_only_in_case_do_not_match(self):
        with mock.patch.object(prompts, "ask_yes_no", return_value=False):
            with self.assertRaises(errors.Abort):
                join.check_plug_network(self.ctx("home-2g"))

    def test_no_reported_network_stops_rather_than_guessing(self):
        with self.assertRaisesRegex(errors.Abort, "did not say which Wi-Fi network"):
            join.check_plug_network(self.ctx(None))

    def test_a_weak_signal_needs_a_deliberate_yes(self):
        with mock.patch.object(prompts, "ask_yes_no", return_value=False):
            with self.assertRaisesRegex(errors.Abort, "weak Wi-Fi signal"):
                join.check_plug_network(self.ctx("Home-2G", signal=-82))
        with mock.patch.object(prompts, "ask_yes_no", return_value=True):
            self.assertEqual(
                join.check_plug_network(self.ctx("Home-2G", signal=-82))["signal_dbm"], -82
            )


class AdvertSignalTests(unittest.TestCase):
    """From Sonoff's firmware the signal is read at A2, before anything is
    written, from the plug's own eWeLink advert, opened with its LAN key."""

    def report(self, source="192.0.2.50"):
        from tests.autoflash.test_ewelink_lan import ADVERT
        return {"packets": [{"source": source, "records": [
            {"type": "TXT", "value": [f"{k}={v}" for k, v in ADVERT.items()]}]}]}

    def ctx(self, devices):
        from types import SimpleNamespace
        return SimpleNamespace(account_devices=lambda: devices)

    def test_the_plugs_own_advert_gives_its_signal(self):
        from tests.autoflash.test_ewelink_lan import ADVERT, KEY
        ctx = self.ctx([{"deviceid": "1000ffffff", "devicekey": "other"},
                        {"deviceid": ADVERT["id"], "devicekey": KEY}])
        self.assertEqual(join.advert_signal(ctx, self.report(), "192.0.2.50"), -61)

    def test_another_devices_advert_or_no_key_gives_nothing(self):
        from tests.autoflash.test_ewelink_lan import ADVERT, KEY
        key = [{"deviceid": ADVERT["id"], "devicekey": KEY}]
        self.assertIsNone(join.advert_signal(self.ctx(key), self.report("192.0.2.7"), "192.0.2.50"))
        self.assertIsNone(join.advert_signal(self.ctx([]), self.report(), "192.0.2.50"))
        self.assertIsNone(join.advert_signal(self.ctx(None), self.report(), "192.0.2.50"))


class PlugFinderTests(unittest.TestCase):
    """The plug is found at its address, or by its hardware address if it moved."""

    def finder(self, inputs):
        from types import SimpleNamespace

        moved = []
        state = SimpleNamespace(input=lambda key: inputs.get(key))
        ctx = SimpleNamespace(state=state, set_plug_ip=moved.append)
        return join.PlugFinder(ctx), moved

    def test_the_plug_at_its_usual_address_is_found_without_a_sweep(self):
        from tools.autoflash import hostsys
        finder, moved = self.finder({"plug_ip": "10.0.0.5", "plug_mac": "aa:bb:cc:dd:ee:ff"})
        with mock.patch.object(hostsys, "ping_ok", return_value=True), \
             mock.patch.object(hostsys, "neighbour_table", return_value={"10.0.0.5": "aa:bb:cc:dd:ee:ff"}), \
             mock.patch.object(hostsys, "find_by_mac") as sweep:
            self.assertEqual(finder.poll(), "10.0.0.5")
        sweep.assert_not_called()
        self.assertEqual(moved, [])

    def test_a_different_device_at_the_old_address_is_not_mistaken_for_the_plug(self):
        from tools.autoflash import hostsys
        finder, moved = self.finder({"plug_ip": "10.0.0.5", "plug_mac": "aa:bb:cc:dd:ee:ff"})
        with mock.patch.object(hostsys, "ping_ok", return_value=True), \
             mock.patch.object(hostsys, "neighbour_table", return_value={"10.0.0.5": "11:22:33:44:55:66"}), \
             mock.patch.object(hostsys, "find_by_mac", return_value="10.0.0.9"):
            self.assertEqual(finder.poll(), "10.0.0.9")
        self.assertEqual(moved, ["10.0.0.9"])

    def test_sweeps_are_rate_limited(self):
        from tools.autoflash import hostsys
        finder, _ = self.finder({"plug_ip": "10.0.0.5", "plug_mac": "aa:bb:cc:dd:ee:ff"})
        with mock.patch.object(hostsys, "ping_ok", return_value=False), \
             mock.patch.object(hostsys, "find_by_mac", return_value=None) as sweep:
            self.assertIsNone(finder.poll())
            self.assertIsNone(finder.poll())
        self.assertEqual(sweep.call_count, 1)


class BusyPlugTests(unittest.TestCase):
    """A plug still finishing a stopped step is waited for, not misread."""

    def ctx(self, *, d1_done=True, answers=(), pings=True):
        from types import SimpleNamespace
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        state.set_input("plug_ip", "10.0.0.63")
        if d1_done:
            state.mark("D1", DONE)
        device = mock.Mock(answers=mock.Mock(side_effect=list(answers)))
        return SimpleNamespace(state=state, device=device, steps=routes.STEPS), pings

    def wait(self, ctx, pings):
        clock = iter(range(0, 100_000, 3))
        with mock.patch.object(hostsys, "ping_ok", return_value=pings), \
             mock.patch.object(time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(time, "sleep"), \
             mock.patch.object(output, "tell") as told, \
             mock.patch.object(output, "progress"), \
             mock.patch.object(output, "progress_clear"):
            join.wait_while_plug_busy(ctx)
        return told

    def test_a_plug_that_answers_is_not_waited_for(self):
        ctx, pings = self.ctx(answers=[True])
        self.wait(ctx, pings).assert_not_called()

    def test_a_busy_plug_is_waited_for_until_it_answers(self):
        ctx, pings = self.ctx(answers=[False, False, False, True])
        told = self.wait(ctx, pings)
        self.assertEqual(ctx.device.answers.call_count, 4)
        self.assertIn("answering again", told.call_args.args[0])

    def test_a_plug_off_the_network_is_left_to_the_steps(self):
        ctx, _ = self.ctx(answers=[False])
        self.wait(ctx, False).assert_not_called()

    def test_before_tasmota_nothing_is_checked(self):
        ctx, pings = self.ctx(d1_done=False)
        self.wait(ctx, pings)
        ctx.device.answers.assert_not_called()


class UserActionWaitTests(unittest.TestCase):
    """Waiting on a person has no time limit - unless nobody is coming."""

    def test_a_person_is_waited_for_as_long_as_it_takes(self):
        with mock.patch.object(prompts, "UNATTENDED", False):
            self.assertEqual(join.user_action_limit(), float("inf"))
        with mock.patch.object(prompts, "UNATTENDED", True):
            self.assertEqual(join.user_action_limit(), 900.0)


class NamedWithTheWifiTests(unittest.TestCase):
    """A Tasmota whose settings were erased (switched off and on 7 times) is
    named again as it saves the Wi-Fi given on its setup network."""

    def run_it(self, current, done=()):
        from types import SimpleNamespace
        hooked = []

        class Plug:
            def __init__(self, host, timeout=None):
                self.host = host

            def command(self, c, timeout=None):
                # As Tasmota answers on its setup network: Hostname would give
                # the setting (%s-%04d for its default), Status 5 the name.
                return {"StatusNET": {"Hostname": current}} if c == "Status 5" else None

            def at_next_save(self, commands, timeout=None):
                hooked.append((self.host, commands))
                return True

        inputs = {"plug_mac": "12:34:56:ab:cd:ef"}
        ctx = SimpleNamespace(state=SimpleNamespace(input=inputs.get, is_done=lambda step: step in done))
        with mock.patch.object(device, "Device", Plug), mock.patch.object(output, "say"):
            join.name_with_the_wifi(ctx)
        return hooked

    def test_part_way_through_it_is_the_runs_name_for_it_again(self):
        self.assertEqual(self.run_it("tasmota-ABCDEF-1234"),
                         [("192.168.4.1", ["Hostname autoflash-recovery-CDEF"])])

    def test_once_converted_it_is_its_converted_name_again(self):
        self.assertEqual(self.run_it("tasmota-ABCDEF-1234", done=("F4",)),
                         [("192.168.4.1", ["Hostname tasmota-abcdef"])])

    def test_a_name_it_still_has_is_left_alone(self):
        for name in ("autoflash-recovery-CDEF", "tasmota-abcdef", "kitchen-plug"):
            self.assertEqual(self.run_it(name), [], name)


class SetupNetworkGoneTests(unittest.TestCase):
    """Whether the plug's setup network has gone, after the Wi-Fi was sent."""

    def gone(self, answering, visible, network="tasmota-ABCDEF-1234"):
        from types import SimpleNamespace
        clock = iter(range(0, 1000, 2))
        ctx = SimpleNamespace(state=SimpleNamespace(input={"plug_mac": "12:34:56:ab:cd:ef"}.get))
        with mock.patch.object(setup_network, "port_open", return_value=answering), \
             mock.patch.object(prompts, "scan_wifi_ssids", return_value=visible), \
             mock.patch.object(time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(time, "sleep"):
            return join.setup_network_gone(ctx, network)

    def test_nothing_answering_and_not_listed_is_gone(self):
        self.assertTrue(self.gone(False, ["Home"]))

    def test_still_answering_is_not(self):
        self.assertFalse(self.gone(True, ["Home"]))

    def test_still_listed_is_not(self):
        self.assertFalse(self.gone(False, ["Home", "tasmota-ABCDEF-1234"]))

    def test_a_scan_that_sees_nothing_cannot_say(self):
        self.assertFalse(self.gone(False, []))

    def test_given_as_a_hint_any_of_its_names_counts(self):
        hint = "tasmota-abcdef, tasmota-ABCDEF-NNNN, or ESP_ABCDF0"
        self.assertFalse(self.gone(False, ["ESP_ABCDF0"], network=hint))
        self.assertTrue(self.gone(False, ["Home"], network=hint))


if __name__ == "__main__":
    unittest.main()
