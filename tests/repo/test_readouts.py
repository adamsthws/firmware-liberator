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
        self.assertEqual(out.splitlines(), ["NSPANEL  me@example.com  E32-MSW-NX  2026-11-07T10:00:00Z  "
                                            "(no removal link)", "1 addresses"])
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


class ChangesTests(unittest.TestCase):
    """The devices whose status changed since a release, and whom to tell."""

    def changes(self, before, now, given):
        items = [{"key": f"notify/{product}/{n:064x}.json", "last_modified": "2026-11-09T10:00:00Z",
                  "custom_metadata": {"product": product, "email": email, "models": "-",
                                      "forget": f"https://api.firmwareliberator.com/forget?a={n:064x}&t={'0' * 32}"}}
                 for n, (product, email) in enumerate(given)]
        items.append({"key": "notify/_who/" + "e" * 64 + ".json"})
        out = io.StringIO()
        with mock.patch.object(readouts, "statuses", side_effect=lambda ref=None: now if ref is None else before), \
                mock.patch.object(readouts, "objects", return_value=items), redirect_stdout(out):
            code = readouts.main(["changes", "v2.3.0"])
        return code, out.getvalue().splitlines()

    def test_each_device_changed_is_named_with_whom_to_tell_and_their_link(self):
        code, lines = self.changes({"sonoff-basicr4": "not-supported", "sonoff-s60": "supported"},
                                   {"sonoff-basicr4": "readout-wanted", "sonoff-s60": "supported"},
                                   [("BASICR4", "me@example.com"), ("S60TPG", "you@example.com")])
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "Sonoff BASICR4: not-supported -> readout-wanted")
        self.assertIn("BASICR4  me@example.com", lines[1])
        self.assertIn("https://api.firmwareliberator.com/forget?a=", lines[1])
        self.assertNotIn("you@example.com", "\n".join(lines), "the S60's did not change")
        self.assertEqual(lines[-1], "1 changed since v2.3.0, 1 to tell")

    def test_nothing_changed_says_so(self):
        code, lines = self.changes({"sonoff-s60": "supported"}, {"sonoff-s60": "supported"}, [])
        self.assertEqual((code, lines), (0, ["No device's status changed since v2.3.0."]))

    def test_never_run_by_ci_whose_logs_are_public(self):
        from pathlib import Path
        workflows = Path(readouts.__file__).resolve().parents[2] / ".github/workflows"
        self.assertEqual([path.name for path in workflows.glob("*.yml")
                          if "readouts.py" in path.read_text(encoding="utf-8")], [])


