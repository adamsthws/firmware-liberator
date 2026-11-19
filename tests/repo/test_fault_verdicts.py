"""What both fault suites judge a run by beyond its steps: the report's
end-of-run checks, and the readout a readout keeps."""

import hashlib
import io
import json
import tarfile
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from tools.autoflash import readout, report, report_upload
from tools.ewelink.offered_firmware import Offered
from tools.repo import fault_verdicts


def run_log(*lines, before=""):
    """A run log as the tool writes one: each line with its time first."""
    return before + "".join(f"[10:00:0{n}] {line}\n" for n, line in
                            enumerate(["=== run started, tool version 2.3.0 ===", *lines, "=== run ended ==="]))


class EndChecksTests(unittest.TestCase):
    def test_a_relay_that_switched_passes_on_every_firmware(self):
        log = run_log("    Relay: Switches (meter confirms)", "    Checks passed: True")
        for firmware in ("tasmota", "esphome", "openbeken", "sonoff"):
            self.assertIsNone(fault_verdicts.checks_problem(log, firmware), firmware)

    def test_checks_that_failed_say_what_the_relay_check_said(self):
        # As every Espressif run back to Sonoff's firmware ended before 83c517d:
        # the relay asked through eWeLink before the device was there.
        log = run_log("    Relay: Could not be checked", "    Device model:   S60TPG", "    Checks passed: False")
        self.assertEqual(fault_verdicts.checks_problem(log, "sonoff"),
                         "Checks passed: False; Relay: Could not be checked")

    def test_checks_never_run_fail_only_where_the_emulator_can_run_them(self):
        log = run_log("    Checks passed: Not run")
        self.assertIsNone(fault_verdicts.checks_problem(log, "sonoff"))   # no eWeLink device id kept
        for firmware in ("tasmota", "esphome", "openbeken"):
            self.assertEqual(fault_verdicts.checks_problem(log, firmware),
                             "Checks passed: Not run; Relay: not said", firmware)

    def test_a_metered_device_needs_its_meter_to_confirm_the_switch(self):
        """As the Beken suite asks: its emulated S61 measures a load while the
        relay is on (tools/beken/emulator/board.py)."""
        unchanged = run_log("    Relay: Switches (meter unchanged)", "    Checks passed: True")
        for firmware in ("esphome", "openbeken"):
            self.assertIsNone(fault_verdicts.checks_problem(unchanged, firmware), firmware)
            self.assertEqual(fault_verdicts.checks_problem(unchanged, firmware, metered=True),
                             "the meter did not confirm the switch; Relay: Switches (meter unchanged)")
        confirms = run_log("    Relay: Switches (meter confirms)", "    Checks passed: True")
        self.assertIsNone(fault_verdicts.checks_problem(confirms, "openbeken", metered=True))
        # Back on Sonoff's firmware the relay is switched through eWeLink, and no meter is read.
        through = run_log("    Relay: Switches (through eWeLink)", "    Checks passed: True")
        self.assertIsNone(fault_verdicts.checks_problem(through, "sonoff", metered=True))

    def test_a_run_that_kept_its_checks_is_judged_by_them_not_its_words(self):
        reported = run_log("    Relay: Switches (meter confirms)", "    Checks passed: Pass",
                           "    Result: Converted")
        def outcome(answer, relay="switches (meter confirms)", confirms=True):
            return {"result": "converted", "checks": {"answer": answer, "relay": relay, "meter_confirms": confirms}}
        self.assertEqual(fault_verdicts.checks_problem(reported, "esphome", outcome=outcome("failed", "did not switch")),
                         "Checks passed: Fail; Relay: did not switch")
        self.assertIsNone(fault_verdicts.checks_problem(reported, "sonoff", outcome=outcome("not-run", None, None)),
                          "back on Sonoff's with no device id kept, as by the words")
        self.assertEqual(fault_verdicts.checks_problem(reported, "openbeken", metered=True,
                                                       outcome=outcome("passed", "switches (meter unchanged)", False)),
                         "the meter did not confirm the switch; Relay: switches (meter unchanged)")
        self.assertIsNone(fault_verdicts.checks_problem(reported, "openbeken", metered=True, outcome=outcome("passed")))
        # A run that made no report wrote no outcome of its own: an earlier one's is not it.
        self.assertIsNone(fault_verdicts.checks_problem(run_log("    Stage 9 - Checking ESPHome"), "esphome",
                                                        outcome=outcome("failed")))

    def test_only_the_last_run_counts(self):
        earlier = run_log("    Relay: Did not switch", "    Checks passed: False")
        self.assertIsNone(fault_verdicts.checks_problem(
            run_log("    Checks passed: True", before=earlier), "tasmota"))
        self.assertIsNotNone(fault_verdicts.checks_problem(
            run_log("    Checks passed: False", before=run_log("    Checks passed: True")), "tasmota"))

    def test_the_report_is_read_as_the_tool_lays_it_out(self):
        fields = [("Result", "Converted"), ("Device Wi-Fi signal", "-60 dBm"), ("Checks passed", "False")]
        log = run_log("    Relay: Did not switch", *report.report_screen(fields))
        self.assertEqual(fault_verdicts.checks_problem(log, "tasmota"), "Checks passed: False; Relay: Did not switch")

    def test_a_run_with_no_report_has_nothing_to_judge(self):
        self.assertIsNone(fault_verdicts.checks_problem(run_log("Nothing to convert."), "tasmota"))
        self.assertIsNone(fault_verdicts.checks_problem("", "tasmota"))


