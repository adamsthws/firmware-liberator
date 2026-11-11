"""The device in its eWeLink account (tools/autoflash/account.py): the devices
there matched on the network (A1), and only a kind this tool converts let past
A2, with the firmware name its updater checks."""

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import (account, errors, esphome, hostsys, kinds, openbeken, output, records, prompts, runs,
                             setup_network)
from tools.autoflash.routes.espressif import bundle
from tools.autoflash.state import RunState
from tools.ewelink import discover_ewelink, firmware_name

S60 = {"deviceid": "1001234abc", "name": "Kitchen", "productModel": "S60TPG", "online": True,
       "devicekey": "secret", "extra": {"model": "SN-ESP32C3-S60-01"},
       "params": {"staMac": "AA:BB:CC:12:34:56", "ssid": "Home-2G"}}
S61 = {"deviceid": "1001234abd", "name": "Desk", "productModel": "S61STPF", "online": True,
       "extra": {"model": "SN-BK7238-S61-01"}, "params": {"staMac": "12:34:56:AB:CD:EF"}}
SWITCH = {"deviceid": "1000aaaaaa", "name": "Hall light", "productModel": "MINIR4M", "online": True,
          "extra": {"model": "SN-ESP32-MINIR4M"}, "params": {}}


class AccountDeviceTests(unittest.TestCase):
    def test_an_s60_is_kept_without_its_key(self):
        device = account.account_device(S60)
        self.assertEqual((device.device_id, device.name, device.model, device.online, device.mac, device.ssid, device.kind),
                         ("1001234abc", "Kitchen", "S60TPG", True, "aa:bb:cc:12:34:56", "Home-2G", "sonoff-s60"))
        self.assertNotIn("secret", repr(device))

    def test_other_sonoff_devices_are_not_offered(self):
        self.assertIsNone(account.account_device(SWITCH))

    def test_the_firmware_family_alone_is_enough(self):
        self.assertIsNotNone(account.account_device({**S60, "productModel": ""}))


class MatchTests(unittest.TestCase):
    device = account.account_device(S60)

    def test_matched_by_its_ewelink_id(self):
        [seen] = account.match_devices([self.device], {"1001234abc": "192.0.2.50"}, {})
        self.assertEqual((seen.ip, seen.answering), ("192.0.2.50", True))

    def test_another_device_answering_is_not_mistaken_for_it(self):
        [seen] = account.match_devices([self.device], {"1000aaaaaa": "192.0.2.60"}, {})
        self.assertEqual((seen.ip, seen.answering), (None, False))

    def test_seen_by_hardware_address_only_means_lan_control_is_off(self):
        [seen] = account.match_devices([self.device], {}, {"192.0.2.50": "aa:bb:cc:12:34:56"})
        self.assertEqual((seen.ip, seen.answering), ("192.0.2.50", False))
        self.assertIn("turn on LAN Control", account.why_not_found(seen))

    def test_an_s60_seen_by_hardware_address_alone_is_not_ready(self):
        [seen] = account.match_devices([self.device], {}, {"192.0.2.50": "aa:bb:cc:12:34:56"})
        self.assertFalse(account.ready(seen))

    def test_an_s61_needs_no_lan_control(self):
        # Its conversion goes through eWeLink alone: online there, and at the
        # hardware address the account gives it, it is found.
        [seen] = account.match_devices([account.account_device(S61)], {}, {"192.0.2.51": "12:34:56:ab:cd:ef"})
        self.assertTrue(account.ready(seen))

    def test_an_s61_offline_in_ewelink_is_not_ready(self):
        [seen] = account.match_devices([account.account_device({**S61, "online": False})], {},
                                     {"192.0.2.51": "12:34:56:ab:cd:ef"})
        self.assertFalse(account.ready(seen))

    def test_offline_in_ewelink_says_so(self):
        [seen] = account.match_devices([account.account_device({**S60, "online": False})], {}, {})
        self.assertIn("offline", account.why_not_found(seen))

    def test_answering_but_offline_in_ewelink_means_no_internet(self):
        offline = account.account_device({**S60, "online": False})
        [seen] = account.match_devices([offline], {"1001234abc": "192.0.2.50"}, {})
        self.assertTrue(account.cut_off(seen))
        self.assertIn("allow it internet access", account.why_not_found(seen))

    def test_online_but_unseen_names_its_network(self):
        [seen] = account.match_devices([self.device], {}, {})
        self.assertIn("(it is on 'Home-2G')", account.why_not_found(seen))


