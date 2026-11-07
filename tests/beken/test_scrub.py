"""A Beken readout's flash leaves the computer with no Wi-Fi password and no
eWeLink identity in it - nodes replaced since included, and OpenBeken's own
copy - with its firmware untouched and every node's CRC good."""
import unittest

from tools.beken import ef40
from tools.beken import scrub

PASSWORD = b"correct-horse-9"
APIKEY = b"12345678-1234-4abc-8def-123456789abc"


def flash() -> bytes:
    image = bytearray(b"\xff" * 0x400000)
    store = b"".join([
        ef40.encode_node("wifi-ssid", "ExampleNet"),
        ef40.encode_node("wifi-pass", b"older-pass-1"),   # replaced since: still there
        ef40.encode_node("wifi-pass", PASSWORD),
        ef40.encode_node("pass", b"\x13\x37encoded\x00"),
        ef40.encode_node("apiKey", APIKEY),
        ef40.encode_node("user", b'{"account":"someone"}'),
        ef40.encode_node("lan&info", b'{"deviceid":"1000abcdef","port":8081}'),
        ef40.encode_node("f/1/k", b"\x01\x02\x03\x04matter-key"),
        ef40.encode_node("elecCalibFlag", b"\x01"),
    ])
    image[0x3E2000:0x3E2000 + len(store)] = store
    image[0x3E1000:0x3E1000 + 30] = b"obk:" + PASSWORD + b"\0" * 11    # OpenBeken's imported copy
    image[0x20000:0x20000 + len(PASSWORD)] = PASSWORD                   # in the firmware: code, left alone
    return bytes(image)


def nodes(image: bytes) -> dict[str, bytes]:
    return ef40.parse_store(image, (scrub.STORES[0], scrub.STORES[1]))


class ScrubTests(unittest.TestCase):
    def test_no_password_or_identity_is_left_in_the_stores(self):
        out, changed = scrub.scrub(flash())
        stores = out[scrub.STORES[0]:]
        for secret in (PASSWORD, b"older-pass-1", APIKEY, b"someone", b"1000abcdef", b"matter-key", b"encoded"):
            self.assertNotIn(secret, stores, secret)
        self.assertGreaterEqual(changed, 7)

    def test_every_node_still_checks_out_and_the_rest_is_kept(self):
        out, _ = scrub.scrub(flash())
        found = nodes(out)  # parse_store keeps only nodes whose CRC holds
        self.assertEqual(found["wifi-ssid"], b"ExampleNet")
        self.assertEqual(found["elecCalibFlag"], b"\x01")
        self.assertEqual(found["apiKey"], b"10000000-0000-0000-0000-000000000000")
        self.assertIn(b'"deviceid":"1000000000"', found["lan&info"])
        self.assertEqual(set(found), set(nodes(flash())))

    def test_the_firmware_is_left_byte_for_byte(self):
        before = flash()
        out, _ = scrub.scrub(before)
        self.assertEqual(out[:scrub.STORES[0]], before[:scrub.STORES[0]])
        self.assertEqual(len(out), len(before))

    def test_a_flash_with_no_store_is_returned_as_it_was(self):
        blank = b"\xff" * 0x400000
        self.assertEqual(scrub.scrub(blank), (blank, 0))


if __name__ == "__main__":
    unittest.main()
