"""The docs speak to their reader, not from the project's side
(CONTRIBUTING.md, Writing docs), and say what is known rather than what is not
yet tried.  Released changelog sections are history, so
of CHANGELOG.md only the top section is read."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIRST_PERSON = re.compile(r"\b(?:[Ww]e|[Oo]urs?|us)\b|\b[Ff]rom here\b")
# "None has been tried on a real S61 yet", "not yet on hardware": stale the day it is tried.
NOT_YET_TRIED = re.compile(
    r"\b(?:[Uu]ntried|[Uu]ntested|[Nn]ot yet|(?:[Nn]ot|[Nn]one(?: of it| of this)? has|[Nn]ever)(?: yet)?"
    r" (?:been )?(?:tried|tested|done|run))\b[^.;]{0,30}?"
    r"\b(?:on|against|with) (?:a |the )?(?:real|hardware)\b"
    r"|\b(?:tried|tested|run) on (?:a |the )?real [^.;:]{0,20}?\byet\b")


def top_section(changelog: str) -> str:
    return "## " + changelog.split("\n## ")[1]


def docs() -> dict[str, str]:
    pages = [ROOT / "README.md", ROOT / "artifacts/README.md", ROOT / "website/README.md",
             *(ROOT / "docs").rglob("*.md"), *(ROOT / "devices").rglob("*.md")]
    found = {page.relative_to(ROOT).as_posix(): page.read_text(encoding="utf-8") for page in pages}
    found["CHANGELOG.md"] = top_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    return found


def pages_saying_what_is_known() -> dict[str, str]:
    """The docs, and the pages for contributors and the maintainer, which a
    line on what is not yet tried goes as stale in."""
    found = docs()
    for page in [*(ROOT / "tools").rglob("*.md"), *(ROOT / ".github").rglob("*.md")]:
        found[page.relative_to(ROOT).as_posix()] = page.read_text(encoding="utf-8")
    return found


class DocsVoiceTests(unittest.TestCase):
    def test_no_page_speaks_from_the_projects_side(self):
        for name, text in docs().items():
            for number, line in enumerate(text.splitlines(), 1):
                with self.subTest(page=name, line=number):
                    self.assertIsNone(FIRST_PERSON.search(line), line)

    def test_no_page_says_what_is_not_yet_tried(self):
        for name, text in pages_saying_what_is_known().items():
            for number, paragraph in enumerate(text.split("\n\n"), 1):
                with self.subTest(page=name, paragraph=number):
                    self.assertIsNone(NOT_YET_TRIED.search(" ".join(paragraph.split())), paragraph)

    def test_it_catches_not_yet_tried_and_leaves_what_is_known(self):
        for line in ("None has been tried on a real S61 yet.", "Untried on a real device; a cut",
                     "proven in the emulator, not yet on a real Beken device",
                     "Not yet done: a check against a real metered load.", "settled, not yet on hardware:",
                     "None of it has been done on a real device"):
            self.assertTrue(NOT_YET_TRIED.search(line), line)
        for line in ("Seen in the emulator.", "which its author checked on hardware", "from the public dump of a real one",
                     "Tasmota not yet set up - the device",
                     "has been run on a real S60TPG"):
            self.assertFalse(NOT_YET_TRIED.search(line), line)

    def test_it_catches_the_wording_and_leaves_alike_words(self):
        for line in ("What we ask of you", "changes nothing of ours", "fetches nothing from here", "Our config"):
            self.assertTrue(FIRST_PERSON.search(line), line)
        for line in ("US law: 17 U.S.C.", "yours to change", "your device", "colours", "hours"):
            self.assertFalse(FIRST_PERSON.search(line), line)

    def test_the_tools_and_maintainers_pages_are_read_for_what_is_not_yet_tried(self):
        pages = pages_saying_what_is_known()
        self.assertIn("tools/beken/README.md", pages)
        self.assertIn(".github/readouts.md", pages)

    def test_only_the_changelogs_top_section_counts(self):
        self.assertEqual(top_section("# Changelog\n\n## 2.0.0\nnew\n\n## 1.0.0\nwe did\n"), "## 2.0.0\nnew\n")
