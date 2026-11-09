"""A readout, what every chip family's shares: what it sends (the files a
read gives, and what the device said it is), where to and when not, which way
a device is read, and a run ending on it without a conversion."""
import contextlib
import http.server
import io
import lzma
import json
import os
import tarfile
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import account, errors, hostsys, output, plugs, prompts, readout, readout_beken
from tools.autoflash import readout_espressif, runner, runs
from tools.autoflash.state import RunState
from tools.espressif import chips
from tools.ewelink import firmware_name, offered_firmware

# The website asked, where start_run says it wants every readout.
ASK_WANTED = readout.ask_wanted
MANIFEST = {"format": 1, "product": "NEWTHING", "stock_model": "SN-ESP32C3-NEW-01", "version": "1.0.0"}
DEVICE = {"deviceid": "1000abcdef", "name": "Hall light", "productModel": "NEWTHING",
          "extra": {"model": "SN-ESP32C3-NEW-01", "uiid": 77}, "params": {"fwVersion": "1.0.0"}}


def record(product: str, model: str) -> dict:
    """The account's record of a device eWeLink knows as `product`, its firmware as `model`."""
    return {**DEVICE, "productModel": product, "extra": {**DEVICE["extra"], "model": model}}


def members(data: bytes) -> list[str]:
    """The readout's files as they are stored, in order."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        return [member.name for member in archive.getmembers()]


def unpacked(data: bytes) -> dict[str, bytes]:
    """The readout's files, each .xz one decompressed, by its name without .xz."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        files = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    return {name.removesuffix(".xz"): lzma.decompress(body) if name.endswith(".xz") else body
            for name, body in files.items()}


def a_ctx(tmp: str) -> SimpleNamespace:
    return SimpleNamespace(state=RunState.open(Path(tmp)), dir=Path(tmp), listen_ip=lambda: "192.0.2.141",
                           email=lambda: "me@example.com", ewelink_env=lambda: {}, helper_env=lambda: {},
                           ewelink_region=lambda: "eu")


# The anonymised files a read gives offer(): the scrubbing itself is in the
# chip family's run path and tested in tests/espressif/test_scrub.py.
FILES = {"flash.bin": b"flash bytes", "efuse.bin": b"\0" * 124, "info.txt": b"bridge 4.4.5\n",
         "report.json": b"{}"}


class PackageTests(unittest.TestCase):
    def test_the_prepared_files_go_in_as_they_are(self):
        files = unpacked(readout.package(MANIFEST, FILES))
        self.assertEqual(set(files), {"manifest.json", "flash.bin", "efuse.bin", "info.txt", "report.json"})
        self.assertEqual(files["flash.bin"], b"flash bytes")

    def test_the_manifest_comes_first_as_it_is_and_the_firmware_in_xz(self):
        offered = [offered_firmware.Offered("user1.bin", "1.1.0", b"\xe9offered app")]
        names = members(readout.package(MANIFEST, FILES, offered))
        self.assertEqual(names[0], "manifest.json")
        self.assertIn("flash.bin.xz", names)
        self.assertIn("offered/user1.bin.xz", names)
        self.assertIn("report.json", names)

    def test_contact_details_go_in_the_manifest_only_when_given(self):
        given = json.loads(unpacked(readout.package(MANIFEST, None, (), {"email": "me@example.com"}))["manifest.json"])
        self.assertEqual(given["contact"], {"email": "me@example.com"})
        self.assertNotIn("contact", json.loads(unpacked(readout.package(MANIFEST, None))["manifest.json"]))

    def test_not_read_only_what_it_said_goes(self):
        self.assertEqual(set(unpacked(readout.package(MANIFEST, None))), {"manifest.json"})

    def test_the_firmware_ewelink_offers_goes_too_as_it_is(self):
        offered = [offered_firmware.Offered("user1.bin", "1.1.0", b"\xe9offered app")]
        files = unpacked(readout.package(MANIFEST, FILES, offered))
        self.assertEqual(files["offered/user1.bin"], b"\xe9offered app")
        self.assertIn("flash.bin", files)
        self.assertEqual(unpacked(readout.package(MANIFEST, None, offered))["offered/user1.bin"], b"\xe9offered app")


class DeviceAtTests(unittest.TestCase):
    """--plug-ip picks the account device at that address: by discovery, by its
    hardware address (a Beken plug does no discovery), or the one device there."""
    S61 = {"deviceid": "1000abcdef", "params": {"staMac": "12:34:56:AB:CD:EF"}}
    OTHER = {"deviceid": "2000bbbbbb", "params": {"staMac": "aa:bb:cc:dd:ee:ff"}}

    def device_at(self, address, devices, answering=None, mac=None):
        with mock.patch.object(readout.discover_ewelink, "discover", return_value={"packets": []}), \
                mock.patch.object(readout, "answering_ids", return_value=answering or {}), \
                mock.patch.object(readout.hostsys, "mac_of", return_value=mac):
            return readout.device_at(address, devices)

    def test_by_its_hardware_address_when_it_answers_no_discovery(self):
        self.assertIs(self.device_at("192.0.2.60", [self.OTHER, self.S61], mac="12:34:56:ab:cd:ef"), self.S61)

    def test_by_discovery_when_it_answers(self):
        self.assertIs(self.device_at("192.0.2.60", [self.OTHER, self.S61],
                                     answering={"2000bbbbbb": "192.0.2.60"}), self.OTHER)

    def test_the_one_device_in_the_account_when_nothing_else_tells(self):
        self.assertIs(self.device_at("192.0.2.60", [self.S61]), self.S61)

    def test_it_stops_when_it_cannot_tell_among_several(self):
        with self.assertRaisesRegex(readout.Abort, "could not tell which device"):
            self.device_at("192.0.2.60", [self.OTHER, self.S61])


class WhereTests(unittest.TestCase):
    def test_the_website_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(prompts, "UNATTENDED", False):
            self.assertEqual(readout.upload_url(), readout.UPLOAD_URL)

    def test_never_from_the_emulator_or_unattended(self):
        with mock.patch.dict(os.environ, {"AUTOFLASH_EMULATOR_URL": "http://127.0.0.1:1"}, clear=True):
            self.assertIsNone(readout.upload_url())
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(prompts, "UNATTENDED", True):
            self.assertIsNone(readout.upload_url())

    def test_a_test_server_when_given(self):
        with mock.patch.dict(os.environ, {"AUTOFLASH_READOUT_URL": "http://127.0.0.1:9/readout",
                                          "AUTOFLASH_EMULATOR_URL": "x"}):
            self.assertEqual(readout.upload_url(), "http://127.0.0.1:9/readout")