FLASH = b"\xff" * fault_verdicts.WHOLE_FLASH
OFFERED = [Offered("user1.bin", "1.2.0", b"stock app")]


def kept(family, flash=FLASH, files=None, updates=OFFERED, matches=True, **manifest):
    """A readout.tar as the tool packs one (readout.package) for `family`;
    `matches`, an Espressif read's own hash check."""
    whole = {"espressif": {"flash.bin": flash, "efuse.bin": b"efuse", "info.txt": b"info", "report.json": b"{}"},
             "beken": {"flash.bin": flash, "info.txt": b"{}"}}[family]
    flash_entry = {"size": len(flash), "sha256": "ab"} | ({"matches_device": matches} if family == "espressif" else {})
    return readout.package({"format": 1, "read": True, "flash": flash_entry, **manifest},
                           whole if files is None else files, updates)


class RunEventsTests(unittest.TestCase):
    """A run followed by its events (output.EVENTS), as both suites follow it."""

    @staticmethod
    def events(*names):
        """Events by name; "switch-now" an ask to switch the device off and on then."""
        return [{"event": "switch", "now": True} if name == "switch-now" else {"event": name} for name in names]

    def test_the_last_runs_events_are_from_its_start(self):
        found = self.events("run", "began", "ended", "run", "began")
        self.assertEqual(fault_verdicts.last_run_events(found), found[3:])
        self.assertEqual(fault_verdicts.last_run_events(found[1:2]), found[1:2])   # its start not kept
        self.assertEqual(fault_verdicts.last_run_events([]), [])

    def test_advice_to_switch_counts_as_the_run_stopped_and_not_asked_for_then(self):
        self.assertTrue(fault_verdicts.advised_to_switch(self.events("run", "began", "switch", "ended")))
        self.assertTrue(fault_verdicts.advised_to_switch(self.events("run", "switch")))   # a readout: no steps
        self.assertFalse(fault_verdicts.advised_to_switch(self.events("run", "switch", "began", "ended")))
        self.assertFalse(fault_verdicts.advised_to_switch(self.events("run", "began", "switch-now")))


class UnexpectedTests(unittest.TestCase):
    def test_an_error_nobody_foresaw_by_its_words_or_its_runs_own_outcome(self):
        said = "[10:00:00] FAILED: something unexpected went wrong: KeyError: 'safeboot'\n"
        self.assertTrue(fault_verdicts.unexpected([said], []))
        self.assertTrue(fault_verdicts.unexpected(["reworded"], [{"stopped": {"step": "K2", "error": "crash"}}]))
        self.assertFalse(fault_verdicts.unexpected(["[10:00:00] FAILED: the delivery stopped part way"],
                                                   [{"stopped": {"step": "J2", "error": "upload-cut"}},
                                                    {"stopped": None}]))


class RunOutcomesTests(unittest.TestCase):
    def test_each_runs_outcome_since_the_scenario_began_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            captures = Path(tmp)
            for folder, lines in (("devices/12-34-56-ab-cd-ef/runs/20261114-180016",
                                   ['{"at": 50.0, "result": "converted", "stopped": null}',
                                    '{"at": 100.0, "result": "failed", "stopped": {"step": "K2", "error": "network-join"}}',
                                    "not json", '{"at": 300.0, "result": "converted", "stopped": null}']),
                                  ("devices/unidentified/runs/20261114-181000",
                                   ['{"at": 200.0, "result": "crashed", "stopped": {"step": "K2", "error": "crash"}}'])):
                (captures / folder).mkdir(parents=True)
                (captures / folder / report_upload.OUTCOMES).write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual([said["result"] for said in fault_verdicts.run_outcomes(captures, since=60.0)],
                             ["failed", "crashed", "converted"], "the saved device's own run, before it, left out")
            self.assertEqual(fault_verdicts.run_outcomes(captures / "none", since=0), [])


