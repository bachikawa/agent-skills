"""Tests for build_vocabulary.py, find_vocabulary.py and their data files.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import collections
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_vocabulary as build  # noqa: E402
import fetch_corpus  # noqa: E402
import find_vocabulary  # noqa: E402
from fetch_corpus import Revision  # noqa: E402

KANJI = {"国": ["國"], "学": ["學"]}


def entry(word: str, **values: str) -> build.Entry:
    """Returns a row of vocabulary_entries.tsv for a test."""
    fields = dict(
        word=word,
        reading="よみ",
        kind="副詞",
        meaning="意味",
        exclude_prev="",
        exclude_next="",
    )
    fields.update(values)
    return build.Entry(**fields)


def page(
    title: str, oldid: int, year: int, lines: list[str], new_forms=False
) -> build.TextPage:
    """Returns a page for a test."""
    row = Revision(title, oldid, "a" * 40, year, "all", "listed", "rule")
    return build.TextPage(row, new_forms, lines)


class FormsTest(unittest.TestCase):
    def test_search_key_follows_the_rule_of_the_tests(self) -> None:
        cases = {
            "須ク〜スヘシ": "須ク",
            "〜スルコト能ハス": "コト能ハス",
            "〜ニ於テ": "ニ於テ",
            "苟モ": "苟モ",
        }
        for word, key in cases.items():
            with self.subTest(word=word):
                self.assertEqual(build.search_key(word), key)

    def test_forms_cover_modern_and_old_kanji(self) -> None:
        self.assertEqual(
            sorted(build.forms_of("国学ニ", KANJI)),
            sorted(["国学ニ", "國学ニ", "国學ニ", "國學ニ"]),
        )


class FindInTest(unittest.TestCase):
    def test_example_starts_after_the_sentence_break(self) -> None:
        line = "前ノ文ナリ。苟モ國アレハ則チ兵備アリ兵備アレハ"
        found = build.find_in([line], ["苟モ"], entry("苟モ"))
        self.assertEqual(found, ("苟モ", "苟モ國アレハ則チ兵備アリ兵備アレハ"))

    def test_word_inside_a_longer_kanji_word_is_skipped(self) -> None:
        line = "其事故ニ因リ之ヲ罰ス故ニ人々之ヲ守ル"
        form, example = build.find_in([line], ["故ニ"], entry("故ニ"))
        # The example starts after the skipped 事故ニ, so that its first
        # 故ニ is the word.
        self.assertEqual(example, "因リ之ヲ罰ス故ニ人々之ヲ守ル")
        self.assertEqual(form, "故ニ")

    def test_excluded_next_character_skips_a_longer_word(self) -> None:
        lines = ["若シク之ヲ犯ス者アラハ", "然ルニ若シ之ヲ犯ス者アラハ罰ス"]
        found = build.find_in(lines, ["若シ"], entry("若シ", exclude_next="ク"))
        self.assertEqual(found[1], "然ルニ若シ之ヲ犯ス者アラハ罰ス")

    def test_construction_shows_forty_characters_after_it(self) -> None:
        after = "一二三四五六七八九十" * 5
        line = "之ヲ以テ" + after
        plain = build.find_in([line], ["ヲ以テ"], entry("以テ"))
        construction = build.find_in([line], ["ヲ以テ"], entry("〜ヲ以テ"))
        self.assertEqual(len(plain[1]), len("之ヲ以テ") + 20)
        self.assertEqual(len(construction[1]), len("之ヲ以テ") + 40)

    def test_skipped_match_in_another_spelling_is_cut_off(self) -> None:
        line = "其事總テ之ヲ定メ総テ人々之ヲ守ル"
        found = build.find_in([line], ["総テ", "總テ"], entry("総テ"))
        self.assertEqual(found[1], "之ヲ定メ総テ人々之ヲ守ル")

    def test_example_starts_after_a_space(self) -> None:
        line = "前ノ文ナリ" + chr(0x3000) + "苟モ國アレハ則チ兵備アリ"
        found = build.find_in([line], ["苟モ"], entry("苟モ"))
        self.assertEqual(found[1], "苟モ國アレハ則チ兵備アリ")

    def test_excluded_neighbours_skip_a_word_of_the_same_form(self) -> None:
        conjunction = entry("及ヒ", exclude_prev="ニ")
        verb_only = "其害ハ遂ニ他ニ及ヒ甚シキ者アリ"
        self.assertIsNone(build.find_in([verb_only], ["及ヒ"], conjunction))
        both = "其害ハ遂ニ他ニ及ヒ甚シ又甲ノモノ及ヒ乙ノモノハ之ヲ罰ス"
        found = build.find_in([both], ["及ヒ"], conjunction)
        self.assertIn("モノ及ヒ乙", found[1])

    def test_short_or_numbered_segments_are_skipped(self) -> None:
        lines = ["故ニ", "第1條故ニ之ヲ定ム"]
        self.assertIsNone(build.find_in(lines, ["故ニ"], entry("故ニ")))


class BuildRowsTest(unittest.TestCase):
    def test_first_page_in_order_gives_the_example(self) -> None:
        pages = [
            page("甲法", 1, 1880, ["苟モ國アレハ則チ兵備アリ"]),
            page("乙法", 2, 1890, ["苟モ學ヲ修メサレハ則チ"]),
        ]
        rows, missing = build.build_rows(
            [entry("苟モ"), entry("加之")], pages, KANJI
        )
        self.assertEqual(missing, ["加之"])
        self.assertEqual(
            rows,
            [
                [
                    "苟モ",
                    "よみ",
                    "副詞",
                    "意味",
                    "苟モ國アレハ則チ兵備アリ",
                    "苟モ",
                    "甲法（1880）",
                    "https://ja.wikisource.org/w/index.php?oldid=1",
                ]
            ],
        )

    def test_pages_in_new_forms_come_after_the_others(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            new = "{{Header|year=1872}}\n== 新字体 ==\n苟モ国アレハ則チ兵備アリ"
            old = "{{Header|year=1890}}\n苟モ國アレハ則チ兵備アリ"
            pages = [
                fetch_corpus.Page("学制", 1, new, []),
                fetch_corpus.Page("某法", 2, old, []),
            ]
            rows = []
            for item, year in zip(pages, (1872, 1890)):
                fetch_corpus.save_page(work, item)
                rows.append(
                    Revision(
                        item.title, item.oldid, item.sha1, year, "all",
                        "listed", "rule",
                    )
                )
            ordered = build.text_pages(work, rows)
        self.assertEqual([p.row.oldid for p in ordered], [2, 1])
        self.assertEqual([p.new_forms for p in ordered], [False, True])

    def test_page_written_over_in_new_forms_comes_after_too(self) -> None:
        pairs = [("国", "國"), ("体", "體"), ("会", "會"), ("従", "從")]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            new = "{{Header|year=1868}}\n広ク会議ヲ興シ国是ヲ定メ政体ヲ立テ従前ノ国法ヲ改ム"
            old = "{{Header|year=1890}}\n國是ヲ定メ政體ヲ立テ從前ノ國法ヲ改メ會議ヲ興ス"
            items = [
                fetch_corpus.Page("政体", 1, new, []),
                fetch_corpus.Page("某法", 2, old, []),
            ]
            rows = []
            for item, year in zip(items, (1868, 1890)):
                fetch_corpus.save_page(work, item)
                rows.append(
                    Revision(
                        item.title, item.oldid, item.sha1, year, "all",
                        "listed", "rule",
                    )
                )
            ordered = build.text_pages(work, rows, pairs)
        self.assertEqual([p.row.oldid for p in ordered], [2, 1])


class RenderTest(unittest.TestCase):
    def test_tab_in_a_value_is_an_error(self) -> None:
        with self.assertRaises(build.VocabularyError):
            build.render([["苟モ\t", "", "", "", "", "", "", ""]])


class EntriesFileTest(unittest.TestCase):
    def test_unexpected_rows_are_errors(self) -> None:
        header = "\t".join(build.ENTRY_COLUMNS)
        cases = {
            "kind": "苟モ\tいやしくも\t名前\t意味\t\t",
            "empty": "苟モ\t\t副詞\t意味\t\t",
            "duplicate": "苟モ\tいやしくも\t副詞\t意味\t\t\n"
            "苟モ\tいやしくも\t副詞\t意味\t\t",
        }
        for name, lines in cases.items():
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory, "vocabulary_entries.tsv")
                    path.write_text(f"{header}\n{lines}\n", "utf-8")
                    with self.assertRaises(build.VocabularyError):
                        build.read_entries(path)

    def test_table_has_every_entry_in_order(self) -> None:
        entries = build.read_entries()
        with open(build.VOCABULARY_FILE, encoding="utf-8", newline="") as f:
            rows = list(
                csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
            )
        self.assertEqual(
            [(r["word"], r["reading"], r["kind"], r["meaning"]) for r in rows],
            [(e.word, e.reading, e.kind, e.meaning) for e in entries],
        )


class FindVocabularyTest(unittest.TestCase):
    def test_words_with_fixed_kana_are_counted_once_per_text(self) -> None:
        texts = [
            ("a", ["悉ク之ヲ廢ス本令ハ甲ナリ", "悉ク改ム"]),
            ("a", ["悉ク之ヲ廢ス本令ハ甲ナリ", "悉ク改ム"]),
            ("b", ["悉ク定ム本令ニ依ル本令ノ規定"]),
        ]
        counts = find_vocabulary.fixed_kana_words(texts)
        self.assertEqual(counts["悉ク"], 2)
        self.assertNotIn("本令ハ", counts)

    def test_stem_after_a_kanji_is_not_counted(self) -> None:
        texts = [("a", ["其事故ニ因リ"]), ("b", ["故ニ之ヲ定ム"])]
        counts = find_vocabulary.fixed_kana_words(texts)
        self.assertEqual(counts["故ニ"], 1)
        self.assertNotIn("事故ニ", counts)

    def test_fixed_share_is_nine_tenths(self) -> None:
        nine = [f"悉ク之ヲ{n}" for n in "一二三四五六七八九"]
        at_limit = [("a", nine + ["悉ニ之ヲ十"])]
        below = [("a", nine[:8] + ["悉ニ之ヲ十", "悉ニ之ヲ百"])]
        self.assertIn("悉ク", find_vocabulary.fixed_kana_words(at_limit))
        self.assertNotIn("悉ク", find_vocabulary.fixed_kana_words(below))

    def test_word_only_in_a_later_version_is_counted(self) -> None:
        texts = [
            ("法", ["本令ハ甲ナリ"]),
            ("法", ["悉ク之ヲ廢ス"]),
            ("乙", ["悉ク改ム"]),
        ]
        self.assertEqual(find_vocabulary.fixed_kana_words(texts)["悉ク"], 2)

    def test_amendment_and_old_forms_share_the_key_of_the_law(self) -> None:
        modern = {"條": "条"}
        pairs = [
            ("道路取締令中改正/道路取締令", "道路取締令"),
            ("民事訴訟法施行条例/公布時", "民事訴訟法施行條例"),
            ("北海道國有未開地處分法(明治41年法律第57号)", "北海道國有未開地處分法"),
        ]
        for first, second in pairs:
            with self.subTest(title=first):
                self.assertEqual(
                    find_vocabulary.law_key(first, modern),
                    find_vocabulary.law_key(second, modern),
                )

    def test_versions_of_one_law_share_a_key(self) -> None:
        titles = (
            "國債整理基金特別會計法 (大正八年三月二十四日法律第十四号)",
            "國債整理基金特別會計法",
        )
        self.assertEqual(
            {find_vocabulary.law_key(t) for t in titles},
            {"國債整理基金特別會計法"},
        )
        self.assertEqual(
            find_vocabulary.law_key("地所質入書入規則/公布時"), "地所質入書入規則"
        )

    def test_clause_words_need_a_break_before_and_a_kanji_after(self) -> None:
        lines = ["苟モ國アレハ兵備アリ。加之其內不都合アリ"]
        self.assertEqual(
            find_vocabulary.clause_words(lines), {"苟モ"}
        )

    def test_counts_are_per_distinct_text(self) -> None:
        texts = [("a", ["苟モ國アレハ"]), ("a", ["苟モ國アレハ"]), ("b", ["苟モ國"])]
        counts = find_vocabulary.count_texts(texts)
        self.assertEqual(counts, collections.Counter({"苟モ": 2}))


if __name__ == "__main__":
    unittest.main()
