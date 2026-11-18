"""The firmware eWeLink offers a device, fetched whole as the device asks for
it, for its readout: never more than an update file, never twice, never
stopping the readout; or its links alone, for the website to fetch."""
import hashlib
import unittest
from unittest import mock

from tools.ewelink import offered_firmware

OFFERS = [{"version": "1.1.0", "binList": [
    {"name": "user1.bin", "downloadUrl": "https://example.com/fw/user1.bin?x=1"},
    {"name": "user2.bin", "downloadUrl": "https://example.com/fw/user2.bin"},
    {"name": "local.bin", "downloadUrl": "file:///etc/passwd"},
]}]


class FetchTests(unittest.TestCase):
    def test_each_file_is_asked_for_as_the_device_asks(self):
        asked = []

        def get(url, deviceid):
            asked.append(offered_firmware.as_device(url, deviceid))
            return url.encode()

        found, problems = offered_firmware.fetch(OFFERS, "1000abcdef", get)
        self.assertEqual([(item.name, item.version) for item in found], [("user1.bin", "1.1.0"), ("user2.bin", "1.1.0")])
        self.assertEqual(problems, [])
        self.assertEqual(asked[0], "https://example.com/fw/user1.bin?x=1&deviceid=1000abcdef&ts=1&sign=0")

    def test_a_file_not_matching_its_digest_is_left_out(self):
        offers = [{"version": "1.1.0", "binList": [
            {"name": "user1.bin", "downloadUrl": "https://example.com/1", "digest": hashlib.sha256(b"one").hexdigest()},
            {"name": "user2.bin", "downloadUrl": "https://example.com/2", "digest": hashlib.sha256(b"two").hexdigest()},
        ]}]
        found, problems = offered_firmware.fetch(offers, "1000abcdef", lambda url, deviceid: b"one",
                                                 wait=lambda seconds: None)
        self.assertEqual([item.name for item in found], ["user1.bin"])
        self.assertEqual(problems, ["user2.bin: not the file its digest names, 3 tries"])

    def test_a_file_that_does_not_come_whole_is_tried_again_after_a_pause(self):
        tries, pauses = iter([OSError("reset"), b"cut", b"whole"]), []

        def get(url, deviceid):
            answer = next(tries)
            if isinstance(answer, Exception):
                raise answer
            return answer
        offers = [{"version": "1.1.0", "binList": [{"name": "user1.bin", "downloadUrl": "https://example.com/1",
                                                    "digest": hashlib.sha256(b"whole").hexdigest()}]}]
        found, problems = offered_firmware.fetch(offers, "1000abcdef", get, wait=pauses.append)
        self.assertEqual([item.data for item in found], [b"whole"])
        self.assertEqual(problems, [])
        self.assertEqual(pauses, [offered_firmware.RETRY_PAUSE] * 2)

    def test_the_same_bytes_twice_are_kept_once(self):
        found, _ = offered_firmware.fetch(OFFERS, "1000abcdef", lambda url, deviceid: b"same")
        self.assertEqual(len(found), 1)

    def test_a_failure_is_noted_by_its_type_alone(self):
        def get(url, deviceid):
            raise OSError(f"could not fetch {url}")

        found, problems = offered_firmware.fetch(OFFERS, "1000abcdef", get, wait=lambda seconds: None)
        self.assertEqual(found, [])
        self.assertEqual(problems, ["user1.bin: OSError, 3 tries", "user2.bin: OSError, 3 tries"])
        self.assertNotIn("example.com", " ".join(problems))

    def test_a_file_held_already_is_not_fetched_by_the_digest_ewelink_gives(self):
        offers = [{"version": "1.1.0", "binList": [{**OFFERS[0]["binList"][0], "digest": "A" * 64},
                                                   OFFERS[0]["binList"][1]]}]
        asked = []
        found, _ = offered_firmware.fetch(offers, "1000abcdef", lambda url, deviceid: asked.append(url) or url.encode(),
                                          skip=frozenset({"a" * 64}))
        self.assertEqual([item.name for item in found], ["user2.bin"])
        self.assertEqual(len(asked), 1)

    def test_nothing_is_asked_without_the_devices_id(self):
        self.assertEqual(offered_firmware.fetch(OFFERS, "", lambda url, deviceid: b"x"),
                         ([], ["no device id to ask with"]))