class TypedAddressTests(unittest.TestCase):
    """An address typed in is used only once the device answers eWeLink's discovery there."""

    def found(self, ids):
        packets = [{"source": "192.0.2.50", "records": [{"type": "TXT", "value": [f"id={i}"]}]}
                   for i in ids]
        return mock.patch.object(discover_ewelink, "discover", return_value={"packets": packets})

    def test_the_accounts_s60_answering_will_do(self):
        with self.found(["1001234abc"]), mock.patch.object(output, "tell"):
            self.assertEqual(account.device_at({"1001234abc"})("192.0.2.50"), [])

    def test_something_that_only_answers_a_ping_is_probably_the_router(self):
        with self.found([]), mock.patch.object(output, "tell"):
            problem = account.device_at({"1001234abc"})("192.0.2.1")
        self.assertIn("not as an eWeLink device", problem[0])
        self.assertIn("such as your router", " ".join(problem))

    def test_another_ewelink_device_is_not_taken_for_the_device(self):
        with self.found(["1000aaaaaa"]), mock.patch.object(output, "tell"):
            problem = account.device_at({"1001234abc"})("192.0.2.60")
        self.assertEqual(problem, ["192.0.2.60 is a different eWeLink device, not one of this "
                                   "account's supported devices."])

    def test_without_an_account_any_ewelink_device_will_do(self):
        with self.found(["1000aaaaaa"]), mock.patch.object(output, "tell"):
            self.assertEqual(account.device_at(None)("192.0.2.60"), [])


class DescribeTests(unittest.TestCase):
    def test_a_name_that_is_the_model_is_not_repeated(self):
        self.assertEqual(account.describe_device(account.account_device({**S60, "name": "S60TPG"})), "'S60TPG'")
        self.assertEqual(account.describe_device(account.account_device(S60)), "'Kitchen' (S60TPG)")