class ReadoutTests(unittest.TestCase):
    def test_a_whole_readout_of_each_family_lacks_nothing(self):
        for family in ("espressif", "beken"):
            self.assertEqual(fault_verdicts.readout_problems(kept(family), family), [], family)

    def test_each_file_docs_readouts_lists_must_be_there(self):
        self.assertEqual(fault_verdicts.readout_problems(kept("espressif", files={"flash.bin": FLASH}), "espressif"),
                         ["no efuse.bin.xz", "no info.txt", "no report.json"])
        self.assertEqual(fault_verdicts.readout_problems(kept("beken", updates=()), "beken"),
                         ["no offered firmware"])

    def test_the_whole_flash_read(self):
        part = kept("beken", flash=FLASH[:0x100000])
        self.assertEqual(fault_verdicts.readout_problems(part, "beken"),
                         ["flash.bin is 1,048,576 bytes, the manifest says 1048576: not the whole 4,194,304"])
        self.assertEqual(fault_verdicts.readout_problems(kept("espressif", matches=None), "espressif"),
                         ["the flash read does not match the device's own hash"])

    def test_read_unsent_and_with_no_contact(self):
        self.assertEqual(fault_verdicts.readout_problems(kept("beken", read=False), "beken"),
                         ["the manifest says the memory was not read"])
        self.assertEqual(fault_verdicts.readout_problems(
            readout.package({"format": 1, "read": True, "flash": {"size": len(FLASH)},
                             "contact": {"email": "someone@example.com"}},
                            {"flash.bin": FLASH, "info.txt": b"{}"}, OFFERED),
            "beken"), ["a contact in it, from an unattended run"])

    def test_the_manifest_first_as_the_api_reads_it(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(kept("beken"))) as tar, tarfile.open(fileobj=buffer, mode="w") as out:
            for member in reversed(tar.getmembers()):
                out.addfile(member, tar.extractfile(member))
        self.assertEqual(fault_verdicts.readout_problems(buffer.getvalue(), "beken"), ["manifest.json is not first"])

    def test_where_the_api_takes_links_the_offered_firmware_is_named_by_link_or_as_held(self):
        for entry in ({"file": None, "version": "1.2.0", "sha256": "a" * 64, "link": "/ota/rom/x/user1.bin"},
                      {"file": None, "version": "1.2.0", "sha256": "a" * 64, "held": True}):
            with self.subTest(entry=entry):
                self.assertEqual(fault_verdicts.readout_problems(kept("espressif", updates=(), offered=[entry]),
                                                                 "espressif"), [])
        self.assertEqual(fault_verdicts.readout_problems(kept("espressif", updates=(), offered=[
            {"file": None, "version": "1.2.0", "sha256": "a" * 64}]), "espressif"), ["no offered firmware"])
        self.assertEqual(fault_verdicts.readout_problems(kept("espressif", offered=[
            {"file": "offered/user2.bin", "version": "1.2.0", "sha256": "a" * 64}]), "espressif"),
            ["offered/user2.bin named, not in it"])

    def test_something_not_a_readout_says_so(self):
        self.assertTrue(fault_verdicts.readout_problems(b"not a tar", "beken")[0].startswith("not a tar"))


class OwnOfferTests(unittest.TestCase):
    """A readout sent after the offer its run sent of the device, the API
    taking links (the attended suite's api_links): judged by the API's own
    answers and store (own_offer_problems)."""

    FILE = b"the stub's offered file"
    SHA = hashlib.sha256(FILE).hexdigest()
    ASKED = '2026-11-18T10:00:00.000Z POST /v1/offers 101 -> 200 {"send":"links","offers":[]}'
    TAKEN = '2026-11-18T10:00:02.000Z POST /v1/offers 455 -> 200 {"send":"links","offers":[{"result":"taken"}]}'
    HAVE = '2026-11-18T10:00:02.000Z POST /v1/offers 455 -> 200 {"send":"links","offers":[{"result":"have"}]}'
    WANTED = '2026-11-18T10:00:30.000Z POST /v1/wanted 306 -> 200 {"wanted":true,"held":[],"send":"links"}'
    HELD = '2026-11-18T10:00:30.000Z POST /v1/wanted 306 -> 200 {"wanted":false,"why":"held","held":[],"send":"links"}'
    SENT = '2026-11-18T10:01:00.000Z POST /v1/readout 9000 -> 201 {"id":"2026-11-18-0123456789ab"}'

    def problems(self, requests, offer=True, model="S60TPF", held=FILE, readout_sha=None):
        """own_offer_problems' verdicts, the store holding the offer (`offer`,
        of `model`), its file as `held` (None for not fetched) and, with
        `readout_sha`, a whole readout naming that file too."""
        with tempfile.TemporaryDirectory() as tmp:
            api = Path(tmp)
            (api / "requests.log").write_text("".join(line + "\n" for line in requests), encoding="utf-8")
            if offer:
                (api / "readouts__2026-11-18__900a1374a6e679bc71fb0396.json").write_text(json.dumps(
                    {"format": 1, "cloud": "ewelink", "model": model, "read": False, "offered": [
                        {"name": "user1.bin", "version": "1.2.0", "sha256": self.SHA, "link": "/ota/rom/x/user1.bin"}]}),
                    encoding="utf-8")
                (api / "readouts__2026-11-18__900a1374a6e679bc71fb0396.json.meta.json").write_text("{}", encoding="utf-8")
            if held is not None:
                (api / f"readouts__firmware__{self.SHA}").write_bytes(held)
            if readout_sha:
                (api / "readouts__2026-11-18__0123456789abcdef01234567.tar").write_bytes(kept("espressif", offered=[
                    {"file": None, "version": "1.2.0", "sha256": readout_sha, "link": "/ota/rom/y/user2.bin"}]))
            return [verdict for verdict, _ in fault_verdicts.own_offer_problems(api, "S60TPF")]

    def test_the_offer_taken_and_fetched_first_then_the_readout_wanted_passes(self):
        self.assertEqual(self.problems([self.ASKED, self.TAKEN, self.WANTED, self.SENT]), [])
        self.assertEqual(self.problems([self.ASKED, self.TAKEN, self.WANTED, self.SENT], model="s60tpf"), [])

    def test_an_offer_not_sent_or_not_taken(self):
        self.assertEqual(self.problems([self.WANTED, self.SENT], offer=False, held=None), ["FAIL-OFFER"])
        self.assertEqual(self.problems([self.ASKED, self.HAVE, self.WANTED, self.SENT], offer=False, held=None),
                         ["FAIL-OFFER"])

    def test_an_offer_sent_after_the_readout_asked_has_not_tried_the_case(self):
        # Not fired, as a fault that never happened: the offer's own checks left out.
        self.assertEqual(self.problems([self.ASKED, self.WANTED, self.TAKEN, self.SENT]), ["NOT-FIRED"])
        self.assertEqual(self.problems([self.ASKED, self.WANTED, self.SENT, self.HAVE], offer=False, held=None),
                         ["NOT-FIRED"])
        # The empty question whether to send links, asked first, is no offer.
        self.assertEqual(self.problems([self.ASKED, self.WANTED, self.SENT]), ["FAIL-OFFER"])

    def test_the_readout_not_wanted_after_its_own_offer(self):
        # As THEIRS once took the sender's own first stage for a repeat.
        self.assertEqual(self.problems([self.ASKED, self.TAKEN, self.HELD]), ["FAIL-NOT-WANTED"])
        failed = '2026-11-18T10:00:30.000Z POST /v1/wanted 306 -> 500 {"error":"x"}'
        self.assertEqual(self.problems([self.ASKED, self.TAKEN, failed]), ["FAIL-NOT-WANTED"])

    def test_the_offer_not_in_the_store_or_of_another_device(self):
        self.assertEqual(self.problems([self.ASKED, self.TAKEN, self.WANTED, self.SENT], offer=False), ["FAIL-OFFER"])
        self.assertEqual(self.problems([self.ASKED, self.TAKEN, self.WANTED, self.SENT], model="S61STPF"),
                         ["FAIL-OFFER"])

    def test_each_file_the_offer_or_the_readout_names_is_fetched_and_is_that_file(self):
        requests = [self.ASKED, self.TAKEN, self.WANTED, self.SENT]
        self.assertEqual(self.problems(requests, held=None), ["FAIL-OFFER"])
        self.assertEqual(self.problems(requests, held=b"another file"), ["FAIL-OFFER"])
        self.assertEqual(self.problems(requests, readout_sha=self.SHA), [])
        self.assertEqual(self.problems(requests, readout_sha="b" * 64), ["FAIL-OFFER"])


