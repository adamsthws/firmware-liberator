"""The attended runs (tools/espressif/emulator/attended_suite.py): each
scenario's answers still answer questions the tool asks, and a run is judged
from its recording as it should be - here with recordings made up, as a
terminal would have them."""
import ast
import io
import itertools
import json
import re
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.espressif.emulator import attended_suite
from tools.repo import suite_coverage

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

    def judge(self, raw, marks, code=0, scenario=None, answers=None, result="Converted", clipboard=None):
        scenario = scenario or self.SCENARIO
        answers = answers or [{"when": item["when"], "type": item["type"], "typed_at": marks[i:i + 1]}
                              for i, item in enumerate(scenario["answers"])]
        return attended_suite.judge(scenario, raw, answers, code, result=result, clipboard=clipboard)

    def test_a_run_as_it_should_be_passes(self):
        raw, marks = recording(" Stage 1 - Choosing the firmware - Underway\n\n     1. Tasmota\n",
                               f" Which firmware would you like {HINT}: ", "|", "1\n",
                               "\x1b[1F\x1b[2K" * 4, " Stage 1 - Choosing the firmware - Complete\n\n",
                               " Conversion complete!\n")
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

    def test_a_question_its_timeout_took_off_the_screen_is_found_unanswered(self):
        raw, _ = recording(f" Which firmware would you like {HINT}: ")
        stopped_at = len(raw)
        raw += "\r\x1b[2K Stage 3 - Choosing the firmware - Stopped\n".encode()
        scenario = {"answers": [], "expect": {"exit": 143}}
        self.assertEqual(self.judge(raw, [], code=143, scenario=scenario), [])
        self.assertEqual(attended_suite.judge(scenario, raw, [], 143, stopped_at=stopped_at),
                         ["FAIL-UNANSWERED: 'Which firmware would you like (q to quit):'"])

    def test_an_optional_answer_need_not_be_typed(self):
        scenario = {"answers": [{"when": "Use it anyway", "type": "y\r", "optional": True}], "expect": {}}
        self.assertEqual(self.judge(b" done\r\n", [], scenario=scenario), [])

    def test_the_report_must_say_what_was_expected(self):
        raw, _ = recording(" Sonoff's firmware could not be put back, so the device is back on ESPHome.\n")
        scenario = {"answers": [], "expect": {"result": "Converted"}}
        self.assertEqual(self.judge(raw, [], scenario=scenario, result="Put back on ESPHome"),
                         ["FAIL-RESULT: Result 'Put back on ESPHome', not 'Converted'"])
        self.assertEqual(self.judge(raw, [], scenario=scenario, result=None),   # a run that made no report
                         ["FAIL-RESULT: Result 'none', not 'Converted'"])

    def test_a_key_typed_anywhere_answers_no_question(self):
        scenario = {"answers": [{"when": "Stage 4", "type": "\x03", "anywhere": True}], "expect": {}}
        raw, marks = recording(" Stage 4 - Leaving Tasmota - Underway\n", "|")
        answers = [{"when": "Stage 4", "type": "\x03", "typed_at": marks, "anywhere": True}]
        self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), [])

    def test_an_answer_that_switches_the_device_or_signals_the_run_is_judged_too(self):
        scenario = {"answers": [{"when": "Stage 8", "power": "off", "anywhere": True},
                                {"when": "Which firmware", "signal": "TERM", "optional": True}], "expect": {}}
        raw, marks = recording(" Stage 8 - Installing interim firmware - Underway\n", "|")
        answers = [{"when": "Stage 8", "type": "", "typed_at": marks, "anywhere": True, "power": "off"},
                   {"when": "Which firmware", "type": "", "typed_at": [], "signal": "TERM"}]
        self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), [])
        answers[0]["typed_at"] = []
        self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), ["FAIL-UNUSED: 'Stage 8'"])

    def test_an_answer_that_failed_to_act_is_an_answer_fault(self):
        scenario = {"answers": [{"when": "setup network", "wifi": "join", "anywhere": True}], "expect": {}}
        raw, marks = recording(" Join the device's setup network\n", "|")
        answers = [{"when": "setup network", "type": "", "typed_at": marks, "anywhere": True, "wifi": "join",
                    "failed": ["wifi join: No network of the device's own is on the air."]}]
        self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers),
                         ["FAIL-ANSWER: 'setup network': wifi join: No network of the device's own is on the air."])

    def test_how_the_run_ended_is_judged_with_what_it_copied(self):
        # unattended_run.end_problems, on the screen it ended on.
        scenario = {"answers": [], "expect": {}}
        report = (" FAILED: the device did not answer\n\n     Liberator: 3.0.0\n     OS:        Ubuntu\n\n"
                  " If it should not have stopped, please open an issue here:\n"
                  "     https://firmwareliberator.com/issues\n     Report ID: 0123456789abcdef\n"
                  "     This report is on your clipboard.\n\n This run's log:\n     run.log\n")
        raw, _ = recording(report)
        copied = "Liberator: 3.0.0\nReport ID: 0123456789abcdef\n"
        self.assertEqual(self.judge(raw, [], scenario=scenario, clipboard=copied), [])
        self.assertEqual(self.judge(raw, [], scenario=scenario, clipboard=""),
                         ["FAIL-SCREEN: said its report is on the clipboard, and nothing was put there"])

    def test_a_line_the_scenario_wants_shown_must_be_on_the_final_screen(self):
        scenario = {"answers": [], "expect": {"shows": [r"^ *Not needed: +1$"]}}
        raw, _ = recording(" Nothing was read out.\n")
        self.assertEqual(self.judge(raw, [], scenario=scenario),
                         ["FAIL-SCREEN: not shown: '^ *Not needed: +1$'"])

    def test_a_line_an_answer_wants_shown_must_be_on_the_screen_as_it_is_typed(self):
        # As why the answer before it was not taken: gone again by the end.
        scenario = {"answers": [{"when": "Account", "type": "123\r"},
                                {"when": "Account", "type": "a@b.example\r", "shows": ["^ *! Hint: "]}],
                    "expect": {}}
        for said, verdicts in ((" ! Hint: a phone number needs its country code\n", []),
                               ("", ["FAIL-SCREEN: not shown at 'Account': '^ *! Hint: '"])):
            with self.subTest(said=said):
                raw, marks = recording(f" Account {HINT}: ", "|", "123\n", "\x1b[1F\x1b[2K", said,
                                       f" Account {HINT}: ", "|", "a@b.example\n", "\x1b[1F\x1b[2K" * 2, " Done\n")
                answers = [{"when": "Account", "type": item["type"], "typed_at": [marks[i]]}
                           for i, item in enumerate(scenario["answers"])]
                self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), verdicts)

    def test_two_answers_alike_are_judged_each_by_its_own_checks(self):
        # The right password typed twice: only the first, after a wrong one,
        # wants why on the screen - and finds it missing, once.
        scenario = {"answers": [{"when": "Password", "type": "wrong\r"},
                                {"when": "Password", "type": "right\r", "shows": ["^ *! Not accepted"]},
                                {"when": "Password", "type": "right\r"}], "expect": {}}
        wipe = "\n\x1b[2J\x1b[H"
        raw, marks = recording(f" Password {HINT}: ", "|", wipe, f" Password {HINT}: ", "|",
                               wipe + " Later\n", f" Password {HINT}: ", "|", wipe + " Done\n")
        answers = [{"when": "Password", "type": item["type"], "typed_at": [marks[i]]}
                   for i, item in enumerate(scenario["answers"])]
        self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers),
                         ["FAIL-SCREEN: not shown at 'Password': '^ *! Not accepted'"])

    def test_a_line_an_answer_never_wants_must_be_off_the_screen_as_it_is_typed(self):
        # As a list a key was pressed to clear, gone by the next question.
        scenario = {"answers": [{"when": "Press any key", "type": "x"},
                                {"when": "Which firmware", "type": "1\r", "never": ["^ *You'll need:"]}],
                    "expect": {}}
        for cleared, verdicts in (("\x1b[1F\x1b[2K" * 3, []),
                                  ("", ["FAIL-SCREEN: shown at 'Which firmware': \"^ *You'll need:\""])):
            with self.subTest(cleared=bool(cleared)):
                raw, marks = recording(" You'll need:\n     - This computer\n", f" Press any key {HINT}", "|",
                                       "\r\x1b[2K", cleared, f" Which firmware would you like {HINT}: ", "|", "1\n")
                answers = [{"when": item["when"], "type": item["type"], "typed_at": [marks[i]]}
                           for i, item in enumerate(scenario["answers"])]
                self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), verdicts)

    def test_the_recorder_is_given_no_judge_s_keys(self):
        scenario = {"answers": [{"when": "Account", "type": "x\r", "optional": True, "shows": ["Hint"],
                                 "never": ["Hint"]}]}
        with tempfile.TemporaryDirectory() as folder:
            given = json.loads(attended_suite.run_answers(scenario, Path(folder)).read_text(encoding="utf-8"))
        self.assertEqual(given, [{"when": "Account", "type": "x\r"}])

    def test_a_line_the_scenario_never_wants_is_found_at_an_answer_or_at_the_end(self):
        scenario = {"answers": [{"when": "Stage 3", "type": "zq", "anywhere": True}], "expect": {"never": ["zq"]}}
        for shown, verdicts in ((" Stage 3 - Choosing the firmware - Underway zq\n", ["FAIL-SCREEN: shown: 'zq'"]),
                                (" Stage 3 - Choosing the firmware - Underway\n", [])):
            with self.subTest(shown=shown):
                # Echoed at one answer, then taken off by the end: found all the same.
                raw, marks = recording(shown, "|", "\x1b[1F\x1b[2K Stage 3 - Choosing the firmware - Complete\n")
                answers = [{"when": "Stage 3", "type": "zq", "typed_at": marks, "anywhere": True}]
                self.assertEqual(self.judge(raw, marks, scenario=scenario, answers=answers), verdicts)

    def test_a_row_the_terminal_wrapped_after_the_last_answer_is_a_screen_fault(self):
        scenario = {"answers": [], "expect": {}}
        raw, _ = recording(" " + "x" * 130 + "\n")
        self.assertEqual(self.judge(raw, [], scenario=scenario),
                         ["FAIL-SCREEN: a row the terminal wrapped itself, after the last answer"])


