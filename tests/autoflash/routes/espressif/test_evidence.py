"""What a cut-off step left on the device (tools/autoflash/routes/espressif/evidence.py)."""
import unittest
from types import SimpleNamespace
from unittest import mock

from tools.autoflash.routes.espressif import evidence, images
from tools.autoflash.state import RunState
from tools.espressif import chips


class BootloaderEvidenceTests(unittest.TestCase):
    def test_on_a_classic_esp32_it_is_looked_for_where_that_chip_keeps_it(self):
        # At 0x1000: looked for at 0, ESPHome's was never found in place there.
        expected = images.bootloader_file(chips.ESP32)[2]
        device = mock.Mock()
        device.sha_region.side_effect = lambda at, length: expected if at == chips.ESP32.bootloader_at else "0" * 64
        state = mock.Mock(spec=RunState)
        state.input.side_effect = {"device_kind": "sonoff-pow-elite"}.get
        finished, _ = evidence.evidence_bootloader(SimpleNamespace(state=state, device=device))
        self.assertTrue(finished)


if __name__ == "__main__":
    unittest.main()
