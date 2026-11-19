"""The offers of every device in the account, sent beside a run: on the run's
own sign-in, in turn with its eWeLink calls, only where reports go and the
website takes links, nothing on the screen, each device again only after
OFFER_RESEND_DAYS."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import account, hostsys, offers, output, prompts, proof_of_work, readout, report_data
from tools.ewelink import get_device_key
from tools.ewelink.get_device_key import SessionEnded
from tools.ewelink.query_ota import QUERY_MOST

TODAY = dt.date(2026, 11, 18)
TOKEN = "Ab3" * 10 + "Z9"
HALL = {"deviceid": "1000abcdef", "name": "Hall", "productModel": "S60TPG",
        "extra": {"model": "PSF-BS60-GL"}, "params": {"fwVersion": "1.2.0", "staMac": "12:34:56:AB:CD:EF"}}
PORCH = {"deviceid": "1000fedcba", "name": "Porch", "productModel": "NEWTHING",
         "extra": {"model": "SN-NEW-01"}, "params": {"fwVersion": "1.0.0"}}


def offer_of(n: int) -> dict:
    return {"offers": [{"version": "1.3.0", "binList": [
        {"name": "user1.bin", "downloadUrl": f"https://example.com/ota/rom/{TOKEN}/user{n}.bin",
         "digest": (f"{n:x}" * 64)[:64]}]}]}


def many(n: int) -> list[dict]:
    """`n` devices of the account, each with an id of its own."""
    return [{**PORCH, "deviceid": f"1000{k:06x}"} for k in range(n)]


def a_ctx(**args) -> SimpleNamespace:
    """A run signed in to eWeLink, its sign-in never to be made again."""
    sign_in = mock.Mock(side_effect=AssertionError("the background signed in"))
    return SimpleNamespace(args=SimpleNamespace(**args), offers=None, account_device=None,
                           ewelink=get_device_key.Account(sign_in, held=("eu", {"at": "run-token"})))


class World(unittest.TestCase):
    """Each test's own SENT, the website taking links and every offer, and
    eWeLink stood in for (self.queries: each query's devices, by id;
    self.asked: every device asked of)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sent_path = Path(tmp.name) / "offers-sent.json"
        self.asked: list[str] = []
        self.queries: list[list[str]] = []
        self.logged: list[str] = []
        self.answers = lambda items: ("links", [("taken", "")] * len(items))

        def latest(region, token, identities):
            self.assertEqual((region, token), ("eu", "run-token"))   # the run's own sign-in
            self.queries.append([identity["deviceid"] for identity in identities])
            found = {}
            for identity in identities:
                self.asked.append(identity["deviceid"])
                found[identity["deviceid"]] = offer_of(len(self.asked))
            return found
        self.latest = mock.Mock(side_effect=latest)
        self.sends = mock.Mock(return_value="links")
        # Never asked: the website counts each /wanted question as a device wanted.
        self.wanted = mock.Mock(side_effect=AssertionError("/wanted asked"))
        self.send_offers = mock.Mock(side_effect=lambda items, prove: self.answers(items))
        for patch in (mock.patch.object(offers, "SENT", self.sent_path),
                      mock.patch.object(offers, "PAUSE", 0.0),
                      mock.patch.object(offers, "latest_offers", self.latest),
                      mock.patch.object(readout, "ask_wanted", self.wanted),
                      mock.patch.object(readout, "website_sends", self.sends),
                      mock.patch.object(readout, "send_offers", self.send_offers),
                      mock.patch.dict(account.ASKED_OFFERS, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_sender(self, ctx=None, devices=(HALL, PORCH), today=TODAY) -> offers.Sender:
        sender = offers.Sender(ctx or a_ctx(), list(devices), today, log=self.logged.append)
        sender.run()
        return sender

    def sent(self) -> list[dict]:
        return [item for call in self.send_offers.call_args_list for item in call.args[0]]


class SendTests(World):
    def test_each_device_s_offer_goes_in_one_request_on_the_run_s_sign_in(self):
        ctx = a_ctx()
        self.run_sender(ctx)
        self.assertEqual(self.queries, [["1000abcdef", "1000fedcba"]])   # one query for both
        ctx.ewelink._sign_in.assert_not_called()
        self.send_offers.assert_called_once()
        hall, porch = self.sent()
        self.assertEqual(hall, {"model": "S60TPG", "stock_model": "PSF-BS60-GL", "stock_version": "1.2.0",
                                "device": report_data.device_hash("12:34:56:ab:cd:ef"),
                                "files": [{"name": "user1.bin", "version": "1.3.0", "sha256": "1" * 64,
                                           "path": f"/ota/rom/{TOKEN}/user1.bin"}]})
        self.assertIsNone(porch["device"])   # no MAC: no device hash
        self.assertEqual(self.logged, ["Offers of the account's devices: 2 sent: 2 taken, 0 held already, "
                                       "0 for another day, 0 turned down."])

    def test_no_device_id_goes_to_the_website(self):
        self.run_sender()
        self.assertNotIn("1000abcdef", json.dumps(self.sent()))
        self.assertNotIn("1000abcdef", self.sent_path.read_text(encoding="utf-8"))

    def test_only_where_the_website_takes_links_asked_before_eWeLink(self):
        self.sends.return_value = "files"
        sender = self.run_sender()
        self.sends.assert_called_once_with(prove=sender.prove)
        self.assertEqual(self.asked, [])   # eWeLink not asked either
        self.send_offers.assert_not_called()
        self.assertEqual(self.logged, ["Offers of the account's devices: not sent: the website takes files, "
                                       "not links, for now."])

    def test_the_website_asked_first_is_asked_with_no_offers(self):
        sender = self.run_sender()
        self.sends.assert_called_once_with(prove=sender.prove)
        self.assertEqual(self.send_offers.call_args.kwargs, {"prove": sender.prove})
        self.assertLess(self.sends.call_count, self.latest.call_count + 1)

    def test_one_switched_to_files_meanwhile_is_not_sent(self):
        self.answers = lambda items: ("files", [("full", "")] * len(items))
        self.run_sender()
        self.assertFalse(self.sent_path.exists())   # nothing remembered: sent again next run
        self.assertIn("takes files", self.logged[-1])

    def test_without_a_sign_in_held_nothing_is_asked_and_none_made(self):
        ctx = a_ctx()
        ctx.ewelink.held = None
        self.run_sender(ctx)
        ctx.ewelink._sign_in.assert_not_called()
        self.assertEqual(self.asked, [])
        self.send_offers.assert_not_called()

    def test_eWeLink_ending_the_sign_in_stops_it(self):
        self.latest.side_effect = [{}, SessionEnded("eWeLink ended the sign-in"), {}]
        ctx = a_ctx()
        self.run_sender(ctx, devices=many(QUERY_MOST * 2 + 1))
        self.assertEqual(self.latest.call_count, 2)   # not the next query
        ctx.ewelink._sign_in.assert_not_called()
        self.send_offers.assert_not_called()
        self.assertEqual(self.logged, ["Offers of the account's devices: not sent: eWeLink ended the sign-in."])

    def test_an_offer_the_run_asked_for_is_not_asked_again(self):
        account.ASKED_OFFERS["1000abcdef"] = offer_of(9)
        self.run_sender()
        self.assertEqual(self.asked, ["1000fedcba"])
        self.assertEqual(self.sent()[0]["files"][0]["sha256"], "9" * 64)

    def test_eWeLink_is_asked_of_at_most_its_limit_of_devices_at_once(self):
        self.run_sender(devices=many(QUERY_MOST + 1))
        self.assertEqual([len(query) for query in self.queries], [QUERY_MOST, 1])
        self.assertEqual(len(self.sent()), QUERY_MOST + 1)
        self.send_offers.assert_called_once()

    def test_each_offer_goes_with_the_device_eWeLink_gave_it_to(self):
        self.latest.side_effect = [{"1000fedcba": offer_of(7), "1000abcdef": offer_of(5)}]
        self.run_sender()
        self.assertEqual([(item["model"], item["files"][0]["sha256"]) for item in self.sent()],
                         [("S60TPG", "5" * 64), ("NEWTHING", "7" * 64)])

    def test_a_query_that_failed_leaves_its_devices_out_and_the_rest_go(self):
        devices = many(QUERY_MOST + 1)
        self.latest.side_effect = [{device["deviceid"]: {"problem": "EwelinkError"} for device in devices[:QUERY_MOST]},
                                   {devices[-1]["deviceid"]: offer_of(1)}]
        self.run_sender(devices=devices)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn(f"eWeLink said nothing of {QUERY_MOST}", self.logged[-1])

    def test_one_eWeLink_says_nothing_of_is_left_out(self):
        self.latest.side_effect = [{"1000abcdef": {"problem": "EwelinkError"}, "1000fedcba": offer_of(2)}]
        self.run_sender()
        self.assertEqual([item["model"] for item in self.sent()], ["NEWTHING"])
        self.assertIn("eWeLink said nothing of 1", self.logged[-1])

    def test_one_eWeLink_offers_nothing_is_sent_with_no_files(self):
        self.latest.side_effect = [{"1000abcdef": {"offers": []}, "1000fedcba": {"offers": []}}]
        self.run_sender()
        self.assertEqual([item["files"] for item in self.sent()], [[], []])

    def test_one_with_a_link_of_another_shape_is_not_sent_nor_remembered(self):
        def latest(region, token, identities):
            found = {identity["deviceid"]: offer_of(1) for identity in identities}
            found["1000fedcba"]["offers"][0]["binList"].append(
                {"name": "user2.bin", "downloadUrl": "https://example.com/rom/user2.bin", "digest": "2" * 64})
            return found
        self.latest.side_effect = latest
        self.run_sender()
        self.assertEqual([item["stock_model"] for item in self.sent()], ["PSF-BS60-GL"])
        self.assertEqual(list(json.loads(self.sent_path.read_text(encoding="utf-8"))),
                         [offers.key_of(HALL)])
        self.assertEqual(self.logged, ["Offers of the account's devices: 1 sent: 1 taken, 0 held already, "
                                       "0 for another day, 0 turned down; 1 not sent: a link of another shape, "
                                       "unfit for review."])

    def test_none_sent_when_every_one_has_a_link_of_another_shape(self):
        self.latest.side_effect = lambda region, token, identities: {identity["deviceid"]: {"offers": [
            {"version": "1.3.0", "binList": [{"downloadUrl": "https://example.com/rom/u.bin", "digest": "1" * 64}]}]}
            for identity in identities}
        self.run_sender()
        self.send_offers.assert_not_called()
        self.assertFalse(self.sent_path.exists())
        self.assertEqual(self.logged, ["Offers of the account's devices: none sent; 2 not sent: a link of another "
                                       "shape, unfit for review."])

    def test_a_link_of_another_shape_fit_for_review_is_sent_whole_without_its_query(self):
        def latest(region, token, identities):
            found = {identity["deviceid"]: offer_of(1) for identity in identities}
            found["1000fedcba"]["offers"][0]["binList"].append(
                {"name": "user2.bin", "downloadUrl": "http://eu-otadl.coolkit.cc:8088/rom/user2.bin?sign=x",
                 "digest": "2" * 64})
            return found
        self.latest.side_effect = latest
        self.run_sender()
        self.assertEqual(self.sent()[1]["files"][1], {"name": "user2.bin", "version": "1.3.0", "sha256": "2" * 64,
                                                       "url": "http://eu-otadl.coolkit.cc:8088/rom/user2.bin"})
        self.assertEqual(len(json.loads(self.sent_path.read_text(encoding="utf-8"))), 2)
        self.assertEqual(self.logged, ["Offers of the account's devices: 2 sent: 2 taken, 0 held already, "
                                       "0 for another day, 0 turned down; 1 of their files by a link of another "
                                       "shape, for review."])

    def test_a_link_naming_the_device_is_not_sent_even_for_review(self):
        for named, link in (("1000fedcba", "http://eu-otadl.coolkit.cc/rom/1000FEDCBA/user2.bin"),
                            ("1000abcdef", "http://eu-otadl.coolkit.cc/rom/12-34-56-ab-cd-ef.bin"),
                            ("1000abcdef", "http://eu-otadl.coolkit.cc/rom/user2.bin?mac=123456ABCDEF")):
            with self.subTest(link=link):
                self.sent_path.unlink(missing_ok=True)
                self.send_offers.reset_mock()
                self.logged.clear()

                def latest(region, token, identities, named=named, link=link):
                    found = {identity["deviceid"]: offer_of(1) for identity in identities}
                    found[named]["offers"][0]["binList"].append({"name": "user2.bin", "downloadUrl": link,
                                                                 "digest": "2" * 64})
                    return found
                self.latest.side_effect = latest
                self.run_sender()
                self.assertEqual(len(self.sent()), 1)   # the other device's
                self.assertNotIn("user2.bin", json.dumps(self.sent()))
                self.assertIn("1 not sent: a link of another shape, unfit for review", self.logged[-1])

    def test_a_pause_between_two_queries_alone(self):
        for count, pauses in ((2, 0), (QUERY_MOST * 2 + 1, 2)):
            with self.subTest(devices=count):
                self.sent_path.unlink(missing_ok=True)   # none sent before
                sender = offers.Sender(a_ctx(), many(count), TODAY, log=self.logged.append)
                with mock.patch.object(offers, "PAUSE", 3.0), mock.patch.object(sender.stop, "wait",
                                                                                 return_value=False) as paused:
                    sender.run()
                self.assertEqual(paused.call_args_list, [mock.call(3.0)] * pauses)

    def test_a_problem_is_logged_never_raised(self):
        self.send_offers.side_effect = OSError("down")
        self.run_sender()
        self.assertEqual(self.logged, ["Offers of the account's devices: not sent (OSError: down)."])

    def test_nothing_is_said_on_the_screen(self):
        screen = io.StringIO()
        with contextlib.redirect_stdout(screen), contextlib.redirect_stderr(screen), \
                mock.patch.multiple(output, say=mock.DEFAULT, tell=mock.DEFAULT, show=mock.DEFAULT,
                                    progress=mock.DEFAULT) as shown:
            self.run_sender()
            self.send_offers.side_effect = OSError("down")
            self.run_sender()
        self.assertEqual(screen.getvalue(), "")
        for name, said in shown.items():
            self.assertEqual(said.call_count, 0, name)


class StopTests(World):
    def test_dropped_as_the_run_ends_it_sends_nothing_and_says_it_once(self):
        ctx, logged = a_ctx(), []
        inside, gate = threading.Event(), threading.Event()

        def latest(region, token, identities):
            inside.set()
            gate.wait(2)
            return {}
        self.latest.side_effect = latest
        sender = offers.Sender(ctx, many(QUERY_MOST + 1), TODAY, log=logged.append)
        ctx.offers = sender
        thread = threading.Thread(target=sender.run, daemon=True)
        thread.start()
        inside.wait(2)
        offers.drop(ctx)
        gate.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.latest.call_count, 1)
        self.send_offers.assert_not_called()
        self.assertEqual(logged, ["Offers of the account's devices: not sent: the run ended first."])
        self.assertFalse(self.sent_path.exists())   # nothing kept to send later

    def test_it_waits_for_the_run_s_eWeLink_calls(self):
        ctx = a_ctx()
        sender = offers.Sender(ctx, [HALL], TODAY, log=self.logged.append)
        with get_device_key.CALLS.run():
            thread = threading.Thread(target=sender.run, daemon=True)
            thread.start()
            thread.join(0.3)
            self.assertEqual(self.asked, [])
        thread.join(2)
        self.assertEqual(self.asked, ["1000abcdef"])

    def test_a_run_s_call_never_overlaps_one_of_its(self):
        inside, overlapped = threading.Event(), []

        def latest(region, token, identities):
            inside.set()
            threading.Event().wait(0.3)
            overlapped.append(False)
            return {"1000abcdef": offer_of(1)}
        self.latest.side_effect = latest
        thread = threading.Thread(target=self.run_sender, kwargs={"devices": [HALL]}, daemon=True)
        thread.start()
        inside.wait(2)
        with get_device_key.CALLS.run():
            overlapped.append(True)
        thread.join(2)
        self.assertEqual(overlapped, [False, True])


class ResendTests(World):
    def test_a_device_sent_is_not_sent_again_within_the_resend_days(self):
        self.run_sender()
        self.asked.clear()
        self.send_offers.reset_mock()
        self.run_sender(today=TODAY + dt.timedelta(days=offers.OFFER_RESEND_DAYS - 1))
        self.assertEqual(self.asked, [])
        self.send_offers.assert_not_called()
        self.run_sender(today=TODAY + dt.timedelta(days=offers.OFFER_RESEND_DAYS))
        self.assertEqual(self.asked, ["1000abcdef", "1000fedcba"])

    def test_kept_by_device_hash_or_its_id_hashed_never_the_id(self):
        self.run_sender()
        kept = json.loads(self.sent_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(kept), sorted([report_data.device_hash("12:34:56:ab:cd:ef"), offers.key_of(PORCH)]))
        self.assertRegex(offers.key_of(PORCH), r"^[0-9a-f]{64}$")
        self.assertEqual(set(kept.values()), {TODAY.isoformat()})

    def test_one_the_website_had_no_room_for_goes_again_next_run(self):
        self.answers = lambda items: ("links", [("full", ""), ("have", "")])
        self.run_sender()
        self.assertEqual(list(json.loads(self.sent_path.read_text(encoding="utf-8"))), [offers.key_of(PORCH)])
        self.assertIn("1 for another day", self.logged[-1])

    def test_those_sent_long_ago_are_forgotten(self):
        old = (TODAY - dt.timedelta(days=offers.OFFER_RESEND_DAYS)).isoformat()
        self.sent_path.write_text(json.dumps({"f" * 64: old}), encoding="utf-8")
        self.run_sender()
        self.assertNotIn("f" * 64, json.loads(self.sent_path.read_text(encoding="utf-8")))

    def test_a_broken_file_sends_them_all(self):
        self.sent_path.write_text("not json", encoding="utf-8")
        self.run_sender()
        self.assertEqual(len(self.sent()), 2)


class EnabledTests(unittest.TestCase):
    """Sent where the person lets reports be sent, only where readouts may go."""

    def setUp(self):
        for patch in (mock.patch.dict(os.environ, {"AUTOFLASH_NO_REPORT": "", "AUTOFLASH_READOUT_URL": ""}),
                      mock.patch.object(prompts, "UNATTENDED", False)):
            patch.start()
            self.addCleanup(patch.stop)
        for name in hostsys.EMULATOR_MARKS:
            patch = mock.patch.dict(os.environ, {name: ""})
            patch.start()
            self.addCleanup(patch.stop)

    def test_on_by_default(self):
        self.assertTrue(offers.enabled(a_ctx()))

    def test_off_with_no_report_either_way(self):
        self.assertFalse(offers.enabled(a_ctx(no_report=True)))
        with mock.patch.dict(os.environ, {"AUTOFLASH_NO_REPORT": "1"}):
            self.assertFalse(offers.enabled(a_ctx()))

    def test_never_from_a_dry_run_the_emulator_or_an_unattended_run(self):
        self.assertFalse(offers.enabled(a_ctx(dry_run=True)))
        with mock.patch.dict(os.environ, {hostsys.EMULATOR_MARKS[0]: "http://127.0.0.1:9"}):
            self.assertFalse(offers.enabled(a_ctx()))
        with mock.patch.object(prompts, "UNATTENDED", True):
            self.assertFalse(offers.enabled(a_ctx()))

    def test_the_emulator_sends_to_its_own_api_where_readouts_go(self):
        with mock.patch.dict(os.environ, {hostsys.EMULATOR_MARKS[0]: "http://127.0.0.1:9",
                                          "AUTOFLASH_READOUT_URL": "http://127.0.0.1:9/v1/readout"}):
            self.assertTrue(offers.enabled(a_ctx()))
            self.assertFalse(offers.enabled(a_ctx(no_report=True)))

    def test_started_once_a_run_and_only_where_enabled(self):
        ctx = a_ctx()
        with mock.patch.object(offers.threading, "Thread") as thread:
            self.assertIsNotNone(offers.start(ctx, [HALL]))
            self.assertIsNone(offers.start(ctx, [HALL]))
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs["daemon"])
            self.assertIsNone(offers.start(a_ctx(no_report=True), [HALL]))
            self.assertIsNone(offers.start(a_ctx(), []))
        self.assertEqual(thread.call_count, 1)

    def test_begun_once_the_run_has_listed_the_account_s_devices(self):
        from tools.autoflash import context
        with mock.patch.object(context, "get_devices", return_value=[HALL]), \
                mock.patch.object(context.account, "keep_account_private"), \
                mock.patch.object(context.signup, "saw_devices"), \
                mock.patch.object(context.offers, "start") as started:
            ctx = SimpleNamespace(ewelink=get_device_key.Account(lambda: ("eu", {"at": "t"})))
            self.assertEqual(context.Context.account_devices(ctx), [HALL])
        started.assert_called_once_with(ctx, [HALL])


