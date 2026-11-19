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


OWN = ("1000abcdef", "12:34:56:AB:CD:EF")   # the device's id and MAC


class ReviewUrlTests(unittest.TestCase):
    """A link of a shape not seen before, sent whole for review only if every
    part of it is plain and it names nothing of the device."""

    def url(self, link, own=OWN):
        return offered_firmware.review_url(link, own)

    def test_an_ewelink_name_or_a_public_address_is_sent_as_it_is(self):
        for link in ("http://eu-otadl.coolkit.cc:8088/ota/rom/user1.bin", "https://cn-otadl.coolkit.cn/fw/a_b-1.2.bin",
                     "http://8.8.8.8/fw/user1.bin", "https://1.1.1.1:443/fw/user1.bin", "http://x.coolkit.cc:80/a"):
            with self.subTest(link=link):
                self.assertEqual(self.url(link), link)

    def test_scheme_and_host_are_lower_cased(self):
        self.assertEqual(self.url("HTTPS://EU-OTADL.COOLKIT.CC/Fw/User1.bin"), "https://eu-otadl.coolkit.cc/Fw/User1.bin")

    def test_its_query_and_fragment_are_dropped(self):
        for link in ("http://eu-otadl.coolkit.cc/fw/user1.bin?deviceid=1&ts=2&sign=x",
                     "http://eu-otadl.coolkit.cc/fw/user1.bin#part", "http://eu-otadl.coolkit.cc/fw/user1.bin?a=<b>#'c'",
                     "http://eu-otadl.coolkit.cc/fw/user1.bin?"):
            with self.subTest(link=link):
                self.assertEqual(self.url(link), "http://eu-otadl.coolkit.cc/fw/user1.bin")

    def test_only_http_or_https(self):
        for scheme in ("ftp", "file", "javascript", "httpx", "data", ""):
            with self.subTest(scheme=scheme):
                self.assertIsNone(self.url(f"{scheme}://eu-otadl.coolkit.cc/fw/user1.bin"))
        self.assertIsNone(self.url("eu-otadl.coolkit.cc/fw/user1.bin"))
        self.assertIsNone(self.url("//eu-otadl.coolkit.cc/fw/user1.bin"))

    def test_a_name_must_be_one_of_ewelink_s(self):
        for host in ("example.com", "coolkit.cc", "coolkit.cn", "eu-otadl.coolkit.cc.example.com",
                     "eu-otadl.coolkit.com", "eu-otadlcoolkit.cc", "eu..coolkit.cc", ".coolkit.cc", "eu_otadl.coolkit.cc",
                     "localhost", ""):
            with self.subTest(host=host):
                self.assertIsNone(self.url(f"http://{host}/fw/user1.bin"))

    def test_an_address_on_no_public_network_is_refused(self):
        for address in ("10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.99.10",   # private
                        "127.0.0.1", "127.255.255.254",                              # loopback
                        "169.254.1.1",                                               # link-local
                        "100.64.0.1", "100.127.255.255",                             # CGNAT
                        "224.0.0.1", "239.255.255.250",                              # multicast
                        "240.0.0.1", "250.1.2.3",                                    # reserved
                        "0.0.0.0", "0.1.2.3",                                        # 0/8
                        "255.255.255.255",                                           # broadcast
                        "192.0.2.50", "198.51.100.7", "203.0.113.9",                 # documentation
                        "198.18.0.1", "192.0.0.8"):                                  # benchmarking, IETF
            with self.subTest(address=address):
                self.assertIsNone(self.url(f"http://{address}/fw/user1.bin"))

    def test_an_address_in_another_form_is_refused(self):
        for host in ("2130706433", "0x7f.0.0.1", "010.1.2.3", "8.8.8", "8.8.8.8.8", "08.8.8.8", "8.8.8.256"):
            with self.subTest(host=host):
                self.assertIsNone(self.url(f"http://{host}/fw/user1.bin"))

    def test_no_user_or_ipv6(self):
        for netloc in ("user@eu-otadl.coolkit.cc", "user:pw@eu-otadl.coolkit.cc", "@eu-otadl.coolkit.cc",
                       "eu-otadl.coolkit.cc@evil.example", "[::1]", "[2001:4860:4860::8888]", "[::ffff:8.8.8.8]:80"):
            with self.subTest(netloc=netloc):
                self.assertIsNone(self.url(f"http://{netloc}/fw/user1.bin"))

    def test_only_its_ports(self):
        for port in ("80", "443", "8088"):
            with self.subTest(port=port):
                self.assertEqual(self.url(f"http://eu-otadl.coolkit.cc:{port}/a"), f"http://eu-otadl.coolkit.cc:{port}/a")
        for port in ("8080", "22", "0", "080", "65536", "", "8088x", "-1"):
            with self.subTest(port=port):
                self.assertIsNone(self.url(f"http://eu-otadl.coolkit.cc:{port}/a"))

    def test_its_path_is_plain(self):
        self.assertEqual(self.url("http://8.8.8.8/" + "a" * 199), "http://8.8.8.8/" + "a" * 199)
        for path in ("", "/" + "a" * 200, "/fw/../user1.bin", "/fw/..", "/fw//user1.bin", "//fw",
                     "/fw/user%2e.bin", "/fw/%2e%2e/x", "/fw/user1.bin~", "/fw/ü.bin"):
            with self.subTest(path=path):
                self.assertIsNone(self.url("http://eu-otadl.coolkit.cc" + path))

    def test_nothing_that_could_be_read_as_more_than_a_link(self):
        for odd in ("'", '"', "<", ">", " ", "\n", "\r", "\t", "`", "$(id)", ";", "|", "%", "&", "\\", "\x00",
                    "{", "}", "*", "!", ":", "@", "=", "+", ","):
            for where in ("http://eu-otadl.coolkit.cc/fw/a{}b.bin", "http://eu-otadl.coolkit{}.cc/fw/a.bin",
                          "http://eu-otadl.coolkit.cc:80{}/fw/a.bin", "http{}://eu-otadl.coolkit.cc/fw/a.bin",
                          "{}http://eu-otadl.coolkit.cc/fw/a.bin"):
                link = where.format(odd)
                with self.subTest(link=link):
                    self.assertIsNone(self.url(link))

    def test_a_link_naming_the_device_is_refused_in_any_form(self):
        for named in ("1000abcdef", "1000ABCDEF", "x1000abcdefx", "12:34:56:ab:cd:ef", "12-34-56-AB-CD-EF",
                      "123456abcdef", "123456ABCDEF", "1234.56ab.cdef", "12_34_56_ab_cd_ef"):
            for where in ("http://eu-otadl.coolkit.cc/fw/{}/user1.bin", "http://eu-otadl.coolkit.cc/fw/user1.bin?d={}",
                          "http://eu-otadl.coolkit.cc/fw/user1.bin#{}"):
                link = where.format(named)
                with self.subTest(link=link):
                    self.assertIsNone(self.url(link))
        self.assertIsNone(self.url("http://1000abcdef.coolkit.cc/fw/user1.bin"))
        self.assertEqual(self.url("http://eu-otadl.coolkit.cc/fw/1000fedcba.bin"),
                         "http://eu-otadl.coolkit.cc/fw/1000fedcba.bin")   # another device's id: not its own
        self.assertEqual(self.url("http://eu-otadl.coolkit.cc/fw/x.bin", ("", "::")),
                         "http://eu-otadl.coolkit.cc/fw/x.bin")   # an identity not known matches nothing


