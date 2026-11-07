import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.ewelink import query_ota
from tools.ewelink.get_device_key import EwelinkError
from tools.ewelink.query_ota import ota_endpoints, ota_identity


class QueryOtaTests(unittest.TestCase):
    def test_uses_module_model_and_reported_firmware(self):
        device = {
            "deviceid": "abc",
            "productModel": "S60TPG",
            "extra": {"model": "SN-ESP32C3-S60-01"},
            "params": {"fwVersion": "1.1.1"},
        }
        self.assertEqual(
            ota_identity(device),
            {"deviceid": "abc", "model": "SN-ESP32C3-S60-01", "version": "1.1.1"},
        )

    def test_requires_version(self):
        with self.assertRaises(EwelinkError):
            ota_identity({"deviceid": "abc", "productModel": "S60TPG"})

    def test_extracts_safe_unique_router_endpoints(self):
        ota_list = [
            {
                "binList": [
                    {"downloadUrl": "http://192.0.2.10:8088/private/one?token=secret"},
                    {"downloadUrl": "http://192.0.2.10:8088/private/two?token=secret"},
                    {"downloadUrl": "https://updates.example/private/three"},
                ]
            }
        ]

        self.assertEqual(
            ota_endpoints(ota_list),
            [("192.0.2.10", 8088), ("updates.example", 443)],
        )


class LatestOfferTests(unittest.TestCase):
    IDENTITY = {"deviceid": "10000e32ab", "model": "SN-ESP32C3-S60-01", "version": "1.2.0"}

    def test_asks_as_a_device_behind_all_with_its_own_id(self):
        offer = [{"version": "1.2.0", "binList": []}]
        with mock.patch.object(query_ota, "query_ota", return_value={"error": 0, "data": {"otaInfoList": offer}}) as asked:
            self.assertEqual(query_ota.latest_offer("eu", "token", self.IDENTITY), {"offers": offer})
        asked.assert_called_once_with("eu", "token", dict(self.IDENTITY, version="0.0.1"))
        self.assertEqual(self.IDENTITY["version"], "1.2.0")   # what the run reports stays the device's

    def test_a_failure_is_only_noted(self):
        for problem, name in ((EwelinkError("OTA metadata query failed"), "EwelinkError"), (TimeoutError(), "TimeoutError")):
            with mock.patch.object(query_ota, "query_ota", side_effect=problem):
                self.assertEqual(query_ota.latest_offer("eu", "token", self.IDENTITY), {"problem": name})
        with mock.patch.object(query_ota, "query_ota", return_value={"error": 0}):
            self.assertEqual(query_ota.latest_offer("eu", "token", self.IDENTITY), {"problem": "KeyError"})

    def saved(self, *flags, offered=()):
        """What main() saves, and the versions it asked with."""
        device = {"deviceid": "10000e32ab", "devicekey": "k", "extra": {"model": "SN-ESP32C3-S60-01"},
                  "params": {"fwVersion": "1.2.0"}}
        asked = []

        def answer(_region, _token, identity):
            asked.append(identity["version"])
            found = [{"version": "1.2.0"}] if identity["version"] == "0.0.1" else list(offered)
            return {"error": 0, "data": {"otaInfoList": found}}

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "meta.json"
            arguments = ["query_ota", "--email", "a@b.c", "--device-id", "10000e32ab", "--output", str(output), *flags]
            with mock.patch.object(sys, "argv", arguments), \
                    mock.patch.dict(os.environ, {"EWELINK_PASSWORD": "p"}), \
                    mock.patch.object(query_ota, "login", return_value=("eu", "token")), \
                    mock.patch.object(query_ota, "get_devices", return_value=[device]), \
                    mock.patch.object(query_ota, "query_ota", side_effect=answer), \
                    mock.patch("builtins.print"):
                self.assertEqual(query_ota.main(), 0)
            return json.loads(output.read_text(encoding="utf-8")), asked

    def test_a_run_asks_for_it_beside_the_query_it_reports(self):
        saved, asked = self.saved("--latest-offer")
        self.assertEqual(asked, ["1.2.0", "0.0.1"])
        self.assertEqual(saved["response"]["data"]["otaInfoList"], [])   # up to date, as it is
        self.assertEqual(saved["query"]["version"], "1.2.0")
        self.assertEqual(saved["latest_offer"], {"offers": [{"version": "1.2.0"}]})

    def test_a_real_offer_needs_no_second_call(self):
        saved, asked = self.saved("--latest-offer", offered=[{"version": "1.3.0"}])
        self.assertEqual(asked, ["1.2.0"])
        self.assertEqual(saved["latest_offer"], {"offers": [{"version": "1.3.0"}]})

    def test_other_callers_ask_once_as_before(self):
        saved, asked = self.saved()
        self.assertEqual(asked, ["1.2.0"])
        self.assertNotIn("latest_offer", saved)


if __name__ == "__main__":
    unittest.main()
