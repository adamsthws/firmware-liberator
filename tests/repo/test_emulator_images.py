"""The two emulators' images, kept alike.

What an image is built from is hashed into its tag (image.sh tag), so a change
to any of it, a comment included, is a new image for every computer. A change
that would only make the two emulators alike there waits for that image's
next: write its test to let the old form be while `image.sh tag` is today's
tag, so it fails once an image comes without it.
"""

import fnmatch
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMULATORS = {"espressif": ROOT / "tools/espressif/emulator", "beken": ROOT / "tools/beken/emulator"}
PIN_FIELDS = ("repo", "commit", "archive", "sha256")
# A patch: its place in the order, then a few lowercase words saying what it does.
PATCH_NAME = re.compile(r"^\d{4}(-[a-z0-9]+){1,7}\.patch$")


def image_sh(folder: Path, *args: str) -> str:
    return subprocess.run(["bash", str(folder / "image.sh"), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def upstream(folder: Path) -> dict[str, str]:
    path = folder / "UPSTREAM"
    if not path.is_file():
        return {}
    return dict(line.split(" ", 1) for line in path.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#"))


# Upstream BekenEmulator's last commit before its C++ rewrite. While the image
# pins it, the docs say what moving off it would take; once it moves, they must
# stop saying the image stays on it.
LAST_PYTHON_BEKEN = "d9a3772a26fa92158be72faf46855726ef8e21d7"
# What each doc says while the image stays on Python, and where.
STAYS_ON_PYTHON = {
    "tools/beken/emulator/README.md": "The image stays on the Python commit",
    "docs/upstream-contributions.md": "will be ported once that settles",
}


def stale_rewrite_notes(commit: str, docs: dict[str, str]) -> list[str]:
    """The docs out of step with the pin: missing their note while the image is
    on Python, still carrying it once the pin has moved."""
    on_python = commit == LAST_PYTHON_BEKEN
    return [path for path, phrase in STAYS_ON_PYTHON.items() if (phrase in docs[path]) != on_python]


class BekenRewriteNoteTests(unittest.TestCase):
    """tools/beken/emulator/README.md#upstreams-c-rewrite measures the C++
    rewrite against the Python emulator the image is built on: a move to the
    rewrite rewrites it too."""

    def docs(self) -> dict[str, str]:
        return {path: (ROOT / path).read_text(encoding="utf-8") for path in STAYS_ON_PYTHON}

    def test_the_rewrite_notes_match_the_pin(self):
        commit = upstream(EMULATORS["beken"])["commit"]
        self.assertEqual(stale_rewrite_notes(commit, self.docs()), [],
                         "the Beken pin and these docs disagree on whether the image is on upstream's "
                         "Python emulator: say what the move changed, and drop the 'stays on Python' words")

    def test_either_side_out_of_step_is_found(self):
        with_notes = {path: "... %s ..." % phrase for path, phrase in STAYS_ON_PYTHON.items()}
        without = dict.fromkeys(STAYS_ON_PYTHON, "ported to the C++ emulator")
        self.assertEqual(stale_rewrite_notes(LAST_PYTHON_BEKEN, with_notes), [])
        self.assertEqual(stale_rewrite_notes("0" * 40, without), [])
        self.assertEqual(stale_rewrite_notes("0" * 40, with_notes), list(STAYS_ON_PYTHON))
        self.assertEqual(stale_rewrite_notes(LAST_PYTHON_BEKEN, without), list(STAYS_ON_PYTHON))


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "needs bash and sha256sum")
class NextImageTests(unittest.TestCase):
    def test_each_upstream_pin_is_in_the_one_form(self):
        """repo, commit, archive and sha256 in UPSTREAM, and nowhere else."""
        for family, folder in EMULATORS.items():
            with self.subTest(family=family):
                self.assertEqual(tuple(upstream(folder)), PIN_FIELDS, "UPSTREAM: image.sh pin writes it")
                self.assertRegex(upstream(folder)["commit"], r"^[0-9a-f]{40}$")
        dockerfile = (EMULATORS["espressif"] / "emu.Dockerfile").read_text(encoding="utf-8")
        self.assertNotRegex(dockerfile, r"(?m)^ARG QEMU_SOURCE_SHA256=", "pinned twice: UPSTREAM is the pin")

    def test_each_source_release_is_named_after_its_upstream_project(self):
        """emulator-source-<the archive's project>-<commit>: the Beken one's was
        named after the family, and keeps that name for the archive it holds."""
        for family, folder in EMULATORS.items():
            with self.subTest(family=family):
                release = image_sh(folder, "release")
                pin = upstream(folder)
                if family == "beken" and release == "emulator-source-beken-d9a3772":
                    self.assertEqual(pin["sha256"], "f4e3169f0b240e1978febafe2bb8452c5f6f8727ebb8ff4ac36c18a890588abf",
                                     "the pin moved: take the earlier release's name out of image.sh")
                    continue
                archive = pin.get("archive", "qemu-")
                self.assertRegex(release, rf"^emulator-source-{re.escape(archive.split('-')[0])}-[0-9a-f]{{7}}$")

    def test_each_emulators_files_are_named_after_its_image(self):
        """emu.sh and emu.Dockerfile for autoflash-emu, beken-emu.sh and
        beken-emu.Dockerfile for autoflash-beken-emu."""
        beken = EMULATORS["beken"]
        self.assertTrue((beken / "beken-emu.sh").is_file())
        self.assertTrue((beken / "beken-emu.Dockerfile").is_file())
        self.assertTrue((EMULATORS["espressif"] / "emu.sh").is_file())
        self.assertTrue((EMULATORS["espressif"] / "emu.Dockerfile").is_file())
        self.assertEqual(sorted(path.name for path in beken.glob("bk-emu*")), [])
        hashed = [beken / "UPSTREAM", beken / "requirements.txt", beken / "beken-emu.Dockerfile",
                  *sorted((beken / "patches").glob("*.patch"))]
        self.assertEqual([path.name for path in hashed if "bk-emu" in path.read_text(encoding="utf-8")], [],
                         "names the script by its earlier name: beken-emu.sh")

    def test_the_espressif_image_speaks_utf8(self):
        """Its tmux session is the terminal a run is watched in: with no UTF-8
        locale the tmux client draws the spinner's braille as a still _.  The
        Beken emulator has no tmux, and Python writes UTF-8 without a locale."""
        dockerfile = (EMULATORS["espressif"] / "emu.Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^ENV .*\bLANG=C\.UTF-8\b")

    def test_every_run_in_either_image_is_marked_as_the_emulators(self):
        """AUTOFLASH_EMULATED in the image itself, not only in what starts it:
        a run started any way there - docker exec, a second window, by hand -
        never reaches the project's website (hostsys.emulated, urlopen)."""
        from tools.autoflash import hostsys
        self.assertIn("AUTOFLASH_EMULATED", hostsys.EMULATOR_MARKS)
        for dockerfile in (EMULATORS["espressif"] / "emu.Dockerfile", EMULATORS["beken"] / "beken-emu.Dockerfile"):
            with self.subTest(dockerfile=dockerfile.name):
                text = dockerfile.read_text(encoding="utf-8")
                final = text[text.rindex("\nFROM "):]   # the image's own stage, not a build stage before it
                self.assertRegex(final, r"(?m)^ENV .*\bAUTOFLASH_EMULATED=1\b")

    def test_a_windows_computer_on_an_emulator_is_marked_while_linked(self):
        """windows_link.py, the only way Windows reaches an emulated device, keeps
        hostsys.EMULATOR_LINKED for as long as it runs."""
        link = (EMULATORS["espressif"] / "windows_link.py").read_text(encoding="utf-8")
        self.assertIn("from tools.autoflash.hostsys import EMULATOR_LINKED", link)
        written, carried = link.index("EMULATOR_LINKED.write_text("), link.index("link.carry(conn)")
        self.assertLess(written, carried)
        self.assertRegex(link[carried:], r"finally:\n(?:.*\n)*?\s+EMULATOR_LINKED\.unlink\(missing_ok=True\)")

    def test_each_patch_is_named_alike(self):
        """NNNN-what-it-does.patch, numbered in the order they are applied."""
        for family, folder in EMULATORS.items():
            with self.subTest(family=family):
                names = sorted(path.name for path in (folder / "patches").glob("*.patch"))
                self.assertTrue(names)
                self.assertEqual([name for name in names if not PATCH_NAME.match(name)], [])
                self.assertEqual([name[:4] for name in names], [f"{n:04d}" for n in range(1, len(names) + 1)])


# The workflow that publishes each family's image for every tag a push brings.
IMAGE_WORKFLOWS = {"espressif": ROOT / ".github/workflows/emulator-image.yml",
                   "beken": ROOT / ".github/workflows/beken-emulator-image.yml"}


def push_paths(workflow: Path) -> list[str]:
    """The paths a push must change to start the workflow."""
    text = workflow.read_text(encoding="utf-8")
    block = re.search(r"(?m)^  push:\n    paths:\n((?:      - .*\n)+)", text)
    return re.findall(r"(?m)^      - (\S+)$", block.group(1)) if block else []


def path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(path, pattern)


def unpublished_inputs(family: str, paths: list[str]) -> list[str]:
    """Each of the emulator's files that changes its tag and no push path
    names: a push changing only it would leave its new tag unpublished. One
    file of each top-level entry is changed, in a copy of what git tracks."""
    folder = EMULATORS[family]
    relative = folder.relative_to(ROOT).as_posix()
    tracked = subprocess.run(["git", "ls-files", "-z", "--", relative], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.split("\0")
    with tempfile.TemporaryDirectory() as directory:
        for name in filter(None, tracked):
            (Path(directory) / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, Path(directory) / name)
        copy = Path(directory) / relative
        before = image_sh(copy, "tag")
        firsts = {}
        for name in sorted(filter(None, tracked)):
            firsts.setdefault(Path(name).relative_to(relative).parts[0], name)
        missed = []
        for name in firsts.values():
            changed = Path(directory) / name
            original = changed.read_bytes()
            changed.write_bytes(original + b"\n")
            if image_sh(copy, "tag") != before and not any(path_matches(name, pattern) for pattern in paths):
                missed.append(name)
            changed.write_bytes(original)
        return missed


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum") and shutil.which("git"),
                     "needs bash, sha256sum and git")
class PublishedOnPushTests(unittest.TestCase):
    """A push that brings a new tag starts the workflow that publishes it, so
    no one publishes an image by hand (docs/updating-firmware.md#6-release)."""

    def test_every_input_of_a_tag_starts_its_image_workflow(self):
        for family, workflow in IMAGE_WORKFLOWS.items():
            with self.subTest(family=family):
                paths = push_paths(workflow)
                self.assertTrue(paths, f"{workflow.name}: no push paths read")
                self.assertEqual(unpublished_inputs(family, paths), [],
                                 f"add it to {workflow.name}'s push paths")

    def test_an_input_left_out_is_found(self):
        paths = [path for path in push_paths(IMAGE_WORKFLOWS["beken"]) if not path.endswith("/UPSTREAM")]
        self.assertEqual(unpublished_inputs("beken", paths), ["tools/beken/emulator/UPSTREAM"])


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "needs bash and sha256sum")
class PinHelperTests(unittest.TestCase):
    """The helpers that move a pin write it in the one form."""

    def copy(self, family: str, names: tuple[str, ...]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name) / "tools" / family / "emulator"
        folder.mkdir(parents=True)
        for name in names:
            source = EMULATORS[family] / name
            (shutil.copytree if source.is_dir() else shutil.copyfile)(source, folder / name)
        return folder

    def test_the_espressif_pin_moves_into_upstream(self):
        """From emu.Dockerfile the first time, and from UPSTREAM after."""
        pinned = ("UPSTREAM",) if upstream(EMULATORS["espressif"]) else ()
        folder = self.copy("espressif", ("image.sh", "emu.Dockerfile", "patches", "container", *pinned))
        before = image_sh(folder, "tag")
        archive = folder.parent / "qemu-lcgamboa-1234567.tar.xz"
        archive.write_bytes(b"a made-up archive")
        image_sh(folder, "pin", str(archive), "1234567" + "0" * 33)
        pin = upstream(folder)
        self.assertEqual(tuple(pin), PIN_FIELDS)
        self.assertEqual((pin["archive"], pin["commit"][:7]), (archive.name, "1234567"))
        self.assertNotRegex((folder / "emu.Dockerfile").read_text(encoding="utf-8"), r"(?m)^ARG QEMU_SOURCE_SHA256=")
        self.assertRegex((folder / "emu.Dockerfile").read_text(encoding="utf-8"), r"(?m)^ARG QEMU_SOURCE_SHA256$")
        self.assertEqual(image_sh(folder, "release"), "emulator-source-qemu-1234567")
        self.assertNotEqual(image_sh(folder, "tag"), before)

    def test_a_new_beken_pin_names_its_release_after_upstream(self):
        folder = self.copy("beken", ("image.sh", "UPSTREAM", "requirements.txt", "beken-emu.Dockerfile", "patches"))
        self.assertEqual(image_sh(folder, "release"), "emulator-source-beken-d9a3772")
        text = (folder / "UPSTREAM").read_text(encoding="utf-8")
        (folder / "UPSTREAM").write_text(re.sub(r"(?m)^sha256 .*$", "sha256 " + "0" * 64, text), encoding="utf-8")
        self.assertEqual(image_sh(folder, "release"), "emulator-source-bekenemulator-d9a3772")


if __name__ == "__main__":
    unittest.main()
