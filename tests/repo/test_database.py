import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tools.repo import cloudflare_settings as cs
from tools.repo import database


class DatabaseTests(unittest.TestCase):
    """database.py: one question asked of the API's database, with the read-only token."""

    def run_with(self, args, rows=(), refused=None):
        asked = []

        def fake(path, method="GET", body=None, **kwargs):
            if path == f"/zones?name={cs.ZONE}":
                return [{"id": "z1", "account": {"id": "a1"}}]
            if refused:
                raise cs.SettingsError(refused, 403)
            asked.append((path, method, body))
            return [{"results": list(rows), "success": True}]
        out, err = io.StringIO(), io.StringIO()
        try:
            with mock.patch.object(cs, "api", fake), mock.patch.object(cs, "bound_database_id", return_value="u1"), \
                    redirect_stdout(out), mock.patch("sys.stderr", err):
                code = database.main(args)
            variable = cs.TOKEN_VARIABLE
        finally:
            cs.TOKEN_VARIABLE = cs.TOKENS["settings-apply"]["variable"]
        return code, out.getvalue().splitlines(), err.getvalue(), asked, variable

    def test_the_rows_are_lined_up_under_their_names(self):
        rows = [{"model": "S60TPF", "n": 26, "error": None}, {"model": "S60TPG", "n": 3, "error": "cut"}]
        code, lines, _, asked, variable = self.run_with(["SELECT model, count(*) AS n FROM reports"], rows)
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["model   n   error", "S60TPF  26", "S60TPG  3   cut"])
        self.assertEqual(asked, [("/accounts/a1/d1/database/u1/query", "POST",
                                  {"sql": "SELECT model, count(*) AS n FROM reports", "params": []})])
        self.assertEqual(variable, "CLOUDFLARE_DATABASE_TOKEN")

    def test_what_a_computer_sent_cannot_drive_the_terminal(self):
        rows = [{"text": "name\x1b[2Jgone"}]
        _, lines, _, _, _ = self.run_with(["SELECT text FROM reports"], rows)
        self.assertEqual(lines[1], "name\\x1b[2Jgone")
        _, lines, _, _, _ = self.run_with(["--json", "SELECT text FROM reports"], rows)
        self.assertEqual(json.loads(lines[0]), rows[0])
        self.assertNotIn("\x1b", lines[0])

    def test_tables_lists_each_with_its_columns(self):
        _, lines, _, asked, _ = self.run_with(["--tables"], [{"tables": "reports", "columns": "id, day"}])
        self.assertEqual(asked[0][2]["sql"], database.TABLES)
        self.assertEqual(lines, ["tables   columns", "reports  id, day"])

    def test_no_rows_says_so(self):
        self.assertEqual(self.run_with(["SELECT 1 WHERE 0"])[1], ["(no rows)"])

    def test_no_question_or_two_shows_how_to_ask(self):
        for args in ([], ["  "], ["SELECT 1", "SELECT 2"], ["--json"]):
            with self.subTest(args=args):
                code, lines, err, asked, _ = self.run_with(args)
                self.assertEqual((code, lines, asked), (2, [], []))
                self.assertIn("database.py", err)

    def test_a_refusal_is_said_not_raised(self):
        code, _, err, _, _ = self.run_with(["DELETE FROM reports"], refused="POST: HTTP 403: not allowed to write")
        self.assertEqual(code, 1)
        self.assertIn("The database was not read: POST: HTTP 403: not allowed to write", err)


if __name__ == "__main__":
    unittest.main()
