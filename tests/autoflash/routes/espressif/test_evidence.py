"""What a cut-off step left on the device (tools/autoflash/routes/espressif/evidence.py)."""
import unittest
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import kinds, plans
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

    def test_on_the_temporary_layout_still_the_step_is_done_again(self):
        # Killed as D5 wrote the TH Elite's own table back: ESPHome's
        # bootloader was in place from an earlier conversion, D5 was taken for
        # done, and every R1 after was refused for the table.
        kind = kinds.find("sonoff-th-elite")
        layout = images.layout_of(kind)
        expected = images.bootloader_file(chips.ESP32)[2]
        state = mock.Mock(spec=RunState)
        state.input.side_effect = {"device_kind": kind.key}.get
        for table, finished_then in ((layout.temporary_sha256, False), (layout.interim_sha256, False),
                                     (layout.table_sha256, True)):
            device = mock.Mock()
            device.sha_region.side_effect = lambda at, length, table=table: (
                table if at == plans.TABLE_ADDRESS else expected)
            with self.subTest(table=table):
                finished, said = evidence.evidence_bootloader(SimpleNamespace(state=state, device=device))
                self.assertIs(finished, finished_then)
                if not finished:
                    self.assertIn("not back on its own", said)


if __name__ == "__main__":
    unittest.main()