class ReviewTests(unittest.TestCase):
    """The readouts waiting for review, and settling one."""

    ROWS = [
        {"id": "2026-11-09-aaaaaaaaaaaa", "key": "BASICR4|SN-ESP32C3-BASICR4-01|1.1.0", "stage": "whole",
         "fingerprint": "1" * 64, "object": "readouts/2026-11-09/a.tar", "received": "2026-11-09T10:00:00Z"},
        {"id": "2026-11-09-bbbbbbbbbbbb", "key": "BASICR4|SN-ESP32C3-BASICR4-01|1.1.0", "stage": "whole",
         "fingerprint": "2" * 64, "object": "readouts/2026-11-09/b.tar", "received": "2026-11-09T11:00:00Z"},
        {"id": "2026-11-09-cccccccccccc", "key": "BASICR4|SN-ESP32C3-BASICR4-01|1.1.0", "stage": "whole",
         "fingerprint": "1" * 64, "object": "readouts/2026-11-09/c.tar", "received": "2026-11-09T12:00:00Z"},
    ]

    def run_with(self, args, rows):
        asked = []

        def fake(path, method="GET", body=None, **kwargs):
            if path == f"/zones?name={cs.ZONE}":
                return [{"id": "z1", "account": {"id": "a1"}}]
            asked.append((path, method, body))
            return [{"results": rows(body), "success": True}]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cs, "api", fake), mock.patch.object(cs, "bound_database_id", return_value="u1"), \
                redirect_stdout(out), mock.patch("sys.stderr", err):
            code = readouts.main(args)
        return code, out.getvalue().splitlines(), err.getvalue(), asked

    def test_each_waiting_readout_is_listed_by_key_the_odd_one_marked(self):
        code, lines, _, asked = self.run_with(["review"], lambda body: self.ROWS)
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "BASICR4|SN-ESP32C3-BASICR4-01|1.1.0 (whole): 3 waiting")
        self.assertEqual([line.endswith("ODD") for line in lines[1:4]], [False, True, False])
        self.assertEqual(lines[-1], "3 waiting for review")
        self.assertEqual(asked[0][0], "/accounts/a1/d1/database/u1/query")
        self.assertIn("review = 'pending'", asked[0][2]["sql"])

    def test_one_product_alone(self):
        _, _, _, asked = self.run_with(["review", "basicr4"], lambda body: [])
        self.assertEqual(asked[0][2]["params"], ["BASICR4|%"])

    def test_rotated_lists_the_keys_taken_as_right_from_both_sides_of_the_time(self):
        kept = [dict(row) for row in self.ROWS] + [
            {"id": "2026-11-09-dddddddddddd", "key": "S60TPF|SN-ESP32C3-S60-01|1.0.0", "stage": "whole",
             "fingerprint": "3" * 64, "object": "readouts/2026-11-09/d.tar", "received": "2026-11-09T09:00:00Z"}]
        code, lines, _, asked = self.run_with(["rotated", "2026-11-09T10:30:00Z"], lambda body: kept)
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "BASICR4|SN-ESP32C3-BASICR4-01|1.1.0 (whole): 3 taken as right, "
                                   "sent both sides of 2026-11-09T10:30:00Z")
        self.assertEqual([line.split()[-1] for line in lines[1:4]], ["before", "after", "after"])
        self.assertEqual(lines[-1], "1 to look at again", "one key all on one side is left out")
        self.assertIn("review IN ('quorum', 'approved')", asked[0][2]["sql"])

    def test_rotated_takes_a_time_as_rotate_prints_it(self):
        code, _, err, asked = self.run_with(["rotated", "yesterday"], lambda body: [])
        self.assertEqual((code, asked), (2, []))
        self.assertIn("not a time as rotate prints it", err)

    @staticmethod
    def database(review="pending", bytes_=1234):
        """The API's database as settle() asks it: the readout, then the change."""
        def rows(body):
            if body["sql"].startswith("SELECT review"):
                return [{"review": review, "bytes": bytes_}]
            if body["sql"].startswith("UPDATE readouts"):
                return [{"id": body["params"][1], "key": "BASICR4|x|1", "review": body["params"][0]}]
            return []
        return rows

    def test_approve_and_reject_settle_one_by_its_reference(self):
        for action, state in (("approve", "approved"), ("reject", "rejected")):
            code, lines, _, asked = self.run_with([action, "2026-11-09-bbbbbbbbbbbb"], self.database())
            self.assertEqual(code, 0)
            self.assertEqual(asked[1][2]["params"], [state, "2026-11-09-bbbbbbbbbbbb"])
            self.assertEqual(lines, [f"2026-11-09-bbbbbbbbbbbb (BASICR4|x|1): {state}"])

    def test_one_settled_from_waiting_gives_back_its_room(self):
        _, _, _, asked = self.run_with(["approve", "2026-11-09-bbbbbbbbbbbb"], self.database("pending", 1234))
        self.assertEqual(asked[2][2]["params"], [1234, "readouts:pending"])
        _, _, _, asked = self.run_with(["reject", "2026-11-09-bbbbbbbbbbbb"], self.database("quorum"))
        self.assertEqual(len(asked), 2, "one agreed already gave its room back")

    def test_what_a_sender_wrote_is_printed_escaped(self):
        code, lines, _, _ = self.run_with(["review"], lambda body: [{**self.ROWS[0], "key": "BAD\x1b[2J|x|1"}])
        self.assertEqual(lines[0], "BAD\\x1b[2J|x|1 (whole): 1 waiting")

    def test_a_reference_not_one_or_not_there_is_said_so(self):
        code, _, err, asked = self.run_with(["reject", "bobby-tables"], lambda body: [])
        self.assertEqual((code, asked), (2, []))
        code, _, err, _ = self.run_with(["reject", "2026-11-09-000000000000"], lambda body: [])
        self.assertEqual(code, 1)
        self.assertIn("no readout 2026-11-09-000000000000", err)
