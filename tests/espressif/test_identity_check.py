"""The identity check: a device's identity against what its eWeLink account
record rebuilds, said as outcomes alone - never a value."""
import json
import struct
import unittest

from tests.espressif.test_scrub import basicr4_flash
from tools.espressif import ais_records, identity_check, nvs_image, sonoff_identity
from tools.espressif.identity_check import MISSING, NO, UNKNOWN, YES

DISPATCH = ("eu-dispd.coolkit.cc", 443)
DEVICEID, KEY = "10000e32ab", "00000000-1111-2222-3333-444444444444"
ACCOUNT = {"deviceid": DEVICEID, "devicekey": KEY, "extra": {"model": "SN-ESP32C3-S60-01", "uiid": 190}}
FRAME = {"init": "true", "uiid": 190, "deviceid": DEVICEID, "apikey": KEY, "model": "SN-ESP32C3-S60-01",
         "dis_server": DISPATCH[0], "dis_port": DISPATCH[1], "sha256": "ab" * 32, "ssid": "Home-2G",
         "passwd": "correct-horse-9"}
BACKUPS = {"init": "true", "deviceid": DEVICEID, "apikey": KEY, "model": "SN-ESP32C3-S60-01"}
MAC = bytes.fromhex("123456abcdef")


def esf_flash(frame=FRAME, backups=BACKUPS, *extra: nvs_image.Item) -> bytes:
    """Sonoff's settings where they are on a flash with no table (0x9000)."""
    items = [nvs_image.Item("frame_params", "content", "blob", json.dumps(frame).encode())]
    if backups is not None:
        items.append(nvs_image.Item("data_backups", "content", "blob",
                                    backups if isinstance(backups, bytes) else json.dumps(backups).encode()))
    items += [nvs_image.Item("productId", "productId", "blob", b"24440700000000\r\n"), *extra]
    return (b"\xff" * 0x9000 + nvs_image.build(items, 0x10000)).ljust(0x20000, b"\xff")


AIS_ACCOUNT = {"deviceid": "1000abcdee", "devicekey": "12345678-1234-4abc-8def-123456789abc",
               "extra": {"model": "SN-ESP32C3-BASICR4-01", "uiid": 138}}
AIS_FIELDS = {"chip_mac": "123456ABCDEF", "device_model": "SN-ESP32C3-BASICR4-01", "deviceid": "1000abcdee",
              "factory_apikey": "12345678-1234-4abc-8def-123456789abc", "sta_mac": "12:34:56:AB:CD:EF",
              "sap_mac": "12:34:56:AB:CD:F0"}


def efuse(mac: bytes = MAC) -> bytes:
    """An ESP32-C3's eFuses as the bridge reads them: the MAC at 0x18, backwards."""
    out = bytearray(0x200)
    out[0x18:0x1E] = mac[::-1]
    return bytes(out)


def ais_flash(record: bytes) -> bytes:
    """A BASICR4's whole flash, its factory record in its settings."""
    items = [nvs_image.Item("factory", "info", "blob", record),
             nvs_image.Item("pair", "info", "blob", ais_records.pair_record("Home-2G", "correct-horse-9",
                                                                            "eu-disp.coolkit.cc", 443))]
    return basicr4_flash(nvs=nvs_image.build(items, 0x10000))


def ais(record=None, account=AIS_ACCOUNT, fuses=None) -> dict[str, str]:
    found = identity_check.check(ais_flash(record or ais_records.factory_record(AIS_FIELDS, 138)), account,
                                 efuse=efuse() if fuses is None else fuses or None, dispatch=DISPATCH,
                                 encrypted=False)
    assert found["platform"] == "ais"
    return found["fields"]


def esf(flash=None, account=ACCOUNT, encrypted=False) -> dict[str, str]:
    found = identity_check.check(flash or esf_flash(), account, efuse=efuse(), dispatch=DISPATCH,
                                 encrypted=encrypted)
    assert found["platform"] == "esf"
    return found["fields"]


