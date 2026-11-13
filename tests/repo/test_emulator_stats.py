import http.server
import json
import re
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.autoflash import kinds
from tools.repo import emulator_stats

ROOT = Path(__file__).resolve().parents[2]
ADMIN = (ROOT / "website/admin/emulator.js").read_text(encoding="utf-8")


def run_args(**more):
    return {"family": "espressif", "suite": "fault", "device": "sonoff-s60", "scenario": "sonoff>tasmota:D4:deviceoff-1",
            "verdict": "PASS", "cloud": "stub", "route": "sonoff>tasmota", "step": "D4", "fault": "deviceoff",
            "at": 40, "tries": 1, "seconds": 300, "workers": 6, **more}


class Admin(http.server.BaseHTTPRequestHandler):
    """The admin view's upload, as emulator.js answers it: what `answer`
    says, every run taken but those `refuse` names."""
    answer = 200
    refuse: set = set()
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.headers, body))
        if self.answer != 200:
            self.send_response(self.answer)
            self.end_headers()
            return
        runs = body["runs"]
        said = json.dumps({"taken": [run["id"] for run in runs if run["id"] not in self.refuse],
                           "refused": [{"id": run["id"], "why": "made up"} for run in runs if run["id"] in self.refuse]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(said.encode())

    def log_message(self, *args):
        pass


class EmulatorStatsTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Admin)   # a port of its own
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        Admin.answer, Admin.refuse, Admin.seen = 200, set(), []
        self.env_file = self.folder / "home" / "emulator.env"
        self.env_file.parent.mkdir()
        self.env_file.write_text("EMULATOR_STATS_CLIENT_ID=cid.access\nEMULATOR_STATS_CLIENT_SECRET=made-up\n",
                                 encoding="utf-8")
        self.enterContext(mock.patch.object(emulator_stats, "CREDENTIALS", self.env_file))
        self.enterContext(mock.patch.dict("os.environ", {
            "AUTOFLASH_EMU_STATS_URL": f"http://127.0.0.1:{server.server_address[1]}/v1/emulator/runs",
            "EMULATOR_STATS_CLIENT_ID": "", "EMULATOR_STATS_CLIENT_SECRET": "", "AUTOFLASH_EMU_NO_STATS": ""}))

    def settled(self):
        """Once every background send is done: the runs a test kept, sent or not."""
        with emulator_stats._sending:
            pass
        deadline = time.monotonic() + 10
        while emulator_stats._waiting and time.monotonic() < deadline:
            time.sleep(0.01)
        with emulator_stats._sending:
            pass

    def kept(self):
        path = self.folder / emulator_stats.UNSENT
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_a_run_has_every_field_the_admin_view_takes_and_no_other(self):
        fields = re.search(r"const FIELDS = \{\n(.*?)\n\};", ADMIN, re.S).group(1)
        self.assertEqual(sorted(emulator_stats.run_row(**run_args())), sorted(re.findall(r"^  (\w+):", fields, re.M)))

    def test_as_many_a_request_as_the_admin_view_takes(self):
        self.assertIn(f"export const MAX_RUNS = {emulator_stats.CHUNK};", ADMIN)

    def test_a_run_is_said_as_the_admin_view_takes_it(self):
        row = emulator_stats.run_row(**run_args(scenario="caf\u00e9 \x00" + "x" * 300, fault=60, at=150, tries="x",
                                                verdict="FAIL-SCREEN: shown: 'x'"))
        self.assertRegex(row["id"], r"^[0-9a-f]{32}$")
        self.assertRegex(row["finished"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(row["batch"], emulator_stats.BATCH)
        self.assertEqual((row["scenario"][:6], len(row["scenario"])), ("caf? ?", 200))
        self.assertEqual((row["fault"], row["at"], row["tries"], row["verdict"]), ("60", None, None, "FAIL-SCREEN"))
        self.assertIsInstance(row["dirty"], bool)
        self.assertEqual(emulator_stats.verdict_of("pass"), "PASS")
        self.assertEqual(emulator_stats.verdict_of("???"), "ERROR")

    def test_each_device_has_the_firmware_the_checkout_installs_on_it(self):
        for kind in kinds.EVERY:
            firmware = emulator_stats.firmware(kind.key)
            self.assertTrue(firmware, kind.key)
            self.assertLessEqual(len(firmware), 16, "the admin view takes at most 16")
        self.assertIn("tasmota", emulator_stats.firmware("sonoff-s60"))
        self.assertIn("openbeken", emulator_stats.firmware("sonoff-s61"))

    def test_each_run_is_sent_as_it_is_kept_with_the_token(self):
        for verdict in ("PASS", "FAIL"):
            emulator_stats.record(self.folder, **run_args(verdict=verdict))
            self.settled()
        self.assertEqual(self.kept(), [])
        self.assertEqual([[run["verdict"] for run in body["runs"]] for _, body in Admin.seen], [["PASS"], ["FAIL"]])
        headers = Admin.seen[0][0]
        self.assertEqual((headers.get("CF-Access-Client-Id"), headers.get("CF-Access-Client-Secret")),
                         ("cid.access", "made-up"))
        self.assertEqual(emulator_stats.send(self.folder), "Emulator stats: none to send")

    def test_runs_kept_while_a_send_is_under_way_are_sent_after_it(self):
        with emulator_stats._sending:   # a send under way
            for _ in range(3):
                emulator_stats.record(self.folder, **run_args())
            self.assertEqual(len(self.kept()), 3)
        self.assertEqual(emulator_stats.send(self.folder), "Emulator stats: 3 sent")
        self.settled()
        self.assertEqual(self.kept(), [])
        self.assertEqual(sum(len(body["runs"]) for _, body in Admin.seen), 3, "each sent once")

    def test_runs_left_over_are_sent_as_the_batch_ends(self):
        Admin.answer = 503
        for _ in range(2):
            emulator_stats.record(self.folder, **run_args())
            self.settled()
        self.assertEqual(len(self.kept()), 2)
        Admin.answer = 200
        self.assertEqual(emulator_stats.send(self.folder), "Emulator stats: 2 sent")
        self.assertEqual(self.kept(), [])

    def test_a_run_refused_is_said_and_not_kept_for_ever(self):
        with emulator_stats._sending:
            emulator_stats.record(self.folder, **run_args())
            emulator_stats.record(self.folder, **run_args())
            Admin.refuse = {self.kept()[0]["id"]}
        self.assertIn("1 sent, 1 refused", emulator_stats.send(self.folder))
        self.assertEqual(self.kept(), [])

    def test_runs_the_admin_view_did_not_take_are_kept_for_the_next_batch(self):
        Admin.answer = 503
        emulator_stats.record(self.folder, **run_args())
        self.settled()
        self.assertIn("1 kept to send later (the admin view answered 503)", emulator_stats.send(self.folder))
        self.assertEqual(len(self.kept()), 1)
        Admin.answer = 200
        self.assertEqual(emulator_stats.send(self.folder), "Emulator stats: 1 sent")

    def test_more_than_a_request_takes_are_sent_as_several(self):
        with emulator_stats._sending:
            for _ in range(emulator_stats.CHUNK + 1):
                emulator_stats.record(self.folder, **run_args())
        self.assertIn(f"{emulator_stats.CHUNK + 1} sent", emulator_stats.send(self.folder))
        self.assertEqual([len(body["runs"]) for _, body in Admin.seen], [emulator_stats.CHUNK, 1])

    def test_without_the_token_nothing_is_sent_and_it_says_where_it_comes_from(self):
        self.env_file.unlink()
        emulator_stats.record(self.folder, **run_args())
        self.settled()
        said = emulator_stats.send(self.folder)
        self.assertIn("1 kept, not sent - no EMULATOR_STATS_CLIENT_ID", said)
        self.assertIn("cloudflare_settings.py writes it", said)
        self.assertEqual((Admin.seen, len(self.kept())), ([], 1))

    def test_switched_off_nothing_is_kept_nor_sent(self):
        # As every test has it (tests/no_network.py), so a suite's batch run in a test sends nothing.
        with emulator_stats._sending:   # kept, its send left for later
            emulator_stats.record(self.folder, **run_args())
        with mock.patch.dict("os.environ", {"AUTOFLASH_EMU_NO_STATS": "1"}):
            emulator_stats.record(self.folder, **run_args())
            self.assertEqual(emulator_stats.send(self.folder), "Emulator stats: off (AUTOFLASH_EMU_NO_STATS)")
        self.assertEqual((len(self.kept()), Admin.seen), (1, []))
        emulator_stats._waiting.discard(self.folder)

    def test_the_credentials_are_written_for_their_owner_alone(self):
        with mock.patch.object(emulator_stats, "CREDENTIALS", self.folder / "new" / "emulator.env"):
            path = emulator_stats.write_credentials("cid2.access", "made-up-2")
            self.assertEqual(emulator_stats.client(), ("cid2.access", "made-up-2"))
        self.assertEqual((path.stat().st_mode & 0o777, path.parent.stat().st_mode & 0o777), (0o600, 0o700))

    def test_push_sends_the_credentials_on_ssh_s_input_never_its_command_line(self):
        ran = []

        def ssh(command, **kwargs):
            ran.append((command, kwargs["input"]))
            return subprocess.CompletedProcess(command, 255 if command[-2] == "down" else 0, "", "no route")
        with mock.patch.object(emulator_stats.subprocess, "run", side_effect=ssh):
            self.assertEqual(emulator_stats.push(["up", "down"]), ["down: ssh ended 255: no route"])
        self.assertEqual([command[-2] for command, _ in ran], ["up", "down"])
        for command, given in ran:
            self.assertEqual(given, self.env_file.read_text(encoding="utf-8"))
            self.assertNotIn("made-up", " ".join(command))
            self.assertIn("umask 077", command[-1])
            self.assertIn('"$HOME/.config/firmware-liberator/emulator.env"', command[-1])

    def test_push_names_its_machines_from_the_environment_or_env_infra(self):
        infra = self.folder / ".env.infra"
        infra.write_text("EMULATOR_STATS_HOSTS=one two\n", encoding="utf-8")
        with mock.patch.object(emulator_stats, "INFRA_FILE", infra), mock.patch.dict("os.environ", {"EMULATOR_STATS_HOSTS": ""}):
            self.assertEqual(emulator_stats.hosts(), ["one", "two"])
            with mock.patch.dict("os.environ", {"EMULATOR_STATS_HOSTS": "three"}):
                self.assertEqual(emulator_stats.hosts(), ["three"])

    def test_the_environment_wins_over_the_file(self):
        with mock.patch.dict("os.environ", {"EMULATOR_STATS_CLIENT_ID": "other.access"}):
            self.assertEqual(emulator_stats.client(), ("other.access", "made-up"))


class EveryFamilyTests(unittest.TestCase):
    """Every chip family's suites send their runs alike: each fault suite, a
    family's to come too, and the attended suite for each family it runs."""

    def test_every_fault_suite_records_each_run_and_sends_as_its_batch_ends(self):
        suites = sorted(ROOT.glob("tools/*/emulator/fault_suite.py"))
        self.assertGreaterEqual(len(suites), 2)
        for suite in suites:
            source = suite.read_text(encoding="utf-8")
            self.assertIn("emulator_stats.fault_run(OUT, family=DEVICE.family, device=DEVICE.key, scenario=sc,",
                          source, suite)
            self.assertIn("print(emulator_stats.send(OUT), flush=True)", source, suite)

    def test_the_attended_suite_records_each_family_it_runs_as_that_familys_device(self):
        from tools.espressif.emulator import attended_suite
        source = (ROOT / "tools/espressif/emulator/attended_suite.py").read_text(encoding="utf-8")
        self.assertIn("emulator_stats.attended_run(", source)
        self.assertIn("print(emulator_stats.send(RESULTS), flush=True)", source)
        for family in attended_suite.SCENARIOS:
            self.assertEqual(attended_suite.device_of(family, None).family, family)


if __name__ == "__main__":
    unittest.main()