class RunTests(unittest.TestCase):
    def test_a_beken_device_from_a_saved_state_is_waited_for_until_it_is_up(self):
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            leases = iter(["", "", "1 2 198.18.61.50 openbeken-cdef *"])
            with mock.patch.object(attended_suite, "docker", side_effect=lambda *a: SimpleNamespace(
                    stdout=next(leases, "1 2 198.18.61.50 x *"))), mock.patch.object(attended_suite.time, "sleep"):
                self.assertFalse(attended_suite.beken_ready(61, Path(tmp), wait=0.05))   # no CA yet
                Path(tmp, "ca.crt").write_text("ca", encoding="utf-8")
                self.assertTrue(attended_suite.beken_ready(61, Path(tmp)))

    def test_two_batches_begun_the_same_second_have_a_results_folder_each(self):
        # Seen: an S61 batch and an S60 one started together, and the second
        # stopped at once on the first's folder (FileExistsError).
        import datetime
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(attended_suite, "RESULTS", Path(tmp)):
            now = datetime.datetime(2026, 11, 17, 23, 43, 9)
            first, second = attended_suite.new_results(now), attended_suite.new_results(now)
        self.assertEqual((first.name, second.name), ("20261117-234309", "20261117-234309-2"))

    def test_a_readout_scenario_runs_a_readout(self):
        command = attended_suite.wrapper(61, {"command": "readout"}, "--", "--new", mac="12:34:56:ab:cd:ef")
        self.assertIn("--readout", command[:command.index("--")])
        self.assertNotIn("--readout", attended_suite.wrapper(61, {}, mac="12:34:56:ab:cd:ef"))

    def test_the_api_has_enough_of_the_devices_product_where_the_scenario_says(self):
        from unittest import mock
        with mock.patch("tools.autoflash.records.read_device", return_value={"product_model": "s60tpg"}):
            environment = attended_suite.run_environment({"api_enough": True}, "espressif", "12:34:56:ab:cd:ef")
            self.assertEqual(environment["AUTOFLASH_API_ENOUGH"], "S60TPG")
            with mock.patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("AUTOFLASH_API_ENOUGH", None)
                self.assertNotIn("AUTOFLASH_API_ENOUGH", attended_suite.run_environment({}, "espressif", "12:34:56:ab:cd:ef"))

    def test_the_api_takes_links_where_the_scenario_says_and_files_otherwise_in_either_family(self):
        with mock.patch("tools.autoflash.records.read_device", return_value={}), \
                mock.patch.dict("os.environ", {"AUTOFLASH_LOCAL_OFFERED_SEND": "links"}):
            for family in attended_suite.SCENARIOS:
                with self.subTest(family=family):
                    self.assertEqual(attended_suite.run_environment({"api_links": True}, family, None)
                                     ["AUTOFLASH_LOCAL_OFFERED_SEND"], "links")
                    self.assertEqual(attended_suite.run_environment({}, family, None)
                                     ["AUTOFLASH_LOCAL_OFFERED_SEND"], "files")

    def test_a_readout_where_the_api_is_down_goes_where_nothing_answers_with_no_api_started(self):
        from unittest import mock
        scenario = {"command": "readout", "api_down": True}
        with mock.patch("tools.autoflash.records.read_device", return_value={}):
            self.assertEqual(attended_suite.run_environment(scenario, "espressif", "12:34:56:ab:cd:ef")["AUTOFLASH_READOUT_URL"],
                             attended_suite.API_DOWN)
        command = attended_suite.wrapper(61, scenario, "--", "--new", mac="12:34:56:ab:cd:ef")
        self.assertIn("--no-api", command[:command.index("--")])
        self.assertEqual(attended_suite.api_environment({"command": "readout"}), {})
        self.assertNotIn("--no-api", attended_suite.wrapper(61, {"command": "readout"}, mac="12:34:56:ab:cd:ef"))

    def test_an_accounts_other_devices_reach_either_familys_stub(self):
        import json
        import tempfile
        from tools.ewelink.stub_cloud import accounts
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(attended_suite, "RESULTS", Path(tmp)), \
                mock.patch("tools.autoflash.records.read_device", return_value={}):
            for family in attended_suite.SCENARIOS:
                with self.subTest(family=family):
                    environment = attended_suite.run_environment({"account": "mixed"}, family, None)
                    listed = Path(environment["AUTOFLASH_EMU_STUB_OTHERS"])
                    self.assertEqual(json.loads(listed.read_text(encoding="utf-8")), accounts.ACCOUNTS["mixed"])
                    with mock.patch.dict("os.environ", {"AUTOFLASH_EMU_STUB_OTHERS": str(listed)}):
                        self.assertNotIn("AUTOFLASH_EMU_STUB_OTHERS", attended_suite.run_environment({}, family, None))

    def test_the_stub_lists_the_device_under_its_product_on_a_checkout_with_no_runs(self):
        # Seen on a fresh checkout: the stub listed no product, and the readout
        # of readout-then-not-needed was refused by the API (400) for no model.
        from unittest import mock
        with mock.patch("tools.autoflash.records.read_device", return_value={}), \
                mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("AUTOFLASH_EMU_STUB_PRODUCT_MODEL", None)
            environment = attended_suite.run_environment({}, "espressif", "12:34:56:ab:cd:ef")
        self.assertEqual(environment["AUTOFLASH_EMU_STUB_PRODUCT_MODEL"], "S60TPG")