class EsfTests(unittest.TestCase):
    def test_a_device_the_account_rebuilds_matches_in_every_field_rebuilt(self):
        fields = esf()
        self.assertEqual(tuple(fields), identity_check.ESF_FIELDS)
        rebuilt = [name for name in fields if name.split(".")[0] in identity_check.REBUILT]
        self.assertEqual({name: fields[name] for name in rebuilt if fields[name] != YES},
                         {"frame_params.encrypt_key": MISSING, "frame_params.is_encrypt": MISSING})
        self.assertEqual(fields["rebuild_whole_matches"], YES)
        self.assertEqual((fields["productId.present"], fields["channel_params.present"], fields["PID.present"]),
                         (YES, NO, NO))

    def test_each_field_the_rebuild_fills_has_its_outcome(self):
        # A field from_account gains is a field the check must name.
        built = sonoff_identity.from_account(ACCOUNT, DISPATCH, encrypted=True)
        for item in built.items:
            for name in json.loads(item.data):
                self.assertIn(f"{item.namespace}.{name}", identity_check.ESF_FIELDS)

    def test_a_key_not_the_accounts_is_told_apart(self):
        fields = esf(esf_flash({**FRAME, "apikey": "12345678-1234-4abc-8def-123456789abc"}))
        self.assertEqual((fields["frame_params.apikey"], fields["data_backups.apikey"]), (NO, YES))
        self.assertEqual(fields["frame_params.deviceid"], YES)
        self.assertEqual(fields["rebuild_whole_matches"], NO)

    def test_a_uiid_kept_as_text_is_not_the_rebuilds(self):
        self.assertEqual(esf(esf_flash({**FRAME, "uiid": "190"}))["frame_params.uiid"], NO)

    def test_a_classic_esp32_keeps_the_key_as_its_encrypt_key(self):
        flash = esf_flash({**FRAME, "encrypt_key": KEY, "is_encrypt": True})
        fields = esf(flash, encrypted=True)
        self.assertEqual((fields["frame_params.encrypt_key"], fields["frame_params.is_encrypt"]), (YES, YES))
        self.assertEqual(fields["rebuild_whole_matches"], YES)
        # Rebuilt without it, as an S60 is: told, but the whole still matches,
        # as the firmware goes online without the fields the rebuild leaves out.
        fields = esf(flash, encrypted=False)
        self.assertEqual((fields["frame_params.encrypt_key"], fields["rebuild_whole_matches"]), (NO, YES))

    def test_a_device_without_data_backups_has_them_missing(self):
        fields = esf(esf_flash(FRAME, None))
        self.assertEqual({fields[f"data_backups.{name}"] for name in identity_check.REBUILT["data_backups"]},
                         {MISSING})
        self.assertEqual(fields["rebuild_whole_matches"], NO)

    def test_an_account_record_that_rebuilds_nothing_is_unknown(self):
        fields = esf(account={"deviceid": DEVICEID, "extra": ACCOUNT["extra"]})
        self.assertEqual({fields[name] for name in fields if not name.endswith(".present")}, {UNKNOWN})
        self.assertEqual(fields["productId.present"], YES)

    def test_entries_that_are_not_sonoffs_json_are_unknown(self):
        fields = esf(esf_flash(FRAME, b"{not Sonoff's JSON"))
        self.assertEqual(fields["data_backups.apikey"], UNKNOWN)
        self.assertEqual(fields["rebuild_whole_matches"], UNKNOWN)


