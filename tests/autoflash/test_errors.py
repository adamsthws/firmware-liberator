"""What stops a run, and how a network failure is said (tools/autoflash/errors.py)."""
import errno
import socket
import ssl
import unittest
import urllib.error

from tools.autoflash import errors


class PlainlyTests(unittest.TestCase):
    """A network failure said so someone can act on it, not as urllib gives it."""

    def test_each_kind_is_said_plainly_with_what_to_check(self):
        no_route = urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))
        for exc, said in (
                (no_route, "this computer could not reach eWeLink. Check its internet connection"),
                (urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable")),
                 "this computer could not reach eWeLink. Check its internet connection"),
                (urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
                 "this computer could not reach eWeLink. Check its internet connection"),
                (urllib.error.URLError(TimeoutError("timed out")), "eWeLink did not answer in time. Try again later"),
                (TimeoutError("The read operation timed out"), "eWeLink did not answer in time. Try again later"),
                (urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
                 "eWeLink did not take the connection. Try again later"),
                (ConnectionResetError(104, "Connection reset by peer"), "eWeLink cut the connection off. Try again later"),
                (urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
                 "no secure connection to eWeLink could be made: something on this network may be in the way")):
            with self.subTest(exc=exc):
                self.assertEqual(errors.plainly(exc, "eWeLink"), said)

    def test_the_device_is_checked_for_on_this_network_not_the_internet(self):
        no_route = urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))
        self.assertEqual(errors.plainly(no_route, "the device", errors.CHECK_DEVICE),
                         "this computer could not reach the device. Check it is switched on, and on the same "
                         "network as this computer")

    def test_anything_else_is_said_as_it_is(self):
        # A server's own answer, say: it is already words.
        for exc in (urllib.error.HTTPError("https://example.invalid/", 503, "Busy", None, None),
                    OSError("the website answered 503"), ValueError("no reference")):
            with self.subTest(exc=exc):
                self.assertEqual(errors.plainly(exc, "the website"), str(exc))

    def test_what_is_worth_another_go_still_reads_as_one(self):
        # A step's failure is tried again by its words (TRANSIENT_RE): a
        # network trouble said plainly must still read as one.
        for exc in (urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host")),
                    TimeoutError("timed out"), ConnectionRefusedError(111, "Connection refused"),
                    ConnectionResetError(104, "Connection reset by peer")):
            with self.subTest(exc=exc):
                self.assertRegex(errors.plainly(exc, "the device", errors.CHECK_DEVICE), errors.TRANSIENT_RE)


if __name__ == "__main__":
    unittest.main()