class WorkTests(unittest.TestCase):
    """The light work of its posts made in a process of its own at the lowest
    priority (report_upload.work_beside), never in its thread, and dropped
    with the run."""

    def test_made_in_a_process_of_its_own(self):
        process = mock.Mock()
        with mock.patch.object(offers.report_upload, "work_beside", return_value=process) as beside, \
                mock.patch.object(offers.report_upload, "proof_of", return_value="1:2") as made, \
                mock.patch.object(readout, "proof", side_effect=AssertionError("made in this process")):
            self.assertEqual(offers.Sender(a_ctx(), []).prove(b"body"), "1:2")
        beside.assert_called_once_with(proof_of_work.body_subject(b"body"), readout.LIGHT_PROOF_BITS)
        made.assert_called_once_with(process)

    def test_one_not_made_sends_nothing(self):
        with mock.patch.object(offers.report_upload, "work_beside"), \
                mock.patch.object(offers.report_upload, "proof_of", return_value=None), \
                self.assertRaisesRegex(offers.NotProven, "not made"):
            offers.Sender(a_ctx(), []).prove(b"body")

    def test_dropped_with_the_run_its_process_killed_and_none_begun_after(self):
        ctx = a_ctx()
        ctx.offers = sender = offers.Sender(ctx, [HALL], log=lambda _: None)
        process, inside, killed = mock.Mock(), threading.Event(), threading.Event()

        def proof_of(working):
            inside.set()
            killed.wait(2)
            return None   # as a killed process gives
        with mock.patch.object(offers.report_upload, "work_beside", return_value=process) as beside, \
                mock.patch.object(offers.report_upload, "proof_of", side_effect=proof_of), \
                mock.patch.object(offers.report_upload, "stop", side_effect=lambda _: killed.set()) as stop:
            failed = []
            thread = threading.Thread(target=self.prove_raises, args=(sender, failed), daemon=True)
            thread.start()
            inside.wait(2)
            offers.drop(ctx)
            thread.join(2)
            stop.assert_called_once_with(process)
            self.assertEqual(failed, ["the run ended first"])
            with self.assertRaisesRegex(offers.NotProven, "ended"):
                sender.prove(b"later")
        beside.assert_called_once()

    @staticmethod
    def prove_raises(sender, failed):
        try:
            sender.prove(b"body")
        except offers.NotProven as error:
            failed.append(str(error))


if __name__ == "__main__":
    unittest.main()