class AisTests(unittest.TestCase):
    def test_a_record_the_account_and_chip_rebuild(self):
        fields = ais()
        self.assertEqual(tuple(fields), identity_check.AIS_FIELDS)
        self.assertEqual(set(fields.values()), {YES})

    def test_another_devices_id_or_key(self):
        fields = ais(account={**AIS_ACCOUNT, "deviceid": "1000abcdef", "devicekey": "x" * 36})
        self.assertEqual((fields["deviceid_matches"], fields["apikey_matches_devicekey"]), (NO, NO))
        self.assertEqual((fields["model_matches"], fields["uiid_matches"], fields["checksum_recomputes"]),
                         (YES, YES, YES))
        self.assertEqual(fields["rebuild_whole_matches"], NO)

    def test_another_model_or_kind(self):
        fields = ais(account={**AIS_ACCOUNT, "extra": {"model": "BASICR4", "uiid": 1}})
        self.assertEqual((fields["model_matches"], fields["uiid_matches"]), (NO, NO))

    def test_a_digest_that_is_not_the_fields(self):
        record = bytearray(ais_records.factory_record(AIS_FIELDS, 138))
        record[-1] ^= 1
        fields = ais(bytes(record))
        self.assertEqual((fields["checksum_recomputes"], fields["rebuild_whole_matches"]), (NO, NO))
        self.assertEqual(fields["deviceid_matches"], YES)

    def test_macs_not_the_chips(self):
        fields = ais(ais_records.factory_record({**AIS_FIELDS, "sap_mac": "12:34:56:AB:CD:EF"}, 138))
        self.assertEqual((fields["sta_mac_is_efuse_mac"], fields["sap_mac_is_efuse_mac_plus_one"]), (YES, NO))
        # In another form, the same MAC: alike field by field, not rebuilt byte for byte.
        fields = ais(ais_records.factory_record({**AIS_FIELDS, "sta_mac": "12:34:56:ab:cd:ef"}, 138))
        self.assertEqual((fields["sta_mac_is_efuse_mac"], fields["rebuild_whole_matches"]), (YES, NO))

    def test_missing_fields(self):
        record = ais_records.factory_record({**AIS_FIELDS, "deviceid": "", "factory_apikey": ""}, 138)
        fields = ais(record)
        self.assertEqual((fields["deviceid_matches"], fields["apikey_matches_devicekey"]), (MISSING, MISSING))

    def test_without_the_chips_efuses_its_macs_are_unknown(self):
        fields = ais(fuses=b"")
        self.assertEqual({fields[name] for name in ("chip_mac_is_efuse_mac", "sta_mac_is_efuse_mac",
                                                    "sap_mac_is_efuse_mac_plus_one", "rebuild_whole_matches")},
                         {UNKNOWN})
        self.assertEqual(fields["deviceid_matches"], YES)

    def test_an_account_record_without_the_device_id_is_unknown(self):
        fields = ais(account={**AIS_ACCOUNT, "deviceid": None})
        self.assertEqual((fields["deviceid_matches"], fields["rebuild_whole_matches"]), (UNKNOWN, UNKNOWN))

    def test_a_record_of_another_version_says_only_that(self):
        record = struct.pack("<HH", ais_records.MAGIC, 2) + bytes(ais_records.FACTORY_SIZE - 4)
        fields = ais(record)
        self.assertEqual(fields["record_version_known"], NO)
        self.assertEqual({fields[name] for name in fields if name != "record_version_known"}, {UNKNOWN})


class NoValueTests(unittest.TestCase):
    def test_no_value_of_the_device_or_account_is_in_the_outcomes_or_their_line(self):
        values = {DEVICEID, KEY, "SN-ESP32C3-S60-01", "190", "Home-2G", "correct-horse-9", "ab" * 32,
                  *AIS_FIELDS.values(), "138", "123456abcdef", "12:34:56:ab:cd:ef", DISPATCH[0], "443"}
        for found in (identity_check.check(esf_flash(), ACCOUNT, efuse=efuse(), dispatch=DISPATCH, encrypted=True),
                      identity_check.check(esf_flash({**FRAME, "apikey": "x"}), ACCOUNT, efuse=None,
                                           dispatch=DISPATCH, encrypted=False),
                      identity_check.check(ais_flash(ais_records.factory_record(AIS_FIELDS, 138)), AIS_ACCOUNT,
                                           efuse=efuse(), dispatch=DISPATCH, encrypted=False)):
            said = json.dumps(found) + identity_check.describe(found)
            for value in values:
                self.assertNotIn(value.lower(), said.lower(), value)
            self.assertEqual(identity_check.valid(found), found)
            self.assertTrue(set(found["fields"].values()) <= set(identity_check.OUTCOMES))

    def test_a_flash_with_neither_says_so(self):
        found = identity_check.check(b"\xff" * 0x20000, ACCOUNT, efuse=None, dispatch=DISPATCH, encrypted=False)
        self.assertEqual(found, {"platform": "neither", "fields": {}})


class ValidTests(unittest.TestCase):
    def test_only_the_checks_own_outcomes_are_taken(self):
        good = {"platform": "ais", "fields": {"deviceid_matches": YES}}
        self.assertEqual(identity_check.valid(good), good)
        for bad in (None, "yes", {"platform": "ais"}, {"platform": "other", "fields": {}},
                    {"platform": "ais", "fields": {"deviceid_matches": DEVICEID}},
                    {"platform": "ais", "fields": {"frame_params.apikey": YES}},   # an esf field
                    {"platform": "esf", "fields": {"deviceid": YES}},
                    {**good, "deviceid": DEVICEID}):
            self.assertIsNone(identity_check.valid(bad), bad)


if __name__ == "__main__":
    unittest.main()
