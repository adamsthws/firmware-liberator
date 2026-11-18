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


def ids(n: int) -> list[dict[str, str]]:
    return [{"deviceid": f"1000{k:06x}", "model": "M", "version": "1.2.0"} for k in range(n)]


class LatestOffersTests(unittest.TestCase):
    """Many devices' latest offers, QUERY_MOST a query, matched back by deviceid."""

    def ask(self, identities, answer):
        asked = []

        def api_json(url, body, headers):
            listed = json.loads(body)["deviceInfoList"]
            asked.append(listed)
            return answer(listed)
        with mock.patch.object(query_ota, "api_json", side_effect=api_json):
            return query_ota.latest_offers("eu", "token", identities), asked

    @staticmethod
    def each_offered(listed):
        return {"error": 0, "data": {"otaInfoList": [{"deviceid": item["deviceid"], "version": "9"}
                                                     for item in listed]}}

    def test_at_most_eWeLink_s_limit_a_query_each_asked_as_behind_all(self):
        found, asked = self.ask(ids(31), self.each_offered)
        self.assertEqual([len(listed) for listed in asked], [query_ota.QUERY_MOST, 1])
        self.assertEqual(query_ota.QUERY_MOST, 30)
        self.assertEqual({item["version"] for listed in asked for item in listed}, {query_ota.VERSION_BEHIND_ALL})
        self.assertEqual(len(found), 31)

    def test_each_offer_goes_to_the_device_it_names(self):
        def answer(listed):   # in another order, and one entry for no device asked
            return {"error": 0, "data": {"otaInfoList": [
                {"deviceid": listed[1]["deviceid"], "version": "b"}, {"deviceid": "1000ffffff", "version": "x"},
                {"deviceid": listed[0]["deviceid"], "version": "a"}]}}
        found, _ = self.ask(ids(3), answer)
        self.assertEqual(found, {"1000000000": {"offers": [{"deviceid": "1000000000", "version": "a"}]},
                                 "1000000001": {"offers": [{"deviceid": "1000000001", "version": "b"}]},
                                 "1000000002": {"offers": []}})   # left out of the answer: offered nothing

    def test_a_query_failing_is_noted_for_its_devices_alone(self):
        def answer(listed):
            return {"error": 500, "msg": "busy"} if len(listed) == query_ota.QUERY_MOST else self.each_offered(listed)
        found, _ = self.ask(ids(31), answer)
        self.assertEqual(found["1000000000"], {"problem": "EwelinkError"})
        self.assertEqual(found["100000001e"], {"offers": [{"deviceid": "100000001e", "version": "9"}]})

    def test_the_sign_in_ended_is_raised(self):
        with self.assertRaises(query_ota.SessionEnded):
            self.ask(ids(2), lambda listed: {"error": 401, "msg": "token"})


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

    def saved(self, *flags, offered=(), env=None, refused=()):
        """What main() saves, and the versions it asked with; self.signed_in
        and self.tokens, what it signed in with and asked with. `refused`:
        tokens eWeLink has ended."""
        device = {"deviceid": "10000e32ab", "devicekey": "k", "extra": {"model": "SN-ESP32C3-S60-01"},
                  "params": {"fwVersion": "1.2.0"}}
        asked = []

        self.tokens = []

        def devices(_region, token):
            self.tokens.append(token)
            if token in refused:
                raise query_ota.SessionEnded("eWeLink ended the sign-in")
            return [device]

        def answer(_region, _token, identity):
            asked.append(identity["version"])
            found = [{"version": "1.2.0"}] if identity["version"] == "0.0.1" else list(offered)
            return {"error": 0, "data": {"otaInfoList": found}}

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "meta.json"
            arguments = ["query_ota", "--email", "a@b.c", "--device-id", "10000e32ab", "--output", str(output), *flags]
            with mock.patch.object(sys, "argv", arguments), \
                    mock.patch.dict(os.environ, {"EWELINK_PASSWORD": "p", **(env or {})}), \
                    mock.patch.object(query_ota, "login_session", return_value=("eu", {"at": "token"})) as signed, \
                    mock.patch.object(query_ota, "get_devices", devices), \
                    mock.patch.object(query_ota, "query_ota", side_effect=answer), \
                    mock.patch("builtins.print"):
                self.assertEqual(query_ota.main(), 0)
            self.signed_in = signed.call_count
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

    def test_a_sign_in_the_run_hands_over_is_used_and_none_made(self):
        # One of its own would end the run's (get_device_key.Account).
        session = {"EWELINK_SESSION": '{"region": "us", "at": "run-token", "user": {"apikey": "a"}}'}
        self.saved(env=session)
        self.assertEqual((self.signed_in, self.tokens), (0, ["run-token"]))

    def test_one_eWeLink_has_ended_is_replaced_by_its_own(self):
        session = {"EWELINK_SESSION": '{"region": "eu", "at": "run-token", "user": {"apikey": "a"}}'}
        self.saved(env=session, refused=("run-token",))
        self.assertEqual((self.signed_in, self.tokens), (1, ["run-token", "token"]))

    def test_without_one_it_signs_in_itself(self):
        self.saved(env={"EWELINK_SESSION": "not json"})
        self.assertEqual((self.signed_in, self.tokens), (1, ["token"]))

    def test_other_callers_ask_once_as_before(self):
        saved, asked = self.saved()
        self.assertEqual(asked, ["1.2.0"])
        self.assertNotIn("latest_offer", saved)


if __name__ == "__main__":
    unittest.main()
