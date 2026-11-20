"""public_devices.py, the public devices each final firmware is tried on, and their runs."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import kinds
from tools.espressif.emulator import fault_suite, public_devices
from tools.repo import fault_verdicts


class PublicDevicesTests(unittest.TestCase):
    def test_a_kept_device_counts_as_stopped_only_after_its_step(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory)
            run = saved / "captures/devices/12-34-56-ab-cd-ef/runs/1"
            run.mkdir(parents=True)
            (saved / "captures/.autoflash-current").write_text("devices/12-34-56-ab-cd-ef/runs/1\n")
            (saved / "flash.bin").write_bytes(b"")
            (run / "autoflash-state.json").write_text(json.dumps({"steps": {"E3": {"status": "attempted"}}}))
            self.assertFalse(public_devices.step_done(saved, "E3"))
            (run / "autoflash-state.json").write_text(json.dumps({"steps": {"E3": {"status": "done"}}}))
            self.assertTrue(public_devices.step_done(saved, "E3"))

    def test_each_final_firmware_is_installed_then_left(self):
        self.assertEqual({way[0][1] for way in public_devices.WAYS}, {"tasmota", "esphome"})
        for (_, first, kept), (source, second, _) in public_devices.WAYS:
            self.assertEqual(source, kept)   # from the device its own first run made
            self.assertNotEqual(second, first)

    def converted(self, relay, meter, kind=kinds.SONOFF_S60, firmware="esphome", serial=None):
        """public_devices.run's answer for a run to `firmware` that converted
        the device, its check saying `relay` and `meter`, its serial output
        `serial` (after an earlier run's)."""
        with tempfile.TemporaryDirectory() as directory:
            emu = Path(directory)
            run_log = emu / "state-7/work/captures/devices/12-34-56-ab-cd-ef/runs/1/run.log"
            run_log.parent.mkdir(parents=True)
            uart = emu / "state-7/logs/uart.log"
            uart.parent.mkdir(parents=True)
            uart.write_text("00:00:00.100 NRG: Init driver 19\n", encoding="utf-8")   # an earlier run's boot

            def popen(args, stdout, stderr):
                if serial is not None:
                    with open(uart, "a", encoding="utf-8") as out:
                        out.write(serial)
                stdout.write("    Result:   Converted\n")   # the screen
                run_log.write_text("[10:00:00] === run started, tool version 3.0.0 ===\n"
                                   f"[10:00:01]     Relay: {relay}\n[10:00:01]     Power meter: {meter}\n"
                                   "[10:00:02]     Checks passed: Pass\n[10:00:03] === run ended ===\n",
                                   encoding="utf-8")
                later = time.time() + 60   # past the run's start, as a file's coarser clock may not be
                os.utime(run_log, (later, later))
                return mock.Mock(wait=mock.Mock(return_value=0))
            with mock.patch.object(fault_suite, "EMU", emu), mock.patch.object(public_devices.subprocess, "Popen", popen):
                return public_devices.run(7, f"public-pre-final-{firmware}", firmware, None, emu / "tool.log",
                                          kind=kind)

    def test_a_run_passes_once_its_end_of_run_checks_do(self):
        self.assertEqual(self.converted("Switches (meter confirms)", "On: 230 V, 100 W; off: 0 V, 0 W"),
                         (True, "Converted"))

    def test_a_converted_device_whose_meter_read_nothing_has_failed(self):
        """As the S60's public devices did, in every check, while the emulator
        had not wired their meter."""
        self.assertEqual(self.converted("Switches (meter unchanged)", "No readings"),
                         (False, "Converted, but the meter did not confirm the switch; "
                                 "Relay: Switches (meter unchanged); Power meter: No readings"))
        # The TH Elite has no meter, so nothing to read.
        self.assertTrue(self.converted("Switches", "not shown", kind=kinds.SONOFF_TH_ELITE)[0])

    def test_a_cse7761_tasmota_missed_is_named_as_the_emulator_miss_it_is(self):
        failed = ("Converted, but the meter did not confirm the switch; Relay: Switches (meter unchanged); "
                  "Power meter: No readings")
        missed = "00:00:00.737 NRG: Init driver 19\n00:00:09.100 WIF: Connecting\n"
        self.assertEqual(self.converted("Switches (meter unchanged)", "No readings", kinds.SONOFF_DUALR3, "tasmota",
                                        missed),
                         (False, f"{failed}\n    {fault_verdicts.CSE7761_MISSED}"))
        # Found this time: the meter's failure is some other.
        self.assertEqual(self.converted("Switches (meter unchanged)", "No readings", kinds.SONOFF_DUALR3, "tasmota",
                                        missed + "00:00:04.651 C61: CSE7761 found\n"), (False, failed))
        # Only this run's serial output counts, not an earlier one's boot.
        self.assertEqual(self.converted("Switches (meter unchanged)", "No readings", kinds.SONOFF_DUALR3, "tasmota"),
                         (False, failed))
        self.assertEqual(self.converted("Switches (meter unchanged)", "No readings", kinds.SONOFF_DUALR3, "esphome",
                                        missed), (False, failed))

    def test_each_way_goes_on_without_waiting_for_the_other(self):
        import argparse
        import io
        import threading
        from contextlib import redirect_stdout
        from unittest import mock
        # The way to Tasmota's first run fails, and lasts until the way to
        # ESPHome's second run starts - which it does only if not waiting on it.
        events, lock = [], threading.Lock()
        second_started = threading.Event()

        def fake_run(instance, source, firmware, save, log, kind):
            with lock:
                events.append(("start", source))
            if source == "public-on-esphome":
                second_started.set()
            if source == "public-pre-final-tasmota":
                second_started.wait(10)
            with lock:
                events.append(("end", source))
            ok = source != "public-pre-final-tasmota"
            return ok, "Converted" if ok else "exit 1"

        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory)
            for start in ("public-pre-final-tasmota", "public-pre-final-esphome"):
                (saved / start).mkdir()
                (saved / start / "flash-mode").write_text("public\n")
            out = io.StringIO()
            with mock.patch.object(public_devices, "SAVED", saved), \
                    mock.patch.object(public_devices, "LOGS", saved / "logs"), \
                    mock.patch.object(public_devices, "run", side_effect=fake_run), redirect_stdout(out):
                code = public_devices.command_test(argparse.Namespace(instances=[1, 2]))
        self.assertEqual(code, 1)
        self.assertLess(events.index(("start", "public-on-esphome")),
                        events.index(("end", "public-pre-final-tasmota")))
        self.assertNotIn(("start", "public-on-tasmota"), events)
        said = out.getvalue()
        self.assertIn("FAIL  public-pre-final-tasmota to tasmota: exit 1", said)
        self.assertIn("SKIP  public-on-tasmota to esphome: the run before it failed", said)
        self.assertIn("PASS  public-on-esphome to tasmota: Converted", said)
        self.assertIn("1 failed", said)


if __name__ == "__main__":
    unittest.main()
