"""The screen a whole run leaves, route by route: every route's own stages,
run by the run's own loop (runner.run_steps) and drawn by output.py as on a
terminal, each step standing in for what steps do - a note, a section, a
question answered, a try that fails on the network, a later
step finding an earlier one did not take - then read back as a terminal
shows it (tools/repo/unattended_run.py).  Every stage must leave one line,
and nothing it showed on the way."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import errors, firewall, join, output, report, routes, runner, runs, safety_locks, unlock
from tools.autoflash.routes import update
from tools.autoflash.phases import PhaseError
from tools.autoflash.state import RunState
from tools.repo import unattended_run

SIZE = (100, 40)


class Terminal(io.StringIO):
    def isatty(self):
        return True


def every_route():
    """(family name, family, source, firmware) for every route a plug can take."""
    for name, family in routes.FAMILIES.items():
        for source in family.SOURCES:
            for firmware in family.FIRMWARES:
                try:
                    steps = routes.steps_for(firmware, source, family)
                except ValueError:
                    continue
                if steps:
                    yield name, family, source, firmware


class RouteScreenTests(unittest.TestCase):
    def run_route(self, family, source, firmware):
        """The screen and the log of a whole run of the route."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        ctx = SimpleNamespace(state=state, dry_run=False, research_bridge=False, go_back=None,
                              finished=False, last_runner=None)
        steps = [step for step, _, _ in routes.steps_for(firmware, source, family)]
        retried = next((step for step in steps if step in family.RETRY_STEPS), None)
        back_from = steps[-1] if len(steps) > 1 else None
        calls: dict[str, int] = {}

        def stand_in(step):
            def handler(context):
                calls[step] = calls.get(step, 0) + 1
                index = steps.index(step)
                output.tell(f"    A note shown while {step} is under way.")
                if index % 3 == 1:
                    output.transient_begin()
                    output.show("-- A section, and its question")
                    output.before_input("Go ahead? ")
                    sys.stdout.write("Go ahead? y\n")   # as input() shows it, and the answer echoed
                    output.answered("y")
                    output.transient_end()
                if step == retried and calls[step] == 1:
                    output.tell("    The device's console: 00:00:00.328 BRY: Berry initialized")
                    raise PhaseError(f"the device stopped the {step} without reaching this computer")
                if step == back_from and calls[step] == 1:
                    raise errors.GoBack(steps[-2], f"{steps[-2]}'s firmware was not installed", "not installed")
                return {}
            return handler

        real_apply = routes.apply_target

        def apply_target(*args):
            real_apply(*args)
            routes.STEP_INDEX.update({step: (title, stand_in(step)) for step, (title, _) in routes.STEP_INDEX.items()})

        terminal = Terminal()
        with ExitStack() as patches:
            for owner, name, value in (
                    (safety_locks, "restore_left_locks", mock.Mock()), (unlock, "unlock_tasmota", mock.Mock()),
                    (routes, "settle_route", mock.Mock(return_value=(firmware, source))),
                    (routes, "route_source", mock.Mock(return_value=source)),
                    (routes, "family_of", mock.Mock(return_value=family)),
                    (routes, "apply_target", apply_target),
                    (join, "wait_while_plug_busy", mock.Mock()),
                    (runner, "set_up_if_erased", mock.Mock(return_value=False)),
                    (runner, "wait_for_plug_back", mock.Mock()),
                    (runner, "stop_here_after_ctrl_c", mock.Mock(return_value=False)),
                    (runner.time, "sleep", mock.Mock()),
                    (firewall, "close_firewall_rules", mock.Mock()),
                    (report, "plug_page_url", mock.Mock(return_value=None)),
                    (report, "invite_report", mock.Mock()),
                    (runs, "run_source", mock.Mock(return_value=source)),
                    (runs, "run_firmware", mock.Mock(return_value=firmware)),
                    (update, "versions", mock.Mock(return_value=("1.0.0", "1.1.0"))),
                    (output, "colour_ok", mock.Mock(return_value=True))):
                patches.enter_context(mock.patch.object(owner, name, value))
            patches.enter_context(mock.patch("sys.stdout", terminal))
            patches.enter_context(mock.patch("sys.stdin", io.StringIO()))
            patches.enter_context(mock.patch("shutil.get_terminal_size", return_value=os.terminal_size(SIZE)))
            patches.enter_context(mock.patch.object(routes, "STEPS", routes.STEPS))
            patches.enter_context(mock.patch.object(routes, "STEP_INDEX", routes.STEP_INDEX))
            patches.enter_context(mock.patch.object(routes, "FAMILY", routes.FAMILY))
            patches.enter_context(mock.patch.object(routes, "ROUTE", routes.ROUTE))
            log = Path(directory.name) / "run.log"
            output.start(log, verbose=False)
            try:
                code = runner.run_steps(ctx)
            finally:
                output.finish()
        screen = unattended_run.Screen(*SIZE)
        screen.feed(terminal.getvalue().replace("\n", "\r\n"))  # as a terminal's line discipline does
        return code, steps, screen.render(colour=False), log.read_text(encoding="utf-8")

    def test_every_route_leaves_one_line_a_stage_and_nothing_else_of_them(self):
        routes_run = 0
        for name, family, source, firmware in every_route():
            with self.subTest(route=f"{name} {source}>{firmware}"):
                code, steps, screen, log = self.run_route(family, source, firmware)
                routes_run += 1
                self.assertEqual(code, 0, screen)
                self.assertEqual(unattended_run.stage_problems(screen), [], screen)
                lines = [row for row in screen.splitlines() if unattended_run.STAGE_ROW.match(row)]
                self.assertEqual([row.rsplit(" - ", 1)[-1] for row in lines], ["Complete"] * len(steps), screen)
                for gone in ("under way", "A section", "Go ahead", "console", "FAILED", "trying again",
                             "not installed", "again."):
                    self.assertNotIn(gone, screen)
                self.assertIn("under way", log)   # the log keeps everything
                if any(step in family.RETRY_STEPS for step in steps):
                    self.assertIn("trying again", log)
        self.assertGreater(routes_run, 10)


if __name__ == "__main__":
    unittest.main()