class ReadoutApiTests(unittest.TestCase):
    """What a readout sent to the API beside the emulator did, judged by what
    the API was asked and kept (requests.log, its store): a readout of every
    device in the account, or of the device alone."""

    DEVICE = "BASICR4"
    RUN = ("[10:00:00] Reading out 'Test device 1' (TESTLIGHT) (1 of 3)...\n"
           "[10:00:20] Reading out 'Test device 2' (TESTLIGHT) (2 of 3)...\n"
           "[10:00:30] Reading out 'Device (stub)' (BASICR4) (3 of 3)...\n")
    WANTED = '2026-11-09T10:00:01.000Z POST /v1/wanted 140 -> 200 {"wanted":true,"held":[]}'
    HELD = '2026-11-09T10:00:21.000Z POST /v1/wanted 140 -> 200 {"wanted":false,"why":"held","held":[]}'
    LIMITED = '2026-11-09T10:00:40.000Z POST /v1/readout 9000 -> 429 {"error":"too many readouts at once"}'

    def taken(self, digest):
        return f'2026-11-09T10:00:10.000Z POST /v1/readout 9000 -> 201 {{"id":"2026-11-09-{digest[:12]}"}}'

    def problems(self, requests, run=RUN, finished=True, stored=(), whole=(), kept=(), account=3,
                 family="espressif"):
        """readout_api_problems' verdicts of one run, the API's store holding
        `stored` (digests), the device's readout lacking `whole`."""
        with tempfile.TemporaryDirectory() as tmp:
            api = Path(tmp)
            (api / "requests.log").write_text("".join(line + "\n" for line in requests), encoding="utf-8")
            for digest in stored:
                (api / f"readouts__2026-11-09__{digest[:24]}.tar").write_bytes(b"tar")
            with mock.patch.object(fault_verdicts, "readout_problems", return_value=list(whole)) as judged:
                problems, summary = fault_verdicts.readout_api_problems(run, finished, api, account, self.DEVICE,
                                                                        family, kept)
            self.judged = [call.args[1] for call in judged.call_args_list]
        return [verdict for verdict, _ in problems], summary

    A, B = "a" * 64, "b" * 64

    def test_every_device_asked_each_wanted_one_taken_and_kept(self):
        verdicts, summary = self.problems([self.WANTED, self.taken(self.A), self.HELD, self.WANTED, self.LIMITED,
                                           self.LIMITED, self.taken(self.B)], stored=(self.A, self.B))
        self.assertEqual(verdicts, [])
        self.assertEqual((summary["asked"], summary["wanted"], summary["taken"], summary["429"]), (3, 2, 2, 2))
        self.assertTrue(summary["device_whole"])

    def test_a_device_not_asked(self):
        self.assertEqual(self.problems([self.WANTED, self.taken(self.A), self.HELD], stored=(self.A,))[0],
                         ["FAIL-NOT-ASKED"])
        # Nor read out at all: the account's three, two of them.
        run = self.RUN.rsplit("[10:00:30]", 1)[0]
        self.assertEqual(self.problems([self.WANTED, self.taken(self.A), self.HELD], run=run, stored=(self.A,))[0],
                         ["FAIL-NOT-ASKED"])

    def test_a_device_not_wanted_but_sent(self):
        verdicts, _ = self.problems([self.WANTED, self.taken(self.A), self.HELD, self.taken(self.B), self.WANTED,
                                     self.taken(self.B)], stored=(self.A, self.B))
        self.assertEqual(verdicts, ["FAIL-SENT-UNWANTED"])

    def test_a_wanted_device_never_sent_or_never_taken(self):
        self.assertEqual(self.problems([self.WANTED, self.HELD, self.WANTED, self.taken(self.B)],
                                       stored=(self.B,))[0], ["FAIL-NOT-SENT"])
        self.assertEqual(self.problems([self.WANTED, self.taken(self.A), self.HELD, self.WANTED] + [self.LIMITED] * 7,
                                       stored=(self.A,))[0], ["FAIL-NOT-SENT"])

    def test_a_stopped_run_is_let_off_the_device_it_stopped_on(self):
        requests = [self.WANTED, self.taken(self.A), self.HELD, self.WANTED]
        self.assertEqual(self.problems(requests, finished=False, stored=(self.A,))[0], [])
        self.assertEqual(self.problems(requests, finished=True, stored=(self.A,))[0], ["FAIL-NOT-SENT"])

    def test_an_answer_the_tool_could_not_read_is_wanted(self):
        failed = '2026-11-09T10:00:01.000Z POST /v1/wanted 140 -> 500 {"error":"x"}'
        self.assertEqual(self.problems([failed, self.HELD, self.WANTED, self.taken(self.B)], stored=(self.B,))[0],
                         ["FAIL-NOT-SENT"])

    def test_one_taken_but_not_in_the_store_or_the_devices_not_whole(self):
        requests = [self.WANTED, self.taken(self.A), self.HELD, self.WANTED, self.taken(self.B)]
        self.assertEqual(self.problems(requests, stored=(self.A,))[0], ["FAIL-NOT-STORED"])
        self.assertEqual(self.problems(requests, stored=(self.A, self.B), whole=["no efuse.bin.xz"])[0],
                         ["FAIL-READOUT"])

    def test_one_kept_unsent(self):
        requests = [self.WANTED, self.taken(self.A), self.HELD, self.WANTED, self.taken(self.B)]
        self.assertEqual(self.problems(requests, stored=(self.A, self.B), kept=["readout-1000000002.tar"])[0],
                         ["FAIL-KEPT-UNSENT"])

    def test_a_converted_devices_readout_is_judged_as_its_first_stage_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = Path(tmp)
            (api / "requests.log").write_text(f"{self.WANTED}\n{self.taken(self.A)}\n", encoding="utf-8")
            (api / f"readouts__2026-11-09__{self.A[:24]}.tar").write_bytes(b"tar")
            with mock.patch.object(fault_verdicts, "readout_problems", return_value=["not whole"]), \
                    mock.patch.object(fault_verdicts, "first_stage_problems", return_value=[]) as first:
                problems, _ = fault_verdicts.readout_api_problems("", True, api, 1, self.DEVICE, "espressif",
                                                                  runs="tasmota")
        self.assertEqual(problems, [])
        self.assertEqual(first.call_args.args, (b"tar", "tasmota"))

    def test_a_readout_of_the_device_alone_is_judged_as_the_one_device(self):
        # The attended suite's, and each fault suite's readout sent to the API:
        # no "Reading out" line names it, and the device is the one judged whole.
        for family in ("espressif", "beken"):
            with self.subTest(family=family):
                verdicts, summary = self.problems([self.WANTED, self.taken(self.A)], run="", stored=(self.A,),
                                                  account=1, family=family)
                self.assertEqual(verdicts, [])
                self.assertTrue(summary["device_whole"])
                self.assertEqual(self.judged, [family])
        self.assertEqual(self.problems([self.WANTED, self.taken(self.A)], run="", stored=(self.A,), account=1,
                                       whole=["no flash.bin.xz"])[0], ["FAIL-READOUT"])
        self.assertEqual(self.problems([], run="", account=1)[0], ["FAIL-NOT-ASKED"])
        self.assertEqual(self.problems([], run="", account=1, finished=False)[0], [])
        # Not needed (the API has enough of it): asked, and rightly not sent.
        self.assertEqual(self.problems([self.HELD], run="", account=1)[0], [])
        self.assertEqual(self.problems([self.HELD, self.taken(self.A)], run="", stored=(self.A,), account=1)[0],
                         ["FAIL-SENT-UNWANTED"])


