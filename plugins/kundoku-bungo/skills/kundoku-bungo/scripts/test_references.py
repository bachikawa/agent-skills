"""Tests for the data files in references/.

Run from the repository root with:
    python -m unittest discover \
        -s plugins/kundoku-bungo/skills/kundoku-bungo/scripts
"""

from __future__ import annotations

import csv
import re
import unittest
from pathlib import Path

import convert_chars

REFERENCES = Path(__file__).resolve().parent.parent / "references"
VOCABULARY = REFERENCES / "vocabulary.tsv"
VOCABULARY_COLUMNS = [
    "word",
    "reading",
    "kind",
    "meaning",
    "example",
    "example_form",
    "source",
    "source_url",
]
# In a two-part kind, the first part is the main use of the word.
KINDS = {
    "接続",
    "副詞",
    "構文",
    "名詞",
    "連体詞",
    "助動詞",
    "接続・副詞",
    "副詞・接続",
}
SOURCE_URL = re.compile(r"https://ja\.wikisource\.org/w/index\.php\?oldid=\d+")
SOURCE_YEAR = re.compile(r"（(\d{4})）$")
# Wording used only in the name of the emperor; the table must not teach it.
IMPERIAL_WORDS = re.compile(
    r"朕|臣民|皇祖|皇宗|御名|御璽|聖旨|聖慮|叡|宸|思召|休命|大御心|詔|勅|敕"
    r"|御誓文|御沙汰|朝威|皇基|皇政|皇居"
)
# Stenographers' notes in speeches, such as （拍手） and （大喝采）.
STENOGRAPHY_NOTE = re.compile(
    r"（[^（）]*(?:拍手|喝采|喝釆|謹聴|謹聼|謹聽|笑声|笑聲|大笑|異議ナシ|賛成|贊成"
    r"|ヒヤ|ノー)[^（）]*）"
)
# Section and paragraph numbers in Arabic digits are added by transcribers.
DIGITS = re.compile(r"[0-9０-９]")
# Chapter numbers of the original text are left out of examples.
CHAPTER_HEAD = re.compile(r"^第?[一二三四五六七八九十百]+章")
HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff々〻]")


