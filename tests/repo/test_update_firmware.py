"""tools/repo/update_firmware.py: what an ESPHome build is given besides its config."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.repo import release_notes, update_firmware as uf

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
        self.assertEqual(uf.included(s60), ["../shared-esphome/base.yaml", "../shared-esphome/wifi-esp32.yaml",
                                            "../shared-esphome/relay.yaml", "../shared-esphome/energy-totals.yaml",
                                            "../shared-esphome/overload.yaml", "../shared-esphome/derived-power.yaml"])

    def test_a_package_included_is_a_file_to_copy_once(self):
        config = ("packages:\n  a: !include {file: ../p.yaml, vars: {relay: relay}}\n"
                  "  b: !include {file: ../p.yaml, vars: {relay: relay_2}}\n")
        self.assertEqual(uf.included(config), ["../p.yaml"])

    def test_a_package_included_whole_is_a_file_to_copy_too(self):
        self.assertEqual(uf.included("packages:\n  base: !include ../shared-esphome/base.yaml\n"),
                         ["../shared-esphome/base.yaml"])

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
                (devices / name).parent.mkdir(parents=True, exist_ok=True)
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

    def test_a_shared_package_is_built_beside_the_config_by_its_own_name(self):
        config = self.CONFIG + "packages:\n  a: !include {file: ../p.yaml, vars: {relay: relay}}\n"
        given = self.build_with(config, {"r.h": "shared", "p.yaml": "switch: []\n"})
        self.assertEqual(given["p.yaml"], "switch: []\n")
        self.assertIn("!include {file: p.yaml, vars: {relay: relay}}", given["mover.yaml"])

    def test_a_libretiny_build_has_its_shared_package_beside_it_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            devices, work = Path(tmp) / "devices", Path(tmp) / "work"
            (devices / "chip").mkdir(parents=True)
            work.mkdir()
            (devices / "p.yaml").write_text("switch: []\n", encoding="utf-8")
            config_path = devices / "chip" / "esphome.yaml"
            config_path.write_text('esphome:\n  name: s61\n  project:\n    version: "1.0.0"\n'
                                   "packages:\n  a: !include {file: ../p.yaml, vars: {relay: relay}}\n",
                                   encoding="utf-8")
            with mock.patch.object(uf, "docker", side_effect=OSError("built")), mock.patch("builtins.print"):
                with self.assertRaisesRegex(OSError, "built"):
                    uf.build_libretiny("builder", config_path, "1.0.0", work)
            self.assertEqual((work / "p.yaml").read_text(encoding="utf-8"), "switch: []\n")
            self.assertIn("!include {file: p.yaml,", (work / "esphome.yaml").read_text(encoding="utf-8"))

    def test_what_a_shared_file_includes_is_built_beside_it_too(self):
        # wifi-bk72xx.yaml's ef40.h, named from where that file is.
        config = self.CONFIG + "packages:\n  wifi: !include ../shared/w.yaml\n"
        given = self.build_with(config, {"r.h": "shared", "shared/w.yaml": "esphome:\n  includes:\n    - e.h\n",
                                         "shared/e.h": "header"})
        self.assertEqual(given["e.h"], "header")
        self.assertIn("  wifi: !include w.yaml\n", given["mover.yaml"])

    def test_includes_are_named_from_the_folder_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, source = Path(tmp) / "work", Path(tmp) / "devices" / "chip"
            source.mkdir(parents=True)
            work.mkdir()
            (source.parent / "r.h").write_text("shared", encoding="utf-8")
            config_path = work / "mover.yaml"   # a copy, changed, as the attended suite builds
            config_path.write_text(self.CONFIG, encoding="utf-8")
            with mock.patch.object(uf, "docker", side_effect=OSError("built")), mock.patch("builtins.print"):
                with self.assertRaisesRegex(OSError, "built"):
                    uf.build("builder", config_path, "1.0.0", work, beside=source)
            self.assertEqual((work / "r.h").read_text(encoding="utf-8"), "shared")

    def test_one_not_there_stops_the_build(self):
        with self.assertRaisesRegex(uf.UpdateError, r"includes \.\./r\.h, which is not there"):
            self.build_with(self.CONFIG, {})


class ESPHomeRebuildTests(unittest.TestCase):
    """An ESPHome build again on the release pinned, in a made-up checkout: one no
    release has published keeps its version, and every device's config, built or
    not, is adopted from the release to come."""

    RELEASE = "2099.1.0"
    BUILT = "2099-01-02 00:00:00 +0000"
    CONFIG = ('esphome:\n  name: esphome\n  project:\n    name: x.{key}\n    version: "{version}"\n'
              'dashboard_import:\n  package_import_url: github://someone/repo/devices/{key}/esphome.yaml@v2.0.0\n')
    # Each build's pin: its section in firmware.json, and its file.
    BUILDS = {"sonoff-s60": ("espressif", "artifacts/esphome-sonoff-s60-imprintable-{version}.bin"),
              "sonoff-pow-elite": ("espressif", "artifacts/esphome-sonoff-pow-elite-imprintable-{version}.bin"),
              "sonoff-s61": ("beken", "artifacts/esphome-sonoff-s61-{version}.rbl")}
    MOVER = "artifacts/autoflash-mover-esp32c3-1.2.0.bin"
    BOOTLOADER = "artifacts/esphome-sonoff-s60-bootloader-1.2.0.bin"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = root = Path(folder.name)
        (root / "artifacts").mkdir()
        version = f"{self.RELEASE}-2"
        pins = {"espressif": {"esp32c3": {}}, "beken": {}}
        rows, sums = [], []
        for key, (family, pattern) in self.BUILDS.items():
            file = pattern.format(version=version)
            (root / file).write_bytes(b"the build before")
            pins[family][key] = {"esphome": {
                "file": file, "config": f"devices/{key}/esphome.yaml", "version": version, "esphome": self.RELEASE,
                "esp_idf": "5.5.9", "libretiny": "9.9.9", "built": "2099-01-01 00:00:00 +0000",
                "earlier_builds": ["2098-12-01 00:00:00 +0000"], "size": 16, "sha256": "a" * 64}}
            rows.append(f"| `{Path(file).name}` | {key} | `{'a' * 64}` |")
            sums.append(f"{'a' * 64}  {Path(file).name}")
        for name, file in (("mover", self.MOVER), ("bootloader", self.BOOTLOADER)):
            (root / file).write_bytes(name.encode())
            pins["espressif"]["esp32c3"][name] = {"file": file, "version": "1.2.0", "esphome": self.RELEASE,
                                                  "esp_idf": "5.5.9"}
        (root / "artifacts" / "firmware.json").write_text(json.dumps(pins), encoding="utf-8")
        (root / "artifacts" / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        (root / "artifacts" / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
        (root / ".gitignore").write_text("".join(f"!{pins[family][key]['esphome']['file']}\n"
                                                 for key, (family, _) in self.BUILDS.items()), encoding="utf-8")
        (root / "NOTICES.md").write_text(f"`{pins['espressif']['sonoff-pow-elite']['esphome']['file']}` is built.\n",
                                         encoding="utf-8")
        (root / "CHANGELOG.md").write_text("# Changelog\n\n## 3.0.0\n\n- First.\n\n## 2.0.0\n\n- Old.\n",
                                           encoding="utf-8")
        # A device not ready yet: no build, nothing pinned, its config all the same adopted.
        for key in (*self.BUILDS, "sonoff-nspanel"):
            (root / "devices" / key).mkdir(parents=True)
            (root / "devices" / key / "esphome.yaml").write_text(self.CONFIG.format(key=key, version=version),
                                                                encoding="utf-8")
        (root / uf.ESPHOME_BUILDS["mover"][0]).parent.mkdir(parents=True, exist_ok=True)
        (root / uf.ESPHOME_BUILDS["mover"][0]).write_text(
            'esphome:\n  name: mover\n  project:\n    name: x.mover\n    version: "1.2.0"\n', encoding="utf-8")
        for name, value in (("ROOT", root), ("FIRMWARE_JSON", root / "artifacts" / "firmware.json"),
                            ("SHA256SUMS", root / "artifacts" / "SHA256SUMS"), ("GITIGNORE", root / ".gitignore"),
                            ("BUILD_CACHE", root / "build"), ("CHANGELOG", root / "CHANGELOG.md"),
                            ("ARTIFACTS_README", root / "artifacts" / "README.md"), ("NOTICES", root / "NOTICES.md")):
            mock.patch.object(uf, name, value).start()
        image = f"app {self.RELEASE}-2 x.sonoff-s61 {self.RELEASE} autoflash_wifi {self.BUILT}".encode()
        board = {"flash": {"download": f"0x{uf.uf2.DOWNLOAD_OFFSET:06X}+0x{uf.uf2.DOWNLOAD_SIZE:X}"}}
        self.published = mock.patch.object(uf, "published_by_a_release", return_value=False).start()
        for name, value in (("released", False), ("stable_release", {"tag_name": self.RELEASE}),
                            ("esphome_builder", "esphome@sha256:d"), ("check_esphome_internals", None),
                            ("build", (f"app {self.BUILT}".encode(), b"bootloader")),
                            ("check_esphome_app", {"esp_idf": "5.5.9"}),
                            ("build_libretiny", (image, board, "9.9.9")), ("check_rbl", image)):
            mock.patch.object(uf, name, return_value=value).start()
        mock.patch.object(uf.shutil, "which", return_value="/usr/bin/docker").start()
        self.addCleanup(mock.patch.stopall)

    def run_main(self, *argv):
        with mock.patch("builtins.print"):
            self.assertEqual(uf.main(["esphome", self.RELEASE, *argv]), 0)
        return json.loads((self.root / "artifacts" / "firmware.json").read_text(encoding="utf-8"))

    def esphome_pin(self, pins, key):
        return pins[self.BUILDS[key][0]][key]["esphome"]

    def test_one_never_released_keeps_its_version_and_its_file(self):
        for key, argv in (("sonoff-s60", ["--no-docs"]), ("sonoff-s61", ["--device", "sonoff-s61", "--no-docs"]),
                          ("sonoff-pow-elite", ["--device", "sonoff-pow-elite", "--no-docs"])):
            with self.subTest(key):
                pin = self.esphome_pin(self.run_main(*argv), key)
                file = self.BUILDS[key][1].format(version=f"{self.RELEASE}-2")
                self.assertEqual((pin["version"], pin["file"]), (f"{self.RELEASE}-2", file))
                self.assertNotEqual((self.root / file).read_bytes(), b"the build before")
                config = (self.root / "devices" / key / "esphome.yaml").read_text(encoding="utf-8")
                self.assertIn(f'version: "{self.RELEASE}-2"', config)
                # No device has the build replaced: a released tool never installed it.
                self.assertEqual((pin["built"], pin["earlier_builds"]), (self.BUILT, ["2098-12-01 00:00:00 +0000"]))
                sums = (self.root / "artifacts" / "SHA256SUMS").read_text(encoding="utf-8")
                self.assertIn(f"{pin['sha256']}  {Path(file).name}", sums)

    def test_a_device_with_a_motor_mode_has_each_config_built_at_its_own_version(self):
        # The Dual R3's motor build first published beside its own, at its
        # version: the device answers its release alike whichever it runs.
        version, folder = f"{self.RELEASE}-2", self.root / "devices" / "sonoff-dualr3"
        folder.mkdir(parents=True)
        for name in ("esphome.yaml", "esphome-motor.yaml"):
            (folder / name).write_text(self.CONFIG.format(key="sonoff-dualr3", version="2099.1.0-1")
                                       .replace("esphome.yaml@", f"{name}@"), encoding="utf-8")
        file = f"artifacts/esphome-sonoff-dualr3-imprintable-{version}.bin"
        (self.root / file).write_bytes(b"the build before")
        pins = json.loads((self.root / "artifacts" / "firmware.json").read_text(encoding="utf-8"))
        pins["espressif"]["sonoff-dualr3"] = {"esphome": {
            **pins["espressif"]["sonoff-pow-elite"]["esphome"], "file": file,
            "config": "devices/sonoff-dualr3/esphome.yaml"}}
        (self.root / "artifacts" / "firmware.json").write_text(json.dumps(pins), encoding="utf-8")
        with mock.patch.object(uf.kinds, "find", return_value=uf.kinds.SONOFF_DUALR3):
            section = self.run_main("--device", "sonoff-dualr3", "--no-docs")["espressif"]["sonoff-dualr3"]
        motor = section[uf.kinds.MOTOR_ESPHOME]
        self.assertEqual((motor["version"], motor["file"], motor["config"]),
                         (version, f"artifacts/esphome-sonoff-dualr3-motor-imprintable-{version}.bin",
                          "devices/sonoff-dualr3/esphome-motor.yaml"))
        self.assertEqual(section["esphome"]["version"], version)
        self.assertIn(f'version: "{version}"', (folder / "esphome-motor.yaml").read_text(encoding="utf-8"))
        self.assertIn("devices/sonoff-dualr3/esphome-motor.yaml@v3.0.0\n",
                      (folder / "esphome-motor.yaml").read_text(encoding="utf-8"))

    def test_one_a_release_published_takes_the_next_version(self):
        self.published.return_value = True
        pin = self.esphome_pin(self.run_main("--device", "sonoff-pow-elite", "--no-docs"), "sonoff-pow-elite")
        self.assertEqual(pin["version"], f"{self.RELEASE}-3")
        self.assertEqual(pin["earlier_builds"], ["2098-12-01 00:00:00 +0000", "2099-01-01 00:00:00 +0000"])

    def test_a_mover_never_released_keeps_its_version(self):
        pins = self.run_main("--mover", "--no-docs")
        self.assertEqual((pins["espressif"]["esp32c3"]["mover"]["version"],
                          pins["espressif"]["esp32c3"]["mover"]["file"]), ("1.2.0", self.MOVER))

    def test_one_never_released_writes_no_changelog_line_of_its_own(self):
        for key, argv, texts in (("sonoff-s60", [], "esphome_doc_texts"),
                                 ("sonoff-s61", ["--device", "sonoff-s61"], "beken_esphome_doc_texts")):
            with self.subTest(key), mock.patch.object(uf, texts, return_value=({}, None)) as written:
                self.run_main(*argv)
            self.assertFalse(written.call_args.kwargs["changelog"], key)

    def test_the_version_is_raised_only_when_asked(self):
        for key, argv, texts in (("sonoff-s60", [], "esphome_doc_texts"),
                                 ("sonoff-s61", ["--device", "sonoff-s61"], "beken_esphome_doc_texts")):
            for asked in ([], ["--raise-version"]):
                with self.subTest(key, asked=asked), mock.patch.object(uf, texts, return_value=({}, None)) as written:
                    self.run_main(*argv, *asked)
                self.assertEqual(written.call_args.kwargs["raise_version"], bool(asked), key)

    def test_another_espressif_devices_row_and_notice_say_its_build(self):
        pin = self.esphome_pin(self.run_main("--device", "sonoff-pow-elite"), "sonoff-pow-elite")
        readme = (self.root / "artifacts" / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"| `{Path(pin['file']).name}` | sonoff-pow-elite | `{pin['sha256']}` |", readme)
        self.assertIn(f"`{pin['file']}`", (self.root / "NOTICES.md").read_text(encoding="utf-8"))

    def test_every_build_folded_writes_no_changelog_line(self):
        commands = {name: mock.Mock(return_value=0)
                    for name in ("command_esphome", "command_esphome_beken", "command_esphome_kind")}
        with mock.patch.multiple(uf, **commands), mock.patch.object(uf, "changelog_with") as line:
            self.run_main("--every-device")
        line.assert_not_called()

    def test_every_openbeken_build_folded_writes_no_changelog_line(self):
        with mock.patch.object(uf, "command_openbeken", return_value=0), \
                mock.patch.object(uf, "openbeken_pinned", return_value="1.18.320"), \
                mock.patch.object(uf, "changelog_with") as line, mock.patch("builtins.print"):
            self.assertEqual(uf.main(["openbeken", "1.18.320", "--every-device"]), 0)
        line.assert_not_called()

    def test_every_devices_config_is_adopted_from_the_release_to_come(self):
        # The release workflow holds every config to its tag, a device's not ready yet too.
        self.run_main("--device", "sonoff-pow-elite", "--no-docs")
        for key in (*self.BUILDS, "sonoff-nspanel"):
            config = (self.root / "devices" / key / "esphome.yaml").read_text(encoding="utf-8")
            self.assertIn(f"devices/{key}/esphome.yaml@v3.0.0\n", config, key)


class AdoptionReleaseTests(unittest.TestCase):
    def test_only_the_configs_name_the_release_they_are_adopted_from(self):
        # Each ESPHome build moves every config's package_import_url, and
        # nothing else, to the release to come: a page or test naming that
        # release would be left behind by the next. One shows a release
        # already out, as an example; an image keeps the release it was built at.
        tags = release_notes.adoption_tags()
        self.assertTrue(tags)
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.splitlines()
        left = []
        for name in tracked:
            if name.startswith("artifacts/") or Path(name).match(uf.kinds.ESPHOME_CONFIGS):
                continue
            try:
                text = (ROOT / name).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue   # a picture, a file deleted and not yet committed
            left += [f"{name}: @{tag}" for tag in tags if f"@{tag}" in text]
        self.assertEqual(left, [])


class RaiseVersionTests(unittest.TestCase):
    """The changelog's line goes under the release to come; the tool's version
    is raised to it only with --raise-version, as the weekly update asks:
    pushing main with a new version releases it, so an update made by hand
    leaves it and says so."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.changelog, self.version_file = root / "CHANGELOG.md", root / "__init__.py"
        # 3.1.0 being worked on: its section written, the version not raised yet.
        self.changelog.write_text("# Changelog\n\n## 3.1.0\n\n- First.\n\n## 3.0.0\n\n- Old.\n", encoding="utf-8")
        self.version_file.write_text('"""The tool."""\n\n__version__ = "3.0.0"\n', encoding="utf-8")
        for name, value in (("CHANGELOG", self.changelog), ("VERSION_FILE", self.version_file)):
            mock.patch.object(uf, name, value).start()
        mock.patch.object(uf, "released", return_value=False).start()
        mock.patch.object(uf.shutil, "which", return_value="/usr/bin/docker").start()
        self.addCleanup(mock.patch.stopall)

    def version(self):
        return uf.VERSION.search(self.version_file.read_text(encoding="utf-8")).group(1)

    def test_without_it_the_line_is_written_and_the_version_left(self):
        texts = {}
        self.assertEqual(uf.changelog_with("Second.", texts), "3.1.0")
        self.assertIn("## 3.1.0\n\n- First.\n- Second.\n", texts[uf.CHANGELOG])
        self.assertNotIn(uf.VERSION_FILE, texts)
        self.assertEqual(uf.version_left(texts), ["tools/autoflash/__init__.py is still at 3.0.0: raising it to "
                                                  "3.1.0 and pushing main releases 3.1.0"])

    def test_with_it_the_version_is_raised_and_nothing_left(self):
        texts = {}
        uf.changelog_with("Second.", texts, raise_version=True)
        self.assertIn('\n__version__ = "3.1.0"\n', texts[uf.VERSION_FILE])
        self.assertEqual(uf.version_left(texts), [])

    def test_nothing_is_left_once_the_version_names_the_release(self):
        self.version_file.write_text('__version__ = "3.1.0"\n', encoding="utf-8")
        texts = {}
        uf.changelog_with("Second.", texts)
        self.assertEqual(uf.version_left(texts), [])
        self.assertEqual(uf.version_left({}), [])

    def every_device(self, project, *argv):
        """An every-device update, each device's own build stood in for: what
        it printed."""
        printed = []
        builds = {name: mock.Mock(return_value=0) for name in
                  ("command_esphome", "command_esphome_beken", "command_esphome_kind", "command_openbeken")}
        with mock.patch.object(uf, "stable_release", return_value={"tag_name": "2099.1.0"}), \
                mock.patch.object(uf, "built_versions", side_effect=[{"a": "1"}, {"a": "2"}]), \
                mock.patch.multiple(uf, **builds), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            self.assertEqual(uf.main([project, "2099.1.0", "--every-device", *argv]), 0)
        return "\n".join(printed)

    def test_every_devices_update_raises_the_version_only_when_asked(self):
        for project, name in (("esphome", "ESPHome"), ("openbeken", "OpenBeken")):
            with self.subTest(project):
                self.version_file.write_text('__version__ = "3.0.0"\n', encoding="utf-8")
                printed = self.every_device(project)
                self.assertEqual(self.version(), "3.0.0")
                self.assertIn("tools/autoflash/__init__.py is still at 3.0.0", printed)
                self.assertIn(f"- {name} moved to", self.changelog.read_text(encoding="utf-8"))
                printed = self.every_device(project, "--raise-version")
                self.assertEqual(self.version(), "3.1.0")
                self.assertNotIn("still at", printed)

    def test_the_weekly_update_asks_for_it_on_each_update(self):
        workflow = (ROOT / ".github/workflows/firmware-update.yml").read_text(encoding="utf-8")
        calls = [line for line in workflow.splitlines()
                 if any(f"update_firmware.py {project} " in line for project in ("tasmota", "esphome", "openbeken"))]
        self.assertEqual(len(calls), 3, calls)
        for call in calls:
            self.assertIn(" --raise-version ", call)

    def test_each_update_takes_it(self):
        for project in ("tasmota", "esphome", "openbeken"):
            seen = []
            with mock.patch.object(uf, f"command_{project}", side_effect=lambda args: seen.append(args.raise_version) or 0):
                uf.main([project, "--raise-version"])
                uf.main([project])
            self.assertEqual(seen, [True, False], project)


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
