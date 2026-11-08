"""tools/repo/update_firmware.py: what an ESPHome build is given besides its config."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import update_firmware as uf

ROOT = Path(__file__).resolve().parents[2]


class IncludedFilesTests(unittest.TestCase):
    def test_a_configs_includes_are_listed(self):
        config = "esphome:\n  name: x\n  includes:\n    - a.h\n    - b.h\n  on_boot:\n    - priority: 1\n"
        self.assertEqual(uf.included(config), ["a.h", "b.h"])
        self.assertEqual(uf.included("esphome:\n  name: x\n"), [])

    def test_a_system_header_is_not_a_file_to_copy(self):
        # The S60's ESPHome includes <nvs.h>: its build stopped, the header
        # looked for beside the config.
        config = "esphome:\n  name: x\n  includes:\n    - <nvs.h>\n    - a.h\n"
        self.assertEqual(uf.included(config), ["a.h"])
        s60 = (ROOT / "devices/sonoff-s60/esphome.yaml").read_text(encoding="utf-8")
        self.assertIn("    - <nvs.h>\n", s60)
        self.assertEqual(uf.included(s60), [])

    def test_both_movers_include_the_one_restart_route(self):
        for chip in ("esp32c3", "esp32"):
            config = (ROOT / f"devices/{chip}/mover.yaml").read_text(encoding="utf-8")
            self.assertEqual(uf.included(config), ["../mover_routes.h"], chip)
            self.assertTrue((ROOT / "devices" / chip / "../mover_routes.h").is_file())
            self.assertIn("autoflash::start_routes();", config)

    def build_with(self, config: str, files: dict[str, str]):
        """build() of `config`, in devices/chip/ with `files` (paths from
        devices/), docker stood in for: the build folder's files."""
        with tempfile.TemporaryDirectory() as tmp:
            devices, work = Path(tmp) / "devices", Path(tmp) / "work"
            (devices / "chip").mkdir(parents=True)
            work.mkdir()
            config_path = devices / "chip" / "mover.yaml"
            config_path.write_text(config, encoding="utf-8")
            for name, text in files.items():
                (devices / name).write_text(text, encoding="utf-8")

            def compiled(*args):
                out = work / ".esphome/build/mover/build"
                (out / "bootloader").mkdir(parents=True)
                (out / "firmware.ota.bin").write_bytes(b"app")
                (out / "bootloader" / "bootloader.bin").write_bytes(b"boot")
            with mock.patch.object(uf, "docker", side_effect=compiled), mock.patch("builtins.print"):
                uf.build("builder", config_path, "1.0.0", work)
            return {path.name: path.read_text(encoding="utf-8") for path in work.iterdir() if path.is_file()}

    CONFIG = 'esphome:\n  name: mover\n  project:\n    version: "1.0.0"\n  includes:\n    - ../r.h\n'

    def test_a_shared_include_is_built_beside_the_config_by_its_own_name(self):
        given = self.build_with(self.CONFIG, {"r.h": "shared", "chip/r.h": "not this"})
        self.assertEqual(given["r.h"], "shared")
        self.assertEqual(uf.included(given["mover.yaml"]), ["r.h"])

    def test_one_not_there_stops_the_build(self):
        with self.assertRaisesRegex(uf.UpdateError, r"includes \.\./r\.h, which is not there"):
            self.build_with(self.CONFIG, {})


if __name__ == "__main__":
    unittest.main()
