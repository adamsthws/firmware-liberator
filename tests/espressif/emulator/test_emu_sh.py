"""emu.sh puts the emulator on a /24 Docker network of its own, on a subnet
nothing on the computer uses yet - its routes, its other Docker networks - so
that making it never takes another network's addresses; and takes --instance
1-99."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.autoflash import kinds

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "espressif" / "emulator" / "emu.sh"


def pick(taken: str) -> str:
    """The subnet emu.sh's picker chooses, given what `ip -4 route` and the Docker
    networks' subnets say is taken (one per line)."""
    program = re.search(r"\"\$PY\" -I -c '\n(.*?)'\)", SCRIPT.read_text(encoding="utf-8"), re.S).group(1)
    done = subprocess.run([sys.executable, "-I", "-c", program], input=taken,
                          capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


def product_seen(folder: Path, before: str = "", key: str = "product_model") -> subprocess.CompletedProcess:
    """emu.sh's record_seen KEY, on the captures, saved devices and instance
    states under `folder`, for device 12-34-56-ab-cd-ef; `before` runs first."""
    program = re.search(r"record_seen\(\) \{\n.*?<<'EOF'\n(.*?)\nEOF\n", SCRIPT.read_text(encoding="utf-8"),
                        re.S).group(1)
    arguments = [str(ROOT), str(folder / "captures"), str(folder / "saved"), str(folder / "emulation"), "12-34-56-ab-cd-ef", key]
    return subprocess.run([sys.executable, "-I", "-c", before + program, *arguments],
                          capture_output=True, text=True, timeout=60)


class ProductModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        run = self.folder / "emulation/state-2/work/captures/devices/12-34-56-ab-cd-ef/runs/20261113-120000"
        run.mkdir(parents=True)
        (run / "autoflash-state.json").write_text(json.dumps({"inputs": {"product_model": "s60tpg"}}),
                                                  encoding="utf-8")

    def test_another_instances_run_is_where_the_model_is_found(self):
        self.assertEqual(product_seen(self.folder).stdout.strip(), "S60TPG")

    def test_a_run_deleted_while_it_is_read_breaks_nothing(self):
        # Another instance's run removing its record just after it was opened,
        # as asking for its path's time would then find.
        gone = "import os.path\ndef gone(path): raise FileNotFoundError(path)\nos.path.getmtime = gone\n"
        done = product_seen(self.folder, gone)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "S60TPG")


