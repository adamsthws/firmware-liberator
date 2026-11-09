"""A readout of an Espressif device: only the bridge sent to the device, and
only once PROCEED is typed."""
import unittest
from pathlib import Path
from unittest import mock

from tests.autoflash.test_readout import learned, record, start_run
from tests.espressif.test_scrub import flash as wifi_flash
from tools.autoflash import account, errors, hostsys, kinds, plugs, prompts, readout, readout_espressif, safety_locks

# A kind a whole readout is for (Readout wanted) that the tool does not convert:
# the bridge has never run on a real one.
BASICR4 = kinds.SONOFF_BASICR4
AS_BASICR4 = account.Asked({"packets": []}, "1.1.0", BASICR4.stock_model, "BASICR4", [], {}, "1000abcdef")
DEVICE = record("BASICR4", BASICR4.stock_model)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        start_run(self)
        asked = mock.patch.object(account, "ask_ewelink", return_value=AS_BASICR4)
        asked.start()
        self.addCleanup(asked.stop)

    def test_nothing_is_sent_without_proceed(self):
        with learned(BASICR4.wrapper_model, "ESP32C3"), \
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
        with learned(BASICR4.wrapper_model, "ESP32C3"), \
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
        self.assertEqual(command[command.index("--readout-model") + 1], BASICR4.wrapper_model)
        wrapped = Path(command[command.index("--firmware") + 1]).read_bytes()
        self.assertEqual(analyze_vendor_ota.analyze(wrapped)["wrapper"]["model"], BASICR4.wrapper_model)
        self.assertTrue(offered.call_args.args[1]["read"])
        self.assertTrue(self.ctx.state.input(readout.STATE_KEY))

    def test_on_the_reading_program_already_it_reads_through_it_sending_nothing(self):
        from tools.autoflash import firewall
        from tools.espressif import dump_stock_flash
        self.ctx.state.set_input("plug_ip", "192.0.2.77")
        with mock.patch.object(prompts, "require_word", return_value=True), \
                mock.patch.object(firewall, "choose_port", return_value=8088), \
                mock.patch.object(firewall, "ensure_firewall") as firewalled, \
                mock.patch.object(readout_espressif.installer, "bridge_answers", return_value=True), \
                mock.patch.object(hostsys, "run_tool") as sent, \
                mock.patch.object(readout_espressif, "find_bridge") as waited, \
                mock.patch.object(dump_stock_flash, "read_dump",
                                  return_value=(wifi_flash(), b"\0" * 124, {"size": 1, "sha256": "ab"})) as read, \
                mock.patch.object(dump_stock_flash, "summary", return_value=[]), \
                mock.patch.object(dump_stock_flash, "read_info", return_value=None):
            readout_espressif.read_out(self.ctx, readout_espressif.chips.ESP32C3, "FWSW-E32NEW-NEW-ESP32C3", "1.0.0")
        sent.assert_not_called()
        waited.assert_not_called()
        # A firewall rule the stopped run left is taken over, to be removed as this run ends.
        firewalled.assert_called_once_with(self.ctx)
        self.assertEqual(read.call_args.args[0], "192.0.2.77")
        self.assertEqual(self.ctx.state.input(readout.STATE_KEY), "sent")


class BackOnSonoffTests(unittest.TestCase):
    """A readout's end: the plug restarted by its bridge, else switched off
    and on by the person."""

    def setUp(self):
        start_run(self)

    def test_restarted_by_the_bridge_nobody_is_asked(self):
        self.ctx.state.set_input(readout_espressif.BRIDGE_HOST, "192.0.2.77")
        with mock.patch.object(readout_espressif.installer, "restart_into_sonoff", return_value=True) as restarted, \
                mock.patch.object(prompts, "wait_for_key") as asked:
            self.assertTrue(readout_espressif.back_on_sonoff(self.ctx))
        restarted.assert_called_once_with("192.0.2.77")
        asked.assert_not_called()
        self.assertEqual(self.ctx.state.input(readout.STATE_KEY), "read")

    def test_a_bridge_that_will_not_restart_has_the_person_asked(self):
        self.ctx.state.set_input(readout_espressif.BRIDGE_HOST, "192.0.2.77")
        with mock.patch.object(readout_espressif.installer, "restart_into_sonoff", return_value=False), \
                mock.patch.object(prompts, "wait_for_key") as asked, \
                mock.patch.object(readout_espressif.installer, "back_on_sonoff", return_value=True):
            self.assertTrue(readout_espressif.back_on_sonoff(self.ctx))
        asked.assert_called_once()

    def test_on_its_own_network_the_person_is_asked_to_rejoin_theirs(self):
        self.ctx.state.set_input(readout_espressif.BRIDGE_HOST, readout_espressif.setup_network.SETUP_HOST)
        with mock.patch.object(readout_espressif.installer, "restart_into_sonoff") as restarted, \
                mock.patch.object(prompts, "wait_for_key") as asked, \
                mock.patch.object(readout_espressif.installer, "back_on_sonoff", return_value=True):
            readout_espressif.back_on_sonoff(self.ctx)
        restarted.assert_not_called()
        asked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
