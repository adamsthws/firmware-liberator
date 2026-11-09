"""A public dump's identity never survives into the emulator's copy of it."""

from __future__ import annotations

import json
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


def donor_rows(update: public_dump.Update) -> dict[tuple[str, str], str]:
    """The settings `update`'s test copy is given on a donor: (space, key) to value."""
    names = {1: "frame_params", 2: "user_usual", 3: "user_unusual"}
    values = {(1, "content"): mock.Mock(kind=0x48, data=b'{"uiid":181,"deviceid":"1000000001","apikey":"'
                                        + b"a" * 36 + b'","model":"DONOR"}'),
              (2, "content"): mock.Mock(kind=0x48, data=b'{"init":"true","startup0":2,"chl0state":2}'),
              (3, "content"): mock.Mock(kind=0x48, data=b'{"init":"true","finalUserApikey":"' + b"c" * 36 + b'"}')}
    rows: list[tuple[str, str, str, str]] = []
    with mock.patch.object(public_dump.flashmap, "nvs", return_value=mock.Mock(offset=0x9000, size=0x3000)), \
            mock.patch.object(public_dump, "parse", return_value=(names, values)), \
            mock.patch.object(public_dump.rewrite_nvs, "rebuild", side_effect=lambda r, size: rows.extend(r) or b"\xff" * size):
        public_dump.anonymised(b"\xff" * 0x20000, update, update.stock_model, update.uiid, update.settings)
    # rebuild writes each row as a line of CSV, unquoted: a comma would cut it short.
    assert not any("," in field for row in rows for field in row), rows
    space, out = "", {}
    for key, kind, encoding, value in rows:
        if kind == "namespace":
            space = key
        else:
            out[space, key] = bytes.fromhex(value).decode() if encoding == "hex2bin" else value
    return out


class OwnSettings(unittest.TestCase):
    def test_the_account_it_was_paired_to_is_the_stubs(self):
        # The stub's account's apikey is the plug's own; another, the DualR3's
        # firmware takes for a new owner, and puts its settings back to defaults.
        update = public_dump.Update("u.bin", "0" * 64, 0xAC, "donor", "OWN")
        rows = donor_rows(update)
        self.assertEqual(json.loads(rows["user_unusual", "content"])["finalUserApikey"], update.apikey)

    def test_a_kind_with_its_own_settings_has_them_in_place_of_the_donors(self):
        rows = donor_rows(public_dump.Update("u.bin", "0" * 64, 0xAC, "donor", "OWN", 5, '{"init":"true","x":1}'))
        self.assertEqual(rows["user_usual", "content"], '{"init":"true","x":1}')
        self.assertIn('"uiid":5', rows["frame_params", "content"])

    def test_without_them_the_donors_stay(self):
        rows = donor_rows(public_dump.Update("u.bin", "0" * 64, 0xAC, "donor", "OWN"))
        self.assertIn('"chl0state":2', rows["user_usual", "content"])
        self.assertIn('"uiid":181', rows["frame_params", "content"])

    def test_the_dualr3_starts_as_a_dualr3_in_relay_mode(self):
        # Its firmware runs as a curtain motor (deviceMode 2) when it finds no
        # deviceMode, as on the TH Elite's settings, and refuses every switch.
        dualr3 = public_dump.UPDATES["sonoff-dualr3"]
        rows = donor_rows(dualr3)
        self.assertEqual(json.loads(rows["user_usual", "content"]).get("deviceMode"), 1)
        self.assertEqual(json.loads(rows["frame_params", "content"])["uiid"], 126)


if __name__ == "__main__":
    unittest.main()
