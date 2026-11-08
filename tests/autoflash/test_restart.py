"""tools/autoflash/restart.py: a device that answers, restarted as switching it
off and on would - by the way its firmware has."""
import http.server
import json
import threading
import unittest
from unittest import mock

from tools.autoflash import detect, output, restart


class Plug(http.server.ThreadingHTTPServer):
    """What answers at a plug's address: a mover (POST /restart), or a Tasmota
    (/cm?cmnd=Restart 1, refused without a Referer as Tasmota refuses it)."""

    def __init__(self, kind, restarts=True):
        super().__init__(("127.0.0.1", 0), Handler)
        self.kind, self.restarts, self.asked = kind, restarts, []

    @property
    def host(self):
        return f"127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.asked.append(("POST", self.path))
        ok = self.server.kind == "mover" and self.path == "/restart" and self.server.restarts
        self.reply(200 if ok else 404, b"Restarting" if ok else b"")

    def do_GET(self):
        self.server.asked.append(("GET", self.path))
        if self.server.kind == "tasmota" and self.path.startswith("/cm?") and self.headers.get("Referer"):
            self.reply(200, json.dumps({"Restart": "Restarting"}).encode())
        else:
            self.reply(404, b"")


class RestartTests(unittest.TestCase):
    def serve(self, kind, restarts=True):
        plug = Plug(kind, restarts)
        threading.Thread(target=plug.serve_forever, daemon=True).start()
        self.addCleanup(plug.server_close)
        self.addCleanup(plug.shutdown)
        return plug

    def run_restart(self, plug, running, back=(None, "x")):
        with mock.patch.object(detect, "identify", side_effect=[running, *back]), \
                mock.patch.object(restart.time, "sleep"), mock.patch.object(output, "say"):
            return restart.restart(plug.host)

    def test_a_mover_is_restarted_by_its_route(self):
        plug = self.serve("mover")
        self.assertTrue(self.run_restart(plug, "mover"))
        self.assertIn(("POST", "/restart"), plug.asked)

    def test_a_mover_before_1_2_0_cannot_be(self):
        plug = self.serve("mover", restarts=False)
        self.assertFalse(self.run_restart(plug, "mover"))

    def test_tasmota_and_its_safeboot_are_restarted_by_command(self):
        for running in ("tasmota", "hub", "startup"):
            with self.subTest(running):
                plug = self.serve("tasmota")
                self.assertTrue(self.run_restart(plug, running))
                self.assertTrue(any("Restart" in path for _, path in plug.asked))

    def test_openbeken_by_its_own(self):
        with mock.patch.object(detect, "identify", return_value="interim"), \
                mock.patch.object(restart.openbeken, "restart", return_value=True) as rebooted, \
                mock.patch.object(output, "say"):
            self.assertTrue(restart.restart("192.0.2.77"))
        rebooted.assert_called_once()

    def test_the_bridge_sonoff_and_nothing_are_left_alone(self):
        # The bridge restarts into Sonoff's firmware, which would end a conversion part way.
        for running in ("installer", "sonoff", "esphome", None):
            with self.subTest(running), mock.patch.object(detect, "identify", return_value=running), \
                    mock.patch.object(restart, "ask") as asked:
                self.assertFalse(restart.restart("192.0.2.77"))
                asked.assert_not_called()

    def test_one_that_does_not_answer_again_is_not_taken_as_restarted(self):
        plug = self.serve("mover")
        with mock.patch.object(restart, "BACK_WAIT", 0.0):
            self.assertFalse(self.run_restart(plug, "mover", back=(None,)))


if __name__ == "__main__":
    unittest.main()
