"""Tests for the data files corpus_revisions.tsv and line_exclusions.tsv.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clean_text  # noqa: E402
import fetch_corpus  # noqa: E402

CITED = re.compile(r"oldid=(\d+)")


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

    def test_every_revision_cited_by_a_table_is_pinned_for_it(self) -> None:
        allowed = {
            "kanji.tsv": {"all", "kanji_only"},
            "vocabulary.tsv": {"all", "text_only"},
            "examples.md": {"all", "text_only", "examples_only"},
        }
        rows = {row.oldid: row for row in self.rows}
        for name, usages in allowed.items():
            text = (fetch_corpus.REFERENCES / name).read_text(encoding="utf-8")
            for oldid in sorted({int(m) for m in CITED.findall(text)}):
                with self.subTest(table=name, oldid=oldid):
                    self.assertEqual(rows[oldid].source, "pinned")
                    self.assertIn(rows[oldid].usage, usages)

    def test_pinned_rows_are_what_pinned_oldids_keeps(self) -> None:
        pinned = {row.oldid for row in self.rows if row.source == "pinned"}
        self.assertEqual(pinned, set(fetch_corpus.pinned_oldids()))

    def test_non_government_pages_are_not_kanji_evidence(self) -> None:
        for row in self.rows:
            if row.title in fetch_corpus.NON_GOVERNMENT_PAGES:
                with self.subTest(title=row.title):
                    self.assertIn(row.usage, {"text_only", "examples_only"})

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

    def test_no_post_war_or_foreign_title_is_left(self) -> None:
        # Written apart from the patterns in fetch_corpus.py, so that a
        # gap in those patterns shows up here.
        words = (
            "平成", "令和", "昭和二十一", "昭和二十二", "昭和二十三",
            "昭和二十四", "昭和二十五", "昭和二十六", "昭和二十七",
            "昭和二十八", "昭和二十九", "昭和三十", "昭和四十", "昭和五十",
            "昭和六十", "昭和21", "昭和22", "昭和23", "昭和24", "昭和25",
            "昭和26", "昭和27", "昭和28", "昭和29", "降伏", "條約", "条約",
            "協定", "満洲国", "滿洲國",
        )
        for row in self.rows:
            with self.subTest(title=row.title):
                self.assertFalse(any(w in row.title for w in words))

    def test_vocabulary_and_examples_cite_no_kanji_only_page(self) -> None:
        # The completion condition of #9: pages kept for kanji forms only
        # must not give examples.
        cited = set()
        for name in ("vocabulary.tsv", "examples.md"):
            text = (fetch_corpus.REFERENCES / name).read_text(encoding="utf-8")
            cited.update(int(m) for m in CITED.findall(text))
        usage = {row.oldid: row.usage for row in self.rows}
        for oldid in sorted(cited):
            with self.subTest(oldid=oldid):
                self.assertNotEqual(usage[oldid], "kanji_only")

    def test_every_row_has_a_sha1(self) -> None:
        for row in self.rows:
            with self.subTest(title=row.title):
                self.assertRegex(row.sha1, "^[0-9a-f]{40}$")

    def test_mixed_pages_have_line_exclusions(self) -> None:
        ranges = clean_text.read_line_exclusions()
        mixed = {row.title for row in self.rows if row.rule == "mixed"}
        self.assertEqual(mixed, set(fetch_corpus.MIXED_PAGES))
        self.assertEqual(set(ranges), mixed)


if __name__ == "__main__":
    unittest.main()
