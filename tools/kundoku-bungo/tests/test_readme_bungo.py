"""Tests that README.bungo.md keeps the commands and links of README.md.

README.bungo.md is a playful copy of README.md in 文語, made with
kundoku-bungo. Its prose differs, but a reader may copy a command from it,
so the commands, code and link targets must stay the same as in README.md.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
CODE_BLOCK = re.compile(r"^```.*?^```", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]+`")
# Targets of Markdown links and of href, src and srcset attributes.
LINK = re.compile(r'\]\(([^)]+)\)|(?:href|src|srcset)="([^"]+)"')
# The two READMEs link to each other at the top.
SWITCH_LINKS = frozenset({"README.md", "README.bungo.md"})


def read(name: str) -> str:
    """Returns the text of a file at the repository root."""
    return (REPOSITORY / name).read_text(encoding="utf-8")


def code_blocks(text: str) -> list[str]:
    """Returns the fenced code blocks in the order of the text."""
    return CODE_BLOCK.findall(text)


def inline_code(text: str) -> set[str]:
    """Returns the inline code spans outside the fenced code blocks."""
    return set(INLINE_CODE.findall(CODE_BLOCK.sub("", text)))


def link_targets(text: str) -> set[str]:
    """Returns the link targets, leaving out anchors in the same page."""
    targets = set()
    for markdown, attribute in LINK.findall(text):
        target = markdown or attribute
        if not target.startswith("#") and target not in SWITCH_LINKS:
            targets.add(target)
    return targets


class ReadmeBungoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.modern = read("README.md")
        cls.bungo = read("README.bungo.md")

    def test_code_blocks_are_the_same(self) -> None:
        self.assertEqual(code_blocks(self.bungo), code_blocks(self.modern))

    def test_inline_code_is_the_same(self) -> None:
        self.assertEqual(inline_code(self.bungo), inline_code(self.modern))

    def test_link_targets_are_the_same(self) -> None:
        self.assertEqual(link_targets(self.bungo), link_targets(self.modern))

    def test_each_readme_links_to_the_other(self) -> None:
        self.assertIn('href="README.bungo.md"', self.modern)
        self.assertIn('href="README.md"', self.bungo)

    def test_anchors_in_the_bungo_readme_point_to_its_headings(self) -> None:
        headings = re.findall(r"^#{2,3} (.+)$", self.bungo, re.M)
        anchors = {
            re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
            for h in headings
        }
        for anchor in re.findall(r'href="#([^"]+)"', self.bungo):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, anchors)


if __name__ == "__main__":
    unittest.main()
