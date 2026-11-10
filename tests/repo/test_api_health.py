"""api_health: the public figures fresh, and the account within the free plan."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import api_health as health

NOW = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc)


def figures(**changes):
    return {"format": 1, "generated": "2026-11-10T03:17Z", "schema": health.latest_migration(), **changes}


def run(*args, snapshot=None, analytics=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.object(health, "fetch_snapshot", return_value=snapshot or figures()), \
            mock.patch.object(health.cs, "api", return_value=[{"account": {"id": "a1"}}]), \
            mock.patch.object(health, "graphql", return_value=analytics):
        code = health.main(list(args), now=NOW)
    return code, out.getvalue() + err.getvalue()


def analytics(requests=1000, rows_read=10_000, rows_written=500, d1_bytes=10_000_000, r2_bytes=1_000_000_000,
              class_a=100, class_b=1000):
    return {"viewer": {"accounts": [{
        "workers": [{"sum": {"requests": requests}}],
        "d1": [{"sum": {"rowsRead": rows_read, "rowsWritten": rows_written}}],
        "d1Storage": [{"max": {"databaseSizeBytes": d1_bytes}, "dimensions": {"databaseId": "d"}}],
        "r2Storage": [{"max": {"payloadSize": r2_bytes, "metadataSize": 0}, "dimensions": {"bucketName": "b"}}],
        "r2Operations": [{"sum": {"requests": class_a}, "dimensions": {"actionType": "PutObject"}},
                         {"sum": {"requests": class_b}, "dimensions": {"actionType": "GetObject"}},
                         {"sum": {"requests": 99}, "dimensions": {"actionType": "DeleteObject"}}],
    }]}}


class SnapshotTests(unittest.TestCase):
    def test_fresh_figures_of_todays_schema_pass(self):
        code, out = run("snapshot")
        self.assertEqual(code, 0, out)
        self.assertIn("fresh", out)

    def test_figures_a_night_missed_fail(self):
        self.assertEqual(run("snapshot", snapshot=figures(generated="2026-11-09T09:00Z"))[0], 0, "a day old")
        code, out = run("snapshot", snapshot=figures(generated="2026-11-09T07:59Z"))
        self.assertEqual(code, 1)
        self.assertIn("the nightly job has not run", out)

    def test_a_night_missed_is_seen_at_the_workflows_hour(self):
        # The last figures from the night before, checked at 06:00 after a night missed.
        at_six = dt.datetime(2026, 11, 10, 6, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(health.snapshot_problems(figures(generated="2026-11-09T03:17Z"), at_six))
        self.assertFalse(health.snapshot_problems(figures(generated="2026-11-10T03:17Z"), at_six))
        workflow = (Path(health.ROOT) / ".github/workflows/cloudflare-settings.yml").read_text(encoding="utf-8")
        self.assertIn('- cron: "0 6 * * *"', workflow)
        api = (Path(health.ROOT) / "website/api/wrangler.jsonc").read_text(encoding="utf-8")
        self.assertIn('"crons": ["17 3 * * *"]', api)

    def test_figures_from_a_database_short_of_a_migration_say_to_apply_it(self):
        code, out = run("snapshot", snapshot=figures(schema="0001_counters.sql"))
        self.assertEqual(code, 1)
        self.assertIn("run python3 tools/repo/cloudflare_settings.py apply", out)

    def test_figures_not_as_written_fail(self):
        for changed in ({"format": 2}, {"generated": "yesterday"}, {"generated": None}):
            self.assertEqual(run("snapshot", snapshot=figures(**changed))[0], 1, changed)

    def test_figures_not_there_fail_plainly(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out), mock.patch.object(
                health.urllib.request, "urlopen", side_effect=OSError("Name or service not known")):
            self.assertEqual(health.main(["snapshot"], now=NOW), 1)
        self.assertIn(f"{health.SNAPSHOT_URL}: Name or service not known", out.getvalue())

    def test_its_time_is_written_as_the_nightly_job_writes_it(self):
        snapshot = (Path(health.ROOT) / "website/api/snapshot.js").read_text(encoding="utf-8")
        self.assertIn('generated: now.toISOString().slice(0, 16) + "Z"', snapshot)


class UsageTests(unittest.TestCase):
    def test_well_within_each_limit_passes_and_says_each(self):
        code, out = run("usage", analytics=analytics())
        self.assertEqual(code, 0, out)
        for what in health.INCLUDED:
            self.assertIn(what, out)

    def test_half_of_anything_included_fails(self):
        cases = {"requests": 5_000_000, "rows_read": 12_500_000_000, "rows_written": 25_000_000,
                 "d1_bytes": 2_500_000_000, "r2_bytes": 5_000_000_000, "class_a": 500_000, "class_b": 5_000_000}
        for key, value in cases.items():
            code, out = run("usage", analytics=analytics(**{key: value}))
            self.assertEqual(code, 1, key)
            self.assertIn("past 50%", out, key)

    def test_operations_are_counted_by_class_and_deletes_are_free(self):
        measures = health.used(analytics(class_a=7, class_b=11))
        self.assertEqual(measures["R2 Class A operations this month"], 7)
        self.assertEqual(measures["R2 Class B operations this month"], 11)

    def test_storage_is_each_buckets_most_added_together(self):
        rows = [{"max": {"payloadSize": 5, "metadataSize": 1}, "dimensions": {"bucketName": "a"}},
                {"max": {"payloadSize": 9, "metadataSize": 0}, "dimensions": {"bucketName": "a"}},
                {"max": {"payloadSize": 2, "metadataSize": 0}, "dimensions": {"bucketName": "b"}}]
        self.assertEqual(health.held(rows, ("payloadSize", "metadataSize"), "bucketName"), 11)
        self.assertEqual(health.held(None, ("payloadSize",), "bucketName"), 0)

    def test_a_token_that_sees_no_account_says_what_it_needs(self):
        code, out = run("usage", analytics={"viewer": {"accounts": []}})
        self.assertEqual(code, 1)
        self.assertIn("Account Analytics: Read", out)

    def test_an_analytics_error_is_said_in_full(self):
        answer = io.BytesIO(json.dumps({"data": None, "errors": [{"message": "unknown field d1StorageAdaptiveGroups"}]})
                            .encode())
        out = io.StringIO()
        with contextlib.redirect_stderr(out), mock.patch.object(health.cs, "token", return_value="t"), \
                mock.patch.object(health.cs, "api", return_value=[{"account": {"id": "a1"}}]), \
                mock.patch.object(health.urllib.request, "urlopen", return_value=answer):
            self.assertEqual(health.main(["usage"], now=NOW), 1)
        self.assertIn("unknown field d1StorageAdaptiveGroups", out.getvalue())

    def test_it_asks_for_this_month_to_yesterday(self):
        asked = {}
        with mock.patch.object(health.cs, "api", return_value=[{"account": {"id": "a1"}}]), \
                mock.patch.object(health, "graphql", side_effect=lambda query, variables: asked.update(variables)
                                  or analytics()):
            health.usage(NOW)
        self.assertEqual(asked, {"account": "a1", "day": "2026-11-09", "firstDay": "2026-11-01",
                                 "from": "2026-11-09T00:00:00Z", "to": "2026-11-10T00:00:00Z",
                                 "month": "2026-11-01T00:00:00Z"})
        # On the 1st, the month before, whole.
        asked.clear()
        with mock.patch.object(health.cs, "api", return_value=[{"account": {"id": "a1"}}]), \
                mock.patch.object(health, "graphql", side_effect=lambda query, variables: asked.update(variables)
                                  or analytics()):
            health.usage(dt.datetime(2026, 12, 1, 6, 0, tzinfo=dt.timezone.utc))
        self.assertEqual((asked["firstDay"], asked["day"]), ("2026-11-01", "2026-11-30"))


class WorkflowTests(unittest.TestCase):
    def test_both_are_checked_daily_by_the_settings_workflow_and_not_on_a_push(self):
        workflow = (Path(health.ROOT) / ".github/workflows/cloudflare-settings.yml").read_text(encoding="utf-8")
        job = workflow[workflow.index("\n  health:"):]
        self.assertIn("if: github.event_name != 'push'", job)
        self.assertIn("run: python3 tools/repo/api_health.py snapshot", job)
        self.assertIn("run: python3 tools/repo/api_health.py usage", job)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_READ_TOKEN }}", job)

    def test_the_read_token_is_given_what_usage_needs(self):
        readme = (Path(health.ROOT) / "website/README.md").read_text(encoding="utf-8")
        row = next(line for line in readme.splitlines() if line.startswith("| Settings check |"))
        self.assertIn("Account Analytics: Read", row)