class FindAccountDeviceTests(unittest.TestCase):
    def ctx(self):
        return types.SimpleNamespace(email=lambda: "owner@example.com", forget_account=mock.Mock(),
                                     account_devices=mock.Mock(return_value=[S60]))

    def test_the_one_s60_answering_is_chosen_without_asking(self):
        sighting = account.Sighting(account.account_device(S60), "192.0.2.50", True)
        with mock.patch.object(account, "sight_devices", return_value=[sighting]), \
             mock.patch.object(prompts, "ask_choice") as asked, \
             mock.patch.object(output, "tell") as shown, mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(self.ctx(), [S60, SWITCH]), "192.0.2.50")
        asked.assert_not_called()
        shown.assert_not_called()  # the stage line completing is all the screen needs

    def test_it_is_looked_for_again_by_itself_before_anyone_is_asked(self):
        # Just plugged in, or a busy Wi-Fi: it answers a moment later.
        device = account.account_device(S60)
        with mock.patch.object(account, "sight_devices", side_effect=[
                [account.Sighting(device, "192.0.2.50", False)],
                [account.Sighting(device, "192.0.2.50", True)]]), \
             mock.patch.object(prompts, "ask_choice") as asked, mock.patch.object(time, "sleep"), \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(self.ctx(), [S60]), "192.0.2.50")
        asked.assert_not_called()

    def test_looking_again_finds_it_once_it_answers(self):
        device = account.account_device(S60)
        unseen = [account.Sighting(device, "192.0.2.50", False)]
        with mock.patch.object(account, "sight_devices", side_effect=[unseen] * (account.LOOK_AGAIN + 1) + [
                [account.Sighting(device, "192.0.2.50", True)]]), \
             mock.patch.object(prompts, "ask_choice", return_value=0) as asked, mock.patch.object(time, "sleep"), \
             mock.patch.object(prompts, "section") as shown, \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(self.ctx(), [S60]), "192.0.2.50")
        asked.assert_called_once()   # only once it had been looked for again by itself
        self.assertIn("turn on LAN Control", shown.call_args.args[1][0])

    def clock(self):
        """A clock that moves on 10 s for every sleep, and no real waiting."""
        now = [0.0]
        return (mock.patch.object(time, "monotonic", side_effect=lambda: now[0]),
                mock.patch.object(time, "sleep", side_effect=lambda s: now.__setitem__(0, now[0] + s)))

    def test_a_device_just_started_is_waited_for_until_ewelink_sees_it(self):
        # It answers here a few seconds before eWeLink shows it online.
        offline = {**S60, "online": False}
        ctx = self.ctx()
        ctx.account_devices = mock.Mock(side_effect=[[offline], [S60]])
        monotonic, sleep = self.clock()
        with mock.patch.object(account, "sight_devices",
                               side_effect=lambda devices: [account.Sighting(p, "192.0.2.50", True) for p in devices]), \
             mock.patch.object(prompts, "ask_choice") as asked, monotonic, sleep, \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(ctx, [offline]), "192.0.2.50")
        asked.assert_not_called()

    def test_a_device_just_switched_on_is_waited_for_before_asking(self):
        # Plugged in as the run began: neither answering here nor online yet.
        offline = {**S60, "online": False}
        ctx = self.ctx()
        ctx.account_devices = mock.Mock(side_effect=[[offline], [S60]])
        device = account.account_device(offline)
        monotonic, sleep = self.clock()
        with mock.patch.object(account, "sight_devices", side_effect=[
                [account.Sighting(device, None, False)],
                [account.Sighting(account.account_device(S60), "192.0.2.50", True)]]), \
             mock.patch.object(prompts, "ask_choice") as asked, monotonic, sleep, \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(ctx, [offline]), "192.0.2.50")
        asked.assert_not_called()

    def test_a_device_offline_for_a_minute_is_asked_about(self):
        offline = {**S60, "online": False}
        ctx = self.ctx()
        ctx.account_devices = mock.Mock(return_value=[offline])
        monotonic, sleep = self.clock()
        with mock.patch.object(account, "sight_devices",
                               side_effect=lambda devices: [account.Sighting(p, None, False) for p in devices]), \
             mock.patch.object(prompts, "ask_choice", return_value=2), \
             mock.patch.object(prompts, "ask_ipv4", return_value="192.0.2.99"), \
             mock.patch.object(prompts, "section") as shown, monotonic, sleep, \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(ctx, [offline]), "192.0.2.99")
        self.assertIn("eWeLink shows it offline", shown.call_args.args[1][0])
        # Looked again every 10 s for the minute, then LOOK_AGAIN times more, before asking.
        self.assertEqual(ctx.account_devices.call_count, int(account.CLOUD_CATCH_UP // 10) + account.LOOK_AGAIN)

    def test_without_ewelink_a_device_not_answering_is_looked_for_again_before_asking(self):
        with mock.patch.object(account, "find_ewelink_devices", side_effect=[[], [], [("192.0.2.50", "S60")]]), \
             mock.patch.object(prompts, "ask_choice") as asked, mock.patch.object(time, "sleep"), \
             mock.patch.object(output, "say"):
            self.assertEqual(account.find_any_device(), "192.0.2.50")
        asked.assert_not_called()
        with mock.patch.object(account, "find_ewelink_devices", side_effect=[[]] * (account.LOOK_AGAIN + 1)), \
             mock.patch.object(prompts, "ask_choice", return_value=1) as asked, mock.patch.object(time, "sleep"), \
             mock.patch.object(prompts, "ask_ipv4", return_value="192.0.2.99"), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_any_device(), "192.0.2.99")
        asked.assert_called_once()

    def test_a_device_without_internet_is_not_chosen(self):
        sighting = account.Sighting(account.account_device({**S60, "online": False}), "192.0.2.50", True)
        monotonic, sleep = self.clock()
        with mock.patch.object(account, "sight_devices", return_value=[sighting]), monotonic, sleep, \
             mock.patch.object(prompts, "ask_choice", return_value=2), \
             mock.patch.object(prompts, "ask_ipv4", return_value="192.0.2.99"), \
             mock.patch.object(prompts, "section") as shown, \
             mock.patch.object(output, "tell"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(self.ctx(), [S60]), "192.0.2.99")
        self.assertEqual("Your device is on this network but cannot reach the internet",
                         shown.call_args.args[0])
        self.assertIn("cannot reach the internet", shown.call_args.args[1][0])

    def test_another_account_chosen_is_signed_in_to_and_its_devices_looked_at(self):
        ctx = self.ctx()
        sighting = account.Sighting(account.account_device(S60), "192.0.2.50", True)
        with mock.patch.object(prompts, "ask_choice", side_effect=[1]), \
             mock.patch.object(account, "sight_devices", return_value=[sighting]), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            self.assertEqual(account.find_account_device(ctx, [SWITCH]), "192.0.2.50")
        ctx.forget_account.assert_called_once()
        ctx.account_devices.assert_called_once()

    def test_an_account_without_an_s60_says_what_it_has(self):
        ctx = self.ctx()
        with mock.patch.object(prompts, "ask_choice", return_value=2), \
             mock.patch.object(prompts, "ask_ipv4", return_value="192.0.2.50"), \
             mock.patch.object(prompts, "section") as shown:
            self.assertEqual(account.find_account_device(ctx, [SWITCH]), "192.0.2.50")
        self.assertIn("No supported device in the eWeLink account owner@example.com", shown.call_args.args[0])
        self.assertIn("- 'Hall light' (MINIR4M)", shown.call_args.args[1])


class ModelGuardTests(unittest.TestCase):
    """Only a device of a kind this tool converts may continue past A2 - another
    Sonoff on the account must not."""

    learned = (None, [])
    saved: dict = {}

    written = True

    def discover_with_model(self, model):
        import json
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = self.root = Path(directory.name)
        state = self.state = RunState.open(root)
        state.set_input("device_ip", "10.0.0.63")
        ctx = SimpleNamespace(dir=root, state=state, email=lambda: "a@b.c",
                              ewelink_env=lambda: {}, helper_env=lambda: {}, ewelink_region=lambda: "us")

        def fake_tool(args, **_):
            if "tools/ewelink/query_ota.py" in args and self.written:
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
        self.assertEqual(self.state.input("device_kind"), "sonoff-s60")  # kept for the run
        self.assertEqual(self.state.input("stock_version"), "1.2.0")
        self.assertEqual(self.state.input("product_model"), "")

    def test_the_discovery_report_is_kept_and_the_ota_metadata_is_not(self):
        # Its download links may work as access tokens.
        import json
        self.discover_with_model("SN-ESP32C3-S60-01")
        kept = json.loads((self.root / "mdns.json").read_text(encoding="utf-8"))
        self.assertEqual(kept["packets"][0]["source"], "10.0.0.63")
        self.assertFalse((self.root / "ota-metadata.json").exists())

    def test_metadata_the_query_did_not_write_stops_the_step_plainly(self):
        self.written = False
        with self.assertRaisesRegex(errors.Abort, "could not read the OTA metadata"):
            self.discover_with_model("SN-ESP32C3-S60-01")

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
        # In the account's own region, which the run's sign-in found.
        self.assertEqual(self.tool_args[self.tool_args.index("--region") + 1], "us")

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
    """A device part way through a conversion is not a LAN Control problem."""

    def silent_discover(self, page, running=None):
        """`running`: "esphome" or "openbeken", if that answers at the address."""
        from types import SimpleNamespace
        from unittest import mock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        state.set_input("device_ip", "10.0.0.63")
        ctx = SimpleNamespace(dir=Path(directory.name), state=state)
        with mock.patch.object(discover_ewelink, "discover", return_value={"packets": []}), \
             mock.patch.object(setup_network, "page", return_value=page), \
             mock.patch.object(esphome, "is_esphome", return_value=running == "esphome"), \
             mock.patch.object(openbeken, "is_openbeken", return_value=running == "openbeken"), \
             mock.patch.object(hostsys, "security_software_note", return_value=None), \
             mock.patch.object(prompts, "section"):
            return account.step_discover(ctx)

    def test_the_bridge_is_named(self):
        with self.assertRaisesRegex(errors.Abort, "already running our installer firmware"):
            self.silent_discover("<title>Autoflash bridge</title>")

    def test_tasmota_is_named(self):
        with self.assertRaisesRegex(errors.Abort, "running Tasmota, not Sonoff's firmware"):
            self.silent_discover("<title>Tasmota</title>")

    def test_a_converted_device_says_what_it_runs(self):
        # The eWeLink account still lists a device converted since.
        for running, name in (("esphome", "ESPHome"), ("openbeken", "OpenBeken")):
            with self.subTest(running), self.assertRaises(account.NotOnSonoff) as raised:
                self.silent_discover("<html></html>", running)
            self.assertEqual(raised.exception.firmware, running)
            self.assertIn(f"running {name}, not Sonoff's firmware: it is converted already", str(raised.exception))

    def test_another_page_keeps_the_lan_control_advice(self):
        with self.assertRaisesRegex(errors.Abort, "LAN Control"):
            self.silent_discover("<html></html>")

    def test_no_page_keeps_the_lan_control_advice(self):
        with self.assertRaisesRegex(errors.Abort, "LAN Control"):
            self.silent_discover(None)


class PairStepTests(unittest.TestCase):
    """A1 asks only what matters: it looks again if no device is found, and the
    device it finds in the account says the run's kind."""

    def ctx(self):
        from types import SimpleNamespace
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        return SimpleNamespace(state=RunState.open(root), evidence=root / "live",
                               manifest_path=(runs.ROOT / bundle.DEFAULT_MANIFEST).resolve(),
                               wifi=lambda: ("Home-2G", "password"))

    def test_without_ewelink_no_device_found_offers_to_look_again(self):
        from unittest import mock
        ctx = self.ctx()
        ctx.account_devices = lambda: None  # eWeLink could not be reached
        # Filed under its MAC in a scratch folder, not this checkout's captures.
        with mock.patch.object(records, "DEVICES", ctx.evidence.parent / "devices"), \
             mock.patch.object(records, "hold"), \
             mock.patch.object(account, "find_ewelink_devices",
                               side_effect=[[], [("10.0.0.5", "eWeLink device abc")]]), \
             mock.patch.object(prompts, "ask_choice", return_value=0), \
             mock.patch.object(hostsys, "mac_of", return_value="aa:bb:cc:dd:ee:ff"), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            result = account.step_pair(ctx)
        self.assertEqual(result["device_ip"], "10.0.0.5")

    def test_the_account_devices_kind_is_kept_for_the_run(self):
        from unittest import mock
        ctx = self.ctx()
        ctx.account_devices = lambda: [{"deviceid": "1000000001", "productModel": "S60TPG",
                                        "name": "Sonoff S60", "online": True, "params": {}}]
        with mock.patch.object(account, "sight_devices",
                               side_effect=lambda devices: [account.Sighting(p, "192.168.60.50", True)
                                                          for p in devices]), \
             mock.patch.object(hostsys, "mac_of", return_value=None), \
             mock.patch.object(output, "say"):
            self.assertEqual(account.step_pair(ctx)["device_ip"], "192.168.60.50")
        self.assertEqual(ctx.state.input("device_kind"), "sonoff-s60")

    def pair(self, ctx, arp_mac):
        account_devices = [{"deviceid": "1000000001", "productModel": "S60TPG", "name": "Sonoff S60",
                            "online": True, "params": {"staMac": "12:34:56:AB:CD:EF"}}]
        ctx.account_devices = lambda: account_devices
        with mock.patch.object(account, "sight_devices",
                               side_effect=lambda devices: [account.Sighting(p, "192.0.2.50", True)
                                                          for p in devices]), \
             mock.patch.object(hostsys, "mac_of", return_value=arp_mac), \
             mock.patch.object(runs, "file_device") as filed, mock.patch.object(output, "say"):
            result = account.step_pair(ctx)
        return result, filed

    def test_the_device_found_is_kept_for_the_run_and_filed_under_its_hardware_address(self):
        ctx = self.ctx()
        result, filed = self.pair(ctx, "12:34:56:ab:cd:e0")
        self.assertEqual(result, {"device_ip": "192.0.2.50", "device_mac": "12:34:56:ab:cd:e0"})
        self.assertEqual((ctx.state.input("device_ip"), ctx.state.input("device_mac")),
                         ("192.0.2.50", "12:34:56:ab:cd:e0"))
        self.assertEqual(filed.call_args.args[:2], (ctx, "12:34:56:ab:cd:e0"))
        self.assertEqual(filed.call_args.args[2].device_id, "1000000001")

    def test_one_this_computer_cannot_see_the_hardware_address_of_takes_the_accounts(self):
        ctx = self.ctx()
        result, filed = self.pair(ctx, None)
        self.assertEqual(ctx.state.input("device_mac"), "12:34:56:ab:cd:ef")
        filed.assert_called_once()

    def test_a_typed_address_leaves_the_screen_with_its_question(self):
        # Asked after the "did not answer" menu, it is a block of its own, taken
        # off once answered - not left under the stage line.
        from unittest import mock
        ctx = self.ctx()
        ctx.account_devices = lambda: [{"deviceid": "1000000001", "productModel": "S60TPG",
                                        "name": "Sonoff S60", "online": True, "params": {}}]
        screen = mock.Mock()
        with mock.patch.object(account, "sight_devices",
                               side_effect=lambda devices: [account.Sighting(p, None, False) for p in devices]), \
             mock.patch.object(prompts, "ask_choice", return_value=2), \
             mock.patch.object(prompts, "ask_ipv4", screen.ask_ipv4), \
             mock.patch.object(output, "transient_begin", screen.begin), \
             mock.patch.object(output, "transient_end", screen.end), \
             mock.patch.object(hostsys, "security_software_note", return_value=[]), \
             mock.patch.object(hostsys, "mac_of", return_value=None), \
             mock.patch.object(prompts, "section"), mock.patch.object(output, "say"):
            screen.ask_ipv4.return_value = "192.168.60.50"
            result = account.step_pair(ctx)
        self.assertEqual(result["device_ip"], "192.168.60.50")
        calls = [name for name, _, _ in screen.mock_calls]
        self.assertEqual(calls[-3:], ["begin", "ask_ipv4", "end"])


if __name__ == "__main__":
    unittest.main()
