"""The attended runs (tools/espressif/emulator/attended_suite.py): each
scenario's answers still answer questions the tool asks, and a run is judged
from its recording as it should be - here with recordings made up, as a
terminal would have them."""
import ast
import json
import re
import unittest
from pathlib import Path

from tools.espressif.emulator import attended_suite

ROOT = Path(__file__).resolve().parents[3]
HINT = "\x1b[2m(q to quit)\x1b[0m"


def recording(*parts: str) -> tuple[bytes, list[int]]:
    """screen.raw of made-up parts, and where each "|" between them was:
    where an answer was typed."""
    raw, marks = b"", []
    for part in parts:
        if part == "|":
            marks.append(len(raw))
        else:
            raw += part.replace("\n", "\r\n").encode()
    return raw, marks


class JudgeTests(unittest.TestCase):
    SCENARIO = {"answers": [{"when": "Which firmware", "type": "1\r"}],
                "expect": {"exit": 0, "result": "Converted"}}

    def judge(self, raw, marks, code=0, scenario=None, answers=None):
        scenario = scenario or self.SCENARIO
        answers = answers or [{"when": item["when"], "type": item["type"], "typed_at": marks[i:i + 1]}
                              for i, item in enumerate(scenario["answers"])]
        return attended_suite.judge(scenario, raw, answers, code)

    def test_a_run_as_it_should_be_passes(self):
        raw, marks = recording(" Stage 1 - Choosing the firmware - Underway\n\n     1. Tasmota\n",
                               f" Which firmware would you like {HINT}: ", "|", "1\n",
                               "\x1b[1F\x1b[2K" * 4, " Stage 1 - Choosing the firmware - Complete\n\n",
                               "     Result:        Converted\n")
        self.assertEqual(self.judge(raw, marks), [])

    def test_a_question_without_the_hint_or_shown_twice_is_a_question_fault(self):
        raw, marks = recording(" Which firmware would you like: ", "|", "1\n")
        self.assertIn("FAIL-QUESTION: no (q to quit) at 'Which firmware would you like:'",
                      self.judge(raw, marks))
        raw, marks = recording(f" Which firmware would you like {HINT}: 9\n",
                               f" Which firmware would you like {HINT}: ", "|", "1\n")
        self.assertTrue(any("shown twice" in verdict for verdict in self.judge(raw, marks)))

    def test_a_row_the_terminal_wrapped_is_a_question_fault(self):
        raw, marks = recording(" " + "x" * 130 + "\n", f" Which firmware would you like {HINT}: ", "|")
        self.assertIn("FAIL-QUESTION: a row the terminal wrapped itself", self.judge(raw, marks))

    def test_a_question_left_waiting_unanswered_and_an_answer_never_typed(self):
        raw, _ = recording(f" Which firmware would you like {HINT}: 1\n", f" Type PROCEED to go ahead {HINT}: ")
        verdicts = self.judge(raw, [], code=124)
        self.assertIn("FAIL-UNANSWERED: 'Type PROCEED to go ahead (q to quit):'", verdicts)
        self.assertIn("FAIL-UNUSED: 'Which firmware'", verdicts)
        self.assertIn("FAIL-RESULT: exit 124, not 0", verdicts)

    def test_an_optional_answer_need_not_be_typed(self):
        scenario = {"answers": [{"when": "Use it anyway", "type": "y\r", "optional": True}], "expect": {}}
        self.assertEqual(self.judge(b" done\r\n", [], scenario=scenario), [])

    def test_the_report_must_say_what_was_expected(self):
        raw, _ = recording("     Result:        Put back on ESPHome                   Firmware: ESPHome\n")
        scenario = {"answers": [], "expect": {"result": "Converted"}}
        self.assertEqual(self.judge(raw, [], scenario=scenario),
                         ["FAIL-RESULT: Result 'Put back on ESPHome', not 'Converted'"])

    def test_a_key_typed_anywhere_answers_no_question(self):
        scenario = {"answers": [{"when": "Stage 4", "type": "\x03", "anywhere": True}], "expect": {}}
        raw, marks = recording(" Stage 4 - Leaving Tasmota - Underway\n", "|")
        answers = [{"when": "Stage 4", "type": "\x03", "typed_at": marks, "anywhere": True}]
        self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), [])


def question_texts() -> list[str]:
    """Every text the tool can show, as it is written in its code: each
    string, an f-string's run-time parts as "...". The questions are among
    them."""
    texts = []
    for folder in ("tools/autoflash", "tools/espressif/safeboot"):
        for path in (ROOT / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    texts.append(node.value)
                elif isinstance(node, ast.JoinedStr):
                    texts.append("".join(part.value if isinstance(part, ast.Constant) else "..."
                                         for part in node.values))
    return texts


class ScenarioTests(unittest.TestCase):
    def test_every_answer_still_answers_a_question_the_tool_asks(self):
        # A question reworded fails here, in a second, not as FAIL-UNANSWERED
        # after an hour in the emulator. Optional answers, and keys typed
        # anywhere, answer what may not be asked.
        texts = question_texts()
        found = attended_suite.scenarios()
        self.assertGreater(len(found), 8)
        for name, (_, scenario) in found.items():
            for answer in scenario["answers"] + scenario.get("again", {}).get("answers", []):
                with self.subTest(scenario=name, when=answer["when"]):
                    self.assertLessEqual(set(answer), {"when", "type", "times", "optional", "anywhere"})
                    pattern = re.compile(answer["when"])
                    if answer.get("optional") or answer.get("anywhere"):
                        continue
                    self.assertTrue(any(pattern.search(text) for text in texts),
                                    f"{name}: no question of the tool's says {answer['when']!r}")

    def test_every_scenario_says_what_it_shows_and_how_it_ends(self):
        for name, (_, scenario) in attended_suite.scenarios().items():
            with self.subTest(scenario=name):
                self.assertTrue(scenario.get("about"))
                self.assertIn("expect", scenario)
                self.assertLessEqual(set(scenario), {"about", "start", "prep", "args", "size", "minutes",
                                                     "answers", "expect", "command", "again"})
                for action in scenario.get("prep", []):
                    self.assertIn(action if isinstance(action, str) else action[0],
                                  ("sudo forgets", "tasmota password", "locked esphome", "convert"))
                self.assertEqual(json.loads(json.dumps(scenario)), scenario)


if __name__ == "__main__":
    unittest.main()