def read_vocabulary() -> tuple[list[str] | None, list[dict[str, str]]]:
    """Reads vocabulary.tsv.

    Returns:
        The column names and one dict per row.
    """
    with open(VOCABULARY, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        rows = list(reader)
        columns = reader.fieldnames
    return columns, rows


def search_key(word: str) -> str:
    """Returns the part of a headword that must appear in its example.

    Args:
        word: A headword such as 「須ク〜スヘシ」 or 「〜スルコト能ハス」.

    Returns:
        The fixed part before the first 〜 (after a leading 〜), without a
        leading スル before コト. The part after the 〜 is not checked,
        because examples are short excerpts and some show a variant form
        (such as 「将ニ〜ント欲ス」).
    """
    key = word.removeprefix("〜").split("〜")[0]
    return key.removeprefix("スル") if key.startswith("スルコト") else key


class VocabularyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.columns, cls.rows = read_vocabulary()
        table = convert_chars.load_kanji_table()
        cls.old_forms = {row["old"] for row in table}
        cls.allowed = {}
        for row in table:
            cls.allowed.setdefault(row["modern"], {row["modern"]})
            cls.allowed[row["modern"]].add(row["old"])

    def test_columns(self) -> None:
        self.assertEqual(self.columns, VOCABULARY_COLUMNS)

    def test_table_is_not_empty(self) -> None:
        self.assertGreater(len(self.rows), 0)

    def test_words_are_unique(self) -> None:
        words = [row["word"] for row in self.rows]
        self.assertEqual(len(words), len(set(words)))

    def test_every_row_is_complete_and_trimmed(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertNotIn(None, row)
                for column in VOCABULARY_COLUMNS:
                    self.assertTrue(row[column], column)
                    self.assertEqual(row[column], row[column].strip(), column)

    def test_kind_is_known(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIn(row["kind"], KINDS)

    def test_words_use_modern_kanji(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertEqual(set(row["word"]) & self.old_forms, set())

    def test_example_form_is_the_word_in_period_spelling(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                key = search_key(row["word"])
                form = row["example_form"]
                self.assertEqual(len(form), len(key))
                for modern, written in zip(key, form):
                    self.assertIn(written, self.allowed.get(modern, {modern}))

    def test_example_contains_the_form(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIn(row["example_form"], row["example"])

    def test_form_is_not_part_of_a_longer_kanji_word(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                example, form = row["example"], row["example_form"]
                position = example.index(form)
                before = example[position - 1 : position]
                self.assertFalse(
                    HAN.match(form[0]) and before and HAN.match(before)
                )

    def test_examples_have_no_transcriber_numbers(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIsNone(DIGITS.search(row["example"]))

    def test_examples_have_no_stenographers_notes(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIsNone(STENOGRAPHY_NOTE.search(row["example"]))

    def test_examples_do_not_start_with_a_chapter_number(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIsNone(CHAPTER_HEAD.match(row["example"]))

    def test_source_is_a_pre_1946_wikisource_revision(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIsNotNone(SOURCE_URL.fullmatch(row["source_url"]))
                year = SOURCE_YEAR.search(row["source"])
                self.assertIsNotNone(year)
                self.assertLessEqual(int(year.group(1)), 1945)

    def test_no_column_uses_imperial_wording(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                for column in ("word", "meaning", "example"):
                    self.assertIsNone(IMPERIAL_WORDS.search(row[column]))


EXAMPLES = REFERENCES / "examples.md"
# Brackets hold stenographers' notes such as （拍手）; <ref and Arabic
# digits come from transcribers and editors.
EDITOR_NOTE = re.compile(r"[()（）\[\]［］]|<ref|[0-9０-９]")
EXAMPLE_SECTION = re.compile(
    r"^### (?P<id>[公演新]-\d+) .*?\n\n文語:\n\n```text\n(?P<bungo>.+?)\n```"
    r"\n\n現代語訳:\n\n```text\n(?P<modern>.+?)\n```\n\n"
    r"(?P<notes>.*?)(?=^### |\Z)",
    re.S | re.M,
)


class ExamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        text = EXAMPLES.read_text(encoding="utf-8")
        cls.headings = re.findall(r"^### ", text, re.M)
        cls.sections = list(EXAMPLE_SECTION.finditer(text))

    def test_every_section_has_both_texts_and_a_source(self) -> None:
        self.assertEqual(len(self.sections), len(self.headings))
        for section in self.sections:
            with self.subTest(section=section["id"]):
                self.assertTrue(section["bungo"].strip())
                self.assertTrue(section["modern"].strip())
                urls = SOURCE_URL.findall(section["notes"])
                self.assertEqual(len(urls), 1)

    def test_each_genre_has_at_least_three_sections(self) -> None:
        genres = [section["id"][0] for section in self.sections]
        for genre in ("公", "演", "新"):
            with self.subTest(genre=genre):
                self.assertGreaterEqual(genres.count(genre), 3)

    def test_section_ids_are_unique(self) -> None:
        ids = [section["id"] for section in self.sections]
        self.assertEqual(len(ids), len(set(ids)))

    def test_bungo_has_no_notes_from_stenographers_or_editors(self) -> None:
        for section in self.sections:
            with self.subTest(section=section["id"]):
                self.assertIsNone(EDITOR_NOTE.search(section["bungo"]))

    def test_no_section_uses_imperial_wording(self) -> None:
        for section in self.sections:
            with self.subTest(section=section["id"]):
                self.assertIsNone(IMPERIAL_WORDS.search(section["bungo"]))
                self.assertIsNone(IMPERIAL_WORDS.search(section["modern"]))


if __name__ == "__main__":
    unittest.main()
