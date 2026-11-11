"""Tests for check_examples.py.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_examples  # noqa: E402
import fetch_corpus  # noqa: E402
from fetch_corpus import Page, Revision  # noqa: E402

HEADER = "{{Header\n|title=演説\n|year=1882\n}}\n"


def section(id_: str, bungo: str, source: str) -> str:
    """Returns a section of examples.md for a test."""
    return (
        f"### {id_} 題\n\n文語:\n\n```text\n{bungo}\n```\n\n"
        f"現代語訳:\n\n```text\n訳\n```\n\n"
        f"- 主な言い回し: なし\n- 出典: {source}\n\n"
    )


def url(oldid: int) -> str:
    """Returns the URL of a revision."""
    return f"題。https://ja.wikisource.org/w/index.php?oldid={oldid}"


class ReadExamplesTest(unittest.TestCase):
    def test_sections_give_the_passage_and_the_revision(self) -> None:
        text = section("演-1", "抑モ學問ノ独立", url(5)) + section(
            "新-1", "敢て怪しむに足らず", url(8)
        )

        examples = check_examples.read_examples(text)

        self.assertEqual(
            examples,
            [
                check_examples.Example("演-1", "抑モ學問ノ独立", 5),
                check_examples.Example("新-1", "敢て怪しむに足らず", 8),
            ],
        )

    def test_section_without_a_revision_is_an_error(self) -> None:
        text = section("演-1", "抑モ", "題")

        with self.assertRaises(ValueError):
            check_examples.read_examples(text)

    def test_section_with_two_revisions_is_an_error(self) -> None:
        text = section("演-1", "抑モ", url(5) + " " + url(6))

        with self.assertRaises(ValueError):
            check_examples.read_examples(text)

    def test_heading_without_a_passage_is_an_error(self) -> None:
        text = "### 演-1 題\n\n- 出典: " + url(5) + "\n"

        with self.assertRaises(ValueError):
            check_examples.read_examples(text)


class CheckTest(unittest.TestCase):
    def run_check(
        self, bungo: str, content: str, usage: str = "examples_only"
    ) -> list[str]:
        """Saves one revision in a work directory and checks a passage."""
        page = Page("演説", 5, content, [])
        row = Revision("演説", 5, page.sha1, 1882, usage, "pinned", "pinned")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, page)
            example = check_examples.Example("演-1", bungo, 5)
            return check_examples.check([example], [row], work)

    def test_passage_in_one_line_passes(self) -> None:
        content = HEADER + "抑モ學問ノ独立ヲ全フスル\n"

        self.assertEqual(self.run_check("學問ノ独立", content), [])

    def test_passage_not_in_the_text_fails(self) -> None:
        content = HEADER + "抑モ學問ノ独立ヲ全フスル\n"

        errors = self.run_check("學問ノ自由", content)

        self.assertEqual(errors, ["演-1: the passage is not in revision 5"])

    def test_passage_across_two_lines_fails(self) -> None:
        content = HEADER + "抑モ學問ノ\n独立ヲ全フスル\n"

        self.assertEqual(len(self.run_check("學問ノ独立", content)), 1)

    def test_passage_from_a_line_with_half_width_brackets_fails(self) -> None:
        # The rule of the vocabulary table: such a line may hold an
        # editor's note, so it is not quoted.
        content = HEADER + "抑モ學問ノ独立(注)ヲ全フスル\n"

        self.assertEqual(len(self.run_check("學問ノ独立", content)), 1)

    def test_passage_from_a_line_with_imperial_wording_fails(self) -> None:
        content = HEADER + "朕惟フニ學問ノ独立ハ\n"

        self.assertEqual(len(self.run_check("學問ノ独立", content)), 1)

    def test_kanji_only_revision_fails(self) -> None:
        content = HEADER + "抑モ學問ノ独立ヲ全フスル\n"

        errors = self.run_check("學問ノ独立", content, usage="kanji_only")

        self.assertEqual(
            errors, ["演-1: revision 5 is not pinned for the examples"]
        )

    def test_revision_not_in_the_list_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            example = check_examples.Example("演-1", "學問", 9)
            errors = check_examples.check([example], [], Path(directory))

        self.assertEqual(
            errors, ["演-1: revision 9 is not pinned for the examples"]
        )

    def test_changed_copy_fails(self) -> None:
        page = Page("演説", 5, HEADER + "學問ノ独立\n", [])
        row = Revision("演説", 5, "0" * 40, 1882, "all", "pinned", "pinned")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, page)
            example = check_examples.Example("演-1", "學問ノ独立", 5)
            errors = check_examples.check([example], [row], work)

        self.assertEqual(
            errors,
            ["演-1: revision 5 is not fetched; run fetch_corpus.py fetch"],
        )


class ExamplesFileTest(unittest.TestCase):
    def test_every_section_of_examples_md_is_read(self) -> None:
        text = check_examples.EXAMPLES_FILE.read_text(encoding="utf-8")

        examples = check_examples.read_examples(text)

        self.assertEqual(len(examples), text.count("\n### "))


if __name__ == "__main__":
    unittest.main()