class FirstStageTests(unittest.TestCase):
    """A readout of a device on another firmware: its first stage alone."""

    def first(self, files=None, updates=OFFERED, **manifest):
        return readout.package({"format": 1, "read": False, "runs": "tasmota", **manifest}, files, updates)

    def test_the_offered_firmware_alone_lacks_nothing(self):
        self.assertEqual(fault_verdicts.first_stage_problems(self.first(), "tasmota"), [])

    def test_a_memory_read_or_another_firmware_named_is_wrong(self):
        self.assertEqual(fault_verdicts.first_stage_problems(kept("espressif", runs="tasmota"), "tasmota"), [
            "flash.bin.xz in it, from a device not on Sonoff's firmware",
            "efuse.bin.xz in it, from a device not on Sonoff's firmware",
            "info.txt in it, from a device not on Sonoff's firmware",
            "report.json in it, from a device not on Sonoff's firmware",
            "the manifest does not say the memory was not read"])
        self.assertEqual(fault_verdicts.first_stage_problems(self.first(runs="esphome"), "tasmota"),
                         ["the manifest says it runs esphome, not tasmota"])
        self.assertEqual(fault_verdicts.first_stage_problems(self.first(updates=()), "tasmota"),
                         ["no offered firmware"])

    def test_something_not_a_readout_says_so(self):
        self.assertTrue(fault_verdicts.first_stage_problems(b"not a tar", "tasmota")[0].startswith("not a tar"))


