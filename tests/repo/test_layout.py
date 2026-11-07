"""Every script that finds the repository from its own path finds it: a file
moved to another depth (the 2026-10 reorganisation moved tools/emulator/ two
levels down) must count its parents again."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.autoflash import hostsys

ROOT = Path(__file__).resolve().parents[2]
# `Path(__file__).resolve().parents[N]`, and any `/ "name"` parts joined to it.
PARENTS = re.compile(r'Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]((?:\s*/\s*"[^"]+")*)')
GITIGNORED = {line.strip().strip("/") for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")}


def repository_files(*patterns):
    """The files git tracks or would, from its own lists: walking the checkout
    takes a minute under load, most of it in the ignored captures/."""
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *patterns],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / name for name in sorted(set(listed.split("\0")))
            if name and not name.startswith(".claude/") and (ROOT / name).is_file()]


class RepositoryRootTests(unittest.TestCase):
    def test_every_parents_index_reaches_the_repository(self):
        wrong = []
        for file in sorted(ROOT.glob("tools/**/*.py")):
            for match in PARENTS.finditer(file.read_text(encoding="utf-8")):
                parent = file.resolve().parents[int(match.group(1))]
                parts = re.findall(r'"([^"]+)"', match.group(2))
                if not parts:
                    ok = parent == ROOT   # bare: the repository root
                else:
                    target = parent.joinpath(*parts)
                    # Joined to it: something in the repository, or a directory
                    # the repository makes at run time (captures/) off its root.
                    ok = target.exists() or (parent == ROOT and parts[0] in GITIGNORED)
                if not ok:
                    wrong.append("%s: %s" % (file.relative_to(ROOT), match.group(0)))
        self.assertEqual(wrong, [])

    def test_every_relative_link_in_the_docs_names_a_file(self):
        """A Markdown link to a path, as GitHub follows it from the file it is in;
        code (where a link is only an example) aside."""
        link = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|^\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
        tracked = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.split()
        wrong = []
        for name in tracked:
            text = (ROOT / name).read_text(encoding="utf-8")
            text = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
            for match in link.finditer(text):
                href = match.group(1) or match.group(2)
                if re.match(r"[a-z][a-z0-9+.-]*:|[#/<]", href, re.IGNORECASE):
                    continue
                target = ((ROOT / name).parent / href.split("#")[0]).resolve()
                if not target.exists() or ROOT not in target.parents and target != ROOT:
                    wrong.append(f"{name}: {href}")
        self.assertEqual(wrong, [])

    def test_every_windows_path_names_a_file(self):
        """`tools\\...\\name.ext` in the PowerShell, batch and prose: Windows runs them
        from the checkout root (C:\\Autoflash\\repo in the emulator's VM)."""
        windows = re.compile(r"tools\\[A-Za-z0-9_\\.-]+\.(?:ps1|py|cmd)")
        wrong = []
        for file in repository_files("*.ps1", "*.cmd", "*.md"):
            if file.relative_to(ROOT).parts[0] == "archive":
                continue
            for path in set(windows.findall(file.read_text(encoding="utf-8"))):
                if not (ROOT / path.replace("\\", "/")).is_file():
                    wrong.append("%s: %s" % (file.relative_to(ROOT), path))
        self.assertEqual(wrong, [])

    def test_every_script_the_docs_run_as_a_command_can_be_run(self):
        """`tools/.../name.py args` at the start of a line in a shell block - after
        any `NAME=value` settings - is run as it is, so it must be executable."""
        command = re.compile(r"^\s*(?:\\\s*)?(?:[A-Z_0-9]+=\S+\s+)*(tools/\S+\.(?:py|sh))\b", re.M)
        block = re.compile(r"^```(?:sh|bash)\n(.*?)^```", re.M | re.S)
        wrong = []
        for file in repository_files("*.md"):
            if file.relative_to(ROOT).parts[0] == "archive":
                continue
            for code in block.findall(file.read_text(encoding="utf-8")):
                for path in set(command.findall(code)):
                    if (ROOT / path).is_file() and not os.access(ROOT / path, os.X_OK):
                        wrong.append("%s: %s" % (file.relative_to(ROOT), path))
        self.assertEqual(sorted(set(wrong)), [])

    def test_every_helper_the_tool_launches_runs_on_a_bare_python(self):
        """The scripts the tool starts in a process of their own (hostsys.python_script),
        on a Python with nothing but the script's own folder on sys.path - the
        portable one start.cmd fetches, which -I stands in for here."""
        helpers = ("tools/ewelink/query_ota.py", "tools/espressif/serve_tasmota_ota.py",
                   "tools/espressif/safeboot/serve_safeboot_migration.py")
        with tempfile.TemporaryDirectory() as elsewhere:
            for helper in helpers:
                with self.subTest(helper=helper):
                    command = hostsys.python_script(ROOT / helper, "--help")
                    done = subprocess.run([command[0], "-I", *command[1:]], cwd=elsewhere,
                                          capture_output=True, text=True, timeout=120)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertIn("usage:", done.stdout)


RESEARCH_INDEX = ROOT / "docs" / "device-research.md"
EMULATOR_PAGE = ROOT / "docs" / "emulator.md"


def research_pages():
    """Each device's research.md, beside its files, and each family's
    docs/<family>-research.md."""
    return [page for page in repository_files("*research.md")
            if page.name == "research.md" and page.relative_to(ROOT).parts[0] == "devices"
            or page.parent == ROOT / "docs" and page.name.endswith("-research.md") and page != RESEARCH_INDEX]


def emulator_readmes():
    """Each chip family's emulator, described beside its code: tools/<family>/emulator/."""
    return [page for page in repository_files("tools/*/emulator/README.md") if len(page.relative_to(ROOT).parts) == 4]


def unlisted(index: str, pages, where: Path = RESEARCH_INDEX) -> list[str]:
    """The pages a Markdown file at `where`, its text `index`, does not link to."""
    linked = {os.path.normpath(where.parent / href.split("#")[0])
              for href in re.findall(r"\]\(([^)\s]+)\)", index)}
    return [str(page.relative_to(ROOT)) for page in pages if os.path.normpath(page) not in linked]


class ResearchIndexTests(unittest.TestCase):
    def test_every_research_page_is_listed_in_the_index(self):
        """A device's research is kept with its files, and found from
        docs/device-research.md: a new one has to be listed there too."""
        self.assertEqual(unlisted(RESEARCH_INDEX.read_text(encoding="utf-8"), research_pages()), [])

    def test_there_is_research_to_list(self):
        pages = {str(page.relative_to(ROOT)) for page in research_pages()}
        self.assertLessEqual({"docs/espressif-research.md", "docs/beken-research.md",
                              "devices/sonoff-s61/research.md"}, pages)

    def test_a_page_the_index_leaves_out_is_named(self):
        index = "- [S61](../devices/sonoff-s61/research.md#its-wiring)\n- [Beken](beken-research.md)\n"
        pages = [ROOT / "devices/sonoff-s61/research.md", ROOT / "docs/beken-research.md",
                 ROOT / "devices/not-ready-yet/sonoff-x/research.md"]
        self.assertEqual(unlisted(index, pages), ["devices/not-ready-yet/sonoff-x/research.md"])


class EmulatorPageTests(unittest.TestCase):
    def test_the_emulator_page_links_each_chip_familys_emulator(self):
        """docs/emulator.md is how to use them; how each works is beside its code."""
        self.assertEqual(unlisted(EMULATOR_PAGE.read_text(encoding="utf-8"), emulator_readmes(), EMULATOR_PAGE), [])

    def test_there_are_both_emulators_to_link(self):
        self.assertLessEqual({"tools/espressif/emulator/README.md", "tools/beken/emulator/README.md"},
                             {str(page.relative_to(ROOT)) for page in emulator_readmes()})


def heading_anchors(text: str) -> set[str]:
    """The anchors GitHub gives a Markdown page's headings, a repeated one
    numbered, and any it names itself (<a id=...>)."""
    found, seen, fence = set(), {}, False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
            continue
        heading = None if fence else re.match(r"#{1,6} (.*)", line)
        if heading:
            anchor = re.sub(r"[^\w\- ]", "", heading.group(1).strip().lower()).replace(" ", "-")
            found.add(anchor if anchor not in seen else f"{anchor}-{seen[anchor]}")
            seen[anchor] = seen.get(anchor, 0) + 1
    found |= set(re.findall(r'<a (?:id|name)="([^"]+)"', text))
    return found


class AnchorTests(unittest.TestCase):
    def test_every_link_to_a_heading_names_one(self):
        """A link's #anchor, in this page or another, is a heading there: a moved
        or renamed section takes its links with it."""
        link = re.compile(r"\]\(([^)\s]*)#([^)\s]+)\)")
        wrong = []
        for page in repository_files("*.md"):
            text = page.read_text(encoding="utf-8")
            text = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
            for match in link.finditer(text):
                href, anchor = match.groups()
                if re.match(r"[a-z][a-z0-9+.-]*:", href, re.IGNORECASE):
                    continue
                target = (page.parent / href).resolve() if href else page
                if target.suffix == ".md" and target.is_file() \
                        and anchor not in heading_anchors(target.read_text(encoding="utf-8")):
                    wrong.append(f"{page.relative_to(ROOT)}: {href}#{anchor}")
        self.assertEqual(wrong, [])

    def test_anchors_are_given_as_github_gives_them(self):
        text = "# Title\n## The fork (`../tools/beken/emulator/`)\n## A, b\n## A, b\n```\n## code\n```\n<a id=\"kept\"></a>"
        self.assertEqual(heading_anchors(text), {"title", "the-fork-toolsbekenemulator", "a-b", "a-b-1", "kept"})


# A page a file names in its text, a comment say, rather than in a Markdown link.
NAMED_PAGE = re.compile(r"(?<![\w./-])((?:\.\./)*(?:docs|devices|tools|archive|website|artifacts)/[\w./-]*?\.md)\b")
# A published image's source that names a page since moved: its hash is pinned
# with the image (artifacts/firmware.json), so it cannot change alone. Each is
# let be only while that image is the one built from it; the build that replaces
# the image puts the name right, and takes it out of here.
UNTIL_REBUILT = {
    "devices/sonoff-s61/drv_autoflash_sonoff_s61.c":
        ("docs/beken-s61-emulation.md", ("beken", "sonoff-s61", "openbeken"), "artifacts/openbeken-sonoff-s61-1.18.320-1.rbl"),
}


def missing_pages(file: Path, text: str) -> list[str]:
    """The pages `text` names, from the repository's root or from the file's
    own folder, that are not there."""
    return [name for name in NAMED_PAGE.findall(text)
            if not (ROOT / name).exists() and not (file.parent / name).exists()]


def image_pin(keys) -> dict:
    entry = json.loads((ROOT / "artifacts" / "firmware.json").read_text(encoding="utf-8"))
    for key in keys:
        entry = entry[key]
    return entry


class NamedPageTests(unittest.TestCase):
    def test_every_page_a_file_names_is_there(self):
        """Code, configs and docs name pages in prose and comments too: a moved
        page is followed there. Tests are left out: their pages are made up."""
        wrong = []
        for file in repository_files():
            name = str(file.relative_to(ROOT))
            if {"tests", "test"} & set(file.relative_to(ROOT).parts):
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # a firmware image
                continue
            for page in missing_pages(file, text):
                kept = UNTIL_REBUILT.get(name)
                if kept and kept[0] == page and image_pin(kept[1])["file"] == kept[2]:
                    continue
                wrong.append(f"{name}: {page}")
        self.assertEqual(wrong, [], "a page named here has moved; a published image's source "
                                    "is put right in the build that publishes the image again")

    def test_each_source_let_be_until_its_image_is_built_again_still_needs_it(self):
        for name, (page, keys, image) in UNTIL_REBUILT.items():
            with self.subTest(name=name):
                self.assertIn(page, missing_pages(ROOT / name, (ROOT / name).read_text(encoding="utf-8")),
                              "put right: take it out of UNTIL_REBUILT")
                self.assertIn(name, image_pin(keys)["sources"], "not a source of that image")
                self.assertTrue((ROOT / image).is_file(), "the image it waits on is not published")

    def test_a_moved_page_is_found_wherever_it_is_named(self):
        self.assertEqual(missing_pages(ROOT / "tools/x.py", "# see docs/emulator.md, docs/gone.md and ../docs/x/y.md"),
                         ["docs/gone.md", "../docs/x/y.md"])


if __name__ == "__main__":
    unittest.main()
