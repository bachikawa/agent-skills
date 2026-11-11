"""Tests for check_examples.py.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_examples  # noqa: E402
import fetch_corpus  # noqa: E402
from fetch_corpus import Page, Revision  # noqa: E402

HEADER = "{{Header\n|title=演説\n|year=1882\n}}\n"
TEXT = HEADER + "抑モ學問ノ独立ヲ全フスル\n"


def section(id_: str, bungo: str, source: str) -> str:
    """Returns a section of examples.md for a test."""
    return (
        f"### {id_} 題\n\n文語:\n\n```text\n{bungo}\n```\n\n"
        f"現代語訳:\n\n```text\n訳\n```\n\n"
        f"- 主な言い回し: なし\n- 出典: {source}\n\n"
    )


def url(oldid: int) -> str:
    """Returns the source line of a revision."""
    return f"題。https://ja.wikisource.org/w/index.php?oldid={oldid}"


def saved(work: Path, content: str, usage: str = "examples_only") -> Revision:
    """Saves revision 5 of the page 演説 and returns its row."""
    page = Page("演説", 5, content, [])
    fetch_corpus.save_page(work, page)
    return Revision("演説", 5, page.sha1, 1882, usage, "pinned", "pinned")


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
        with self.assertRaises(ValueError):
            check_examples.read_examples(section("演-1", "抑モ", "題"))

    def test_section_with_two_revisions_is_an_error(self) -> None:
        text = section("演-1", "抑モ", url(5) + " " + url(6))

        with self.assertRaises(ValueError):
            check_examples.read_examples(text)

    def test_heading_without_a_passage_is_an_error(self) -> None:
        text = "### 演-1 題\n\n- 出典: " + url(5) + "\n"

        with self.assertRaises(ValueError):
            check_examples.read_examples(text)

    def test_section_without_a_source_before_another_is_an_error(
        self,
    ) -> None:
        # The pattern would reach the source line of the next section; the
        # count of sections shows it.
        broken = "### 演-1 題\n\n文語:\n\n```text\n抑モ\n```\n\n"
        text = broken + section("演-2", "學問", url(5))

        with self.assertRaises(ValueError):
            check_examples.read_examples(text)


class CheckTest(unittest.TestCase):
    def run_check(
        self,
        bungo: str,
        content: str,
        usage: str = "examples_only",
        ranges: dict | None = None,
    ) -> list[str]:
        """Saves one revision in a work directory and checks a passage."""
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            row = saved(work, content, usage)
            example = check_examples.Example("演-1", bungo, 5)
            return check_examples.check(
                [example], [row], work, {} if ranges is None else ranges
            )

    def test_passage_in_one_line_passes(self) -> None:
        self.assertEqual(self.run_check("學問ノ独立", TEXT), [])

    def test_passage_not_in_the_text_fails(self) -> None:
        errors = self.run_check("學問ノ自由", TEXT)

        self.assertEqual(errors, ["演-1: the passage is not in revision 5"])

    def test_passage_across_two_lines_fails(self) -> None:
        content = HEADER + "抑モ學問ノ\n独立ヲ全フスル\n"

        self.assertEqual(len(self.run_check("學問ノ独立", content)), 1)

    def test_passage_across_a_stenographers_note_passes(self) -> None:
        # The note is taken out of the line, as for the vocabulary table.
        content = HEADER + "抑モ學問ノ（拍手）独立ヲ全フスル\n"

        self.assertEqual(self.run_check("學問ノ独立", content), [])

    def test_passage_from_a_line_with_half_width_brackets_fails(self) -> None:
        # The rule of the vocabulary table: such a line may hold an
        # editor's note, so it is not quoted.
        content = HEADER + "抑モ學問ノ独立(注)ヲ全フスル\n"

        self.assertEqual(len(self.run_check("學問ノ独立", content)), 1)

    def test_passage_from_a_line_with_imperial_wording_fails(self) -> None:
        content = HEADER + "朕惟フニ學問ノ独立ハ\n"

        self.assertEqual(len(self.run_check("學問ノ独立", content)), 1)

    def test_passage_in_a_range_of_line_exclusions_fails(self) -> None:
        content = HEADER + "前ノ行\n抑モ學問ノ独立ヲ全フスル\n後ノ行\n"
        ranges = {"演説": [("抑モ學問", "抑モ學問")]}

        self.assertEqual(
            len(self.run_check("學問ノ独立", content, ranges=ranges)), 1
        )

    def test_kanji_only_revision_fails(self) -> None:
        errors = self.run_check("學問ノ独立", TEXT, usage="kanji_only")

        self.assertEqual(
            errors, ["演-1: revision 5 is not pinned for the examples"]
        )

    def test_revision_not_in_the_list_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            example = check_examples.Example("演-1", "學問", 9)
            errors = check_examples.check([example], [], Path(directory), {})

        self.assertEqual(
            errors, ["演-1: revision 9 is not pinned for the examples"]
        )

    def test_revision_not_saved_fails(self) -> None:
        row = Revision("演説", 5, "a" * 40, 1882, "all", "pinned", "pinned")
        with tempfile.TemporaryDirectory() as directory:
            example = check_examples.Example("演-1", "學問", 5)
            errors = check_examples.check([example], [row], Path(directory), {})

        self.assertEqual(
            errors,
            ["演-1: revision 5 is not fetched; run fetch_corpus.py fetch"],
        )

    def test_changed_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            row = saved(work, TEXT)
            changed = Revision(
                row.title, row.oldid, "0" * 40, row.year, "all", "x", "x"
            )
            example = check_examples.Example("演-1", "學問ノ独立", 5)
            errors = check_examples.check([example], [changed], work, {})

        self.assertEqual(
            errors,
            ["演-1: revision 5 is not fetched; run fetch_corpus.py fetch"],
        )


class MainTest(unittest.TestCase):
    def run_main(self, bungo: str) -> tuple[int, str]:
        """Runs main() on one section and one saved revision."""
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            examples = work / "examples.md"
            examples.write_text(section("演-1", bungo, url(5)), encoding="utf-8")
            row = saved(work, TEXT)
            stderr = io.StringIO()
            with (
                mock.patch.object(check_examples, "EXAMPLES_FILE", examples),
                mock.patch.object(
                    fetch_corpus, "read_revisions", return_value=[row]
                ),
                contextlib.redirect_stderr(stderr),
            ):
                code = check_examples.main(["--work", str(work)])
        return code, stderr.getvalue()

    def test_main_returns_0_when_every_section_passes(self) -> None:
        code, stderr = self.run_main("學問ノ独立")

        self.assertEqual(code, 0)
        self.assertIn("1 sections, 0 failed", stderr)

    def test_main_returns_1_when_a_section_fails(self) -> None:
        code, stderr = self.run_main("學問ノ自由")

        self.assertEqual(code, 1)
        self.assertIn("the passage is not in revision 5", stderr)

    def test_main_returns_1_for_a_directory_in_the_repository(self) -> None:
        work = fetch_corpus.REPOSITORY / "work-for-test"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = check_examples.main(["--work", str(work)])

        self.assertEqual(code, 1)
        self.assertIn("outside the repository", stderr.getvalue())
        self.assertFalse(work.exists())


class ExamplesFileTest(unittest.TestCase):
    def test_every_cited_revision_is_pinned_for_the_examples(self) -> None:
        # Without the network, this finds a section whose revision was
        # not added to corpus_revisions.tsv.
        text = check_examples.EXAMPLES_FILE.read_text(encoding="utf-8")
        examples = check_examples.read_examples(text)
        usage = {row.oldid: row.usage for row in fetch_corpus.read_revisions()}

        self.assertGreater(len(examples), 0)
        for example in examples:
            with self.subTest(section=example.id):
                self.assertIn(
                    usage.get(example.oldid), check_examples.EXAMPLE_USAGES
                )


if __name__ == "__main__":
    unittest.main()
