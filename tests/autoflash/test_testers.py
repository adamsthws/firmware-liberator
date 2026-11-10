"""A kind wanting testers: said to be in the go-ahead before a run's first write,
on every route, with PROCEED and nothing else to type, and its log asked for with
the report; a proven kind's go-ahead says nothing of it."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import errors, firewall, handover, hostsys, kinds, output, prompts, report, testers
from tools.autoflash.routes.espressif import installer
from tools.autoflash.state import RunState

S60 = kinds.SONOFF_S60
WANTING = [kind for kind in kinds.READY if kind.status is kinds.Status.TESTERS_WANTED]


def a_run(tmp: str, kind: kinds.Kind) -> SimpleNamespace:
    state = RunState.open(Path(tmp))
    for key, value in {"device_kind": kind.key, "device_ip": "192.0.2.96", "stock_version": "1.2.0"}.items():
        state.set_input(key, value)
    return SimpleNamespace(state=state, bridge=Path("bridge.ota"), dir=Path(tmp), listen_ip=lambda: "192.0.2.141",
                           email=lambda: "me@example.com", ewelink_env=lambda: {}, helper_env=lambda: {},
                           ewelink_region=lambda: "eu")


class WarningTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(output, "transient_begin"), mock.patch.object(output, "transient_end"),
                      mock.patch.object(output, "say")):
            patch.start()
            self.addCleanup(patch.stop)
        box = mock.patch.object(prompts, "warning_box")
        self.box = box.start()
        self.addCleanup(box.stop)

    def bridge(self, kind, answer):
        """installer.step_bridge on `kind`: the words asked for, and whether anything was sent."""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_run(tmp, kind)
            with mock.patch.object(firewall, "choose_port", return_value=8088), \
                    mock.patch.object(firewall, "ensure_firewall"), \
                    mock.patch.object(installer, "ROOT", Path(tmp)), \
                    mock.patch.object(installer.safety_locks, "lifted"), \
                    mock.patch.object(installer.safety_locks, "say_lock_back"), \
                    mock.patch.object(installer, "bridge_file", return_value=Path("bridge.ota")), \
                    mock.patch.object(prompts, "require_word", side_effect=answer) as asked, \
                    mock.patch.object(hostsys, "run_tool", return_value=0) as sent:
                try:
                    installer.step_bridge(ctx)
                except errors.NothingWritten:
                    pass
        return [call.args[0] for call in asked.call_args_list], sent.called

    def said(self):
        return " ".join(line for call in self.box.call_args_list for line in call.args[1])

    def test_the_s60_is_proven_and_its_go_ahead_says_nothing_of_testers(self):
        self.assertIs(S60.status, kinds.Status.SUPPORTED)
        self.assertEqual(self.bridge(S60, lambda word: True), (["proceed"], True))
        self.assertNotIn("emulator", self.said())

    def test_an_espressif_kind_wanting_testers_is_told_in_the_go_ahead_and_types_proceed(self):
        for kind in (kind for kind in WANTING if kind.family == "espressif"):
            with self.subTest(kind=kind.key):
                self.box.reset_mock()
                self.assertEqual(self.bridge(kind, lambda word: True), (["proceed"], True))
                self.assertEqual(self.box.call_count, 1)
                self.assertIn("The conversion for this particular device has not been proven on a real", self.said())
                self.assertIn("Please report your result so it can be marked as a", self.said())

    def test_the_words_are_the_same_for_every_kind(self):
        said = {tuple(line for line in testers.notice(kind) if not line.startswith("/") and "run.log" not in line)
                for kind in WANTING}
        self.assertEqual(len(said), 1, said)
        self.assertNotIn("go wrong", " ".join(next(iter(said))))
        self.assertEqual(testers.notice(S60), [])

    def test_the_words_claim_no_emulator_run_a_device_may_not_have_had(self):
        # A kind can be added from its official file, with no emulator run: the
        # same words for every kind have to be true of it too.
        for kind in WANTING:
            with self.subTest(kind=kind.key):
                self.assertNotIn("emulator", " ".join(testers.notice(kind)))

    def test_declined_nothing_is_sent(self):
        self.assertEqual(self.bridge(kinds.SONOFF_POW_ELITE, lambda word: False), (["proceed"], False))

    def test_a_converted_device_leaving_its_firmware_is_told_too_and_asked_once(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(prompts, "require_word", return_value=True) as asked:
            ctx = a_run(tmp, kinds.SONOFF_TH_ELITE)
            handover.consent_to_leave(ctx, "Tasmota")
            handover.consent_to_leave(ctx, "Tasmota")
        self.assertEqual([call.args[0] for call in asked.call_args_list], ["proceed"])
        self.assertIn("The conversion for this particular device has not been proven on a real", self.said())


class LogTests(unittest.TestCase):
    def told(self, kind):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hostsys, "copy_to_clipboard", return_value=False), \
                mock.patch.object(output, "show"), mock.patch.object(output, "show_link"), \
                mock.patch.object(output, "tell") as told:
            report.offer_report(a_run(tmp, kind), [("Device", kind.name)], "Please share how it went!")
        return " ".join(str(call.args[0]) for call in told.call_args_list if call.args)

    def test_the_report_asks_for_the_log_of_a_kind_wanting_testers(self):
        self.assertIn("No real Sonoff POW Ring had been reported converted: please attach this run's log",
                      self.told(kinds.SONOFF_POWCT))

    def test_it_says_where_the_log_is_open_or_not(self):
        for path, where in ((Path("/runs/run.log"), str(Path("/runs/run.log"))), (None, testers.LOG_ELSEWHERE)):
            with self.subTest(path=path), mock.patch.object(output, "log_path", return_value=path), \
                    mock.patch.object(output, "tell") as told:
                testers.ask_for_log(kinds.SONOFF_POWCT)
            self.assertEqual(told.call_args_list[-1], mock.call(f"    {where}"))

    def test_not_of_a_proven_one(self):
        self.assertNotIn("log", self.told(S60))


if __name__ == "__main__":
    unittest.main()
