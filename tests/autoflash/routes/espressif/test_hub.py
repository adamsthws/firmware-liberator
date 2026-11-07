"""The hub (tools/autoflash/routes/espressif/hub.py): Tasmota restarted before
a promote is tried again, and an upload it refused."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import output
from tools.autoflash.routes.espressif import hub
from tools.autoflash.state import FAILED, RunState


class PromoteTests(unittest.TestCase):
    """D4 - tried again after an upload cut off, Tasmota is restarted first:
    it still holds that upload, and the next hangs."""

    def promote(self, failed_before, slots=(0, 1)):
        from types import SimpleNamespace

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        if failed_before:
            state.mark("D4", FAILED, error="Berry Tasmota was not uploaded")
        events = []
        slots = iter(slots)
        device = SimpleNamespace(host="192.168.60.50",
            wait_reachable=lambda **_: None, started_at=lambda: "t", uptime_seconds=lambda: 10,
            current_ota=lambda: next(slots), wait_for_reboot=lambda **_: events.append("rebooted"),
            # What the plug is told, in order; a status read changes nothing.
            command=lambda c, timeout=None: {} if c.startswith("Status") else events.append(c))
        ctx = SimpleNamespace(device=device, state=state, dry_run=False)
        with mock.patch.object(hub, "upload_or_prompt", side_effect=lambda *a: events.append("upload")), \
             mock.patch.object(time, "sleep"), mock.patch.object(output, "say"):
            hub.step_promote(ctx)
        return events

    def test_a_slot_not_known_yet_after_the_restart_is_waited_for(self):
        # Seen in the fault suite: just restarted, Berry not up, the slot read
        # as unknown - taken for "not slot 1", and a second upload sent.
        events = self.promote(False, slots=(0, None, None, 1))
        self.assertEqual(events.count("upload"), 1)

    def test_a_first_try_uploads_at_once(self):
        self.assertEqual(self.promote(False)[0], "upload")

    def test_tried_again_it_restarts_first(self):
        events = self.promote(True)
        self.assertEqual(events[:3], ["Restart 1", "rebooted", "upload"])


class RefusedUploadTests(unittest.TestCase):
    """An upload Tasmota took whole and refused (damaged on the way, say) is
    sent again, three tries in all, before it is left to the person."""

    def upload(self, outcomes, boots=None, answers=True):
        """upload_or_prompt with each upload's outcome from `outcomes`, and the
        boot the plug says it is on, in turn, from `boots` (one boot throughout
        if not given)."""
        from types import SimpleNamespace
        from tools.autoflash.device import DeviceError
        tries = iter(outcomes)
        boot = iter(boots or ["boot 1"] * 20)
        sent = []

        def upload_firmware(path, timeout=None):
            sent.append(path)
            outcome = next(tries)
            if isinstance(outcome, Exception):
                raise outcome

        def wait_reachable(timeout=None):
            if not answers:
                raise DeviceError("did not answer")

        ctx = SimpleNamespace(device=SimpleNamespace(upload_firmware=upload_firmware, started_at=lambda: next(boot),
                                                     wait_reachable=wait_reachable))
        with mock.patch.object(hub, "upload_or_prompt_by_hand", return_value="manual") as by_hand, \
             mock.patch.object(time, "sleep"), \
             mock.patch.object(output, "tell") as told, mock.patch.object(output, "say"):
            result = hub.upload_or_prompt(ctx, Path("berry.bin"), "Berry Tasmota")
        return result, len(sent), by_hand.called, " ".join(str(c.args[0]) for c in told.call_args_list)

    def test_an_upload_cut_off_and_not_taken_is_sent_again(self):
        result, sent, by_hand, told = self.upload([TimeoutError("timed out"), None])
        self.assertTrue(result.startswith("http:"))
        self.assertEqual((sent, by_hand), (2, False))
        self.assertIn("cut off", told)

    def test_an_upload_cut_off_but_taken_is_not_sent_again(self):
        # Taken, it is booted: another would write the other slot and boot that.
        result, sent, by_hand, _ = self.upload([ConnectionResetError("reset")], boots=["boot 1", "boot 2"])
        self.assertEqual((result, sent, by_hand), ("http:answer lost", 1, False))

    def test_an_upload_cut_off_from_a_plug_that_does_not_answer_is_left_to_the_person(self):
        result, sent, by_hand, _ = self.upload([TimeoutError("timed out")], answers=False)
        self.assertEqual((result, sent, by_hand), ("manual", 1, True))

    def test_one_refusal_is_sent_again(self):
        from tools.autoflash.device import UploadRefused
        result, sent, by_hand, told = self.upload([UploadRefused("Bad magic byte"), None])
        self.assertTrue(result.startswith("http:"))
        self.assertEqual(sent, 2)
        self.assertIn("refused the upload (Bad magic byte)", told)
        self.assertFalse(by_hand)

    def test_three_refusals_leave_it_to_the_person(self):
        from tools.autoflash.device import UploadRefused
        result, sent, by_hand, _ = self.upload([UploadRefused("Not enough space")] * 3)
        self.assertEqual((result, sent, by_hand), ("manual", 3, True))

    def test_any_other_failure_goes_to_the_person_at_once(self):
        result, sent, by_hand, _ = self.upload([OSError("no route")])
        self.assertEqual((result, sent, by_hand), ("manual", 1, True))


if __name__ == "__main__":
    unittest.main()