class ScreenTests(unittest.TestCase):
    """Each run's screen, as the fault suites keep it, is looked at."""

    RULE = "=" * 72

    def recorded(self, *rows):
        return (f"--- run\nThe emulated device is up; converting it unattended...\n{self.RULE}\n"
                f"The screen at the end of the run (100x40):\n{self.RULE}\n" + "".join(f"{row}\n" for row in rows)
                + f"{self.RULE}\nExit code 0 - finished\n")

    def test_every_run_s_screen_is_looked_at_colours_and_all(self):
        clean = self.recorded("\x1b[0m\x1b[32mStage 1\x1b[0m - Checking - \x1b[32mComplete\x1b[0m")
        left = self.recorded("Stage 1 - Checking - Stopped", "Stage 1 - Checking - Complete")
        self.assertEqual(fault_verdicts.screen_problems(clean), [])
        self.assertEqual(len(fault_verdicts.screen_problems(clean + left)), 1)

    def test_output_with_no_screen_has_nothing_to_judge(self):
        self.assertEqual(fault_verdicts.screen_problems("Stage 1 - Checking - Stopped\nStage 1 - Checking - Complete\n"), [])


class RecordsTests(unittest.TestCase):
    """Where both families' suites find the tool's records in an emulator."""

    def test_an_earlier_releases_records_are_found_until_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.assertEqual(fault_verdicts.records_under(work), work / "captures/devices")
            (work / "captures/plugs").mkdir(parents=True)
            self.assertEqual(fault_verdicts.records_under(work), work / "captures/plugs")
            (work / "captures/plugs").rename(work / "captures/devices")
            self.assertEqual(fault_verdicts.records_under(work), work / "captures/devices")

    def test_a_run_is_filed_as_alike_under_either_name(self):
        for records in ("captures/devices", "captures/plugs"):
            log = Path("/state/work") / records / "12-34-56-ab-cd-ef/runs/20261118-120000/run.log"
            self.assertEqual(fault_verdicts.filed_as(log), Path("12-34-56-ab-cd-ef/runs/20261118-120000/run.log"))


class WaitsTests(unittest.TestCase):
    def test_each_explained_wait_since_the_scenario_began_is_named_once(self):
        events = [{"at": 5, "event": "wait", "wait": "stopped-step"},
                  {"at": 20, "event": "wait", "wait": "fetch-refused"}, {"at": 21, "event": "began", "step": "J2"},
                  {"at": 30, "event": "wait", "wait": "fetch-refused"}, {"at": 31, "event": "wait", "wait": "nothing-asked"}]
        self.assertEqual(fault_verdicts.waits(events, since=10), ["fetch-refused", "nothing-asked"])
        self.assertEqual(fault_verdicts.waits([], since=0), [])


