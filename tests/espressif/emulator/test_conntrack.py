"""Conntrack's table read over netlink, printed as /proc/net/nf_conntrack would."""

import socket
import struct
import unittest

from tools.espressif.emulator import conntrack
from tools.espressif.emulator import fault_suite as fs

NESTED = 0x8000


def attr(kind, value, nested=False):
    data = struct.pack("=HH", 4 + len(value), kind | (NESTED if nested else 0)) + value
    return data + b"\0" * (-len(data) % 4)


def entry(src, dst, sport, dport, protocol=6, state=3, timeout=300):
    ip = attr(1, socket.inet_aton(src)) + attr(2, socket.inet_aton(dst))
    ports = attr(1, bytes([protocol])) + attr(2, struct.pack(">H", sport)) + attr(3, struct.pack(">H", dport))
    tuple_orig = attr(1, ip, nested=True) + attr(2, ports, nested=True)
    payload = struct.pack("=BBH", socket.AF_INET, 0, 0) + attr(1, tuple_orig, nested=True)
    if protocol == 6:
        payload += attr(4, attr(1, attr(1, bytes([state])), nested=True), nested=True)
    payload += attr(7, struct.pack(">I", timeout))
    return message(conntrack.CT_NEW, payload)


def message(kind, payload):
    return struct.pack("=IHHII", 16 + len(payload), kind, 2, 1, 0) + payload


class DumpTests(unittest.TestCase):
    def test_entries_read_as_the_proc_file_lists_them(self):
        found, done = conntrack.lines(entry("192.168.60.50", "192.0.2.80", 50280, 443)
                                      + entry("192.168.60.50", "192.0.2.81", 5, 53, protocol=17)
                                      + message(conntrack.NLMSG_DONE, b"\0" * 4))
        self.assertTrue(done)
        self.assertEqual(found, [
            "ipv4     2 tcp      6 300 ESTABLISHED src=192.168.60.50 dst=192.0.2.80 sport=50280 dport=443",
            "ipv4     2 udp      17 300 src=192.168.60.50 dst=192.0.2.81 sport=5 dport=53"])
        # What the suite reads of them.
        self.assertEqual(fs.established("\n".join(found), ["192.168.60.50"]), {("192.0.2.80", "50280", "443")})

    def test_a_dump_goes_on_until_done(self):
        self.assertEqual(conntrack.lines(entry("192.168.60.50", "192.0.2.80", 1, 443))[1], False)

    def test_a_refusal_is_an_error_not_an_empty_table(self):
        with self.assertRaises(OSError):
            conntrack.lines(message(conntrack.NLMSG_ERROR, struct.pack("=i", -1) + b"\0" * 16))


if __name__ == "__main__":
    unittest.main()
