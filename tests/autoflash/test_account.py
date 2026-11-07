"""The plug in its eWeLink account (tools/autoflash/account.py)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import account, errors, hostsys, kinds, output, plugs, prompts, runs, setup_network
from tools.autoflash.routes.espressif import bundle
from tools.autoflash.state import RunState
from tools.ewelink import discover_ewelink, firmware_name


class ModelGuardTests(unittest.TestCase):
    """Only a plug of a kind this tool converts may continue past A2 - another
    Sonoff on the account must not."""

    learned = (None, [])
    saved: dict = {}

    def discover_with_model(self, model):
        import json
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        state = self.state = RunState.open(root)
        state.set_input("plug_ip", "10.0.0.63")
        ctx = SimpleNamespace(dir=root, state=state, email=lambda: "a@b.c",
                              ewelink_env=lambda: {})

        def fake_tool(args, **_):
            if "tools/ewelink/query_ota.py" in args:
                self.tool_args = args
                (root / "ota-metadata.json").write_text(json.dumps({
                    "query": {"version": "1.2.0", "model": model, "deviceid": self.saved.get("deviceid", "")},
                    "response": {"data": {"otaInfoList": []}},
                    "latest_offer": self.saved.get("latest_offer"),
                }))
            return 0

        answered = {"packets": [{"source": "10.0.0.63", "records": []}]}
        with mock.patch.object(hostsys, "run_tool", side_effect=fake_tool), \
             mock.patch.object(discover_ewelink, "discover", return_value=answered), \
             mock.patch.object(firmware_name, "learn", return_value=self.learned) as self.learn, \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            return account.step_discover(ctx)

    def test_an_s60_continues(self):
        self.assertEqual(self.discover_with_model("SN-ESP32C3-S60-01")["model"], "SN-ESP32C3-S60-01")
        self.assertEqual(self.state.input("plug_kind"), "sonoff-s60")  # kept for the run

    def test_the_name_learned_is_kept_with_the_step(self):
        self.learned = (firmware_name.Learned("FWSW-E32S60-S60-ESP32C3FN4", "ESP32C3", "a test"), [])
        result = self.discover_with_model("SN-ESP32C3-S60-01")
        self.assertEqual(result["firmware_name"], "FWSW-E32S60-S60-ESP32C3FN4")
        self.learn.assert_called_once_with([], "", "", "SN-ESP32C3-S60-01")

    def test_the_latest_offer_names_the_firmware_and_is_not_an_update_on_offer(self):
        offer = [{"version": "1.2.0", "binList": [{"name": "user1.bin", "downloadUrl": "http://192.0.2.9:8088/user1.bin"}]}]
        self.saved = {"latest_offer": {"offers": offer}, "deviceid": "10000e32ab"}
        result = self.discover_with_model("SN-ESP32C3-S60-01")
        self.learn.assert_called_once_with(offer, "10000e32ab", "", "SN-ESP32C3-S60-01")
        self.assertFalse(result["update_offered"])
        self.assertNotIn("10000e32ab", str(result))

    def test_asks_query_ota_for_the_latest_offer(self):
        self.discover_with_model("SN-ESP32C3-S60-01")
        self.assertIn("--latest-offer", self.tool_args)

    def test_the_offer_failing_is_logged_and_the_run_goes_on(self):
        self.saved = {"latest_offer": {"problem": "EwelinkError"}}
        with mock.patch.object(output, "log_only") as logged:
            result = self.discover_with_model("SN-ESP32C3-S60-01")
        self.assertEqual(result["firmware_name"], "")
        self.assertIn("EwelinkError", logged.call_args_list[0].args[0])

    def test_a_device_turned_down_has_its_name_looked_for_first(self):
        with self.assertRaises(errors.Abort):
            self.discover_with_model("SN-ESP32-MINIR4")
        self.learn.assert_called_once()

    def test_any_other_model_is_refused_before_anything_is_sent(self):
        with self.assertRaisesRegex(errors.Abort, "SN-ESP32-MINIR4, which this tool does not convert yet"):
            self.discover_with_model("SN-ESP32-MINIR4")


class FirmwareNameTests(unittest.TestCase):
    """A2 logs the name a stock updater checks, and goes on whatever it learns."""

    def note(self, kind, learned, problems=()):
        answer = firmware_name.Learned(learned, firmware_name.chip_of(learned), "eWeLink's update file") if learned else None
        with mock.patch.object(firmware_name, "learn", return_value=(answer, list(problems))), \
             mock.patch.object(output, "log_only") as logged, mock.patch.object(output, "tell") as told:
            name = account.note_firmware_name(kind, {}, "", "S60TPG", "SN-ESP32C3-S60-01")
        return name, [call.args[0] for call in logged.call_args_list], [call.args[0] for call in told.call_args_list]

    def test_a_name_that_agrees_is_only_logged(self):
        wrapper = kinds.SONOFF_S60.wrapper_model
        name, logged, told = self.note(kinds.SONOFF_S60, wrapper)
        self.assertEqual(name, wrapper)
        self.assertEqual(told, [])
        self.assertIn(wrapper, logged[0])
        self.assertIn("ESP32C3", logged[0])

    def test_a_name_that_differs_is_said_plainly(self):
        name, _, told = self.note(kinds.SONOFF_S60, "FWSW-E32S60B-S60-ESP32C3FN4")
        self.assertEqual(name, "FWSW-E32S60B-S60-ESP32C3FN4")
        self.assertIn("FWSW-E32S60B-S60-ESP32C3FN4", told[0])
        self.assertIn(kinds.SONOFF_S60.wrapper_model, told[0])

    def test_an_unknown_device_has_no_name_to_differ_from(self):
        name, logged, told = self.note(None, "FWSW-E32R9-NEW-ESP32C3")
        self.assertEqual(told, [])
        self.assertIn("FWSW-E32R9-NEW-ESP32C3", logged[0])
        self.assertEqual(name, "FWSW-E32R9-NEW-ESP32C3")

    def test_not_learned_is_logged_with_why(self):
        name, logged, told = self.note(kinds.SONOFF_S60, "", ["the public file list could not be read (URLError)"])
        self.assertEqual(name, "")
        self.assertEqual(told, [])
        self.assertIn("URLError", logged[0])
        self.assertIn("not learned", logged[1])


class AlreadyConvertingTests(unittest.TestCase):
    """A plug part way through a conversion is not a LAN Control problem."""

    def silent_discover(self, page):
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        state.set_input("plug_ip", "10.0.0.63")
        ctx = SimpleNamespace(dir=Path(directory.name), state=state)
        with mock.patch.object(discover_ewelink, "discover", return_value={"packets": []}), \
             mock.patch.object(setup_network, "page", return_value=page), \
             mock.patch.object(hostsys, "security_software_note", return_value=None), \
             mock.patch.object(prompts, "section"):
            return account.step_discover(ctx)

    def test_the_bridge_is_named(self):
        with self.assertRaisesRegex(errors.Abort, "already running our installer firmware"):
            self.silent_discover("<title>Autoflash bridge</title>")

    def test_tasmota_is_named(self):
        with self.assertRaisesRegex(errors.Abort, "running Tasmota, not Sonoff's firmware"):
            self.silent_discover("<title>Tasmota</title>")

    def test_no_page_keeps_the_lan_control_advice(self):
        with self.assertRaisesRegex(errors.Abort, "LAN Control"):
            self.silent_discover(None)


class PairStepTests(unittest.TestCase):
    """A1 asks only what matters: it looks again if no plug is found, and the
    plug it finds in the account says the run's kind."""

    def ctx(self):
        from types import SimpleNamespace
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        return SimpleNamespace(state=RunState.open(root), evidence=root / "live",
                               manifest_path=(runs.ROOT / bundle.DEFAULT_MANIFEST).resolve(),
                               wifi=lambda: ("Home-2G", "password"))

    def test_without_ewelink_no_plug_found_offers_to_look_again(self):
        from unittest import mock
        ctx = self.ctx()
        ctx.account_devices = lambda: None  # eWeLink could not be reached
        # Filed under its MAC in a scratch folder, not this checkout's captures.
        with mock.patch.object(plugs, "PLUGS", ctx.evidence.parent / "plugs"), \
             mock.patch.object(plugs, "hold"), \
             mock.patch.object(account, "find_ewelink_plugs",
                               side_effect=[[], [("10.0.0.5", "eWeLink device abc")]]), \
             mock.patch.object(prompts, "ask_choice", return_value=0), \
             mock.patch.object(hostsys, "mac_of", return_value="aa:bb:cc:dd:ee:ff"), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            result = account.step_pair(ctx)
        self.assertEqual(result["plug_ip"], "10.0.0.5")

    def test_the_account_plugs_kind_is_kept_for_the_run(self):
        from unittest import mock
        ctx = self.ctx()
        ctx.account_devices = lambda: [{"deviceid": "1000000001", "productModel": "S60TPG",
                                        "name": "Sonoff S60", "online": True, "params": {}}]
        with mock.patch.object(account, "sight_plugs",
                               side_effect=lambda plugs: [account.Sighting(p, "192.168.60.50", True)
                                                          for p in plugs]), \
             mock.patch.object(hostsys, "mac_of", return_value=None), \
             mock.patch.object(output, "say"):
            self.assertEqual(account.step_pair(ctx)["plug_ip"], "192.168.60.50")
        self.assertEqual(ctx.state.input("plug_kind"), "sonoff-s60")

    def test_a_typed_address_leaves_the_screen_with_its_question(self):
        # Asked after the "did not answer" menu, it is a block of its own, taken
        # off once answered - not left under the stage line.
        from unittest import mock
        ctx = self.ctx()
        ctx.account_devices = lambda: [{"deviceid": "1000000001", "productModel": "S60TPG",
                                        "name": "Sonoff S60", "online": True, "params": {}}]
        screen = mock.Mock()
        with mock.patch.object(account, "sight_plugs",
                               side_effect=lambda plugs: [account.Sighting(p, None, False) for p in plugs]), \
             mock.patch.object(prompts, "ask_choice", return_value=2), \
             mock.patch.object(prompts, "ask_ipv4", screen.ask_ipv4), \
             mock.patch.object(output, "transient_begin", screen.begin), \
             mock.patch.object(output, "transient_end", screen.end), \
             mock.patch.object(hostsys, "security_software_note", return_value=[]), \
             mock.patch.object(hostsys, "mac_of", return_value=None), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            screen.ask_ipv4.return_value = "192.168.60.50"
            result = account.step_pair(ctx)
        self.assertEqual(result["plug_ip"], "192.168.60.50")
        calls = [name for name, _, _ in screen.mock_calls]
        self.assertEqual(calls[-3:], ["begin", "ask_ipv4", "end"])


if __name__ == "__main__":
    unittest.main()
