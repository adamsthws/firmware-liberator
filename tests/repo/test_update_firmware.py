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


class PublishableTests(unittest.TestCase):
    """What the firmware-update workflow's publish job pushes: the build job's
    commit, made running the releases' own tools, changing only what an update does."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = Path(folder.name)
        uf.git("init", "-q", cwd=self.repo)
        self.files({"tools/autoflash/__init__.py": '"""The tool."""\n\n__version__ = "2.4.0"\n',
                    "artifacts/firmware.json": "{}\n", "tools/repo/lint.py": "print()\n"})
        self.base = self.commit()

    def files(self, files):
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text, encoding="utf-8")

    def commit(self):
        uf.git("add", "-A", cwd=self.repo)
        uf.git("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
               "commit", "-q", "--allow-empty", "-m", "c", cwd=self.repo)
        return uf.git("rev-parse", "HEAD", cwd=self.repo)

    def test_an_update_as_the_workflow_makes_one_is_publishable(self):
        self.files({"artifacts/firmware.json": '{"new": 1}\n', "artifacts/x-1.bin": "x",
                    "devices/sonoff-s60/esphome.yaml": "a: 1\n", "devices/sonoff-s60/esphome.md": "#\n",
                    "CHANGELOG.md": "## 2.4.1\n", "NOTICES.md": "n\n", ".gitignore": "!artifacts/x-1.bin\n",
                    "tools/autoflash/__init__.py": '"""The tool."""\n\n__version__ = "2.4.1"\n'})
        self.assertEqual(uf.unpublishable(self.base, self.commit(), cwd=self.repo), [])

    def test_code_is_not(self):
        self.files({"tools/repo/lint.py": "import os\n", "devices/sonoff-s60/hook.py": "x\n"})
        problems = uf.unpublishable(self.base, self.commit(), cwd=self.repo)
        self.assertEqual(sorted(problems), ["devices/sonoff-s60/hook.py: an update never changes it",
                                            "tools/repo/lint.py: an update never changes it"])

    def test_the_version_file_only_by_its_version(self):
        self.files({"tools/autoflash/__init__.py": '"""The tool."""\nimport os\n__version__ = "2.4.1"\n'})
        problems = uf.unpublishable(self.base, self.commit(), cwd=self.repo)
        self.assertEqual(problems, ["tools/autoflash/__init__.py: '-' is more than the version",
                                    "tools/autoflash/__init__.py: '+import os' is more than the version"])

    def test_only_one_commit_on_main(self):
        self.files({"artifacts/firmware.json": "{}\n{}\n"})
        self.commit()
        self.files({"artifacts/firmware.json": "{}\n{}\n{}\n"})
        self.assertEqual(uf.unpublishable(self.base, self.commit(), cwd=self.repo),
                         [f"{uf.git('rev-parse', 'HEAD', cwd=self.repo)} is not one commit on {self.base}"])


if __name__ == "__main__":
    unittest.main()