class KindTests(unittest.TestCase):
    """emu.sh's kind_of: the kind whose wiring the board takes its meter from,
    and the PSRAM and ADC readings it asks for."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def flash(self, stock_model: str | None) -> Path:
        """A dump whose Sonoff settings (frame_params) call it `stock_model`;
        with None, one with no Sonoff settings at all, as a public device's."""
        from tests.espressif.test_flashmap import flash_with
        from tests.espressif.test_inspect_stock_nvs import entry, page, variable
        data = flash_with(0, 5)
        if stock_model is not None:
            content = json.dumps({"deviceid": "1000000001", "model": stock_model}).encode()
            data += page([entry(0, 0x01, "frame_params", data=b"\x01"), *variable(1, 0x21, "content", content)])
        path = self.folder / f"flash-{stock_model}.bin"
        path.write_bytes(data)
        return path

    def kind_of(self, model: str, flash: Path, recorded: str = "") -> list[str]:
        program = re.search(r"kind_of\(\) \{\n.*?<<'EOF'\n(.*?)\nEOF\n", SCRIPT.read_text(encoding="utf-8"),
                            re.S).group(1)
        done = subprocess.run([sys.executable, "-I", "-c", program, str(ROOT), model, str(flash), recorded],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.split("\n")[:3]

    def test_a_device_with_no_product_model_is_known_by_its_dump(self):
        # An S60's test copy records none: without this its board had no meter,
        # and ESPHome found "No readings", Tasmota 0 V.
        self.assertEqual(self.kind_of("", self.flash(kinds.SONOFF_S60.stock_model)), ["sonoff-s60", "", ""])

    def test_its_dump_alone_does_not_say_what_only_the_product_model_does(self):
        kind = next(kind for kind in kinds.EVERY if kind.product_model_decides)
        self.assertEqual(self.kind_of("", self.flash(kind.stock_model)), ["", "", ""])
        self.assertEqual(self.kind_of(kind.example_product_model, self.flash(kind.stock_model))[0], kind.key)

    def test_a_public_device_is_known_by_the_kind_its_run_recorded(self):
        # Its flash holds nothing of Sonoff's.
        self.assertEqual(self.kind_of("", self.flash(None), "sonoff-s60"), ["sonoff-s60", "", ""])
        self.assertEqual(self.kind_of("", self.flash(None)), ["", "", ""])

    def test_the_kind_gives_its_psram_and_adc_readings(self):
        self.assertEqual(self.kind_of("BASICR4", self.flash(None)), ["sonoff-basicr4", "", "adc1_ch3=2700"])
        kind = next(kind for kind in kinds.EVERY if kind.psram_mb)
        self.assertEqual(self.kind_of(kind.example_product_model, self.flash(None))[:2],
                         [kind.key, str(kind.psram_mb)])

    def test_a_run_from_before_3_0_0_gives_its_kind_under_its_earlier_name(self):
        run = self.folder / "emulation/state-2/work/captures/plugs/12-34-56-ab-cd-ef/runs/20261022-120000"
        run.mkdir(parents=True)
        (run / "autoflash-state.json").write_text(json.dumps({"inputs": {"plug_kind": "sonoff-s60"}}),
                                                  encoding="utf-8")
        self.assertEqual(product_seen(self.folder, key="device_kind").stdout.strip(), "sonoff-s60")

    def test_the_container_is_given_them(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('{ read -r kind; read -r psram; read -r adc; } < <(kind_of "${product:-$(record_seen product_model)}" \\\n'
                      '    "$SRC/flash.bin" "$(record_seen device_kind)")', text)
        self.assertIn('-e PSRAM="$psram" -e ADC="$adc"', text)
        self.assertIn('-e DEVICE_KIND="$kind"', text)


class StockVersionTests(unittest.TestCase):
    def test_the_version_ewelink_gave_is_found_in_the_devices_record(self):
        # The S60's app calls itself "v1.0.0-21-g28df76c-dirty": without this
        # the stub listed a fresh device as on 1.0.0 until it spoke, and a
        # readout asking in that moment had its reader refused ("Device reports
        # 1.2.0, expected 1.0.0").
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        device = folder / "captures/devices/12-34-56-ab-cd-ef"
        device.mkdir(parents=True)
        (device / "device.json").write_text(json.dumps({"stock_version": "1.2.0", "product_model": "s60tpg"}),
                                            encoding="utf-8")
        self.assertEqual(product_seen(folder, key="stock_version").stdout.strip(), "1.2.0")
        self.assertEqual(product_seen(folder).stdout.strip(), "S60TPG")

    def test_the_stub_firmware_is_given_it(self):
        self.assertIn('--version "$(record_seen stock_version)"', SCRIPT.read_text(encoding="utf-8"))


class EmulatorNetworkTests(unittest.TestCase):
    def test_the_first_free_candidate_is_taken(self):
        self.assertEqual(pick("default\n192.0.2.0/24\n172.17.0.0/16\n"), "198.18.60.0/24")

    def test_one_that_overlaps_a_route_or_network_is_passed_over(self):
        self.assertEqual(pick("198.18.0.0/15\n"), "172.31.60.0/24")          # a route wider than it
        self.assertEqual(pick("198.18.60.128/25\n172.16.0.0/12\n"), "10.231.60.0/24")

    def test_none_free_is_none(self):
        self.assertEqual(pick("198.18.0.0/15\n172.16.0.0/12\n10.0.0.0/8\n"), "")

    def test_ipv6_and_blank_lines_are_ignored(self):
        self.assertEqual(pick("\nfd00::/64\ndefault\n"), "198.18.60.0/24")


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class InstanceTests(unittest.TestCase):
    """--instance N is 1-99: enough for every core of a big computer, and a
    range of numbers of its own for each session sharing one."""

    def run_script(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60)

    def test_up_to_99_is_taken(self):
        for instance in ("1", "30", "31", "99"):
            # --cloud with --stub-cloud is refused next, before anything is started.
            for args in (("--instance", instance), (f"--instance={instance}",)):
                done = self.run_script(*args, "--cloud", "--stub-cloud")
                self.assertEqual(done.returncode, 2, args)
                self.assertIn("--stub-cloud cannot be combined with --cloud", done.stderr, args)

    def test_others_are_refused(self):
        for instance in ("0", "05", "100", "x"):
            done = self.run_script(f"--instance={instance}", "--cloud", "--stub-cloud")
            self.assertEqual(done.returncode, 2, instance)
            self.assertIn("--instance needs a number, 1-99", done.stderr, instance)



class ApiTests(unittest.TestCase):
    """Its computer's readouts go to the API's own code beside it, for both
    sides of a readout to be seen here, unless told --no-api; the API goes
    with the emulator (emulator-room.sh's local_api_goes_with)."""

    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_the_api_starts_beside_it_before_it_and_goes_with_it(self):
        start = self.text.index('local_api_start "$NAME-api" "$NETWORK" "$STATE/api"')
        self.assertLess(start, self.text.index('exec "${awake[@]}" docker run'))
        self.assertIn('local_api_goes_with "$NAME" "$NAME-api"', self.text)
        self.assertIn('api_env=(-e AUTOFLASH_READOUT_URL="$LOCAL_API_READOUT_URL")', self.text)
        self.assertIn('-e AUTOFLASH_EMULATOR_URL="http://localhost:$PORT" "${api_env[@]}"', self.text)

    def test_the_api_fetches_offered_files_from_the_stub_by_this_containers_name(self):
        # entrypoint.sh serves them in plaintext on the Docker network, where
        # the name resolves; one given is kept.
        self.assertIn('AUTOFLASH_LOCAL_OTA_BASE=${AUTOFLASH_LOCAL_OTA_BASE:-http://$NAME} \\\n'
                      '        local_api_start "$NAME-api"', self.text)
        self.assertIn('docker run --rm $tty --name "$NAME"', self.text)

    def test_no_api_leaves_it_out_and_windows_has_none(self):
        self.assertIn("--no-api) api=0 ;;", self.text)
        self.assertIn('if [ "$api" = 1 ] && [ "$windows" = 0 ]; then', self.text)

    @unittest.skipIf(shutil.which("bash") is None, "no bash")
    def test_no_api_is_in_its_usage(self):
        done = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--no-api", done.stdout)

    @unittest.skipIf(shutil.which("bash") is None, "no bash")
    def test_its_usage_is_its_whole_header(self):
        done = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True, timeout=60)
        self.assertTrue(done.stdout.endswith("each device's flash is kept there between runs.\n"), done.stdout)


if __name__ == "__main__":
    unittest.main()
