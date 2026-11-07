"""tools/espressif/safeboot/prepare_safeboot_migration.py: the official files
fetched for a bundle, tried again through a moment's trouble."""
import http.client
import ssl
import unittest
import urllib.error
from unittest import mock

from tools.espressif.safeboot import prepare_safeboot_migration as migration


class DownloadTests(unittest.TestCase):
    """Seen in the fault suite: one TLS handshake timing out stopped a run."""

    def fetch(self, *answers):
        replies = iter(answers)

        def urlopen(*_, **__):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        with mock.patch.object(migration, "urlopen", side_effect=urlopen) as opened, \
                mock.patch.object(migration.time, "sleep"):
            try:
                return migration.download("https://example.invalid/file"), opened.call_count
            except OSError as exc:
                return exc, opened.call_count

    @staticmethod
    def http_error(code):
        return urllib.error.HTTPError("https://example.invalid/file", code, "no", None, None)

    def test_a_moment_s_trouble_is_tried_again(self):
        good = mock.MagicMock()
        good.__enter__.return_value.read.return_value = b"file"
        for trouble in (TimeoutError("The handshake operation timed out"), ConnectionResetError(),
                        urllib.error.URLError("no route"), self.http_error(503),
                        http.client.IncompleteRead(b"fi", 2)):
            self.assertEqual(self.fetch(trouble, good), (b"file", 2))

    def test_it_gives_up_after_its_tries_with_the_error_its_callers_expect(self):
        always = [TimeoutError("timed out")] * migration.DOWNLOAD_TRIES
        exc, tries = self.fetch(*always)
        self.assertIsInstance(exc, TimeoutError)
        self.assertEqual(tries, migration.DOWNLOAD_TRIES)
        exc, tries = self.fetch(*[http.client.IncompleteRead(b"fi", 2)] * migration.DOWNLOAD_TRIES)
        self.assertIsInstance(exc, OSError)
        self.assertIn("cut off", str(exc))
        self.assertEqual(tries, migration.DOWNLOAD_TRIES)

    def test_what_would_only_fail_again_is_not_tried_again(self):
        inspected = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        for lasting in (self.http_error(404), inspected):
            exc, tries = self.fetch(lasting)
            self.assertIs(exc, lasting)
            self.assertEqual(tries, 1)


if __name__ == "__main__":
    unittest.main()
