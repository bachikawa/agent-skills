"""Tests for build_kanji_table.py and the data files it uses.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import csv
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_kanji_table as build  # noqa: E402
import fetch_corpus  # noqa: E402
from fetch_corpus import Page, Revision  # noqa: E402

COMPAT_SEA = chr(0xFA45)


def revision(
    title: str, oldid: int, year: int, usage: str = "all"
) -> Revision:
    """Returns a row of corpus_revisions.tsv for a test."""
    return Revision(title, oldid, "a" * 40, year, usage, "listed", "rule")


def override(modern: str, old: str, **values: str) -> build.Override:
    fields = dict(
        modern=modern,
        old=old,
        kind="",
        one_to_many="",
        note="",
        evidence="",
        evidence_url="",
    )
    fields.update(values)
    return build.Override(**fields)


class UnihanPairsTest(unittest.TestCase):
    def test_old_variants_are_read_with_their_count(self) -> None:
        lines = [
            "# comment",
            "U+5B66\tkJapaneseOldVariant\tU+5B78",
            "U+5F01\tkJapaneseOldVariant\tU+8FA8 U+8FAF",
            "U+5B66\tkSemanticVariant\tU+5B78",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "Unihan.zip")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Unihan_Variants.txt", "\n".join(lines))
            pairs = build.unihan_pairs(path)
        self.assertEqual(
            pairs,
            [("学", "學", False), ("弁", "辨", True), ("弁", "辯", True)],
        )


class BuildRowsTest(unittest.TestCase):
    PAGES = [
        (revision("乙法", 2, 1880), "國ノ法"),
        (revision("甲法", 1, 1890), "國學"),
    ]

    def test_earliest_page_is_the_evidence(self) -> None:
        rows, missing = build.build_rows(
            [("国", "國", False), ("学", "學", False), ("体", "體", False)],
            self.PAGES,
            {},
        )
        self.assertEqual(
            rows,
            [
                [
                    "国",
                    "國",
                    "U+570B",
                    "joyo",
                    "0",
                    "",
                    "乙法（1880）",
                    "https://ja.wikisource.org/w/index.php?oldid=2",
                ],
                [
                    "学",
                    "學",
                    "U+5B78",
                    "joyo",
                    "0",
                    "",
                    "甲法（1890）",
                    "https://ja.wikisource.org/w/index.php?oldid=1",
                ],
            ],
        )
        self.assertEqual(missing, [("体", "體")])

    def test_compatibility_ideograph_is_marked_compat(self) -> None:
        pages = [(revision("法", 1, 1880), "陸" + COMPAT_SEA + "軍")]
        rows, _ = build.build_rows([("海", COMPAT_SEA, False)], pages, {})
        self.assertEqual(rows[0][3], "compat")

    def test_override_sets_one_to_many_and_the_note(self) -> None:
        overrides = {
            ("国", "國"): override("国", "國", one_to_many="1", note="選ぶ")
        }
        rows, _ = build.build_rows(
            [("国", "國", False)], self.PAGES, overrides
        )
        self.assertEqual(rows[0][4:6], ["1", "選ぶ"])

    def test_design_row_comes_from_the_override(self) -> None:
        design = override(
            "衆",
            "眾",
            kind="design",
            one_to_many="1",
            note="ふつうは衆",
            evidence="官報",
            evidence_url="https://dl.ndl.go.jp/pid/1",
        )
        rows, _ = build.build_rows([], self.PAGES, {("衆", "眾"): design})
        self.assertEqual(
            rows,
            [
                [
                    "衆",
                    "眾",
                    "U+773E",
                    "design",
                    "1",
                    "ふつうは衆",
                    "官報",
                    "https://dl.ndl.go.jp/pid/1",
                ]
            ],
        )

    def test_several_old_forms_without_a_note_is_an_error(self) -> None:
        with self.assertRaises(build.TableError):
            build.build_rows([("国", "國", True)], self.PAGES, {})

    def test_override_that_matches_no_row_is_an_error(self) -> None:
        overrides = {("体", "體"): override("体", "體", note="x")}
        with self.assertRaises(build.TableError):
            build.build_rows([("体", "體", False)], self.PAGES, overrides)


class EvidencePagesTest(unittest.TestCase):
    def test_pages_are_sorted_and_examples_only_is_skipped(self) -> None:
        pages = [
            Page("乙法", 2, "{{Header|year=1890}}\n國", []),
            Page("甲法", 1, "{{Header|year=1890}}\n學", []),
            Page("株", 3, "{{Header|year=1880}}\n舊", []),
        ]
        rows = [
            Revision(p.title, p.oldid, p.sha1, year, "all", "listed", "rule")
            for p, year in zip(pages[:2], (1895, 1890))
        ] + [
            Revision("株", 3, pages[2].sha1, 1880, "examples_only", "x", "x")
        ]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for page in pages:
                fetch_corpus.save_page(work, page)
            result = build.evidence_pages(work, rows)
        self.assertEqual([r.oldid for r, _ in result], [1, 2])

    def test_page_with_another_sha1_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, Page("法", 1, "書き換えた", []))
            with self.assertRaises(build.TableError):
                build.evidence_pages(work, [revision("法", 1, 1890)])


class RenderTest(unittest.TestCase):
    def test_tab_in_a_value_is_an_error(self) -> None:
        with self.assertRaises(build.TableError):
            build.render([["国", "國\t", "", "", "", "", "", ""]])


class OverridesFileTest(unittest.TestCase):
    def test_file_with_unexpected_columns_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "kanji_overrides.tsv")
            path.write_text("modern\told\n国\t國\n", encoding="utf-8")
            with self.assertRaises(build.TableError):
                build.read_overrides(path)

    def test_every_override_is_in_the_table(self) -> None:
        with open(build.KANJI_FILE, encoding="utf-8", newline="") as source:
            table = {
                (r["modern"], r["old"]): r
                for r in csv.DictReader(
                    source, delimiter="\t", quoting=csv.QUOTE_NONE
                )
            }
        for key, value in build.read_overrides().items():
            with self.subTest(pair=key):
                self.assertIn(key, table)
                if value.note:
                    self.assertEqual(table[key]["note"], value.note)


class KanjiTableEvidenceTest(unittest.TestCase):
    def test_evidence_is_a_revision_the_kanji_table_may_use(self) -> None:
        usage = {r.oldid: r.usage for r in fetch_corpus.read_revisions()}
        with open(build.KANJI_FILE, encoding="utf-8", newline="") as source:
            rows = list(
                csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
            )
        for row in rows:
            with self.subTest(pair=(row["modern"], row["old"])):
                if row["kind"] == "design":
                    self.assertTrue(row["evidence_url"])
                    continue
                oldid = int(re.search(r"oldid=(\d+)", row["evidence_url"])[1])
                self.assertIn(usage.get(oldid), build.EVIDENCE_USAGES)


if __name__ == "__main__":
    unittest.main()
