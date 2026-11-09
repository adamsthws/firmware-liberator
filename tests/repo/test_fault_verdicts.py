"""What both fault suites judge a run by beyond its steps: the report's
end-of-run checks, and the readout a readout keeps."""

import io
import tarfile
import unittest

from tools.autoflash import readout, report
from tools.ewelink.offered_firmware import Offered
from tools.repo import fault_verdicts


def run_log(*lines, before=""):
    """A run log as the tool writes one: each line with its time first."""
    return before + "".join(f"[10:00:0{n}] {line}\n" for n, line in
                            enumerate(["=== run started, tool version 2.3.0 ===", *lines, "=== run ended ==="]))


class EndChecksTests(unittest.TestCase):
    def test_a_relay_that_switched_passes_on_every_firmware(self):
        log = run_log("    Relay: Switches (meter confirms)", "    Checks passed: True")
        for firmware in ("tasmota", "esphome", "openbeken", "sonoff"):
            self.assertIsNone(fault_verdicts.checks_problem(log, firmware), firmware)

    def test_checks_that_failed_say_what_the_relay_check_said(self):
        # As every Espressif run back to Sonoff's firmware ended before 83c517d:
        # the relay asked through eWeLink before the plug was there.
        log = run_log("    Relay: Could not be checked", "    Device model:   S60TPG", "    Checks passed: False")
        self.assertEqual(fault_verdicts.checks_problem(log, "sonoff"),
                         "Checks passed: False; Relay: Could not be checked")

    def test_checks_never_run_fail_only_where_the_emulator_can_run_them(self):
        log = run_log("    Checks passed: Not run")
        self.assertIsNone(fault_verdicts.checks_problem(log, "sonoff"))   # no eWeLink device id kept
        for firmware in ("tasmota", "esphome", "openbeken"):
            self.assertEqual(fault_verdicts.checks_problem(log, firmware),
                             "Checks passed: Not run; Relay: not said", firmware)

    def test_a_metered_plug_needs_its_meter_to_confirm_the_switch(self):
        """As the Beken suite asks: its emulated S61 measures a load while the
        relay is on (tools/beken/emulator/board.py)."""
        unchanged = run_log("    Relay: Switches (meter unchanged)", "    Checks passed: True")
        for firmware in ("esphome", "openbeken"):
            self.assertIsNone(fault_verdicts.checks_problem(unchanged, firmware), firmware)
            self.assertEqual(fault_verdicts.checks_problem(unchanged, firmware, metered=True),
                             "the meter did not confirm the switch; Relay: Switches (meter unchanged)")
        confirms = run_log("    Relay: Switches (meter confirms)", "    Checks passed: True")
        self.assertIsNone(fault_verdicts.checks_problem(confirms, "openbeken", metered=True))
        # Back on Sonoff's firmware the relay is switched through eWeLink, and no meter is read.
        through = run_log("    Relay: Switches (through eWeLink)", "    Checks passed: True")
        self.assertIsNone(fault_verdicts.checks_problem(through, "sonoff", metered=True))

    def test_only_the_last_run_counts(self):
        earlier = run_log("    Relay: Did not switch", "    Checks passed: False")
        self.assertIsNone(fault_verdicts.checks_problem(
            run_log("    Checks passed: True", before=earlier), "tasmota"))
        self.assertIsNotNone(fault_verdicts.checks_problem(
            run_log("    Checks passed: False", before=run_log("    Checks passed: True")), "tasmota"))

    def test_the_report_is_read_as_the_tool_lays_it_out(self):
        fields = [("Result", "Converted"), ("Device Wi-Fi signal", "-60 dBm"), ("Checks passed", "False")]
        log = run_log("    Relay: Did not switch", *report.report_screen(fields))
        self.assertEqual(fault_verdicts.checks_problem(log, "tasmota"), "Checks passed: False; Relay: Did not switch")

    def test_a_run_with_no_report_has_nothing_to_judge(self):
        self.assertIsNone(fault_verdicts.checks_problem(run_log("Nothing to convert."), "tasmota"))
        self.assertIsNone(fault_verdicts.checks_problem("", "tasmota"))


FLASH = b"\xff" * fault_verdicts.WHOLE_FLASH
OFFERED = [Offered("user1.bin", "1.2.0", b"stock app")]


def kept(family, flash=FLASH, files=None, updates=OFFERED, matches=True, **manifest):
    """A readout.tar as the tool packs one (readout.package) for `family`;
    `matches`, an Espressif read's own hash check."""
    whole = {"espressif": {"flash.bin": flash, "efuse.bin": b"efuse", "info.txt": b"info", "report.json": b"{}"},
             "beken": {"flash.bin": flash, "info.txt": b"{}"}}[family]
    flash_entry = {"size": len(flash), "sha256": "ab"} | ({"matches_device": matches} if family == "espressif" else {})
    return readout.package({"format": 1, "read": True, "flash": flash_entry, **manifest},
                           whole if files is None else files, updates)


