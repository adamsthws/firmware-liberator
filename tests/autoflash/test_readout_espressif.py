"""A readout of an Espressif device: only the bridge sent to the device, and
only once PROCEED is typed."""
import unittest
from pathlib import Path
from unittest import mock

from tests.autoflash.test_readout import DEVICE, learned, start_run
from tests.espressif.test_scrub import flash as wifi_flash
from tools.autoflash import errors, hostsys, plugs, prompts, readout, readout_espressif, safety_locks


class BridgeTests(unittest.TestCase):
    def setUp(self):
        start_run(self)

    def test_nothing_is_sent_without_proceed(self):
        with learned("FWSW-E32NEW-NEW-ESP32C3", "ESP32C3"), \
                mock.patch.object(prompts, "require_word", return_value=False), \
                mock.patch.object(hostsys, "run_tool") as sent:
            with self.assertRaises(errors.NothingWritten):
                readout.run(self.ctx, DEVICE)
        sent.assert_not_called()
        warned = " ".join(prompts.warning_box.call_args.args[1])
        self.assertIn("never run on this kind of device", warned)

    def test_proceed_sends_the_bridge_wrapped_as_its_firmware_name(self):
        from tools.autoflash import firewall
        from tools.espressif import analyze_vendor_ota, dump_stock_flash
        with learned("FWSW-E32NEW-NEW-ESP32C3", "ESP32C3"), \
                mock.patch.object(prompts, "require_word", return_value=True), \
                mock.patch.object(firewall, "choose_port", return_value=8088), \
                mock.patch.object(firewall, "ensure_firewall"), \
                mock.patch.object(safety_locks, "lifted"), mock.patch.object(safety_locks, "say_lock_back"), \
                mock.patch.object(hostsys, "run_tool", return_value=0) as sent, \
                mock.patch.object(readout_espressif, "find_bridge", return_value="192.0.2.77"), \
                mock.patch.object(dump_stock_flash, "read_dump",
                                  return_value=(wifi_flash(), b"\0" * 124, {"size": 1, "sha256": "ab"})), \
                mock.patch.object(dump_stock_flash, "summary", return_value=[]), \
                mock.patch.object(dump_stock_flash, "read_info", return_value=None), \
                mock.patch.object(plugs, "save_backup", return_value=(Path(self.tmp.name), False)), \
                mock.patch.object(readout_espressif, "back_on_sonoff"), \
                mock.patch.object(readout, "offer", return_value="done") as offered:
            self.assertEqual(readout.run(self.ctx, DEVICE), "done")
        command = sent.call_args.args[0]
        self.assertEqual(command[1], "-u")  # each line of the sender's in the log as it comes
        self.assertEqual(command[command.index("--readout-model") + 1], "FWSW-E32NEW-NEW-ESP32C3")
        wrapped = Path(command[command.index("--firmware") + 1]).read_bytes()
        self.assertEqual(analyze_vendor_ota.analyze(wrapped)["wrapper"]["model"], "FWSW-E32NEW-NEW-ESP32C3")
        self.assertTrue(offered.call_args.args[1]["read"])
        self.assertTrue(self.ctx.state.input(readout.STATE_KEY))


if __name__ == "__main__":
    unittest.main()