class ResultsTests(unittest.TestCase):
    def test_each_scenarios_newest_row_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            self.assertEqual(fault_verdicts.latest(results), {})
            results.write_text('{"id": "a", "verdict": "FAIL"}\n\n{"id": "a", "verdict": "PASS"}\n', encoding="utf-8")
            self.assertEqual(fault_verdicts.latest(results), {"a": {"id": "a", "verdict": "PASS"}})

    def test_a_row_from_before_the_verdict_key_is_read_as_one(self):
        # The Beken suite's rows kept their verdict as "result" until 2026-11-19.
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            results.write_text('{"id": "a", "result": "PASS"}\n{"id": "b", "notes": {}}\n', encoding="utf-8")
            found = fault_verdicts.latest(results)
        self.assertEqual((found["a"]["verdict"], found["b"]["verdict"]), ("PASS", "HARNESS-ERROR"))
        self.assertEqual(fault_verdicts.verdict({"verdict": "FAIL", "result": "PASS"}), "FAIL")

    def test_a_batch_line_is_alike_from_both_suites(self):
        at = time.mktime((2026, 11, 19, 14, 5, 9, 0, 0, -1))
        self.assertEqual(fault_verdicts.batch_line(81, "PASS", "sonoff>esphome:J2:cancel-new", {}, at),
                         "14:05:09 [81] PASS             sonoff>esphome:J2:cancel-new")
        self.assertTrue(fault_verdicts.batch_line(3, "FAIL", "x", {"memory": {"starved": True}}, at)
                        .endswith("x  (memory-starved)"))

    def test_a_row_cut_short_is_passed_over_not_every_row_after_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            results.write_text('{"id": "a", "result": "PASS"}\n{"id": "b", "res\n{"id": "c", "result": "FAIL"}\n'
                               '[1]\n{"id": "d", "res', encoding="utf-8")
            self.assertEqual(set(fault_verdicts.latest(results)), {"a", "c"})

    def test_a_row_is_written_whole_and_after_a_line_left_unended(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "sub" / "results.jsonl"
            fault_verdicts.append_row(results, {"id": "a", "result": "PASS"})
            with results.open("a", encoding="utf-8") as file:
                file.write('{"id": "b", "res')
            fault_verdicts.append_row(results, {"id": "c", "result": "FAIL"})
            self.assertEqual(set(fault_verdicts.latest(results)), {"a", "c"})
            self.assertTrue(results.read_text(encoding="utf-8").endswith('"FAIL"}\n'))

    def test_a_write_failing_part_way_leaves_the_file_as_it_was(self):
        real_open = open

        class FullDisk:
            """The file, filling after half the row: a short write, then ENOSPC."""
            def __init__(self, *args, **kwargs):
                self.file = real_open(*args, **kwargs)
                self.writes = 0

            def __getattr__(self, name):
                return getattr(self.file, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.file.close()

            def write(self, data):
                self.writes += 1
                if self.writes > 1:
                    raise OSError(28, "No space left on device")
                return self.file.write(data[:len(data) // 2])

        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            fault_verdicts.append_row(results, {"id": "a", "result": "PASS"})
            before = results.read_bytes()
            with mock.patch("tools.repo.fault_verdicts.open", FullDisk, create=True), \
                    self.assertRaises(OSError):
                fault_verdicts.append_row(results, {"id": "b", "result": "FAIL"})
            self.assertEqual(results.read_bytes(), before)

    def test_only_a_failure_is_labelled_starved(self):
        starved = {"memory": {"starved": True}}
        self.assertIn("memory-starved", fault_verdicts.starved_label("FAIL", starved))
        self.assertEqual(fault_verdicts.starved_label("PASS", starved), "")
        self.assertEqual(fault_verdicts.starved_label("ERROR", {"error": "x"}), "")
        self.assertEqual(fault_verdicts.starved_label(None, {}), "")


class ScenarioFolderTests(unittest.TestCase):
    """Both suites keep a scenario's logs alike: a folder each, its runs in it."""

    def test_a_scenario_is_named_alike_in_both_suites(self):
        from pathlib import Path
        self.assertEqual(fault_verdicts.scenario_folder(Path("logs"), "sonoff>esphome:J2@10:crash-1"),
                         Path("logs/sonoff-esphome_J2@10_crash-1"))

    def test_each_run_log_and_its_events_are_kept_numbered_from_where_the_scenario_began(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root, folder = Path(directory), Path(directory) / "logs/s"
            first, second = root / "runs/1/run.log", root / "runs/2/run.log"
            for log, text in ((first, "saved\nours\n"), (second, "second\n")):
                log.parent.mkdir(parents=True)
                log.write_text(text, encoding="utf-8")
            (first.parent / "events.jsonl").write_text('{"saved": 1}\n{"ours": 1}\n', encoding="utf-8")
            before = {first: len("saved\n"), first.parent / "events.jsonl": len('{"saved": 1}\n')}
            fault_verdicts.keep_runs([first, second], folder, lambda path: before.get(path, 0))
            self.assertEqual((folder / "run-1.log").read_text(encoding="utf-8"), "ours\n")
            self.assertEqual((folder / "run-1.events.jsonl").read_text(encoding="utf-8"), '{"ours": 1}\n')
            self.assertEqual((folder / "run-2.log").read_text(encoding="utf-8"), "second\n")
            self.assertFalse((folder / "run-2.events.jsonl").exists())   # it wrote none

    def test_the_routers_dhcp_log_is_kept_by_both_suites_alike(self):
        # Every address the router gave the device: the Espressif suite kept
        # none, though its router logs them as the Beken one's does.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "logs/s"
            fault_verdicts.keep_dhcp_log("DHCPACK(br0) 192.168.60.55 12:34:56:ab:cd:ef sonoff\n", folder)
            self.assertIn("192.168.60.55", (folder / "dhcp.log").read_text(encoding="utf-8"))
        root = Path(fault_verdicts.__file__).resolve().parents[2]
        for suite in ("tools/espressif/emulator/fault_suite.py", "tools/beken/emulator/fault_suite.py"):
            with self.subTest(suite=suite):
                text = (root / suite).read_text(encoding="utf-8")
                self.assertIn("fault_verdicts.keep_dhcp_log(", text)
                self.assertNotIn('"dhcp.log"', text, "through keep_dhcp_log, which both suites share")

    def test_the_devices_address_is_found_by_both_suites_alike(self):
        mac = "12:34:56:AB:CD:EF"
        leases = ("9 12:34:56:00:00:01 192.168.60.120 laptop *\n"
                  "7 12:34:56:ab:cd:ef 192.168.60.50 sonoff *\n8 12:34:56:ab:cd:ef 192.168.60.55 sonoff *\n")
        self.assertEqual(fault_verdicts.device_address(leases, "", mac), "192.168.60.55")   # its newest
        self.assertEqual(fault_verdicts.device_address(leases, ""), "192.168.60.120")       # any, without a MAC
        log = ("dnsmasq-dhcp[9]: 1 DHCPACK(br0) 198.18.61.50 12:34:56:ab:cd:ef openbeken-cdef\n"
               "dnsmasq-dhcp[9]: 2 DHCPACK(br0) 198.18.61.120 12:34:56:00:00:01 laptop\n"
               "dnsmasq-dhcp[9]: 3 DHCPACK(br0) 198.18.61.51 12:34:56:ab:cd:ef esphome-cdef\n"
               "dnsmasq-dhcp[9]: 4 DHCPRELEASE(br0) 198.18.61.51 12:34:56:ab:cd:ef\n")
        # No lease - forgotten, or given back and still used: the last given.
        self.assertEqual(fault_verdicts.device_address("", log, mac), "198.18.61.51")
        self.assertEqual(fault_verdicts.dhcp_addresses(log, mac), ["198.18.61.50", "198.18.61.51"])
        self.assertIsNone(fault_verdicts.device_address("", "", mac))
        root = Path(fault_verdicts.__file__).resolve().parents[2]
        for suite in ("tools/espressif/emulator/fault_suite.py", "tools/beken/emulator/fault_suite.py"):
            with self.subTest(suite=suite):
                self.assertIn("fault_verdicts.device_address(", (root / suite).read_text(encoding="utf-8"))


class EvidenceTests(unittest.TestCase):
    def test_a_failures_records_are_kept_whole_but_its_firmware_images(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            records, folder = Path(directory) / "devices", Path(directory) / "logs/s"
            backup = records / "12-34-56-ab-cd-ef/sonoff-backup"
            backup.mkdir(parents=True)
            for name in ("backup.json", "flash.bin", "stock-app-1.0.2.rbl"):
                (backup / name).write_text("x", encoding="utf-8")
            fault_verdicts.keep_evidence(records, folder)
            self.assertEqual(sorted(path.name for path in (folder / "evidence").rglob("*") if path.is_file()),
                             ["backup.json"])


class RecordingTests(unittest.TestCase):
    def test_each_runs_recording_is_kept_beside_the_scenario(self):
        # The emulator's own copies go when its next scenario begins: seen,
        # only the last scenario's recording was left to replay.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            work, kept = Path(directory) / "work", Path(directory) / "logs/scenario-screens"
            for name, drawn in (("20261109-000339", b"first run"), ("20261109-000607", b"second run")):
                (work / "captures/unattended" / name).mkdir(parents=True)
                (work / "captures/unattended" / name / "screen.raw").write_bytes(drawn)
            output = ("--- run\nKept in captures/unattended/20261109-000339/: screen.ansi (cat it), screen.txt, screen.raw\n"
                      "--- run\nKept in /work/captures/unattended/20261109-000607/: screen.ansi (cat it), screen.txt, "
                      "screen.raw\n--- run\nKept in captures/unattended/gone/: screen.ansi (cat it)\n")
            found = fault_verdicts.keep_recordings(output, work, kept)
            self.assertEqual([path.name for path in found], ["run-1.raw", "run-2.raw"])
            self.assertEqual((kept / "run-2.raw").read_bytes(), b"second run")


class ReportLabelTests(unittest.TestCase):
    def test_the_checks_are_read_however_the_report_said_them(self):
        # Shorter, and in two columns, since 3.0.0; an earlier release's runs
        # (the legacy scenarios) say it as before.
        for log, said in (("[10:00:02]     Checks passed: False\n", "False"),
                          ("[10:00:02]     Setup network: No                      Checks:   False\n", "False"),
                          ("[10:00:02]     Setup network: No                      Checks:   Fail\n", "Fail")):
            with self.subTest(log=log):
                self.assertEqual(fault_verdicts.checks_problem(log, "tasmota"),
                                 f"Checks passed: {said}; Relay: not said")
        # Pass, as 3.0.0 says it, as True did before.
        self.assertIsNone(fault_verdicts.checks_problem(
            "[10:00:02]     Setup network: No                      Checks:   Pass\n", "tasmota"))


if __name__ == "__main__":
    unittest.main()