class PrepTests(unittest.TestCase):
    def test_the_run_quit_for_prep_answers_the_question_it_ends_with(self):
        # Seen: it waited at the email question as it ended, eight minutes,
        # until its --timeout, before every scenario with a prep.
        asked = "Get an email as more devices are supported? [Y/n] (q to quit): "
        self.assertTrue(any(re.search(answer["when"], asked) for answer in attended_suite.QUIT_FIRST))


class BekenAgainTests(unittest.TestCase):
    """An S61's scenario can be run again on the device as its first run left
    it, as an S60's can (run_again): its device left running for that, and
    stopped once it is done, or not needed."""

    def run_one(self, first_passes):
        calls = []
        scenario = {"answers": [], "expect": {}, "again": {"answers": [], "expect": {}}}
        with tempfile.TemporaryDirectory() as results, \
                mock.patch.object(attended_suite, "run_beken", lambda *a: calls.append("first") or (0, Path("r"))), \
                mock.patch.object(attended_suite, "run_again_beken",
                                  lambda *a: calls.append("again") or (0, Path("r"))), \
                mock.patch.object(attended_suite, "judged", return_value=[] if first_passes else ["FAIL-RESULT: x"]), \
                mock.patch.object(attended_suite, "readout_verdicts", return_value=[]), \
                mock.patch.object(attended_suite, "stop_beken", lambda n: calls.append("stopped")), \
                mock.patch.object(attended_suite.emulator_stats, "attended_run"):
            attended_suite.run_one(61, "s61-x", "beken", scenario, Path(results), None)
        return calls

    def test_it_is_run_again_then_stopped(self):
        self.assertEqual(self.run_one(True), ["first", "again", "stopped"])

    def test_a_first_run_that_failed_is_not_run_again_and_its_device_is_stopped(self):
        self.assertEqual(self.run_one(False), ["first", "stopped"])

    def test_the_device_is_left_running_only_for_an_again(self):
        stopped = []
        with mock.patch.object(attended_suite.subprocess, "call"), \
                mock.patch.object(attended_suite, "beken_computer", return_value=(0, Path("r"))), \
                mock.patch.object(attended_suite, "stop_beken", stopped.append), \
                tempfile.TemporaryDirectory() as folder:
            attended_suite.run_beken(61, "s61-x", {"answers": [], "again": {"answers": []}}, Path(folder), None)
            self.assertEqual(stopped, [])
            attended_suite.run_beken(61, "s61-x", {"answers": []}, Path(folder), None)
            self.assertEqual(stopped, [61])


