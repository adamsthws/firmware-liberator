"""failures.classify: every way the tool says a run stopped has a class."""

from __future__ import annotations

import ast
import errno
import unittest
from pathlib import Path

from tools.autoflash import failures, runner

ROOT = Path(__file__).resolve().parents[2]
# What a step raises to stop the run, as the runner reports it.
STOPPING = {"Abort", "NothingWritten", "StoppedPartWay", "DeviceError", "PhaseError", "PlugNeverConnected"}


def literal(node: ast.AST) -> str | None:
    """A message as the code writes it, each value it fills in as {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (literal(node.left) or "{}") + (literal(node.right) or "{}")
    return None


def raised() -> list[tuple[str, str]]:
    """(where, message) for each message a stopping error is raised with."""
    found = []
    for path in sorted((ROOT / "tools/autoflash").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args):
                continue
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            message = literal(node.exc.args[0]) if name in STOPPING else None
            if message is not None:
                found.append((f"{path.relative_to(ROOT)}:{node.lineno}", message))
    return found


class ClassifyTests(unittest.TestCase):
    def test_every_message_raised_has_a_class(self) -> None:
        messages = raised()
        self.assertGreater(len(messages), 100)   # the walk found them
        # One that starts with what it fills in ("{}: {}", "{}; its console
        # says: {}") passes on another's words first, and is classed by them
        # when the run stops.
        own = [(where, message) for where, message in messages if not message.startswith("{}")]
        unclassed = [f"{where}: {message[:80]}" for where, message in own
                     if failures.classify(message) == "other"]
        self.assertEqual(unclassed, [], "add a pattern to failures.PATTERNS")

    def test_each_pattern_names_a_class(self) -> None:
        self.assertLessEqual({name for name, _ in failures.PATTERNS}, set(failures.CLASSES))
        self.assertEqual(failures.classify(""), "other")
        self.assertEqual(failures.classify("words no pattern knows"), "other")

    def test_the_runners_own_words(self) -> None:
        self.assertEqual(failures.classify(runner.connection_lost(ConnectionResetError("reset"))), "device-lost")
        self.assertEqual(failures.classify(runner.unexpected(KeyError("x"))), "crash")
        full = OSError(errno.ENOSPC, "No space left on device")
        self.assertEqual(failures.classify(runner.unexpected(full)), "no-room")
        self.assertEqual(failures.classify(
            "the device turned it down, after it was sent 3 times. Switch the device off and on, "
            "then run the same command again"), "not-taken")

    def test_messages_that_could_be_taken_for_another(self) -> None:
        # Each where an earlier, broader pattern would otherwise win.
        cases = {
            "not confirmed - nothing has been written to the device": "declined",
            "the device already runs Sonoff's firmware: there is nothing to convert": "nothing-to-do",
            "the device is a BASICR4, which this tool does not convert yet. Nothing was written": "unsupported-device",
            "the device did not fetch the D1 instructions from this computer within 90s; nothing was written":
                "computer-network",
            "the device at 192.0.2.50 did not answer the eWeLink discovery; check it is switched on": "ewelink-account",
            "nothing answers at 192.0.2.50: check the device is switched on and on your Wi-Fi": "device-not-found",
            "the device at 192.0.2.50 did not answer within 30s": "device-no-answer",
            "the public dump fetched is not the one this tool knows (its hash differs): refusing it": "integrity",
            "the published tasmota image is missing: 404": "download",
            "Tasmota 15.0.1 is on the device, but its template is gone: it lost its settings.": "settings-lost",
            "the device's memory could not be read (timeout). Switch it off and on": "unexpected-layout",
            "this device cannot go to OpenBeken: a S60TPG takes Tasmota or ESPHome": "bad-option",
        }
        for message, expected in cases.items():
            self.assertEqual(failures.classify(message), expected, message)


if __name__ == "__main__":
    unittest.main()