class ProofTests(unittest.TestCase):
    def test_it_is_the_work_the_worker_checks(self):
        import hashlib
        data = b"\x1f\x8breadout"
        stamp, nonce = readout.proof(data, now=1794000000, bits=12).split(":")
        digest = hashlib.sha256(f"{stamp}:{hashlib.sha256(data).hexdigest()}:{nonce}".encode()).digest()
        self.assertEqual(stamp, "1794000000")
        self.assertLess(int.from_bytes(digest, "big"), 1 << (256 - 12))


class SendTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(readout, "PROOF_BITS", 8)  # the real work takes seconds
        patch.start()
        self.addCleanup(patch.stop)

    def test_posted_as_a_tar_and_its_reference_returned(self):
        got = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                got["type"] = self.headers["Content-Type"]
                got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.dumps({"id": "r-2026-0001"}).encode()
                self.send_response(201)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            reference = readout.send(b"\x1f\x8bdata", f"http://127.0.0.1:{server.server_address[1]}/readout")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual((reference, got["type"], got["body"]), ("r-2026-0001", "application/x-tar", b"\x1f\x8bdata"))


class StillWantedTests(unittest.TestCase):
    """Asked before a readout is made: whether the website still wants one."""

    def ask(self, status=200, answer=None, url="set"):
        got = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                got["path"], got["type"] = self.path, self.headers["Content-Type"]
                got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                got["proof"] = self.headers["X-Proof"]
                body = json.dumps(answer if answer is not None else {"wanted": False}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        given = f"http://127.0.0.1:{server.server_address[1]}/readout" if url == "set" else url
        try:
            with mock.patch.object(readout, "upload_url", return_value=given), mock.patch.object(output, "log_only"):
                wanted = readout.ask_wanted("BASICR4", "SN-BASICR4-01", "1.1.0", "whole", ["a" * 64, "b" * 64])
        finally:
            server.shutdown()
            server.server_close()
        return wanted, got

    def test_it_asks_beside_where_readouts_go_with_lighter_work_of_its_own(self):
        import hashlib
        wanted, got = self.ask(answer={"wanted": False, "why": "held", "held": ["a" * 64, "c" * 64]})
        self.assertEqual(wanted, readout.Wanted(False, "held", frozenset({"a" * 64})))   # c was not asked of
        self.assertEqual((got["path"], got["type"]), ("/wanted", "application/json"))
        self.assertEqual(json.loads(got["body"]), {"format": 1, "product": "BASICR4", "model": "SN-BASICR4-01",
                                                   "version": "1.1.0", "stage": "whole",
                                                   "firmware": ["a" * 64, "b" * 64]})
        stamp, nonce = got["proof"].split(":")
        work = hashlib.sha256(f"{stamp}:{hashlib.sha256(got['body']).hexdigest()}:{nonce}".encode()).digest()
        self.assertLess(int.from_bytes(work, "big"), 1 << (256 - readout.LIGHT_PROOF_BITS))
        self.assertLess(readout.LIGHT_PROOF_BITS, readout.PROOF_BITS)

    def test_wanted_when_the_website_says_so_or_cannot_say(self):
        self.assertTrue(self.ask(answer={"wanted": True})[0].wanted)
        self.assertTrue(self.ask(status=404, answer={"error": "an earlier website"})[0].wanted)
        self.assertTrue(self.ask(status=429, answer={"error": "too many"})[0].wanted)
        self.assertTrue(self.ask(url="http://127.0.0.1:9/readout")[0].wanted)   # nothing there

    def test_never_asked_from_a_test_run(self):
        wanted, got = self.ask(url=None)
        self.assertTrue(wanted.wanted)
        self.assertEqual(got, {})


class RateLimitTests(unittest.TestCase):
    """The website takes two readouts a minute from one address: a readout of
    every device reaches that, as one in the emulator did (a 429, kept unsent)."""

    def post(self, answers):
        got = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                self.rfile.read(int(self.headers["Content-Length"]))
                got.append(self.headers["X-Proof"])
                status, body = answers[len(got) - 1]
                body = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(readout, "PROOF_BITS", 8), mock.patch.object(output, "say"), \
                    mock.patch.object(readout.time, "sleep") as slept:
                try:
                    said = readout.send(b"\x1f\x8bdata", f"http://127.0.0.1:{server.server_address[1]}/readout")
                except OSError as exc:
                    said = exc
        finally:
            server.shutdown()
            server.server_close()
        return said, got, [call.args[0] for call in slept.call_args_list]

    def test_too_many_at_once_is_sent_again_shortly_with_the_same_work(self):
        said, proofs, waits = self.post([(429, {"error": "too many"}), (201, {"id": "r-1"})])
        self.assertEqual(said, "r-1")
        self.assertEqual(len(set(proofs)), 1)
        self.assertEqual(waits, [15.0])

    def test_it_gives_up_after_a_minute_and_a_half_of_tries(self):
        said, proofs, waits = self.post([(429, {"error": "too many"})] * 7)
        self.assertIsInstance(said, OSError)
        self.assertIn("429", str(said))
        self.assertEqual((len(proofs), waits), (7, [15.0] * 6))

    def test_anything_else_turned_away_is_not_sent_again(self):
        said, proofs, waits = self.post([(503, {"error": "enough for today"})])
        self.assertIsInstance(said, OSError)
        self.assertEqual((len(proofs), waits), (1, []))


class LocateTests(unittest.TestCase):
    """A device's address with none given: by discovery, or for a Beken device,
    which does no LAN Control, by the hardware address its record gives."""
    BEKEN = {"deviceid": "1000abcdef", "productModel": "NEWBK", "extra": {"model": "SN-BK7238-NEW-01"},
             "params": {"staMac": "12:34:56:AB:CD:EF"}}

    def setUp(self):
        for patch in (mock.patch.object(output, "say"), mock.patch.object(output, "tell"),
                      mock.patch.object(output, "transient_begin"), mock.patch.object(output, "transient_end"),
                      mock.patch.object(hostsys, "lan_networks", return_value={"192.0.2.10": 24})):
            patch.start()
            self.addCleanup(patch.stop)
        discover = mock.patch.object(readout.discover_ewelink, "discover", return_value={"packets": []})
        self.discover = discover.start()
        self.addCleanup(discover.stop)

    def test_a_beken_device_is_found_by_its_hardware_address_unasked(self):
        with mock.patch.object(hostsys, "find_by_mac", return_value="192.0.2.77") as found, \
                mock.patch.object(prompts, "ask_ipv4") as asked:
            self.assertEqual(readout.locate(None, self.BEKEN), "192.0.2.77")
        self.assertEqual(found.call_args.args, ("12:34:56:ab:cd:ef", ["192.0.2.10"]))
        asked.assert_not_called()
        self.discover.assert_not_called()

    def test_a_beken_address_typed_is_checked_by_its_hardware_address_not_discovery(self):
        with mock.patch.object(hostsys, "find_by_mac", return_value=None), \
                mock.patch.object(prompts, "ask_ipv4", return_value="192.0.2.77") as asked:
            self.assertEqual(readout.locate(None, self.BEKEN), "192.0.2.77")
        self.assertNotIn("LAN Control", " ".join(asked.call_args.kwargs["help_lines"]))
        check = asked.call_args.kwargs["check"]
        with mock.patch.object(hostsys, "mac_of", return_value="12:34:56:ab:cd:ef"):
            self.assertEqual(check("192.0.2.77"), [])
        with mock.patch.object(hostsys, "mac_of", return_value="aa:bb:cc:dd:ee:ff"):
            self.assertIn("another device", check("192.0.2.1")[0])
        self.discover.assert_not_called()

    def test_one_not_answering_discovery_is_found_by_its_hardware_address(self):
        # An Espressif device on the reading program a readout stopped part way left on it.
        device = {**DEVICE, "params": {"staMac": "12:34:56:AB:CD:EF"}}
        with mock.patch.object(hostsys, "find_by_mac", return_value="192.0.2.77") as found, \
                mock.patch.object(prompts, "ask_ipv4") as asked:
            self.assertEqual(readout.locate(None, device), "192.0.2.77")
        self.assertEqual(found.call_args.args[0], "12:34:56:ab:cd:ef")
        asked.assert_not_called()

    def test_another_device_is_found_by_discovery(self):
        self.discover.return_value = {"packets": ["x"]}
        with mock.patch.object(readout, "answering_ids", return_value={"1000abcdef": "192.0.2.88"}), \
                mock.patch.object(hostsys, "find_by_mac") as by_mac:
            self.assertEqual(readout.locate(None, DEVICE), "192.0.2.88")
        by_mac.assert_not_called()


def start_run(test: unittest.TestCase) -> None:
    """`test` set up for readout.run on DEVICE, at 192.0.2.77, asking eWeLink
    nothing: its context (test.ctx), and the filing of its run (test.filed)."""
    for name in ("say", "tell", "log_only", "transient_begin", "transient_end", "progress", "progress_clear"):
        patch = mock.patch.object(output, name)
        patch.start()
        test.addCleanup(patch.stop)
    for patch in (mock.patch.object(prompts, "section"), mock.patch.object(prompts, "warning_box")):
        patch.start()
        test.addCleanup(patch.stop)
    test.tmp = tempfile.TemporaryDirectory()
    test.addCleanup(test.tmp.cleanup)
    test.ctx = a_ctx(test.tmp.name)
    asked = account.Asked({"packets": []}, "1.0.0", "SN-NEW-01", "NEWTHING", [], {}, "1000abcdef")
    filed = mock.patch.object(runs, "file_plug")
    test.filed = filed.start()
    test.addCleanup(filed.stop)
    for patch in (mock.patch.object(readout, "locate", return_value="192.0.2.77"),
                  # On Sonoff's firmware, not a readout's reading program left on it.
                  mock.patch.object(readout_espressif.installer, "bridge_answers", return_value=False),
                  mock.patch.object(hostsys, "mac_of", return_value="12:34:56:ab:cd:ef"),
                  mock.patch.object(account, "ask_ewelink", return_value=asked),
                  mock.patch.object(account, "ask_account", return_value=asked),
                  mock.patch.object(plugs, "remember_plug"),
                  # Wanted, as the website would say: never asked of it from a test.
                  mock.patch.object(readout, "ask_wanted", return_value=readout.Wanted(True)),
                  mock.patch.object(readout, "notify", return_value=True),
                  mock.patch.object(offered_firmware, "fetch",
                                    return_value=([offered_firmware.Offered("user1.bin", "1.1.0", b"\xe9")], []))):
        patch.start()
        test.addCleanup(patch.stop)


def learned(name: str, chip: str):
    """eWeLink's firmware name for the device learned as `name`, on `chip`."""
    return mock.patch.object(firmware_name, "learn", return_value=(firmware_name.Learned(name, chip, "eWeLink"), []))


class RunTests(unittest.TestCase):
    def setUp(self):
        start_run(self)

    def test_a_chip_with_no_bridge_is_sent_nothing_and_what_it_said_is_offered(self):
        with learned("FWSW-01-NEW-BK7238", "BK7238"), \
                mock.patch.object(hostsys, "run_tool") as sent, \
                mock.patch.object(readout, "offer", return_value="done") as offered:
            self.assertEqual(readout.run(self.ctx, DEVICE), "done")
        sent.assert_not_called()
        manifest, read, updates = offered.call_args.args[1:]
        self.assertEqual((manifest["firmware_name"], manifest["read"], read), ("FWSW-01-NEW-BK7238", False, None))
        self.assertEqual([item.name for item in updates], ["user1.bin"])
        self.assertEqual(manifest["offered"][0]["version"], "1.1.0")
        self.assertNotIn("1000abcdef", json.dumps(manifest))

    def test_a_kind_the_tool_knows_takes_its_own_name_when_ewelink_gives_none(self):
        from tools.autoflash import kinds
        s60 = account.Asked({"packets": []}, "1.2.0", kinds.SONOFF_S60.stock_model, "S60TPG", [], {}, "1000abcdef")
        with mock.patch.object(account, "ask_ewelink", return_value=s60), \
                mock.patch.object(firmware_name, "learn", return_value=(None, ["no offer"])), \
                mock.patch.object(readout_espressif, "read_out", side_effect=errors.NothingWritten("stop")) as read:
            with self.assertRaises(errors.NothingWritten):
                readout.run(self.ctx, record("S60TPG", kinds.SONOFF_S60.stock_model))
        self.assertEqual(read.call_args.args[1:3], (chips.ESP32C3, kinds.SONOFF_S60.wrapper_model))
        self.assertTrue(read.call_args.kwargs["known"])

    def asked_as(self, product: str, model: str):
        asked = account.Asked({"packets": []}, "1.0.0", model, product, [], {}, "1000abcdef")
        return mock.patch.multiple(account, ask_ewelink=mock.Mock(return_value=asked),
                                   ask_account=mock.Mock(return_value=asked))

    def read(self, product: str, model: str, name: str, chip: str):
        """What a readout of this device sent it (the reading program, or None),
        and what it offered to send."""
        with self.asked_as(product, model), learned(name, chip), \
                mock.patch.object(readout_espressif, "read_and_anonymise", return_value=({}, True)) as espressif, \
                mock.patch.object(readout_beken, "read_and_anonymise", return_value=({}, True)) as beken, \
                mock.patch.object(readout, "offer", return_value="done") as offered:
            readout.run(self.ctx, record(product, model))
        sent = espressif if espressif.called else beken if beken.called else None
        return sent, offered.call_args.args[1]

    def test_a_device_the_tool_has_not_seen_is_sent_nothing(self):
        # Even with a chip a bridge runs on: it could be laid out so the bridge
        # leaves it unable to start, as the BASICR4 would have been.
        sent, manifest = self.read("NEWTHING", "SN-NEW-01", "FWSW-E32-NEW-ESP32C3", "ESP32C3")
        self.assertIsNone(sent)
        self.assertFalse(manifest["read"])

    def test_a_kind_wanting_a_readout_is_read_in_full(self):
        from tools.autoflash import kinds
        basicr4 = kinds.SONOFF_BASICR4
        self.assertIs(basicr4.status, kinds.Status.READOUT_WANTED)
        sent, _ = self.read("BASICR4", basicr4.stock_model, basicr4.wrapper_model, "ESP32C3")
        self.assertIsNotNone(sent)

    def test_a_listed_product_under_another_firmware_name_is_sent_nothing(self):
        # A variant sharing the product model - a Matter version, say - is not the kind.
        from tools.autoflash import kinds
        sent, _ = self.read("S60TPG", kinds.SONOFF_S60.stock_model, "FWSW-E32S60M-S60M-ESP32C3", "ESP32C3")
        self.assertIsNone(sent)

    def test_a_kind_not_supported_yet_is_sent_nothing(self):
        from tools.autoflash import kinds
        nspanel = kinds.SONOFF_NSPANEL
        self.assertIs(nspanel.status, kinds.Status.NOT_SUPPORTED)
        sent, _ = self.read("NSPANEL", nspanel.stock_model, nspanel.wrapper_model, "ESP32D0WD")
        self.assertIsNone(sent)

    def test_on_the_reading_program_already_it_is_asked_of_ewelink_by_its_id(self):
        # The bridge answers no discovery, as Sonoff's firmware would.
        from tools.autoflash import kinds
        basicr4 = kinds.SONOFF_BASICR4
        by_id = account.Asked({"packets": []}, "1.1.0", basicr4.stock_model, "BASICR4", [], {}, "1000abcdef")
        with learned("FWSW-01-NEW-BK7238", "BK7238"), mock.patch.object(readout, "offer", return_value="done"), \
                mock.patch.object(readout_espressif.installer, "bridge_answers", return_value=True), \
                mock.patch.object(account, "ask_account", return_value=by_id) as asked:
            readout.run(self.ctx, record("BASICR4", basicr4.stock_model))
        self.assertEqual(asked.call_args.args[1], "1000abcdef")
        account.ask_ewelink.assert_not_called()

    def test_its_run_is_filed_under_the_device_as_a_conversions_is(self):
        device = {**DEVICE, "params": {**DEVICE["params"], "staMac": "12:34:56:AB:CD:EF"}}
        with learned("FWSW-01-NEW-BK7238", "BK7238"), mock.patch.object(readout, "offer", return_value="done"):
            readout.run(self.ctx, device)
        self.assertEqual(self.filed.call_args.args[1], "12:34:56:ab:cd:ef")
        self.assertEqual(self.ctx.state.input("plug_mac"), "12:34:56:ab:cd:ef")


def kinds_basicr4_model() -> str:
    from tools.autoflash import kinds
    return kinds.SONOFF_BASICR4.stock_model


class NotWantedTests(unittest.TestCase):
    """A readout that would add nothing, or one the day has no room for:
    nothing more is done - but the owner may still ask to be told."""

    def setUp(self):
        start_run(self)
        readout.ask_wanted.return_value = readout.Wanted(False, "held")
        for patch in (mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"),
                      mock.patch.object(prompts, "UNATTENDED", False)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_basicr4(self, contact=None):
        from tools.autoflash import kinds
        basicr4 = kinds.SONOFF_BASICR4
        asked = account.Asked({"packets": []}, "1.1.0", basicr4.stock_model, "BASICR4", [], {}, "1000abcdef")
        with learned(basicr4.wrapper_model, "ESP32C3"), \
                mock.patch.multiple(account, ask_ewelink=mock.Mock(return_value=asked),
                                    ask_account=mock.Mock(return_value=asked)), \
                mock.patch.object(readout, "ask_contact", return_value=contact) as asked, \
                mock.patch.object(readout_espressif, "read_and_anonymise") as read, \
                mock.patch.object(readout, "offer") as offered:
            said = readout.run(self.ctx, record("BASICR4", basicr4.stock_model))
        read.assert_not_called()
        offered.assert_not_called()
        offered_firmware.fetch.assert_not_called()
        return said, asked

    def test_nothing_is_fetched_sent_to_it_or_sent_in_and_it_asks_as_a_whole_readout(self):
        said, _ = self.run_basicr4()
        self.assertIn("Nothing was read or sent", said)
        product, model, version, stage, _ = readout.ask_wanted.call_args.args
        self.assertEqual((product, version, stage), ("BASICR4", "1.1.0", "whole"))

    def test_the_owner_may_still_ask_to_be_told_when_it_is_supported(self):
        said, asked = self.run_basicr4({"email": "me@example.com"})
        self.assertTrue(asked.call_args.kwargs["when_supported"])
        readout.notify.assert_called_once_with("me@example.com", [("BASICR4", kinds_basicr4_model())])
        self.assertIn("emailed when this device's status changes", said)

    def test_why_is_shown_before_it_is_asked(self):
        # On the screen, not the log alone: else the owner is asked for an
        # address with no word of why nothing is read.
        self.run_basicr4(None)
        self.assertIn("    Not needed: the website has what it needs of this device already.",
                      [call.args[0] for call in output.tell.call_args_list])

    def test_or_not(self):
        said, _ = self.run_basicr4(None)
        readout.notify.assert_not_called()
        self.assertEqual(said, "Nothing was read or sent. Thank you!")

    def test_in_a_batch_it_says_so_and_the_rest_go_on(self):
        with mock.patch.object(readout, "ask_contact", return_value=None), learned("FWSW-E32-NEW-ESP32C3", "ESP32C3"):
            said = readout.run_all(self.ctx, [DEVICE, {**DEVICE, "deviceid": "1000fedcba", "name": "Porch"}])
        self.assertIn("'Hall light' (NEWTHING): " + readout.NOT_NEEDED, said)
        self.assertIn("'Porch' (NEWTHING): " + readout.NOT_NEEDED, said)

    def test_a_full_day_ends_a_batch_there(self):
        readout.ask_wanted.return_value = readout.Wanted(False, "full")
        with mock.patch.object(readout, "ask_contact", return_value=None), learned("FWSW-E32-NEW-ESP32C3", "ESP32C3"):
            said = readout.run_all(self.ctx, [DEVICE, {**DEVICE, "deviceid": "1000fedcba", "name": "Porch"}])
        self.assertEqual(readout.ask_wanted.call_count, 1)
        self.assertIn("'Hall light' (NEWTHING), 'Porch' (NEWTHING): " + readout.FULL_TODAY, said)


class HeldFirmwareTests(unittest.TestCase):
    """The files eWeLink offers that the website holds already: left out."""

    OFFERS = {"offers": [{"version": "1.1.0", "binList": [
        {"name": "user1.bin", "downloadUrl": "https://example.com/user1.bin", "digest": "A" * 64},
        {"name": "user2.bin", "downloadUrl": "https://example.com/user2.bin", "digest": "b" * 64}]}]}

    def setUp(self):
        start_run(self)
        asked = account.Asked({"packets": []}, "1.0.0", "SN-NEW-01", "NEWTHING", [], self.OFFERS, "1000abcdef")
        for patch in (mock.patch.object(account, "ask_account", return_value=asked),
                      mock.patch.object(account, "ask_ewelink", return_value=asked)):
            patch.start()
            self.addCleanup(patch.stop)
        readout.ask_wanted.return_value = readout.Wanted(True, "", frozenset({"a" * 64}))

    def test_the_digests_go_with_the_question_and_a_held_file_is_not_fetched(self):
        with learned("FWSW-E32-NEW-ESP32C3", "ESP32C3"), mock.patch.object(readout, "offer", return_value="done") as offered:
            readout.run(self.ctx, DEVICE)
        self.assertEqual(readout.ask_wanted.call_args.args[4], ["a" * 64, "b" * 64])
        self.assertEqual(offered_firmware.fetch.call_args.kwargs["skip"], frozenset({"a" * 64}))
        manifest = offered.call_args.args[1]
        self.assertIn({"file": None, "version": "1.1.0", "sha256": "a" * 64, "held": True}, manifest["offered"])

    def test_a_beken_device_read_in_full_still_fetches_it_to_put_sonoff_back_but_does_not_send_it(self):
        from tools.autoflash import kinds
        s61 = kinds.SONOFF_S61
        held = offered_firmware.Offered("user1.bin", "1.1.0", b"held")
        readout.ask_wanted.return_value = readout.Wanted(True, "", frozenset({held.sha256}))
        offered_firmware.fetch.return_value = ([held], [])
        asked = account.Asked({"packets": []}, "1.0.2", s61.stock_model, "S61STPF", [], self.OFFERS, "1000abcdef")
        with mock.patch.multiple(account, ask_ewelink=mock.Mock(return_value=asked),
                                 ask_account=mock.Mock(return_value=asked)):
            with learned(s61.wrapper_model, "BK7238"), \
                    mock.patch.object(readout_beken, "stock_from_offered", return_value=mock.Mock()), \
                    mock.patch.object(readout_beken, "read_and_anonymise", return_value=({}, True)), \
                    mock.patch.object(readout, "offer", return_value="done") as offered:
                readout.run(self.ctx, record("S61STPF", s61.stock_model))
        self.assertEqual(offered_firmware.fetch.call_args.kwargs["skip"], frozenset())
        self.assertEqual(offered.call_args.args[3], [])


class NotifyTests(unittest.TestCase):
    """An owner's address, to be told when their devices' status changes."""

    def test_one_request_naming_each_device_once(self):
        with mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"), \
                mock.patch.object(readout, "light_post", return_value={}) as posted:
            self.assertTrue(readout.notify("me@example.com", [("NSPanel", "E32-MSW-NX"), ("NSPanel", "E32-MSW-NX"),
                                                              ("NEWTHING", ""), ("", "x")]))
        path, value = posted.call_args.args
        self.assertEqual((path, value["email"]), ("/notify", "me@example.com"))
        self.assertEqual(value["devices"], [{"product": "NSPanel", "model": "E32-MSW-NX"},
                                            {"product": "NEWTHING", "model": "-"}])

    def test_never_from_a_test_run_and_never_raises(self):
        with mock.patch.object(readout, "upload_url", return_value=None):
            self.assertFalse(readout.notify("me@example.com", [("NSPanel", "x")]))
        with mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"), \
                mock.patch.object(readout, "light_post", side_effect=OSError("refused")), \
                mock.patch.object(output, "log_only"):
            self.assertFalse(readout.notify("me@example.com", [("NSPanel", "x")]))

    def test_a_batch_asks_once_for_every_device_wanted_or_not(self):
        start_run(self)
        with mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"), \
                mock.patch.object(prompts, "UNATTENDED", False), \
                mock.patch.object(readout, "ask_contact", return_value={"email": "me@example.com"}), \
                mock.patch.object(readout, "run", return_value="sent"):
            readout.run_all(self.ctx, [DEVICE, {**DEVICE, "deviceid": "1000fedcba", "productModel": "OTHER"}])
        readout.notify.assert_called_once()
        self.assertEqual(readout.notify.call_args.args[1], [("NEWTHING", "SN-ESP32C3-NEW-01"),
                                                            ("OTHER", "SN-ESP32C3-NEW-01")])

    def test_a_readout_sent_with_an_address_asks_too(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(prompts, "section"), \
                mock.patch.object(output, "say"), mock.patch.object(prompts, "UNATTENDED", False), \
                mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"), \
                mock.patch.object(readout, "ask_contact", return_value={"email": "me@example.com"}), \
                mock.patch.object(readout, "send", return_value="r-1"), \
                mock.patch.object(readout, "notify") as told:
            readout.offer(a_ctx(tmp), MANIFEST, None)
        told.assert_called_once_with("me@example.com", [("NEWTHING", "SN-ESP32C3-NEW-01")])


class ProofProgressTests(unittest.TestCase):
    def test_each_wait_turns_the_stages_spinner(self):
        # Outside a stage nothing else moves: an owner left looking at a still
        # screen for a minute may take it to have stopped.
        shown = []

        @contextlib.contextmanager
        def working(text):
            shown.append(text)
            yield

        answers = iter([urllib.error.HTTPError("u", 429, "too many", {}, io.BytesIO(b"{}")), None])

        def opened(request, timeout):
            failed = next(answers)
            if failed:
                raise failed
            return io.BytesIO(b'{"id": "r-1"}')
        with mock.patch.object(output, "working", working), mock.patch.object(readout, "PROOF_BITS", 8), \
                mock.patch.object(readout.hostsys, "urlopen", side_effect=opened), \
                mock.patch.object(readout.time, "sleep"):
            self.assertEqual(readout.send(b"data", "http://127.0.0.1:9/readout"), "r-1")
        self.assertEqual([text.split(":")[0] for text in shown], ["Getting it ready to send",
                                                                 "The website takes two readouts a minute"])

    def test_long_work_says_it_is_still_going(self):
        ticks = []
        readout.proof(b"data", now=1794000000, bits=21, tick=lambda: ticks.append(1))
        self.assertGreater(len(ticks), 0)


class FirstStageTests(unittest.TestCase):
    """A device the tool has not seen: everything from eWeLink, nothing asked of it."""

    def setUp(self):
        start_run(self)

    def test_it_is_not_looked_for_on_the_network(self):
        # So a device answering no discovery - a Matter one, say - is read out too.
        with learned("FWSW-E32-NEW-ESP32C3", "ESP32C3"), mock.patch.object(readout, "offer", return_value="done"):
            readout.run(self.ctx, DEVICE)
        readout.locate.assert_not_called()
        account.ask_ewelink.assert_not_called()
        self.assertEqual(account.ask_account.call_args.args[1], "1000abcdef")

    def test_with_nothing_from_its_cloud_the_accounts_record_is_sent(self):
        failed = mock.Mock(side_effect=errors.Abort("no OTA model"))
        with mock.patch.multiple(account, ask_account=failed, ask_ewelink=failed), \
                mock.patch.object(firmware_name, "learn", return_value=(None, ["no offer"])), \
                mock.patch.object(readout, "offer", return_value="done") as offered:
            self.assertEqual(readout.run(self.ctx, DEVICE), "done")
        manifest = offered.call_args.args[1]
        self.assertEqual((manifest["product"], manifest["stock_model"], manifest["version"], manifest["read"]),
                         ("NEWTHING", "SN-ESP32C3-NEW-01", "1.0.0", False))


class ChooseTests(unittest.TestCase):
    def choose(self, devices, answer):
        with mock.patch.object(output, "transient_begin"), mock.patch.object(output, "transient_end"), \
                mock.patch.object(prompts, "ask_choice", return_value=answer) as asked:
            chosen = readout.choose(SimpleNamespace(), devices)
        return asked.call_args.args[1], chosen

    def test_every_device_not_supported_is_offered_first(self):
        from tools.autoflash import kinds
        s60 = record("S60TPG", kinds.SONOFF_S60.stock_model)
        other = {**DEVICE, "deviceid": "1000fedcba", "name": "Porch"}
        options, chosen = self.choose([s60, DEVICE, other], 0)
        self.assertEqual(options[0], "Every device the tool does not convert yet (2)")
        self.assertIsNone(chosen)
        self.assertEqual(self.choose([s60, DEVICE, other], 2)[1], DEVICE)

    def test_with_one_such_device_only_the_devices_are_offered(self):
        from tools.autoflash import kinds
        options, _ = self.choose([record("S60TPG", kinds.SONOFF_S60.stock_model), DEVICE], 0)
        self.assertEqual(len(options), 2)

    def test_every_one_is_every_device_the_tool_does_not_convert(self):
        from tools.autoflash import kinds
        s60 = record("S60TPG", kinds.SONOFF_S60.stock_model)
        with mock.patch.object(readout, "choose", return_value=None), \
                mock.patch.object(readout, "run_all", return_value="done") as every:
            self.assertEqual(readout.read_out_chosen(SimpleNamespace(), [s60, DEVICE]), "done")
        self.assertEqual(every.call_args.args[1], [DEVICE])


class RunAllTests(unittest.TestCase):
    """Every device the tool does not convert, read out in one go, each sent on its own."""

    def setUp(self):
        start_run(self)
        from tools.autoflash import kinds
        self.basicr4 = {**record("BASICR4", kinds.SONOFF_BASICR4.stock_model), "deviceid": "1000000004",
                        "name": "Shed"}
        self.other = {**DEVICE, "deviceid": "1000fedcba", "name": "Porch"}
        for patch in (mock.patch.object(prompts, "UNATTENDED", False),
                      mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"),
                      mock.patch.object(readout, "ask_contact", return_value={"email": "me@example.com"}),
                      mock.patch.object(runs, "hold_plug")):
            patch.start()
            self.addCleanup(patch.stop)

    def run_all(self, devices, send=None):
        """run_all through readout.run, each device's readout sent by `send`
        (default: a reference each); what it ended by saying, and what was sent."""
        sent = mock.Mock(side_effect=send or (lambda data, url: f"r-{len(sent.call_args_list)}"))
        with learned("FWSW-E32-NEW-ESP32C3", "ESP32C3"), mock.patch.object(readout, "send", sent), \
                mock.patch.object(readout_espressif, "read_and_anonymise", return_value=(FILES, True)) as read:
            said = readout.run_all(self.ctx, devices)
        self.read = read
        return said, [json.loads(unpacked(call.args[0])["manifest.json"]) for call in sent.call_args_list]

    def test_each_is_sent_on_its_own_with_the_contact_asked_once(self):
        said, manifests = self.run_all([DEVICE, self.other])
        self.assertEqual(len(manifests), 2)
        self.assertEqual([manifest["contact"] for manifest in manifests], [{"email": "me@example.com"}] * 2)
        readout.ask_contact.assert_called_once()
        self.assertIn("'Hall light' (NEWTHING): sent, reference r-1.", said)
        self.assertIn("'Porch' (NEWTHING): sent, reference r-2.", said)

    def test_the_first_stage_ones_go_before_a_full_readout(self):
        from tools.autoflash import kinds
        with mock.patch.object(account, "ask_ewelink", side_effect=lambda ctx, ip: account.Asked(
                {"packets": []}, "1.1.0", kinds.SONOFF_BASICR4.stock_model, "BASICR4", [], {}, "1000000004")), \
                learned(kinds.SONOFF_BASICR4.wrapper_model, "ESP32C3"), \
                mock.patch.object(readout, "send", side_effect=["r-1", "r-2"]) as sent, \
                mock.patch.object(readout_espressif, "read_and_anonymise", return_value=(FILES, True)):
            readout.run_all(self.ctx, [self.basicr4, DEVICE])
        self.assertEqual([json.loads(unpacked(call.args[0])["manifest.json"]).get("read", True)
                          for call in sent.call_args_list], [False, True])

    def test_one_the_website_has_enough_of_is_said_so_and_the_rest_go_on(self):
        said, manifests = self.run_all([DEVICE, self.other], send=[readout.ENOUGH, "r-2"])
        self.assertEqual(len(manifests), 2)
        self.assertIn("'Hall light' (NEWTHING): not needed, as enough readouts of this device are in already.", said)

    def test_one_not_sent_is_kept_under_its_id_and_the_rest_go_on(self):
        said, manifests = self.run_all([DEVICE, self.other], send=[OSError("no route"), "r-2"])
        self.assertEqual(len(manifests), 2)
        self.assertTrue((Path(self.tmp.name) / "readout-1000abcdef.tar").exists())
        self.assertIn("readout --send", said)

    def test_a_full_readout_passed_by_at_its_question_lets_the_rest_go_on(self):
        # The first stage's go first: DEVICE's, then the BASICR4's full one.
        with mock.patch.object(readout, "run", side_effect=["sent", errors.NothingWritten("not confirmed")]):
            said = readout.run_all(self.ctx, [self.basicr4, DEVICE])
        self.assertIn("'Shed' (BASICR4): not read out - not confirmed.", said)
        self.assertIn("(NEWTHING): sent", said)

    def test_a_full_readout_failing_part_way_stops_the_rest(self):
        # It may be left on its reading program, and need seeing to first.
        second = {**self.basicr4, "deviceid": "1000000005", "name": "Barn"}
        with mock.patch.object(readout, "run", side_effect=["sent", errors.Abort("the bridge did not answer"),
                                                             "sent"]) as each:
            with self.assertRaises(errors.Abort) as stopped:
                readout.run_all(self.ctx, [self.basicr4, DEVICE, second])
        self.assertEqual(each.call_count, 2)
        self.assertIn("Not read out yet: 'Barn' (BASICR4).", str(stopped.exception))
        self.assertIn("'Shed' (BASICR4): the bridge did not answer", str(stopped.exception))
        self.assertIn("'Hall light' (NEWTHING): sent", str(stopped.exception))

    def test_a_first_stage_failing_lets_the_rest_go_on(self):
        with mock.patch.object(readout, "run", side_effect=[errors.Abort("eWeLink is down"), "sent"]):
            said = readout.run_all(self.ctx, [DEVICE, self.other])
        self.assertIn("'Hall light' (NEWTHING): not read out - eWeLink is down.", said)
        self.assertIn("'Porch' (NEWTHING): sent", said)

    def test_nothing_of_one_device_is_taken_for_the_next(self):
        seen = []

        def each(ctx, device, batch):
            seen.append(ctx.state.input("plug_ip"))
            ctx.state.set_input("plug_ip", "192.0.2.77")
            ctx.state.set_input(readout_espressif.BRIDGE_HOST, "192.0.2.77")
            return "sent"
        with mock.patch.object(readout, "run", side_effect=each):
            readout.run_all(self.ctx, [DEVICE, self.other])
        self.assertEqual(seen, [None, None])

    def test_the_run_stays_where_it_is_and_a_device_written_to_is_held(self):
        from tools.autoflash import kinds
        device = {**self.basicr4, "params": {**DEVICE["params"], "staMac": "12:34:56:AB:CD:EF"}}
        with mock.patch.object(account, "ask_ewelink", return_value=account.Asked(
                {"packets": []}, "1.1.0", kinds.SONOFF_BASICR4.stock_model, "BASICR4", [], {}, "1000000004")), \
                learned(kinds.SONOFF_BASICR4.wrapper_model, "ESP32C3"), \
                mock.patch.object(readout, "send", return_value="r-1"), \
                mock.patch.object(readout_espressif, "read_and_anonymise", return_value=(FILES, True)):
            readout.run_all(self.ctx, [device])
        self.filed.assert_not_called()
        self.assertEqual(runs.hold_plug.call_args.args[1], "12:34:56:ab:cd:ef")

    def test_a_full_readouts_question_may_pass_its_device_by(self):
        from tools.autoflash import kinds
        with mock.patch.object(account, "ask_ewelink", return_value=account.Asked(
                {"packets": []}, "1.1.0", kinds.SONOFF_BASICR4.stock_model, "BASICR4", [], {}, "1000000004")), \
                learned(kinds.SONOFF_BASICR4.wrapper_model, "ESP32C3"), \
                mock.patch.object(readout_espressif, "read_and_anonymise",
                                  side_effect=errors.NothingWritten("skipped as asked, nothing was sent to it")) as read:
            said = readout.run_all(self.ctx, [self.basicr4])
        self.assertTrue(read.call_args.kwargs["skippable"])
        self.assertIn("'Shed' (BASICR4): not read out - skipped as asked, nothing was sent to it.", said)

    def test_q_at_a_full_readouts_question_stops_the_rest(self):
        second = {**self.basicr4, "deviceid": "1000000005", "name": "Barn"}
        with mock.patch.object(readout, "run", side_effect=["sent", prompts.QuitRun("q"), "sent"]) as each:
            said = readout.run_all(self.ctx, [self.basicr4, DEVICE, second])
        self.assertEqual(each.call_count, 2)
        self.assertIn("Stopped as asked", said)
        self.assertIn("'Hall light' (NEWTHING): sent", said)
        self.assertIn("Not read out: 'Shed' (BASICR4), 'Barn' (BASICR4).", said)

    def test_each_device_sent_carries_work_of_its_own(self):
        # One proof is good for one readout's bytes alone, so a batch cannot
        # spread one proof over many devices: each POST is worked for afresh.
        import hashlib
        posted = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                body = self.rfile.read(int(self.headers["Content-Length"]))
                posted.append((self.path, hashlib.sha256(body).hexdigest(), self.headers["X-Proof"]))
                answer = json.dumps({"wanted": True} if self.path == "/wanted" else {"id": f"r-{len(posted)}"}).encode()
                self.send_response(201)
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def asked(ctx, deviceid):
            return account.Asked({"packets": []}, "1.0.0", "SN-NEW-01", f"NEW-{deviceid}", [], {}, deviceid)
        with mock.patch.object(readout, "PROOF_BITS", 8), learned("FWSW-E32-NEW-ESP32C3", "ESP32C3"), \
                mock.patch.object(readout, "ask_wanted", ASK_WANTED), \
                mock.patch.object(account, "ask_account", side_effect=asked), \
                mock.patch.object(readout, "upload_url",
                                  return_value=f"http://127.0.0.1:{server.server_address[1]}/readout"):
            readout.run_all(self.ctx, [DEVICE, self.other])
        # Each device asked first, then its readout sent: each with work of its own.
        self.assertEqual([path for path, _, _ in posted], ["/wanted", "/readout"] * 2)
        self.assertEqual(len({digest for _, digest, _ in posted}), 4)
        for path, digest, proof in posted:
            stamp, nonce = proof.split(":")
            work = hashlib.sha256(f"{stamp}:{digest}:{nonce}".encode()).digest()
            bits = readout.LIGHT_PROOF_BITS if path == "/wanted" else 8
            self.assertLess(int.from_bytes(work, "big"), 1 << (256 - bits))

    def test_quit_at_the_contact_question_reads_nothing(self):
        with mock.patch.object(readout, "ask_contact", side_effect=prompts.QuitRun("q")), \
                mock.patch.object(readout, "run") as each:
            self.assertEqual(readout.run_all(self.ctx, [DEVICE, self.other]), "Nothing was read out or sent.")
        each.assert_not_called()


class OfferTests(unittest.TestCase):
    def offer(self, choice, typed=None):
        """offer() with the contact menu answered `choice` (index; None: Quit) and
        `typed` typed: what was sent, and what the run ends by saying."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(prompts, "section"), \
                mock.patch.object(output, "transient_begin"), mock.patch.object(output, "transient_end"), \
                mock.patch.object(output, "say"), mock.patch.object(prompts, "UNATTENDED", False), \
                mock.patch.object(prompts, "ask_choice",
                                  side_effect=prompts.QuitRun("q") if choice is None else None,
                                  return_value=choice) as menu, \
                mock.patch.object(prompts, "ask_text", return_value=typed) as asked, \
                mock.patch.object(readout, "upload_url", return_value="http://127.0.0.1:9/readout"), \
                mock.patch.object(readout, "send", return_value="r-1") as sent:
            said = readout.offer(a_ctx(tmp), MANIFEST, None)
            kept = (Path(tmp) / readout.NAME).exists()
        self.menu, self.asked = menu, asked
        return (unpacked(sent.call_args.args[0]) if sent.called else None), said, kept

    def test_the_menu_offers_an_email_or_none(self):
        self.offer(1)
        self.assertEqual(self.menu.call_args.args[1], ["Add an email address", "Submit without contact information"])

    def test_an_email_goes_with_it(self):
        sent, said, _ = self.offer(0, " me@example.com ")
        self.assertEqual(json.loads(sent["manifest.json"])["contact"], {"email": "me@example.com"})
        self.assertIn("Sent, thank you", said)

    def test_without_contact_nothing_is_asked_or_sent_of_it(self):
        sent, _, _ = self.offer(1)
        self.assertNotIn("contact", json.loads(sent["manifest.json"]))
        self.asked.assert_not_called()

    def test_quit_keeps_it_unsent(self):
        sent, said, kept = self.offer(None)
        self.assertIsNone(sent)
        self.assertTrue(kept)
        self.assertIn("Not sent", said)

    def test_what_is_typed_is_checked(self):
        with mock.patch.object(prompts, "UNATTENDED", False), \
                mock.patch.object(prompts, "ask_choice", return_value=0), \
                mock.patch.object(prompts, "ask_text", return_value="me@example.com") as asked, \
                mock.patch.object(output, "say"):
            self.assertEqual(readout.ask_contact(), {"email": "me@example.com"})
        valid = asked.call_args.kwargs["validate"]
        self.assertTrue(valid("me@example.com")[0])
        for typed in ("not an email", "a@b.c", "test@test"):
            self.assertFalse(valid(typed)[0], typed)

    def test_the_owner_is_never_asked_for_their_wifi_password(self):
        # Where the settings hold none, the copy goes as it is: a question
        # here only makes people hesitate to send it.
        flash = b"\xff" * 0x20000
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(prompts, "section") as shown, \
                mock.patch.object(prompts, "ask_secret") as asked, mock.patch.object(output, "say"), \
                mock.patch.object(readout, "upload_url", return_value=None):
            readout.offer(a_ctx(tmp), MANIFEST, {"flash.bin": flash})
        asked.assert_not_called()
        self.assertIn("- the device's firmware readout, anonymised;", shown.call_args.args[1])


class OfferedAtA1Tests(unittest.TestCase):
    def test_an_account_of_devices_not_supported_offers_a_readout(self):
        with mock.patch.object(output, "transient_begin"), mock.patch.object(output, "transient_end"), \
                mock.patch.object(prompts, "section") as shown, \
                mock.patch.object(prompts, "ask_choice", return_value=3) as asked, \
                mock.patch.object(readout, "from_run", side_effect=errors.Finished("read")) as read:
            with self.assertRaises(errors.Finished):
                account.find_account_plug(SimpleNamespace(email=lambda: "me@example.com"), [DEVICE])
        self.assertIn("Read-out my device to help add support for it", asked.call_args.args[1])
        self.assertEqual(read.call_args.args[1], [DEVICE])
        self.assertIn("'Hall light' (NEWTHING)", " ".join(shown.call_args.args[1]))

    def test_an_empty_account_does_not(self):
        with mock.patch.object(output, "transient_begin"), mock.patch.object(output, "transient_end"), \
                mock.patch.object(prompts, "section"), \
                mock.patch.object(prompts, "ask_choice", side_effect=prompts.QuitRun) as asked:
            with self.assertRaises(prompts.QuitRun):
                account.find_account_plug(SimpleNamespace(email=lambda: "me@example.com"), [])
        self.assertEqual(len(asked.call_args.args[1]), 3)


class EndsTheRunTests(unittest.TestCase):
    def test_a_readout_at_a1_ends_the_run_with_nothing_converted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_ctx(tmp)
            with mock.patch.object(output, "stage_end") as ended, mock.patch.object(output, "tell") as told, \
                    mock.patch.object(runner.routes, "begin_stage"), mock.patch.object(output, "say"), \
                    mock.patch.object(runner.routes, "STEP_INDEX",
                                      {"A1": ("Pair", mock.Mock(side_effect=errors.Finished("Sent, thank you!")))}):
                self.assertEqual(runner.execute(ctx, "A1"), 0)
            self.assertTrue(ctx.finished)
            self.assertEqual(ctx.state.status("A1"), "failed")
        ended.assert_called_with("complete")
        self.assertIn("Sent, thank you!", [call.args[0] for call in told.call_args_list])


if __name__ == "__main__":
    unittest.main()


class OfferScreenTests(unittest.TestCase):
    """The offer to send and its question go together once answered: what the
    run then says is the answer.  Seen in an attended readout recorded in the
    emulator: the offer - a question - was left with nothing under it."""

    def offer(self, url, sending=None):
        from tests.autoflash.test_unlock import drawn
        said = {}

        def body(look):
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(prompts, "UNATTENDED", False), \
                    mock.patch.object(readout, "upload_url", return_value=url), \
                    mock.patch.object(readout, "ask_contact", return_value=None), \
                    mock.patch.object(readout, "send", side_effect=sending or (lambda data, url: "r-1")):
                try:
                    said["end"] = readout.offer(a_ctx(tmp), MANIFEST, None)
                except readout.Abort as exc:
                    said["end"] = str(exc)
        _, final = drawn(body)
        return final, said["end"]

    def test_taken_off_once_answered(self):
        for url in (None, "http://127.0.0.1:9/readout"):   # a test run's, and one sent
            with self.subTest(url=url):
                final, said = self.offer(url)
                self.assertNotIn("Send this firmware readout", final)
                self.assertTrue(said.startswith(("Readout saved", "Sent")), said)

    def test_left_when_sending_fails_to_say_what(self):
        final, said = self.offer("http://127.0.0.1:9/readout", sending=OSError("no route"))
        self.assertIn("Send this firmware readout", final)
        self.assertIn("could not be sent", said)