TOKEN = "Ab3" * 10 + "Z9"   # 32 letters and digits, as eWeLink's
SHA = hashlib.sha256(b"one").hexdigest()


class LinksTests(unittest.TestCase):
    def offer(self, url, digest=SHA, name="user1.bin"):
        return [{"version": "1.2.0", "binList": [{"name": name, "downloadUrl": url, "digest": digest}]}]

    def test_each_file_s_path_name_version_and_sha256_without_its_host_or_query(self):
        url = f"https://eu-otadl.coolkit.cc:8088/ota/rom/{TOKEN}/user1.1024.new.2.bin?deviceid=1&sign=x"
        self.assertEqual(offered_firmware.links(self.offer(url, SHA.upper())), [
            {"name": "user1.bin", "version": "1.2.0", "sha256": SHA,
             "path": f"/ota/rom/{TOKEN}/user1.1024.new.2.bin"}])

    def test_nothing_is_downloaded(self):
        with mock.patch.object(offered_firmware, "download") as downloaded, \
                mock.patch.object(offered_firmware, "urlopen") as opened:
            offered_firmware.links(self.offer(f"https://example.com/ota/rom/{TOKEN}/user1.bin"))
        downloaded.assert_not_called()
        opened.assert_not_called()

    def test_only_a_path_of_the_expected_shape_is_passed_on(self):
        for path in ("/ota/rom/short/user1.bin", f"/ota/rom/{'a' * 65}/user1.bin", f"/ota/rom/{'a' * 31}/user1.bin", f"/ota/rom/{TOKEN}/../x.bin",
                     f"/ota/rom/{TOKEN}/a b.bin", f"/ota/rom/{TOKEN}/" + "a" * 65, f"/other/{TOKEN}/user1.bin",
                     f"/ota/rom/{TOKEN}/sub/user1.bin"):
            with self.subTest(path=path):
                self.assertEqual(offered_firmware.links(self.offer("https://example.com" + path)), [])
                self.assertEqual(offered_firmware.unlinked(self.offer("https://example.com" + path)), 1)

    def test_an_nspanel_s_token_of_64_hex_is_passed_on(self):
        # eWeLink's server serves one such link today (docs/espressif-research.md).
        path = f"/ota/rom/{'0123456789abcdef' * 4}/user1.1024.new.2.bin"
        self.assertEqual([link["path"] for link in offered_firmware.links(self.offer("https://example.com" + path))],
                         [path])

    def test_those_left_out_are_counted(self):
        good, other = f"https://example.com/ota/rom/{TOKEN}/user1.bin", "https://example.com/rom/user2.bin"
        self.assertEqual(offered_firmware.unlinked(self.offer(good)), 0)
        self.assertEqual(offered_firmware.unlinked(self.offer(good, None)), 1)
        self.assertEqual(offered_firmware.unlinked(self.offer(good) + self.offer(other) + self.offer(other)), 2)

    def test_only_a_file_with_a_sha256_beside_it(self):
        url = f"https://example.com/ota/rom/{TOKEN}/user1.bin"
        for digest in ("", "a" * 63, "g" * 64, None):
            with self.subTest(digest=digest):
                self.assertEqual(offered_firmware.links(self.offer(url, digest)), [])

    def test_the_same_file_twice_once_and_no_more_than_an_update_has(self):
        offers = [{"version": "1.2.0", "binList": [
            {"name": f"user{n}.bin", "downloadUrl": f"https://example.com/ota/rom/{TOKEN}/user{n}.bin",
             "digest": f"{n:x}" * 64} for n in (1, 1, 2, 3, 4, 5, 6)]}]
        found = offered_firmware.links(offers)
        self.assertEqual([item["name"] for item in found], ["user1.bin", "user2.bin", "user3.bin", "user4.bin"])
        self.assertEqual(len(found), offered_firmware.MAX_FILES)


if __name__ == "__main__":
    unittest.main()
