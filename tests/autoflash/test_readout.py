"""A readout, what every chip family's shares: what it sends (the files a
read gives, and what the device said it is), where to and when not, which way
a device is read, and a run ending on it without a conversion."""
import http.server
import io
import lzma
import json
import os
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import account, errors, hostsys, output, plugs, prompts, readout, readout_espressif, runner, runs
from tools.autoflash.state import RunState
from tools.espressif import chips
from tools.ewelink import firmware_name, offered_firmware

MANIFEST = {"format": 1, "product": "NEWTHING", "stock_model": "SN-ESP32C3-NEW-01", "version": "1.0.0"}
DEVICE = {"deviceid": "1000abcdef", "name": "Hall light", "productModel": "NEWTHING",
          "extra": {"model": "SN-ESP32C3-NEW-01", "uiid": 77}, "params": {"fwVersion": "1.0.0"}}


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
                           email=lambda: "me@example.com", ewelink_env=lambda: {})


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
                  mock.patch.object(plugs, "remember_plug"),
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
                readout.run(self.ctx, DEVICE)
        self.assertEqual(read.call_args.args[1:3], (chips.ESP32C3, kinds.SONOFF_S60.wrapper_model))
        self.assertTrue(read.call_args.kwargs["known"])

    def test_on_the_reading_program_already_it_is_asked_of_ewelink_by_its_id(self):
        # The bridge answers no discovery, as Sonoff's firmware would.
        by_id = account.Asked({"packets": []}, "1.0.0", "SN-NEW-01", "NEWTHING", [], {}, "1000abcdef")
        with learned("FWSW-01-NEW-BK7238", "BK7238"), mock.patch.object(readout, "offer", return_value="done"), \
                mock.patch.object(readout_espressif.installer, "bridge_answers", return_value=True), \
                mock.patch.object(account, "ask_account", return_value=by_id) as asked:
            readout.run(self.ctx, DEVICE)
        self.assertEqual(asked.call_args.args[1], "1000abcdef")
        account.ask_ewelink.assert_not_called()

    def test_its_run_is_filed_under_the_device_as_a_conversions_is(self):
        with learned("FWSW-01-NEW-BK7238", "BK7238"), mock.patch.object(readout, "offer", return_value="done"):
            readout.run(self.ctx, DEVICE)
        self.assertEqual(self.filed.call_args.args[1], "12:34:56:ab:cd:ef")
        self.assertEqual(self.ctx.state.input("plug_mac"), "12:34:56:ab:cd:ef")


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

    def test_the_menu_offers_an_email_a_telegram_name_or_neither(self):
        self.offer(2)
        self.assertEqual(self.menu.call_args.args[1], ["Add an email address", "Add a Telegram username",
                                                       "Submit without contact information"])

    def test_an_email_goes_with_it(self):
        sent, said, _ = self.offer(0, " me@example.com ")
        self.assertEqual(json.loads(sent["manifest.json"])["contact"], {"email": "me@example.com"})
        self.assertIn("Sent, thank you", said)

    def test_a_telegram_name_goes_with_it_with_its_at(self):
        sent, _, _ = self.offer(1, "example_name")
        self.assertEqual(json.loads(sent["manifest.json"])["contact"], {"telegram": "@example_name"})

    def test_without_contact_nothing_is_asked_or_sent_of_it(self):
        sent, _, _ = self.offer(2)
        self.assertNotIn("contact", json.loads(sent["manifest.json"]))
        self.asked.assert_not_called()

    def test_quit_keeps_it_unsent(self):
        sent, said, kept = self.offer(None)
        self.assertIsNone(sent)
        self.assertTrue(kept)
        self.assertIn("Not sent", said)

    def test_what_is_typed_is_checked(self):
        self.assertTrue(readout.EMAIL.fullmatch("me@example.com"))
        self.assertFalse(readout.EMAIL.fullmatch("not an email"))
        self.assertTrue(readout.TELEGRAM.fullmatch("@example_name"))
        self.assertFalse(readout.TELEGRAM.fullmatch("abc"))

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
