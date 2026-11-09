"""Updates (tools/autoflash/routes/update.py): replacing a person's own build."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import esphome, kinds, prompts
from tools.autoflash.errors import Abort
from tools.autoflash.routes import update
from tools.autoflash.state import RunState
from tests.autoflash.test_unlock import drawn


class ReplaceOwnBuildTests(unittest.TestCase):
    """Its warning and go-ahead leave the screen once agreed, as every other
    go-ahead does, and stay when declined: they say why the run stops."""

    def replace(self, agreed):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = RunState.open(Path(directory.name))
        state.set_input("plug_ip", "192.0.2.50")
        state.set_input("plug_kind", "sonoff-s60")
        ctx = SimpleNamespace(state=state, esphome_key=None)
        info = {"project_name": kinds.SONOFF_S60.esphome_project, "built": "someone's own", "encrypted": True}
        result = {}

        def body(look):
            with mock.patch.object(esphome, "api_device_info", return_value=info), \
                 mock.patch.object(prompts, "require_word", side_effect=lambda word: look() or agreed):
                try:
                    result["replaced"] = update.replace_own_build(ctx, "esphome", "2026.10.0-1")
                except Abort as exc:
                    result["refused"] = str(exc)
        asked, final = drawn(body)
        return asked[0], final, result

    def test_agreed_its_warning_goes(self):
        asked, final, result = self.replace(agreed=True)
        self.assertIn("Replacing your own build", asked)
        self.assertNotIn("Replacing your own build", final)
        self.assertTrue(result["replaced"])

    def test_declined_it_stays_to_say_why(self):
        _, final, result = self.replace(agreed=False)
        self.assertIn("Replacing your own build", final)
        self.assertEqual(result["refused"], "not confirmed")


if __name__ == "__main__":
    unittest.main()
