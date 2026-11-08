"""The upgrade command sent again when a plug has lost it."""

import socket
import threading
import unittest
import urllib.request
from unittest import mock

from tools.ewelink import ota_push
from tools.ewelink.ota_push import Resending

BACK = {"action": "sysmsg", "params": {"online": True}}
AWAY = {"action": "sysmsg", "params": {"online": False}}


class ResendingTests(unittest.TestCase):
    def test_a_plug_back_that_asks_for_nothing_has_lost_it(self):
        # Seen on the real cloud: offline as it fetched, a power cut, back online
        # and nothing more asked for, with a run waiting out its two minutes.
        watch = Resending(100.0)
        watch.heard(AWAY, 101.0)
        watch.heard(BACK, 130.0)
        self.assertFalse(watch.lost(125.0, 130.0 + ota_push.RESEND_SETTLE - 1))
        self.assertTrue(watch.lost(125.0, 130.0 + ota_push.RESEND_SETTLE))

    def test_a_plug_back_and_fetching_is_left_alone(self):
        # It may say it is online with its download under way.
        watch = Resending(100.0)
        watch.heard(BACK, 101.0)
        self.assertFalse(watch.lost(102.0, 101.0 + ota_push.RESEND_SETTLE))
        self.assertFalse(watch.lost(102.0, 102.0 + ota_push.RESEND_QUIET - 1))

    def test_a_plug_away_is_waited_for_however_long(self):
        watch = Resending(100.0)
        watch.heard(AWAY, 101.0)
        self.assertFalse(watch.lost(0.0, 1000.0))

    def test_a_plug_that_fetched_part_is_not_sent_it_again_until_it_is_back(self):
        # Seen on the S61: cut off part way, the plug said nothing of going
        # offline; sent again after 45 s quiet, it was still starting, a send wasted.
        watch = Resending(100.0)
        self.assertFalse(watch.lost(110.0, 110.0 + ota_push.RESEND_QUIET + 60))
        watch.heard(BACK, 200.0)
        self.assertTrue(watch.lost(110.0, 200.0 + ota_push.RESEND_SETTLE))

    def test_a_plug_online_all_along_that_never_asks_has_lost_it(self):
        # A command gone down a connection eWeLink had not yet let go of.
        watch = Resending(100.0)
        self.assertFalse(watch.lost(0.0, 100.0 + ota_push.RESEND_QUIET - 1))
        self.assertTrue(watch.lost(0.0, 100.0 + ota_push.RESEND_QUIET))


class OfflineWaitTests(unittest.TestCase):
    def test_a_plug_away_its_five_minutes_after_a_send_cut_off_is_waited_for(self):
        # Seen on the real cloud: offline about 300 s after a stopped send, then
        # back once it restarted by itself; waited for 180 s, the run gave up first.
        looks = iter([{"online": False}] * 30 + [{"online": True}])   # back after 310 s
        device = ota_push.until_online({"online": False}, lambda: next(looks), sleep=lambda _: None,
                                       say=lambda _: None)
        self.assertTrue(device["online"])


class LastAskedTests(unittest.TestCase):
    def test_a_request_counts_as_the_plug_asking(self):
        server = ota_push.FirmwareServer(("127.0.0.1", 0), b"x" * 64, "127.0.0.1", say=lambda _: None, live=False)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.assertEqual(server.last_asked, 0.0)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/user1.bin", timeout=5) as answer:
            answer.read()
        self.assertGreater(server.last_asked, 0.0)


class FakeCloud:
    """The app's connection: `script` the messages it brings, one to a look."""
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def send_json(self, message):
        self.sent.append(message)

    def receive_json(self, timeout):
        if self.script:
            message = self.script.pop(0)
            if message is not None:
                return message
        raise socket.timeout

    def close(self):
        pass


class SendingAgainTests(unittest.TestCase):
    def send(self, script, firmware=b"x" * 64, **limits):
        cloud = FakeCloud(script)
        said = []
        device = {"deviceid": "1000000000", "apikey": "a", "online": True, "params": {"fwVersion": "1.2.0"},
                  "extra": {"model": "M"}}
        settings = {"RESEND_SETTLE": 0.0, "RESEND_QUIET": 3600.0, "RESEND_WAIT": 0.3} | limits
        with mock.patch.object(ota_push, "login_session", return_value=("eu", {"at": "t"})), \
                mock.patch.object(ota_push, "get_devices", return_value=[device]), \
                mock.patch.object(ota_push, "select_device", return_value=device), \
                mock.patch.object(ota_push, "app_socket", return_value=cloud), \
                mock.patch.multiple(ota_push, **settings):
            delivered = ota_push.deliver(
                email="a@example.com", password="p", country_code="+44", region="eu", device_id="1000000000",
                firmware=firmware, version="9", listen_ip="127.0.0.1", listen_port=0, device_ip="127.0.0.1",
                wait=0.6, say=said.append, live=False)
        return delivered, cloud, said

    def test_a_plug_back_gets_the_command_again_up_to_the_limit(self):
        _, cloud, said = self.send([AWAY, BACK, None, AWAY, BACK, None, AWAY, BACK])
        self.assertEqual(len(cloud.sent), ota_push.COMMAND_SENDS)
        self.assertEqual({message["action"] for message in cloud.sent}, {"upgrade"})
        self.assertIn("upgrade command sent again (attempt 2 of 3).", " ".join(said))

    def test_a_plug_that_stays_away_is_sent_it_once(self):
        _, cloud, _ = self.send([AWAY])
        self.assertEqual(len(cloud.sent), 1)

    def test_a_send_again_refused_does_not_end_the_wait(self):
        delivered, cloud, _ = self.send([AWAY, BACK, None, {"sequence": "any", "error": 503}])
        self.assertGreaterEqual(len(cloud.sent), 2)
        self.assertIsNone(delivered.error)

    def test_the_first_send_refused_ends_it(self):
        with mock.patch.object(ota_push.time, "time", return_value=1.0):
            delivered, cloud, _ = self.send([{"sequence": "1000", "error": 504}])
        self.assertIn("rejected", delivered.error)
        self.assertEqual(len(cloud.sent), 1)


if __name__ == "__main__":
    unittest.main()
