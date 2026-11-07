"""A public dump's identity never survives into the emulator's copy of it."""

from __future__ import annotations

import unittest
from unittest import mock

from tools.espressif.emulator import public_dump

DUMP = public_dump.Dump("d.bin", "0" * 64, 0xAB)


class Identity(unittest.TestCase):
    def test_every_identifier_is_given_a_made_up_one_of_its_length(self):
        frame = {"deviceid": "1000000001", "apikey": "a" * 36, "encrypt_key": "b" * 36}
        new = public_dump.identities(frame, DUMP)
        self.assertEqual(set(new), {b"1000000001", b"a" * 36, b"b" * 36})
        for old, made_up in new.items():
            self.assertEqual(len(old), len(made_up))

    def test_keys_that_are_one_value_stay_one(self):
        new = public_dump.identities({"deviceid": "1000000001", "apikey": "a" * 36, "encrypt_key": "a" * 36}, DUMP)
        self.assertEqual(new[b"a" * 36], DUMP.apikey.encode())

    def test_a_copy_outside_the_settings_goes_too(self):
        flash = bytearray(b"\xff" * 0x20000)
        real = b"1000000001"
        flash[0x18000:0x18000 + len(real)] = real
        rows = []
        with mock.patch.object(public_dump.flashmap, "nvs", return_value=mock.Mock(offset=0x9000, size=0x3000)), \
                mock.patch.object(public_dump, "parse", return_value=({1: "frame_params"}, {
                    (1, "content"): mock.Mock(kind=0x48, data=b'{"deviceid":"1000000001","apikey":"' + b"a" * 36 + b'"}\0')})), \
                mock.patch.object(public_dump.rewrite_nvs, "rebuild", side_effect=lambda r, size: rows.extend(r) or b"\xff" * size):
            out = public_dump.anonymised(bytes(flash), DUMP)
        self.assertNotIn(real, out)
        self.assertNotIn(real.hex(), str(rows))
        self.assertIn(DUMP.deviceid.encode(), out)


if __name__ == "__main__":
    unittest.main()
