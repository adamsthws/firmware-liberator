"""What a step cut off left on the device, and whether a step recorded done
still holds: judged by what the device answers as."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.autoflash.routes.beken.test_beken import a_context
from tools.autoflash import detect
from tools.autoflash.routes import beken
from tools.autoflash.routes.beken import evidence


class EvidenceInstalledTests(unittest.TestCase):
    def test_a_device_still_on_the_interim_firmware_did_not_take_esphome(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = a_context(tmp, firmware="esphome", source="sonoff")
            for running, expected in (
                    ("interim", (False, "The device still answers as the interim firmware: the image was not "
                                        "applied.")),
                    ("esphome", (True, "The device answers as ESPHome."))):
                with mock.patch.object(detect, "identify", return_value=running):
                    self.assertEqual(evidence.evidence_installed(ctx), expected)


class StillInstalledTests(unittest.TestCase):
    """A power cut while the image is written leaves the device on the firmware
    it was leaving, its step recorded done (seen in the fault suite)."""

    def judge(self, *answers):
        with mock.patch.object(evidence, "evidence_installed", side_effect=list(answers)), \
                mock.patch.object(evidence.time, "sleep"), \
                mock.patch.object(evidence.time, "monotonic", side_effect=[0, 0, 10, 100, 200]):
            return evidence.still_installed(SimpleNamespace(), wait=60)

    def test_still_on_the_firmware_it_left_is_doubted(self):
        ok, message = self.judge((False, "The device still answers as OpenBeken: the image was not applied."))
        self.assertFalse(ok)
        self.assertEqual(message, "the device still answers as OpenBeken: the image was not applied")

    def test_on_the_new_firmware_it_stands(self):
        self.assertTrue(self.judge((True, "The device answers as ESPHome."))[0])

    def test_a_device_not_answering_yet_is_waited_for_then_left_be(self):
        waiting = (None, "The device does not answer as the new firmware yet.")
        self.assertTrue(self.judge(waiting, waiting, waiting)[0])

    def test_the_onward_steps_are_checked_on_resume(self):
        self.assertEqual(set(beken.STEP_VERIFIERS), {"J2", "J4", "O1", "O2", "E1", "E2", "U1", "U3"})


if __name__ == "__main__":
    unittest.main()
