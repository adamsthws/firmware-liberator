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
        keys = [f"readouts/2026-11-07/{n:024x}.tar" for n in range(5)] + ["notify/NSPANEL/" + "b" * 64 + ".json"]
        fake = FakeBucket(keys)
        code, out = run(fake)
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in out.splitlines()[:-1]], keys[:5])
        self.assertIn("product=S60TPF", out)
        self.assertEqual(out.splitlines()[-1], f"5 in {cs.READOUTS_BUCKET}")
        self.assertEqual([query.get("cursor") for query in fake.asked], [None, "2", "4"])

    def test_the_marks_beside_the_readouts_are_left_out_unless_asked_for(self):
        keys = ["readouts/2026-11-07/aa.tar", "readouts/held/NSPANEL/E32-MSW-NX/1.5.0/first",
                "readouts/firmware/" + "a" * 64]
        self.assertEqual(run(FakeBucket(keys))[1].splitlines()[-1], f"1 in {cs.READOUTS_BUCKET}")
        self.assertIn("2 in", run(FakeBucket(keys[1:]), "readouts/")[1])
        self.assertIn("1 in", run(FakeBucket(keys), "readouts/held/")[1])

    def test_who_asked_to_be_told_of_a_product(self):
        class Subscribers(FakeBucket):
            def __call__(self, path, **kwargs):
                answer = super().__call__(path, **kwargs)
                for item in answer.get("result", []) if isinstance(answer, dict) else []:
                    item["custom_metadata"] = {"product": "NSPANEL", "email": "me@example.com",
                                               "models": "E32-MSW-NX"}
                return answer
        fake = Subscribers(["notify/NSPANEL/" + "b" * 64 + ".json", "notify/BASICR4/" + "c" * 64 + ".json"])
        code, out = run(fake, "notify", "nspanel")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["NSPANEL  me@example.com  E32-MSW-NX  2026-11-07T10:00:00Z",
                                            "1 addresses"])
        self.assertEqual(fake.asked[0]["prefix"], "notify/NSPANEL/")

    def test_a_prefix_narrows_it(self):
        self.assertIn("1 in", run(FakeBucket(["readouts/a.tar", "notify/B/c.json"]), "notify/")[1])

    def test_an_api_error_is_said_plainly(self):
        def refused(*args, **kwargs):
            raise cs.SettingsError("GET /zones: HTTP 403: Authentication error")
        with mock.patch.object(cs, "api", refused), redirect_stdout(io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(readouts.main([]), 1)
        self.assertIn("readouts: GET /zones: HTTP 403", err.getvalue())