class ForReviewTests(unittest.TestCase):
    def item(self, url, digest=SHA):
        return {"name": "user2.bin", "downloadUrl": url, "digest": digest}

    def test_a_link_of_another_shape_with_a_sha256_beside_it(self):
        self.assertEqual(offered_firmware.for_review(self.item("http://eu-otadl.coolkit.cc/rom/user2.bin?x=1"), OWN),
                         "http://eu-otadl.coolkit.cc/rom/user2.bin")
        for digest in ("", None, "a" * 63):
            with self.subTest(digest=digest):
                self.assertIsNone(offered_firmware.for_review(
                    self.item("http://eu-otadl.coolkit.cc/rom/user2.bin", digest), OWN))

    def test_none_for_a_link_of_the_known_shape(self):
        self.assertIsNone(offered_firmware.for_review(
            self.item(f"http://eu-otadl.coolkit.cc/ota/rom/{TOKEN}/user1.bin"), OWN))

    def test_links_given_the_device_names_it_by_url_and_unlinked_counts_only_those_unfit(self):
        offers = [{"version": "1.2.0", "binList": [
            {"name": "user1.bin", "downloadUrl": f"https://eu-otadl.coolkit.cc/ota/rom/{TOKEN}/user1.bin",
             "digest": "1" * 64},
            self.item("http://eu-otadl.coolkit.cc:8088/rom/user2.bin?deviceid=x", "2" * 64),
            self.item("http://10.0.0.1/rom/user3.bin", "3" * 64)]}]
        self.assertEqual(offered_firmware.links(offers, OWN), [
            {"name": "user1.bin", "version": "1.2.0", "sha256": "1" * 64, "path": f"/ota/rom/{TOKEN}/user1.bin"},
            {"name": "user2.bin", "version": "1.2.0", "sha256": "2" * 64,
             "url": "http://eu-otadl.coolkit.cc:8088/rom/user2.bin"}])
        self.assertEqual([item["sha256"] for item in offered_firmware.links(offers)], ["1" * 64])   # no `own`: paths alone
        self.assertEqual(offered_firmware.unlinked(offers, OWN), 1)
        self.assertEqual(offered_firmware.unlinked(offers), 2)


if __name__ == "__main__":
    unittest.main()
