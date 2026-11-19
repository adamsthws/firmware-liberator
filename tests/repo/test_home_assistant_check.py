"""tools/repo/home_assistant_check.py: what it expects from the repository, and
how it reads what Home Assistant and the builder say. The check itself runs by
hand, on an emulator (docs/testing.md)."""

import json
import socket
import threading
import unittest
from unittest import mock

from tools.repo import home_assistant_check as check


class ExpectedTests(unittest.TestCase):
    def test_each_family_adopts_from_its_own_config(self):
        for family, kind in check.FAMILIES.items():
            with self.subTest(family=family):
                url = check.import_url(kind["device"])
                self.assertRegex(url, rf"^github://adamsthws/firmware-liberator/devices/{kind['device']}/"
                                      r"esphome\.yaml@v\d+\.\d+\.\d+$")
                self.assertTrue(check.builder_image(family, kind["device"]).startswith("ghcr.io/esphome/esphome:"))

    def test_a_device_is_offered_only_if_its_url_fits_its_mdns(self):
        # An S61's does not fit LibreTiny's mDNS; were it shorter, the check
        # would say the page is wrong.
        self.assertFalse(check.announced_whole("sonoff-s61"))
        self.assertFalse(check.FAMILIES["beken"]["offered"])
        self.assertTrue(check.FAMILIES["espressif"]["offered"])

    def test_the_documented_config_is_for_the_device_and_its_name(self):
        text = check.documented_config("sonoff-s60", "esphome-123456")
        self.assertIn("name: esphome-123456", text)
        self.assertIn("friendly_name: esphome 123456", text)
        self.assertIn("liberator.sonoff-s60:", text)
        self.assertIn(check.import_url("sonoff-s60"), text)
        self.assertNotIn("abcdef", text)
        self.assertNotIn("sonoff-s61", text)

    def test_a_config_written_as_documented_differs_in_nothing(self):
        shown = check.documented_config("sonoff-s61", "esphome-123456")
        written = shown.replace('"', "")   # unquoted, as the builder writes it
        self.assertEqual(check.config_differences("# by the builder\n" + written + "\n\n", shown), [])
        self.assertEqual(check.config_differences(written + "api:\n  encryption:\n    key: x\n", shown),
                         ["written, not shown: api:", "written, not shown: encryption:",
                          "written, not shown: key: x"])
        self.assertEqual(check.config_differences(written.replace("wifi:", "network:"), shown),
                         ["written, not shown: network:", "shown, not written: wifi:"])


class ReadingTests(unittest.TestCase):
    def test_the_esphome_flow_zeroconf_started_and_the_device_it_names(self):
        flows = [{"handler": "esphome", "context": {"source": "user"}},
                 {"handler": "esphome", "flow_id": "f1",
                  "context": {"source": "zeroconf", "unique_id": "12:34:56:ab:cd:ef",
                              "title_placeholders": {"name": "esphome abcdef (esphome-abcdef)"}}}]
        flow = check.esphome_flow(flows)
        self.assertEqual(flow["flow_id"], "f1")
        self.assertEqual(check.device_name(flow), "esphome-abcdef")
        flow["context"]["title_placeholders"]["name"] = "Lamp"
        self.assertEqual(check.device_name(flow), "esphome-abcdef")
        self.assertIsNone(check.esphome_flow(flows[:1]))

    def test_the_relay_and_the_power_reading(self):
        states = [{"entity_id": "switch.esphome_abcdef_relay", "state": "on"},
                  {"entity_id": "switch.esphome_abcdef_restart", "state": "off"},
                  {"entity_id": "sensor.esphome_abcdef_voltage", "state": "230", "attributes": {"device_class": "voltage"}},
                  {"entity_id": "sensor.esphome_abcdef_power", "state": "103.2", "attributes": {"device_class": "power"}}]
        self.assertEqual(check.relay_and_power(states), ("switch.esphome_abcdef_relay", "sensor.esphome_abcdef_power"))
        self.assertEqual(check.reading(states[3]), 103.2)
        self.assertIsNone(check.reading({"state": "unavailable"}))
        self.assertIsNone(check.available([*states, {"entity_id": "sensor.x", "state": "unavailable"}]))
        self.assertIsNone(check.available([]))

    def test_an_offer_by_name_and_the_newest_job_once_ended(self):
        listed = {"configured": [], "importable": [{"name": "esphome-abcdef", "package_import_url": "u"}]}
        self.assertEqual(check.offer_for(listed, "esphome-abcdef")["package_import_url"], "u")
        self.assertIsNone(check.offer_for(listed, "esphome-123456"))
        self.assertIsNone(check.offer_for(None, "esphome-abcdef"))
        jobs = [{"created_at": "1", "status": "completed", "exit_code": 1},
                {"created_at": "2", "status": "running"}]
        self.assertIsNone(check.finished_job(jobs))
        jobs[1].update(status="completed", exit_code=0)
        self.assertEqual(check.finished_job({"jobs": jobs})["exit_code"], 0)
        self.assertIsNone(check.finished_job([]))

    def test_a_message_wrapped_on_the_screen_is_found_whole(self):
        self.assertIn("It is a build of its own", check.words("│ ... It is a build\n│ of its own, adopted │"))
        self.assertIn("From: Its own build", check.words("     From:     \x1b[0mIts own build\x1b[0m\n"))


class WebSocketTests(unittest.TestCase):
    def test_a_message_each_way_a_ping_answered_and_a_long_one_in_pieces(self):
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        ws = check.WebSocket(ours)
        got = {}

        def server():
            peer = check.WebSocket(theirs)
            first, second = peer.exact(2)
            n = second & 0x7F
            n = int.from_bytes(peer.exact(2), "big") if n == 126 else n
            mask = peer.exact(4)
            got["sent"] = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(peer.exact(n))))
            got["masked"] = bool(second & 0x80)
            theirs.sendall(bytes([0x89, 2]) + b"hi")   # a ping
            long = json.dumps({"type": "result", "big": "x" * 300}).encode()
            theirs.sendall(bytes([0x01, 126]) + len(long[:200]).to_bytes(2, "big") + long[:200])
            theirs.sendall(bytes([0x80, 126]) + len(long[200:]).to_bytes(2, "big") + long[200:])
            first, second = peer.exact(2)
            got["pong"] = (first & 0x0F, peer.exact(4 + (second & 0x7F))[4:])

        thread = threading.Thread(target=server)
        thread.start()
        ws.send({"type": "auth"})
        answer = ws.receive()
        thread.join(5)
        self.assertEqual(got["sent"], {"type": "auth"})
        self.assertTrue(got["masked"])
        self.assertEqual(answer["big"], "x" * 300)
        self.assertEqual(got["pong"][0], 0xA)


class InstanceTests(unittest.TestCase):
    def test_an_instance_past_99_is_refused(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            check.main(["beken", "--instance", "101"])


if __name__ == "__main__":
    unittest.main()
