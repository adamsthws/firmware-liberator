"""The report at the end of a run, as the person sees it and as the fault
suites read it back: shown by report.offer_report, written to the run's log,
and each answer a suite checks read with the suite's own pattern
(tools/espressif/emulator/fault_suite.py, tools/repo/fault_verdicts.py).
Each field holds the longest answer it can, so a label renamed, a column
moved or an answer grown long is found here, not as a wrong verdict after a
night's fault suite."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import hostsys, kinds, output, records, report, routes, testers
from tools.espressif.emulator import fault_suite
from tools.repo import fault_verdicts, unattended_run


class Terminal(io.StringIO):
    def isatty(self):
        return True


def longest(values):
    return max(values, key=len)


# The longest each answer can be (report.report_fields), as it shows them.
FIELDS = [
    # Its reason cut at report.public_text's 240 characters.
    ("Result", "Stopped at stage 15 of 15 (Committing memory layout): "
     + ("the device did not answer in time " * 8)[:240].rstrip()),
    ("Firmware", longest(routes.FIRMWARES.values())),
    ("From", report.answer(f"{longest(routes.FIRMWARES.values())}, {report.CONVERTED_FROM['outdated']}")),
    ("Liberator", "12.34.56"),
    ("Model", longest(kind.name for kind in kinds.EVERY)),
    ("Backup", longest(text for _, text in records.BACKUP_OUTCOMES.values())),
    ("Identity", report.answer(longest(report.SONOFF_IDENTITY.values()))),
    ("OS", "Ubuntu 24.04.5 LTS (Windows Subsystem for Linux)"),
    ("Python", "3.13.11 (start.sh's)"),
    ("Retries", "4 (2 retries), 12 (1 retry)"),
    ("Signal", "-100 dBm"),
    ("Wi-Fi typed", "Yes"),
    ("Setup network", "Yes"),
    ("Power cycled", "False"),   # a Beken device's, in place of the two above
    ("Checks", "Not run"),
]
# What the suites read: the answer, by the pattern each reads it with.
READ = {
    "Result": fault_suite.REPORT_RESULT,
    "Backup": fault_suite.REPORT_BACKUP,
    "Identity": fault_suite.REPORT_IDENTITY,
    "Checks": fault_verdicts.CHECKS_PASSED,
}


def shown(fields, columns, rows=report.output.GOOD_ROWS, stopped=None):
    """The screen and the log of a report offered in a window of that size:
    a stopped run's (`stopped`: its result in full) or a finished one's."""
    directory = tempfile.TemporaryDirectory()
    log = Path(directory.name) / "run.log"
    terminal = Terminal()
    ctx = SimpleNamespace(reported=False, state=mock.Mock(input=mock.Mock(return_value=None)))
    with mock.patch("sys.stdout", terminal), \
            mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((columns, rows))), \
            mock.patch.object(output, "colour_ok", return_value=False), \
            mock.patch.object(hostsys, "copy_to_clipboard", return_value=False), \
            mock.patch.object(testers, "show_log"):
        output.start(log, verbose=False)
        try:
            report.offer_report(ctx, fields, stopped)
        finally:
            output.finish()
    screen = unattended_run.Screen(columns, 200)
    screen.feed(terminal.getvalue().replace("\n", "\r\n"))
    text = log.read_text(encoding="utf-8")
    directory.cleanup()
    return screen.render(colour=False), text


class ReportAnswerTests(unittest.TestCase):
    def test_every_answer_a_suite_checks_is_read_back_whole(self):
        # A stopped run's result as the screen has it, one word; in full in the log alone.
        full = dict(FIELDS)["Result"]
        expected = {**dict(FIELDS), "Result": "Stopped"}
        for columns in (80, 100, 160):
            _, log = shown(FIELDS, columns, stopped=full)
            for label, pattern in READ.items():
                with self.subTest(columns=columns, label=label):
                    found = pattern.findall(log)
                    self.assertEqual([answer.strip() for answer in found], [expected[label]], log)
            self.assertEqual(fault_suite.REPORT_RESULT_FULL.findall(log), [full])

    def test_where_the_identity_came_from_is_in_the_log_not_on_the_screen(self):
        screen, log = shown(FIELDS, 100)
        self.assertNotIn("Identity", screen)
        self.assertEqual(fault_suite.REPORT_IDENTITY.findall(log), [dict(FIELDS)["Identity"]])

    def test_every_answer_a_suite_checks_is_on_the_right_but_the_result(self):
        # So it is the last on its row in two columns (report.report_screen):
        # the result, on the left, is read up to the gap after it.
        self.assertLessEqual(set(READ), {*report.SUITES_READ, *report.LOG_ONLY, "Result"})
        self.assertLessEqual(set(report.SUITES_READ), set(report.RIGHT))
        self.assertLessEqual({*report.RIGHT, "Result"}, set(dict(FIELDS)))

    def test_every_field_the_report_can_have_is_here(self):
        # Read from report.report_fields: a field added there is added to
        # FIELDS, with the longest answer it can have, or this fails.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(report.report_fields))
        labels = {node.elts[0].value for node in ast.walk(tree)
                  if isinstance(node, ast.Tuple) and len(node.elts) == 2
                  and isinstance(node.elts[0], ast.Constant) and isinstance(node.elts[0].value, str)
                  and node.elts[0].value[:1].isupper()}
        self.assertGreater(len(labels), 10)   # read at all
        self.assertEqual(sorted(labels - set(dict(FIELDS))), [])

    def test_the_report_fits_the_window_it_is_promised_to(self):
        # "at 40 rows the report at the end fits on the screen" (wait_for_taller_window):
        # with the lines above it, what the run says once its stages are done.
        above = 4   # a blank, "Conversion complete!", what it runs, its page
        for columns in (80, 100):
            with self.subTest(columns=columns):
                screen, _ = shown(FIELDS, columns)
                rows = len(screen.rstrip("\n").splitlines())
                self.assertLessEqual(rows + above, output.GOOD_ROWS, screen)
                self.assertTrue(all(len(row) < columns for row in screen.splitlines()), screen)


if __name__ == "__main__":
    unittest.main()