class ReadoutTests(unittest.TestCase):
    def test_a_whole_readout_of_each_family_lacks_nothing(self):
        for family in ("espressif", "beken"):
            self.assertEqual(fault_verdicts.readout_problems(kept(family), family), [], family)

    def test_each_file_docs_readouts_lists_must_be_there(self):
        self.assertEqual(fault_verdicts.readout_problems(kept("espressif", files={"flash.bin": FLASH}), "espressif"),
                         ["no efuse.bin.xz", "no info.txt", "no report.json"])
        self.assertEqual(fault_verdicts.readout_problems(kept("beken", updates=()), "beken"),
                         ["no offered firmware"])

    def test_the_whole_flash_read(self):
        part = kept("beken", flash=FLASH[:0x100000])
        self.assertEqual(fault_verdicts.readout_problems(part, "beken"),
                         ["flash.bin is 1,048,576 bytes, the manifest says 1048576: not the whole 4,194,304"])
        self.assertEqual(fault_verdicts.readout_problems(kept("espressif", matches=None), "espressif"),
                         ["the flash read does not match the device's own hash"])

    def test_read_unsent_and_with_no_contact(self):
        self.assertEqual(fault_verdicts.readout_problems(kept("beken", read=False), "beken"),
                         ["the manifest says the memory was not read"])
        self.assertEqual(fault_verdicts.readout_problems(
            readout.package({"format": 1, "read": True, "flash": {"size": len(FLASH)}},
                            {"flash.bin": FLASH, "info.txt": b"{}"}, OFFERED, {"email": "someone@example.com"}),
            "beken"), ["a contact in it, from an unattended run"])

    def test_the_manifest_first_as_the_api_reads_it(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(kept("beken"))) as tar, tarfile.open(fileobj=buffer, mode="w") as out:
            for member in reversed(tar.getmembers()):
                out.addfile(member, tar.extractfile(member))
        self.assertEqual(fault_verdicts.readout_problems(buffer.getvalue(), "beken"), ["manifest.json is not first"])

    def test_something_not_a_readout_says_so(self):
        self.assertTrue(fault_verdicts.readout_problems(b"not a tar", "beken")[0].startswith("not a tar"))


if __name__ == "__main__":
    unittest.main()


class ScreenTests(unittest.TestCase):
    """Each run's screen, as the fault suites keep it, is looked at."""

    RULE = "=" * 72

    def recorded(self, *rows):
        return (f"--- run\nThe emulated plug is up; converting it unattended...\n{self.RULE}\n"
                f"The screen at the end of the run (100x40):\n{self.RULE}\n" + "".join(f"{row}\n" for row in rows)
                + f"{self.RULE}\nExit code 0 - finished\n")

    def test_every_run_s_screen_is_looked_at_colours_and_all(self):
        clean = self.recorded("\x1b[0m\x1b[32mStage 1\x1b[0m - Checking - \x1b[32mComplete\x1b[0m")
        left = self.recorded("Stage 1 - Checking - Stopped", "Stage 1 - Checking - Complete")
        self.assertEqual(fault_verdicts.screen_problems(clean), [])
        self.assertEqual(len(fault_verdicts.screen_problems(clean + left)), 1)

    def test_output_with_no_screen_has_nothing_to_judge(self):
        self.assertEqual(fault_verdicts.screen_problems("Stage 1 - Checking - Stopped\nStage 1 - Checking - Complete\n"), [])


class RecordingTests(unittest.TestCase):
    def test_each_runs_recording_is_kept_beside_the_scenario(self):
        # The emulator's own copies go when its next scenario begins: seen,
        # only the last scenario's recording was left to replay.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            work, kept = Path(directory) / "work", Path(directory) / "logs/scenario-screens"
            for name, drawn in (("20261109-000339", b"first run"), ("20261109-000607", b"second run")):
                (work / "captures/unattended" / name).mkdir(parents=True)
                (work / "captures/unattended" / name / "screen.raw").write_bytes(drawn)
            output = ("--- run\nKept in captures/unattended/20261109-000339/: screen.ansi (cat it), screen.txt, screen.raw\n"
                      "--- run\nKept in /work/captures/unattended/20261109-000607/: screen.ansi (cat it), screen.txt, "
                      "screen.raw\n--- run\nKept in captures/unattended/gone/: screen.ansi (cat it)\n")
            found = fault_verdicts.keep_recordings(output, work, kept)
            self.assertEqual([path.name for path in found], ["run-1.raw", "run-2.raw"])
            self.assertEqual((kept / "run-2.raw").read_bytes(), b"second run")


class ReportLabelTests(unittest.TestCase):
    def test_the_checks_are_read_however_the_report_said_them(self):
        # Shorter, and in two columns, since 2.4.0; an earlier release's runs
        # (the legacy scenarios) say it as before.
        for log in ("[10:00:02]     Checks passed: False\n",
                    "[10:00:02]     Setup network: No                      Checks:   False\n"):
            with self.subTest(log=log):
                self.assertEqual(fault_verdicts.checks_problem(log, "tasmota"),
                                 "Checks passed: False; Relay: not said")
