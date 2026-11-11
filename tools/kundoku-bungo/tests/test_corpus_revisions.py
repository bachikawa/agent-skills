"""Tests for the data files corpus_revisions.tsv and line_exclusions.tsv.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clean_text  # noqa: E402
import fetch_corpus  # noqa: E402


class CorpusRevisionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = fetch_corpus.read_revisions()
        cls.by_title = {row.title: row for row in cls.rows}

    def test_titles_and_revisions_are_unique(self) -> None:
        self.assertEqual(len(self.by_title), len(self.rows))
        oldids = [row.oldid for row in self.rows]
        self.assertEqual(len(set(oldids)), len(oldids))

    def test_rows_are_sorted_by_title(self) -> None:
        titles = [row.title for row in self.rows]
        self.assertEqual(titles, sorted(titles))

    def test_every_revision_cited_by_the_tables_is_pinned(self) -> None:
        pinned = {row.oldid for row in self.rows if row.source == "pinned"}
        self.assertEqual(pinned, set(fetch_corpus.pinned_oldids()))

    def test_years_are_in_the_period(self) -> None:
        for row in self.rows:
            with self.subTest(title=row.title):
                self.assertGreaterEqual(row.year, fetch_corpus.FIRST_YEAR)
                self.assertLessEqual(row.year, fetch_corpus.LAST_YEAR)

    def test_imperial_house_laws_are_kanji_only(self) -> None:
        names = ("王公家軌範", "皇統譜令", "華族令", "皇室典範")
        rows = [r for r in self.rows if r.title.startswith(names)]
        self.assertGreaterEqual(len(rows), len(names))
        for row in rows:
            with self.subTest(title=row.title):
                self.assertEqual(row.usage, "kanji_only")

    def test_no_imperial_title_is_used_for_the_vocabulary(self) -> None:
        for row in self.rows:
            if row.usage == "kanji_only" or row.rule == "mixed":
                continue
            with self.subTest(title=row.title):
                found = fetch_corpus.IMPERIAL_TITLE.search(row.title)
                self.assertIsNone(found)

    def test_mixed_pages_have_line_exclusions(self) -> None:
        ranges = clean_text.read_line_exclusions()
        mixed = {row.title for row in self.rows if row.rule == "mixed"}
        self.assertEqual(mixed, set(fetch_corpus.MIXED_PAGES))
        self.assertEqual(set(ranges), mixed)


if __name__ == "__main__":
    unittest.main()