class ReadoutVerdictTests(unittest.TestCase):
    """A readout's other side, after its run: what the API beside the emulator
    was asked and kept, or for an api_down scenario, the readout kept whole."""

    WANTED = '2026-11-17T10:00:01.000Z POST /v1/wanted 140 -> 200 {"wanted":true,"held":[]}'
    DIGEST = "a" * 64

    def verdicts(self, scenario, *, requests=(), stored=False, kept=False, whole=(), code=0, family="espressif",
                 offer=()):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state, folder = Path(tmp, "state"), Path(tmp, "results")
            run = state / "work/captures/devices/12-34-56-ab-cd-ef/runs/1/run.log"
            run.parent.mkdir(parents=True)
            folder.mkdir()
            run.write_text("=== run started ===\n[10:00:00] --- Stage 1 - Querying eWeLink for devices\n"
                           "[10:00:09] --- Stage 7 - Saving the readout\n", encoding="utf-8")
            # A conversion prep made first, not a readout of the sitting.
            converted = state / "work/captures/devices/12-34-56-ab-cd-ef/runs/0/run.log"
            converted.parent.mkdir(parents=True)
            converted.write_text("=== run started ===\n[09:00:00] --- Stage 1 - Confirming prerequisites\n",
                                 encoding="utf-8")
            if kept:
                (run.parent / "readout.tar").write_bytes(b"tar")
            (state / "api").mkdir()
            (state / "api/requests.log").write_text("".join(f"{line}\n" for line in requests), encoding="utf-8")
            if stored:
                (state / f"api/readouts__2026-11-17__{self.DIGEST[:24]}.tar").write_bytes(b"tar")
            with mock.patch.object(attended_suite.fault_verdicts, "readout_problems", return_value=list(whole)), \
                    mock.patch.object(attended_suite.fault_verdicts, "own_offer_problems",
                                      return_value=list(offer)) as self.own_offer:
                found = attended_suite.readout_verdicts(scenario, family, state, 0, code, folder, "S60TPG")
            self.copied = (folder / "api/requests.log").exists()
        return [verdict.split(":")[0] for verdict in found]

    def taken(self):
        return f'2026-11-17T10:00:10.000Z POST /v1/readout 9000 -> 201 {{"id":"2026-11-17-{self.DIGEST[:12]}"}}'

    def test_a_readout_taken_whole_by_the_api_passes_and_what_it_kept_is_kept(self):
        readout = {"command": "readout"}
        for family in ("espressif", "beken"):
            with self.subTest(family=family):
                self.assertEqual(self.verdicts(readout, requests=[self.WANTED, self.taken()], stored=True,
                                               family=family), [])
                self.assertTrue(self.copied)

    def test_one_device_chosen_of_the_accounts_is_judged_as_one_readout(self):
        # Seen: a scenario choosing one of the account's devices judged as a
        # readout of all three, FAIL-NOT-ASKED, as any with an account was.
        chosen = {"command": "readout", "account": "mixed",
                  "answers": attended_suite.with_shared([{"when": attended_suite.CHOOSE_READOUT, "type": "2\r"}])}
        self.assertEqual(self.verdicts(chosen, requests=[self.WANTED, self.taken()], stored=True), [])

    def test_every_device_is_read_out_with_all_or_its_first_option_chosen(self):
        def scenario(*answers, **more):
            return {"account": "mixed", "answers": attended_suite.with_shared(
                [{"when": attended_suite.CHOOSE_READOUT, "type": typed} for typed in answers]), **more}
        self.assertTrue(attended_suite.reads_every_device(scenario(args=["--all"])))
        self.assertTrue(attended_suite.reads_every_device(scenario()))   # the shared answer: 1
        self.assertTrue(attended_suite.reads_every_device(scenario("9\r", "1\r")))
        self.assertFalse(attended_suite.reads_every_device(scenario("1\r", "2\r")))
        self.assertFalse(attended_suite.reads_every_device({"answers": attended_suite.with_shared([])}))

    def test_a_readout_the_api_did_not_get_or_keep_whole_is_found(self):
        readout = {"command": "readout"}
        self.assertEqual(self.verdicts(readout), ["FAIL-NOT-ASKED"])
        self.assertEqual(self.verdicts(readout, requests=[self.WANTED]), ["FAIL-NOT-SENT"])
        self.assertEqual(self.verdicts(readout, requests=[self.WANTED, self.taken()]), ["FAIL-NOT-STORED"])
        self.assertEqual(self.verdicts(readout, requests=[self.WANTED, self.taken()], stored=True,
                                       whole=["no flash.bin.xz"]), ["FAIL-READOUT"])
        self.assertEqual(self.verdicts(readout, requests=[self.WANTED, self.taken()], stored=True, kept=True),
                         ["FAIL-KEPT-UNSENT"])

    def test_with_the_api_down_the_readout_is_kept_whole(self):
        down = {"command": "readout", "api_down": True}
        self.assertEqual(self.verdicts(down, kept=True, code=1), [])
        self.assertEqual(self.verdicts(down, code=1), ["FAIL-READOUT"])
        self.assertEqual(self.verdicts(down, kept=True, whole=["no flash.bin.xz"], code=1), ["FAIL-READOUT"])

    def test_a_device_converted_first_or_saved_so_is_read_out_its_first_stage_alone(self):
        self.assertEqual(attended_suite.firmware_at_start({"prep": [["convert", "tasmota"]]}), "tasmota")
        self.assertEqual(attended_suite.firmware_at_start({"start": "openbeken"}), "openbeken")
        self.assertEqual(attended_suite.firmware_at_start({"start": "stub-suite-esphome"}), "esphome")
        self.assertIsNone(attended_suite.firmware_at_start({"start": "fresh", "prep": ["sudo forgets"]}))

    def test_a_scenarios_first_run_is_judged_from_its_apis_start_its_second_from_where_the_first_left_it(self):
        # Seen: an S61's readout judged from where an earlier scenario's API log
        # had ended - cut off its own first requests - as the device's state,
        # API and all, is made anew for each scenario.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp, "state")
            (state / "api").mkdir(parents=True)
            (state / "api/requests.log").write_text("x" * 500, encoding="utf-8")   # an earlier scenario's
            scenario = {"command": "readout", "answers": [], "expect": {}, "again": {"answers": [], "expect": {}}}
            asked = []

            def first(*_args, **_kwargs):
                (state / "api/requests.log").write_text("y" * 120, encoding="utf-8")   # its API anew
                return 0, Path(tmp)

            def again(*_args, **_kwargs):
                with open(state / "api/requests.log", "a", encoding="utf-8") as log:
                    log.write("z" * 80)
                return 0, Path(tmp)
            with mock.patch.object(attended_suite, "state_of", return_value=state), \
                    mock.patch.object(attended_suite, "run_beken", side_effect=first), \
                    mock.patch.object(attended_suite, "run_again_beken", side_effect=again), \
                    mock.patch.object(attended_suite, "judged", return_value=[]), \
                    mock.patch.object(attended_suite, "readout_verdicts",
                                      side_effect=lambda *a, **k: asked.append(k.get("asked", a[8] if len(a) > 8 else 0))
                                      or []), \
                    mock.patch.object(attended_suite.emulator_stats, "attended_run"), \
                    mock.patch.object(attended_suite, "stop_beken"):   # never this computer's instance 61
                attended_suite.run_one(61, "x", "beken", scenario, Path(tmp, "results"), None)
        self.assertEqual(asked, [0, 120])

    def test_a_readout_after_its_own_offer_is_judged_by_that_offer_too(self):
        links = {"command": "readout", "api_links": True}
        requests = [self.WANTED, self.taken()]
        for family in ("espressif", "beken"):
            with self.subTest(family=family):
                self.assertEqual(self.verdicts(links, requests=requests, stored=True, family=family), [])
                self.assertEqual(self.own_offer.call_args.args[1], "S60TPG")
                self.assertEqual(self.verdicts(links, requests=requests, stored=True, family=family,
                                               offer=[("FAIL-OFFER", "none taken")]), ["FAIL-OFFER"])
        self.verdicts({"command": "readout"}, requests=requests, stored=True)
        self.own_offer.assert_not_called()

    def test_a_case_not_tried_is_not_fired_whatever_else_was_found(self):
        self.assertEqual(attended_suite.overall([]), "PASS")
        self.assertEqual(attended_suite.overall(["FAIL-SCREEN: x", "FAIL-OFFER: y"]), "FAIL-SCREEN")
        self.assertEqual(attended_suite.overall(["FAIL-SCREEN: x", "NOT-FIRED: the offer was sent only after"]),
                         "NOT-FIRED")
        self.assertEqual(attended_suite.overall(["NOT-FIRED: again: the offer was sent only after"]), "NOT-FIRED")

    def test_a_conversion_has_no_readout_to_judge(self):
        self.assertEqual(self.verdicts({}), [])


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


