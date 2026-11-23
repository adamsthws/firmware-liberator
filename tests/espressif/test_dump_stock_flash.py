import hashlib
import struct
import unittest
import zlib

from tools.espressif import dump_stock_flash

FLASH_SIZE = 0x400000
# The stock S60 layout (archive/docs/reference/s60-actual-partition-map.md).
LAYOUT = [
    ("nvs", 1, 0x02, 0x009000, 0x010000),
    ("reserve", 1, 0x02, 0x019000, 0x004000),
    ("otadata", 1, 0x00, 0x01D000, 0x002000),
    ("phy_init", 1, 0x01, 0x01F000, 0x001000),
    ("ota_0", 0, 0x10, 0x020000, 0x1F0000),
    ("ota_1", 0, 0x11, 0x210000, 0x1F0000),
]


def esp_image(version: str = "", project: str = "") -> bytes:
    """A minimal valid ESP32-C3 image, with an app description if named."""
    data = bytearray(256)
    if version:
        struct.pack_into("<I", data, 0, dump_stock_flash.APP_DESC_MAGIC)
        data[16 : 16 + len(version)] = version.encode()
        data[48 : 48 + len(project)] = project.encode()
        data[96:107] = b"Jan  1 2025"
    header = bytearray(24)
    header[0], header[1] = 0xE9, 1
    struct.pack_into("<H", header, 12, 5)  # ESP32-C3
    header[23] = 1  # SHA-256 appended
    body = bytes(header) + struct.pack("<II", 0x3C000020, len(data)) + bytes(data)
    checksum = 0xEF
    for value in data:
        checksum ^= value
    padded = body + bytes(((len(body) // 16) + 1) * 16 - 1 - len(body)) + bytes([checksum])
    return padded + hashlib.sha256(padded).digest()


def otadata_record(seq: int, state: int = 0xFFFFFFFF) -> bytes:
    crc = zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF)
    return struct.pack("<I", seq) + b"\xff" * 20 + struct.pack("<II", state, crc)


def flash(*, seqs=(5, 6), states=(0xFFFFFFFF, 0xFFFFFFFF), table_md5=True) -> bytearray:
    image = bytearray(b"\xff" * FLASH_SIZE)
    boot = esp_image()
    image[: len(boot)] = boot
    table = b"".join(
        struct.pack("<HBBII16sI", 0x50AA, ptype, subtype, offset, size, label.encode(), 0)
        for label, ptype, subtype, offset, size in LAYOUT
    )
    digest = hashlib.md5(table).digest() if table_md5 else bytes(16)
    table += struct.pack("<H", 0xEBEB) + b"\xff" * 14 + digest
    image[0x8000 : 0x8000 + len(table)] = table
    for base, seq, state in zip((0x1D000, 0x1E000), seqs, states):
        if seq is not None:
            image[base : base + 32] = otadata_record(seq, state)
    for offset, version in ((0x20000, "1.2.0"), (0x210000, "1.1.1")):
        app = esp_image(version, "esp32_fw")
        image[offset : offset + len(app)] = app
    return image


class DumpStockFlashTests(unittest.TestCase):
    def test_reports_both_stock_versions_and_the_booted_slot(self):
        report = dump_stock_flash.check(bytes(flash()))
        self.assertTrue(report["partition_table_md5_valid"])
        self.assertTrue(report["bootloader"]["checksum_valid"])
        slots = {p["label"]: p for p in report["partitions"]}
        self.assertEqual(slots["ota_0"]["app"]["version"], "1.2.0")
        self.assertEqual(slots["ota_1"]["app"]["version"], "1.1.1")
        self.assertTrue(slots["ota_0"]["image_valid"])
        # Sequence 6 is the newer record: (6 - 1) % 2 selects ota_1.
        self.assertEqual(report["otadata_selects"], "ota_1")

    def test_a_table_built_to_be_at_0xc000_is_checked_there(self):
        # The BASICR4's firmware and bootloader read it there; 0x8000 is blank.
        image = flash()
        image[0xC000:0xD000], image[0x8000:0x9000] = image[0x8000:0x9000], b"\xff" * 0x1000
        report = dump_stock_flash.check(bytes(image))
        self.assertEqual(report["partition_table_at"], "0xc000")
        self.assertTrue(report["partition_table_md5_valid"])
        self.assertEqual(report["otadata_selects"], "ota_1")
        self.assertIn("partition table at 0xc000, MD5: valid", dump_stock_flash.summary(report))

    def test_a_damaged_partition_table_is_reported(self):
        report = dump_stock_flash.check(bytes(flash(table_md5=False)))
        self.assertFalse(report["partition_table_md5_valid"])

    def test_an_erased_slot_is_not_a_valid_image(self):
        image = flash()
        image[0x210000:0x400000] = b"\xff" * 0x1F0000
        slots = {p["label"]: p for p in dump_stock_flash.check(bytes(image))["partitions"]}
        self.assertFalse(slots["ota_1"]["image_valid"])
        self.assertTrue(slots["ota_0"]["image_valid"])

    def test_otadata_ignores_bad_crc_and_rejected_states(self):
        image = flash(seqs=(7, 8), states=(0xFFFFFFFF, 3))  # 8 is ESP_OTA_IMG_INVALID
        self.assertEqual(dump_stock_flash.check(bytes(image))["otadata_selects"], "ota_0")
        image[0x1D000 + 28] ^= 0xFF  # break record 7's CRC too
        self.assertIsNone(dump_stock_flash.check(bytes(image))["otadata_selects"])

    def test_one_record_is_enough(self):
        self.assertEqual(dump_stock_flash.check(bytes(flash(seqs=(1, None))))["otadata_selects"], "ota_0")

    def test_real_otadata_crc(self):
        # A record read from a device: sequence 20 with its stored CRC.
        record = struct.pack("<I", 20) + b"\xff" * 24 + struct.pack("<I", 0x20843F37)
        self.assertEqual(dump_stock_flash.selected_ota_slot(record + b"\xff" * 0x1FE0, 2), 1)


class ReadInfoTests(unittest.TestCase):
    """A bridge with no /info must never stop a run: only the log loses it."""

    def answers(self, effect):
        from unittest import mock
        with mock.patch.object(dump_stock_flash, "fetch", side_effect=effect):
            return dump_stock_flash.read_info("192.0.2.51")

    def test_a_bridge_with_it(self):
        body = b"bridge 4.4.5\nchip: esp32\n"
        self.assertEqual(self.answers([(body, {})]), body)

    def test_anything_else_is_nothing(self):
        import http.client
        import urllib.error
        for effect in (urllib.error.HTTPError("http://192.0.2.51/info", 404, "Not Found", {}, None),
                       urllib.error.URLError("no route"), TimeoutError(), ConnectionResetError(),
                       http.client.BadStatusLine("x"), http.client.IncompleteRead(b""),
                       [(b"<html>a device's page</html>", {})]):
            self.assertIsNone(self.answers(effect), effect)


class Bridge:
    """A bridge on this computer: GET /flash streams `image` in chunks, as the
    bridge does, cut off after `cut_after` bytes where set (the connection
    closed with no final chunk); /flash-sha256 answers `on_device`'s hash, or
    404 as a bridge before 4.4.2 does where it is None."""

    def __init__(self, image: bytes, on_device=lambda digest: digest, cut_after: int | None = None) -> None:
        import http.server
        import threading
        bridge = self
        self.image, self.on_device, self.cut_after = image, on_device, cut_after

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                if self.path == "/flash":
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("X-Flash-Size", str(len(bridge.image)))
                    self.end_headers()
                    sent = 0
                    for at in range(0, len(bridge.image), 0x1000):
                        if bridge.cut_after is not None and sent >= bridge.cut_after:
                            self.close_connection = True
                            return
                        chunk = bridge.image[at:at + 0x1000]
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        sent += len(chunk)
                    self.wfile.write(b"0\r\n\r\n")
                    return
                if self.path == "/flash-sha256" and bridge.on_device is not None:
                    body = (bridge.on_device(hashlib.sha256(bridge.image).hexdigest()) + "\n").encode()
                elif self.path == "/efuse":
                    body = b"E" * 16
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ReadDumpTests(unittest.TestCase):
    """The whole flash read from a bridge, and checked byte for byte against
    the device's own hash of it."""

    def read(self, **bridge_args):
        bridge = Bridge(bytes(flash()), **bridge_args)
        self.addCleanup(bridge.close)
        self.progress = []
        return dump_stock_flash.read_dump(bridge.host, lambda done, of: self.progress.append((done, of)))

    def test_a_backup_that_matches_the_device_says_so(self):
        image, efuse, report = self.read(on_device=lambda digest: digest.upper())
        self.assertEqual(image, bytes(flash()))
        self.assertEqual(efuse, b"E" * 16)
        self.assertIs(report["matches_device"], True)
        self.assertEqual(self.progress[-1], (FLASH_SIZE, FLASH_SIZE))

    def test_a_backup_that_differs_from_the_device_is_refused(self):
        with self.assertRaises(dump_stock_flash.IncompleteDump):
            self.read(on_device=lambda digest: "0" * 64)

    def test_an_older_bridge_leaves_it_unchecked(self):
        _, _, report = self.read(on_device=None)
        self.assertIsNone(report["matches_device"])

    def test_a_read_cut_off_part_way_says_how_far_it_got(self):
        # Seen on a real device at C3: the reply cut short raised
        # http.client.IncompleteRead, which is no OSError, and escaped the
        # backup's own tries.
        with self.assertRaisesRegex(dump_stock_flash.IncompleteDump, "cut off after 1,048,576 bytes"):
            self.read(cut_after=0x100000)

    def test_a_bridge_that_does_not_answer_is_an_incomplete_dump(self):
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with self.assertRaises(dump_stock_flash.IncompleteDump):
            dump_stock_flash.read_dump(f"127.0.0.1:{port}")


class ReadTableTests(unittest.TestCase):
    """The table read off the start of the bridge's /flash, where the device's
    firmware reads it: 0x8000, or 0xC000 for the BASICR4's."""

    def table(self, image, **where):
        import io
        from unittest import mock
        reply = mock.MagicMock()
        reply.__enter__.return_value = io.BytesIO(image)
        with mock.patch("urllib.request.urlopen", return_value=reply):
            return dump_stock_flash.read_table("192.0.2.51", **where)

    def test_the_sector_where_it_is_asked_for(self):
        image = bytes(flash())
        self.assertEqual(self.table(image), image[0x8000:0x9000])
        self.assertEqual(self.table(image, at=0xC000), image[0xC000:0xD000])

    def test_a_flash_that_stops_short_of_it_is_refused(self):
        with self.assertRaisesRegex(OSError, "stopped short"):
            self.table(bytes(0xC800), at=0xC000)


if __name__ == "__main__":
    unittest.main()
