import io
import unittest
import urllib.parse
from contextlib import redirect_stdout
from unittest import mock

from tools.repo import cloudflare_settings as cs
from tools.repo import readouts

OBJECTS = f"/accounts/a1/r2/buckets/{cs.READOUTS_BUCKET}/objects"


class FakeBucket:
    """The R2 API's object listing, two to a page, as Cloudflare pages it with a cursor."""

    def __init__(self, keys):
        self.keys = keys
        self.asked = []

    def __call__(self, path, method="GET", body=None, ok_missing=False, whole=False):
        if path == f"/zones?name={cs.ZONE}":
            return [{"id": "z1", "account": {"id": "a1"}}]
        where, _, query = path.partition("?")
        assert (where, method, whole) == (OBJECTS, "GET", True), path
        query = dict(urllib.parse.parse_qsl(query))
        self.asked.append(query)
        chosen = [key for key in self.keys if key.startswith(query["prefix"])]
        start = int(query.get("cursor", 0))
        page = chosen[start:start + 2]
        more = start + 2 < len(chosen)
        return {"result": [{"key": key, "size": 10, "last_modified": "2026-11-07T10:00:00Z",
                            "custom_metadata": {"product": "S60TPF", "read": "true"}} for key in page],
                "result_info": {"is_truncated": more, "cursor": str(start + 2) if more else ""}}


def run(fake, *args):
    out = io.StringIO()
    with mock.patch.object(cs, "api", fake), redirect_stdout(out):
        code = readouts.main(list(args))
    return code, out.getvalue()


class ReadoutsTests(unittest.TestCase):
    def test_every_page_is_listed(self):
        keys = [f"readouts/2026-11-07/{n:024x}.tar" for n in range(5)] + ["kept/one.tar"]
        fake = FakeBucket(keys)
        code, out = run(fake)
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in out.splitlines()[:-1]], keys[:5])
        self.assertIn("product=S60TPF", out)
        self.assertEqual(out.splitlines()[-1], f"5 in {cs.READOUTS_BUCKET}")
        self.assertEqual([query.get("cursor") for query in fake.asked], [None, "2", "4"])

    def test_a_prefix_narrows_it(self):
        self.assertIn("1 in", run(FakeBucket(["readouts/a.tar", "kept/b.tar"]), "kept/")[1])

    def test_an_api_error_is_said_plainly(self):
        def refused(*args, **kwargs):
            raise cs.SettingsError("GET /zones: HTTP 403: Authentication error")
        with mock.patch.object(cs, "api", refused), redirect_stdout(io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(readouts.main([]), 1)
        self.assertIn("readouts: GET /zones: HTTP 403", err.getvalue())