def shows_as_a_stage(pattern: re.Pattern[str], texts: list[str]) -> bool:
    """Whether `pattern` finds a stage line, "Stage N - <name>  - Underway",
    with one of the tool's texts as the name."""
    return any(pattern.search(f"Stage {number} - {text}  - Underway")
               for text in texts if "\n" not in text and len(text) < 60 for number in range(1, 21))


class InstanceTests(unittest.TestCase):
    def setUp(self):
        # No times from this checkout's own batches: tests leave captures/ be.
        patcher = mock.patch.object(attended_suite.scenario_times, "times", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_run_uses_one_instance_per_worker_never_two_scenarios_at_once(self):
        found = {f"s60-{i}": ("espressif", {}) for i in range(7)} | {f"s61-{i}": ("beken", {}) for i in range(3)}
        busy, used, lock = set(), [], threading.Lock()

        def run_one(n, name, family, scenario, results, mac, workers, rerun):
            with lock:
                self.assertNotIn(n, busy, f"{name} on {n}, which another scenario is using")
                busy.add(n)
                used.append((family, n))
            time.sleep(0.02)
            with lock:
                busy.discard(n)
            return f"PASS {name}"

        with mock.patch.object(attended_suite, "run_one", run_one), redirect_stdout(io.StringIO()), \
                mock.patch.object(attended_suite.emulator_instances, "running", return_value=set()):
            lines = attended_suite.run_all(list(found), found, Path("no-results"), None, workers=3, first=81)
        self.assertEqual(len(lines), 10)
        self.assertEqual({n for family, n in used if family == "espressif"}, {81, 82, 83})
        self.assertEqual({n for family, n in used if family == "beken"}, {81})


    def test_a_run_refuses_instances_another_run_has_before_any_starts(self):
        found = {"s60-a": ("espressif", {}), "s61-a": ("beken", {})}
        running = {"espressif": set(), "beken": {61}}
        with mock.patch.object(attended_suite, "run_one") as run_one, \
                mock.patch.object(attended_suite.emulator_instances, "running", side_effect=running.get):
            with self.assertRaises(SystemExit) as refused:
                attended_suite.run_all(list(found), found, Path("no-results"), None, workers=2, first=61)
        self.assertIn("beken emulator instance 61", str(refused.exception))
        run_one.assert_not_called()


class BatchTests(unittest.TestCase):
    """A batch run again into its folder carries on, as a fault suite's does;
    one its skip file names is left out; and the scenarios are shared out
    between machines by the fault suites' own helpers (scenario_times)."""

    FOUND = {"a": ("espressif", {}), "b": ("espressif", {}), "c": ("beken", {}), "d": ("espressif", {})}

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        # A batch's folder, where scenario_times reads it: one of several in the suite's.
        self.results = Path(folder.name, "attended", "batch")
        self.results.mkdir(parents=True)
        self.times = attended_suite.scenario_times.times
        for patcher in (mock.patch.object(attended_suite.emulator_instances, "running", return_value=set()),
                        mock.patch.object(attended_suite.emulator_stats, "began"),
                        mock.patch.object(attended_suite.scenario_times, "times", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_all(self, took=None):
        ran = []

        def run_one(n, name, family, scenario, results, mac, workers, rerun):
            ran.append((name, rerun))
            attended_suite.record(results, {"id": name, "verdict": "PASS", "verdicts": [], "seconds": 1})
            return f"PASS {name}"
        with mock.patch.object(attended_suite, "run_one", run_one), redirect_stdout(io.StringIO()), \
                mock.patch.object(attended_suite.scenario_times, "times", return_value=took or {}):
            attended_suite.run_all(list(self.FOUND), self.FOUND, self.results, None, workers=1, first=61)
        return ran

    def test_a_batch_run_again_runs_only_what_has_not_passed_there(self):
        attended_suite.record(self.results, {"id": "a", "verdict": "PASS", "verdicts": [], "seconds": 5})
        attended_suite.record(self.results, {"id": "b", "verdict": "PASS", "verdicts": [], "seconds": 5})
        attended_suite.record(self.results, {"id": "b", "verdict": "FAIL-RESULT", "verdicts": ["FAIL-RESULT: x"],
                                             "seconds": 5})
        self.assertEqual(sorted(self.run_all()), [("b", True), ("c", False), ("d", False)])
        self.assertEqual(self.run_all(), [])

    def test_a_batch_starts_the_longest_first(self):
        self.assertEqual([name for name, _ in self.run_all({"a": 60, "b": 600, "d": 300})], ["b", "d", "a", "c"])

    def test_a_scenario_its_skip_file_names_is_left_out_as_the_batch_runs(self):
        (self.results / "skip.txt").write_text("b\nc\n", encoding="utf-8")
        self.assertEqual(sorted(self.run_all()), [("a", False), ("d", False)])

    def test_a_scenarios_run_is_recorded_in_its_batch_as_it_ends(self):
        with mock.patch.object(attended_suite, "run_espressif", return_value=(1, Path("r"))), \
                mock.patch.object(attended_suite, "judged", return_value=["FAIL-RESULT: exit 1"]), \
                mock.patch.object(attended_suite, "readout_verdicts", return_value=[]), \
                mock.patch.object(attended_suite, "product_of", return_value="S60"), \
                mock.patch.object(attended_suite.time, "monotonic", side_effect=itertools.count(0, 30)), \
                mock.patch.object(attended_suite.emulator_stats, "attended_run") as stats:
            line = attended_suite.run_one(61, "a", "espressif", {"answers": []}, self.results, None, rerun=True)
        row = attended_suite.fault_verdicts.latest(self.results / attended_suite.ROWS)["a"]
        self.assertEqual((row["verdict"], row["verdicts"], row["instance"]), ("FAIL-RESULT", ["FAIL-RESULT: exit 1"], 61))
        self.assertEqual(line, attended_suite.line_of(row))
        self.assertTrue(line.startswith("FAIL-RESULT") and line.endswith("\n    FAIL-RESULT: exit 1"))
        self.assertIs(stats.call_args.kwargs["rerun"], True)
        # Its time read back as the fault suites' are, for the next batch's order and shares.
        self.assertEqual(self.times(self.results.parent), {"a": 30.0})

    def main(self, *argv):
        with mock.patch.object(attended_suite.sys, "argv", ["attended_suite.py", *argv]), \
                mock.patch.object(attended_suite, "scenarios", return_value=self.FOUND), \
                mock.patch.object(attended_suite.emulator_stats, "send", return_value=""), \
                redirect_stdout(io.StringIO()) as said:
            return attended_suite.main(), said.getvalue()

    def test_a_batch_passes_once_every_scenario_given_has_passed_in_its_folder(self):
        (self.results / "names").write_text("a\nc\n", encoding="utf-8")
        attended_suite.record(self.results, {"id": "a", "verdict": "PASS", "verdicts": [], "seconds": 5})
        attended_suite.record(self.results, {"id": "c", "verdict": "FAIL-RESULT", "verdicts": [], "seconds": 5})
        with mock.patch.object(attended_suite, "run_all") as run_all:
            code, _ = self.main("run", "--file", str(self.results / "names"), "--results", str(self.results),
                                 "--device", "12:34:56:ab:cd:ef")
        self.assertEqual(code, 1)
        self.assertEqual(run_all.call_args.args[:3], (["a", "c"], self.FOUND, self.results.resolve()))
        attended_suite.record(self.results, {"id": "c", "verdict": "PASS", "verdicts": [], "seconds": 5})
        with mock.patch.object(attended_suite, "run_all"):
            code, _ = self.main("run", "--file", str(self.results / "names"), "--results", str(self.results),
                                 "--device", "12:34:56:ab:cd:ef")
        self.assertEqual(code, 0)
        # Every scenario's last result in the folder, from before the batch was run again too.
        self.assertEqual((self.results / "results.txt").read_text(encoding="utf-8").split("\n")[0].split()[:2],
                         ["PASS", "a"])

    def test_share_writes_each_machines_share_by_how_long_each_took(self):
        names = self.results / "all.txt"
        names.write_text("a espressif about a\nb espressif\nc beken\n", encoding="utf-8")
        with mock.patch.object(attended_suite.scenario_times, "times", return_value={"a": 600, "b": 300, "c": 300}):
            code, said = self.main("share", str(names), "1", "1")
        self.assertEqual(code, 0)
        self.assertEqual([Path(f"{names}.{n}").read_text(encoding="utf-8") for n in (1, 2)], ["a\n", "b\nc\n"])
        self.assertIn("all.txt.1: 1 scenarios on 1 emulators", said)

    def test_times_come_from_every_batch_here_and_those_kept_from_other_machines(self):
        emulation = self.results.parent.parent
        (emulation / "kept" / "second" / "attended").mkdir(parents=True)
        with mock.patch.object(attended_suite, "RESULTS", self.results.parent):
            self.assertEqual(attended_suite.suite_roots(),
                             [emulation / "attended", emulation / "kept" / "second" / "attended"])


class SharedAnswerTests(unittest.TestCase):
    def test_each_answers_a_question_the_tool_asks_and_need_not_be_asked(self):
        # Optional, so the check on every answer passes them by: a question
        # reworded must still fail here, not leave every run to hang at it.
        texts = question_texts()
        # A yes-or-no hint ("Y/n") is put in its brackets as the question is
        # asked (prompts.ask_yes_no, signup.ask_on_exit).
        texts += [f"{opening}{text}{closing}" for text in texts if re.fullmatch("[Yy]/[Nn]", text)
                  for opening, closing in ("()", "[]")]
        for answer in attended_suite.SHARED_ANSWERS:
            with self.subTest(when=answer["when"]):
                self.assertTrue(answer.get("optional"))
                self.assertTrue(any(re.search(answer["when"], text) for text in texts),
                                f"nothing the tool shows says {answer['when']!r}")

    def test_a_named_question_is_matched_by_its_words_and_anything_where_it_is_filled_in(self):
        asked = attended_suite.asked
        self.assertEqual(asked("Type {} to go ahead"), "Type .+ to go ahead")
        self.assertEqual(asked("eWeLink password for {}"), "eWeLink password for")
        self.assertEqual(asked("This computer's password (for sudo)"), "This computer's password \\(for sudo\\)")
        self.assertTrue(re.search(asked("Type {} to go ahead"), "Type PROCEED to go ahead"))
        self.assertFalse(re.search(asked("Done? (y/n)"), "Done! (y/n)"))

    def test_a_scenario_answering_a_shared_question_itself_says_it_as_the_shared_one_does(self):
        # Else its answer is not taken in the shared one's place (with_shared):
        # both are typed, and its own count and checks no longer hold.
        from tools.autoflash import cli, context, prompts, readout, signup
        from tools.autoflash.routes import source
        shown = [source.CHOOSE, readout.CHOOSE, prompts.SUDO_PASSWORD, f"{prompts.PRESS_ANY_KEY} to continue",
                 prompts.GO_AHEAD.format("PROCEED"), context.WHICH_ACCOUNT, context.EWELINK_ACCOUNT,
                 context.EWELINK_PASSWORD.format("someone@example.invalid"), cli.WHAT_NEXT, signup.QUESTION]
        shared = [answer["when"] for answer in attended_suite.SHARED_ANSWERS[:len(shown)]]
        for folder in attended_suite.SCENARIOS.values():
            for path in sorted(folder.glob("*.json")):
                scenario = json.loads(path.read_text(encoding="utf-8"))
                for run in (scenario, *([scenario["again"]] if "again" in scenario else [])):
                    for answer in run.get("answers", []):
                        for question, when in zip(shown, shared):
                            if re.search(answer["when"], question):
                                with self.subTest(scenario=path.stem, question=question):
                                    self.assertEqual(answer["when"], when)

    def test_a_scenarios_own_answer_to_a_question_replaces_the_shared_one(self):
        # Its count and checks hold: one "What next" answered once, a second
        # is unanswered, not quit by the shared one.
        own = [{"when": "What next", "type": "1\r"}]
        answers = attended_suite.with_shared(own)
        self.assertEqual(answers[0], own[0])
        self.assertEqual([answer for answer in answers if answer["when"] == "What next"], own)
        self.assertEqual(answers[1:], [answer for answer in attended_suite.SHARED_ANSWERS
                                       if answer["when"] != "What next"])

    def test_every_run_of_a_scenario_has_them(self):
        for name, (_, scenario) in attended_suite.scenarios().items():
            for run in (scenario, *([scenario["again"]] if "again" in scenario else [])):
                with self.subTest(scenario=name):
                    asked = {answer["when"] for answer in run["answers"]}
                    self.assertLessEqual({answer["when"] for answer in attended_suite.SHARED_ANSWERS}, asked)

    def test_no_scenario_file_copies_one(self):
        # A copy would hold the old words when a question is reworded.
        for folder in attended_suite.SCENARIOS.values():
            for path in sorted(folder.glob("*.json")):
                scenario = json.loads(path.read_text(encoding="utf-8"))
                for run in (scenario, *([scenario["again"]] if "again" in scenario else [])):
                    own = run.get("answers", [])
                    for answer in own:
                        with self.subTest(scenario=path.stem, when=answer["when"]):
                            alone = [other for other in own if other["when"] == answer["when"]] == [answer]
                            self.assertFalse(alone and answer in attended_suite.SHARED_ANSWERS,
                                             "attended_suite.SHARED_ANSWERS answers it already")


class ScenarioTests(unittest.TestCase):
    def test_every_answer_still_answers_a_question_the_tool_asks(self):
        # A question reworded fails here, in a second, not as FAIL-UNANSWERED
        # after an hour in the emulator. Optional answers answer what may not
        # be asked.
        texts = question_texts()
        found = attended_suite.scenarios()
        self.assertGreater(len(found), 8)
        for name, (_, scenario) in found.items():
            for answer in scenario["answers"] + scenario.get("again", {}).get("answers", []):
                with self.subTest(scenario=name, when=answer["when"]):
                    self.assertLessEqual(set(answer), {"when", "type", "times", "optional", "anywhere", "power",
                                                       "signal", "router", "wifi", "after", "shows", "never"})
                    for wanted in answer.get("shows", []) + answer.get("never", []):
                        re.compile(wanted)
                    if "after" in answer:
                        self.assertTrue(answer.get("anywhere"), "after: for an answer typed anywhere")
                    # Each does one thing: types, switches the device or the
                    # router, moves the computer's Wi-Fi, or signals the run.
                    self.assertEqual(sum(key in answer for key in ("type", "power", "signal", "router", "wifi")), 1,
                                     answer)
                    self.assertIn(answer.get("power", "off"), ("off", "on"))
                    self.assertIn(answer.get("signal", "TERM"), ("TERM", "HUP", "INT"))
                    self.assertRegex(answer.get("router", "off"), r"^(off|on( \S{8,63})?)$")
                    self.assertRegex(answer.get("wifi", "home"), r"^(home|join( \S+)?)$")
                    pattern = re.compile(answer["when"])
                    if answer.get("optional"):
                        continue
                    # A key typed, or the device switched, anywhere still waits
                    # for a line the run must show: a note, or a stage line as
                    # output draws it.
                    self.assertTrue(any(pattern.search(text) for text in texts)
                                    or answer.get("anywhere") and shows_as_a_stage(pattern, texts),
                                    f"{name}: nothing the tool shows says {answer['when']!r}")

    def test_a_scenario_is_in_the_cell_of_its_family_and_command(self):
        # Its situations each in every cell, or NOT_EVERYWHERE says why not:
        # tests/repo/test_suite_coverage.py.
        self.assertEqual(attended_suite.cell("beken", {"command": "readout"}), ("readout", "beken"))
        self.assertEqual(attended_suite.cell("espressif", {}), ("conversion", "espressif"))
        self.assertEqual(set(attended_suite.SCENARIOS), set(suite_coverage.FAMILIES))

    def test_a_key_one_family_alone_takes_is_said_by_that_familys_scenarios_alone(self):
        for name, (family, scenario) in attended_suite.scenarios().items():
            for key in scenario:
                if key in attended_suite.ONE_FAMILY_KEYS:
                    with self.subTest(scenario=name, key=key):
                        self.assertEqual(family, attended_suite.ONE_FAMILY_KEYS[key][0])

    def test_both_families_runners_take_a_scenarios_settings_from_one_place(self):
        # So a setting - api_enough, api_down, again, start - reaches both.
        import ast
        tree = ast.parse(Path(attended_suite.__file__).read_text(encoding="utf-8"))
        calls = {node.name: {call.func.id for call in ast.walk(node)
                             if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}
                 for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("api_options", calls["wrapper"])
        self.assertIn("api_options", calls["beken_computer"])
        for runner, shared in (("run_espressif", {"run_environment", "wrapper"}),
                               ("run_again", {"run_environment", "wrapper"}),
                               ("run_beken", {"run_environment", "beken_computer"}),
                               ("run_again_beken", {"run_environment", "beken_computer"})):
            with self.subTest(runner=runner):
                self.assertLessEqual(shared, calls[runner])

    def test_every_scenario_says_what_it_shows_and_how_it_ends(self):
        for name, (_, scenario) in attended_suite.scenarios().items():
            with self.subTest(scenario=name):
                self.assertTrue(scenario.get("about"))
                self.assertIn("expect", scenario)
                self.assertLessEqual(set(scenario), set(attended_suite.KEYS))
                self.assertTrue(scenario.get("covers"), "say which situations it covers (attended_suite.NOT_EVERYWHERE)")
                for expect in (scenario["expect"], scenario.get("again", {}).get("expect", {})):
                    self.assertLessEqual(set(expect), {"exit", "result", "screen", "shows", "never"})
                    for wanted in expect.get("shows", []) + expect.get("never", []):
                        re.compile(wanted)
                for action in scenario.get("prep", []):
                    self.assertIn(action if isinstance(action, str) else action[0],
                                  ("sudo forgets", "tasmota password", "locked esphome", "convert"))
                self.assertEqual(json.loads(json.dumps(scenario)), scenario)


if __name__ == "__main__":
    unittest.main()
