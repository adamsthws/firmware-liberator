"""The cloud stub's helpers run on their own, as the emulator runs them: in
isolated mode (-I), where only their own path puts the repository on sys.path."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.espressif.emulator.stub_cloud import stub_server

STUB = Path(__file__).resolve().parents[2] / "tools" / "espressif" / "emulator" / "stub_cloud"


class StubHelpersRunIsolatedTests(unittest.TestCase):
    def run_isolated(self, script):
        return subprocess.run([sys.executable, "-I", str(STUB / script), "--help"],
                              capture_output=True, text=True, timeout=60)

    def test_device_identity_imports_the_repository(self):
        done = self.run_isolated("device_identity.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("usage:", done.stdout)

    def test_make_ca_imports_the_repository(self):
        done = self.run_isolated("make_ca.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("usage:", done.stdout)

    def test_the_stub_and_its_firmware_import_the_repository(self):
        for script in ("stub_server.py", "firmware.py"):
            done = self.run_isolated(script)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("usage:", done.stdout)


class FirmwareOfferTests(unittest.TestCase):
    """Asked as query_ota.latest_offer asks, with a version older than any,
    the stub offers the plug its own firmware, and serves it to its id."""

    def setUp(self):
        from tools.espressif.emulator.stub_cloud.firmware import Update
        self.stub = stub_server.Stub("192.0.2.1", 443, 10, {"deviceid": "1000abcdef", "devicekey": "k"},
                                     Update("1.2.0", b"the file"))

    def query(self, version, deviceid="1000abcdef"):
        body = json.dumps({"deviceInfoList": [{"deviceid": deviceid, "model": "m", "version": version}]})
        return self.stub.ota_query(body.encode())["data"]["otaInfoList"]

    def test_offered_only_to_a_device_behind_it(self):
        offer, = self.query("0.0.1")
        self.assertEqual((offer["version"], offer["binList"][0]["name"]), ("1.2.0", "user1.bin"))
        self.assertEqual(offer["binList"][0]["downloadUrl"], "https://192.0.2.1/ota/user1.bin")
        self.assertEqual(self.query("1.2.0"), [])  # the conversion's own question: nothing on offer
        self.assertEqual(self.query("0.0.1", "1000fedcba"), [])
        self.assertEqual(stub_server.Stub("192.0.2.1", 443, 10, {"deviceid": "1000abcdef"}).ota_query(b"{}"),
                         {"error": 0, "data": {"otaInfoList": []}})

    def test_a_register_says_whether_it_is_the_accounts_own_device(self):
        handler = stub_server.Handler.__new__(stub_server.Handler)
        handler.stub = stub_server.Stub("192.0.2.1", 443, 10, {"deviceid": "1000abcdef", "apikey": "a",
                                                                "devicekey": "a"})
        for deviceid, key, said in (("1000abcdef", "a", ("yes", "yes")), ("1000fedcba", "b", ("NO", "NO"))):
            with mock.patch.dict(stub_server._devices, {}, clear=True), \
                    mock.patch.object(stub_server, "log") as logged:
                handler._device_message(mock.Mock(peer="test"), {"action": "register", "deviceid": deviceid,
                                                                 "apikey": key}, None)
            line = next(call.args for call in logged.call_args_list if "register:" in call.args[1])
            self.assertEqual((line[2], line[4]), said)
            self.assertNotIn(deviceid, " ".join(map(str, line)))

    def test_the_file_goes_to_the_devices_own_id(self):
        self.assertEqual(self.stub.ota_file("deviceid=1000abcdef&ts=1&sign=0"), b"the file")
        self.assertIsNone(self.stub.ota_file("deviceid=1000fedcba&ts=1&sign=0"))
        self.assertIsNone(self.stub.ota_file(""))

    def test_the_file_emu_sh_made_is_read_with_its_version(self):
        from tools.autoflash import kinds
        from tools.espressif import build_vendor_ota
        from tools.espressif.emulator.stub_cloud import firmware
        from tests.espressif.test_dump_stock_flash import esp_image
        wrapped = build_vendor_ota.build_wrapper(esp_image("1.2.0", "esp32_fw"), version="1.2.0",
                                                 model=kinds.SONOFF_S60.wrapper_model)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stub-firmware.bin"
            self.assertIsNone(firmware.read(path))
            path.write_bytes(wrapped)
            self.assertEqual(firmware.read(path), firmware.Update("1.2.0", wrapped))


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "needs the cryptography package")
class RsaCaTests(unittest.TestCase):
    def test_an_rsa_ca_is_eWeLinks_size_fits_the_slot_and_can_be_trusted(self):
        from cryptography import x509
        from tools.espressif.emulator.stub_cloud import make_ca
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(make_ca.main(["--out-dir", folder, "--rsa"]), 0)
            pem = (out / "ca.crt").read_bytes()
            self.assertLess(len(pem), make_ca.SLOT_BYTES)
            self.assertEqual(x509.load_pem_x509_certificate(pem).public_key().key_size, make_ca.RSA_CA_BITS)
            leaf = x509.load_pem_x509_certificate((out / "server.crt").read_bytes())
            leaf.verify_directly_issued_by(x509.load_pem_x509_certificate(pem))
            self.assertIn("SECLEVEL=1", (out / "openssl.cnf").read_text(encoding="utf-8"))


class SendToDeviceTests(unittest.TestCase):
    def test_a_command_reaches_the_device_online_and_no_other(self):
        conn = mock.Mock()
        with mock.patch.dict(stub_server._devices, {"1000abcdef": conn}, clear=True):
            self.assertTrue(stub_server.send_to_device("1000abcdef", {"action": "upgrade"}))
            self.assertFalse(stub_server.send_to_device("1000fedcba", {"action": "upgrade"}))
        self.assertEqual(json.loads(conn.send.call_args.args[0]), {"action": "upgrade"})


class AccountDeviceTests(unittest.TestCase):
    """A plug whose identity the stub is not given - the S61's, sealed in its
    store - is listed as it registers, as eWeLink lists it."""

    def stub(self, device=None):
        return stub_server.Stub("192.0.2.1", 443, 10, device)

    def test_nothing_listed_before_the_plug_registers(self):
        stub = self.stub({"staMac": "12:34:56:ab:cd:ef", "model": "SN-BK7238-S61-01"})
        self.assertEqual(stub.device_thing()["error"], 404)

    def test_the_registered_identity_completes_the_record(self):
        stub = self.stub({"staMac": "12:34:56:ab:cd:ef", "model": "SN-BK7238-S61-01"})
        stub.heard({"action": "register", "deviceid": "1000abcdef", "apikey": "made-up-apikey",
                    "romVersion": "1.0.2"})
        item = stub.device_thing()["data"]["thingList"][0]["itemData"]
        self.assertEqual((item["deviceid"], item["apikey"]), ("1000abcdef", "made-up-apikey"))
        self.assertEqual(item["extra"]["model"], "SN-BK7238-S61-01")
        self.assertEqual(item["params"]["staMac"], "12:34:56:ab:cd:ef")
        self.assertEqual(item["params"]["fwVersion"], "1.0.2")
        self.assertEqual(stub.account_login()["data"]["user"]["apikey"], "made-up-apikey")

    def test_a_given_identity_is_kept(self):
        stub = self.stub({"deviceid": "1000fedcba", "devicekey": "k", "apikey": "a"})
        stub.heard({"action": "register", "deviceid": "1000abcdef", "apikey": "other"})
        item = stub.device_thing()["data"]["thingList"][0]["itemData"]
        self.assertEqual(item["deviceid"], "1000fedcba")


class SignInTests(unittest.TestCase):
    """Each sign-in ends the one before, as at eWeLink."""

    def setUp(self):
        self.stub = stub_server.Stub("192.0.2.1", 443, 10, {"deviceid": "1000abcdef", "devicekey": "k"})
        self.handler = stub_server.Handler.__new__(stub_server.Handler)
        self.handler.stub = self.stub
        self.sent = []
        patcher = mock.patch.object(stub_server.Handler, "_send_http",
                                    lambda _self, sock, status, body, ctype: self.sent.append(json.loads(body)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def asked_with(self, token):
        self.handler._serve_signed_in(None, {"authorization": f"Bearer {token}"}, self.stub.device_thing, "peer", "list")
        return self.sent[-1]["error"]

    def test_a_later_sign_in_ends_the_earlier_token(self):
        first = self.stub.account_login()["data"]["at"]
        self.assertEqual(self.asked_with(first), 0)
        second = self.stub.account_login()["data"]["at"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.asked_with(first), 401)
        self.assertEqual(self.asked_with(second), 0)

    def test_nothing_is_answered_before_a_sign_in(self):
        self.handler._serve_signed_in(None, {}, self.stub.device_thing, "peer", "list")
        self.assertEqual(self.sent[-1]["error"], 401)

    def test_an_ended_token_cannot_open_the_app_channel(self):
        first = self.stub.account_login()["data"]["at"]
        conn = mock.Mock(peer="peer")
        self.handler._app_message(conn, {"action": "userOnline", "at": first})
        self.assertEqual(json.loads(conn.send.call_args.args[0])["error"], 0)
        self.stub.account_login()
        self.handler._app_message(conn, {"action": "userOnline", "at": first})
        self.assertEqual(json.loads(conn.send.call_args.args[0])["error"], 401)


class TimeZoneTests(unittest.TestCase):
    def test_a_time_zone_query_is_answered_with_one(self):
        stub = stub_server.Stub("192.0.2.1", 443, 10)
        reply = stub.ws_reply({"action": "query", "deviceid": "1000abcdef", "params": ["TZ"]})
        tz = reply["params"]["TZ"]
        self.assertEqual((tz["dst"], tz["offset"], tz["nextOffset"]), (0, 0, 0))
        self.assertRegex(tz["utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertGreater(tz["nextDst"], tz["utc"])

    def test_another_query_is_answered_plainly(self):
        stub = stub_server.Stub("192.0.2.1", 443, 10)
        self.assertNotIn("params", stub.ws_reply({"action": "query", "params": ["switch"]}))


class SilentDeviceTests(unittest.TestCase):
    """A device switched off sends no close: missing its heartbeats, it goes
    offline, as at eWeLink."""

    def test_a_silent_device_is_dropped(self):
        import socket
        import threading
        stub = stub_server.Stub("192.0.2.1", 443, 10)
        stub.silence_limit = lambda: 0.5
        ours, theirs = socket.socketpair()
        handler = stub_server.Handler.__new__(stub_server.Handler)
        handler.stub = stub
        register = json.dumps({"action": "register", "deviceid": "1000abcdef", "apikey": "made-up"}).encode()
        theirs.sendall(bytes([0x81, len(register)]) + register)
        with mock.patch.dict(stub_server._devices, {}, clear=True), mock.patch.object(stub_server, "log"):
            serving = threading.Thread(target=handler._serve_ws, args=(ours, {}, "test"))
            serving.start()
            serving.join(10)
            self.assertFalse(serving.is_alive())
            self.assertFalse(stub_server._online("1000abcdef"))
        ours.close()
        theirs.close()


if __name__ == "__main__":
    unittest.main()
