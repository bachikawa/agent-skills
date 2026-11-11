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
SOURCE_URL = re.compile(r"https://ja\.wikisource\.org/w/index\.php\?oldid=\d+")
# Wording used only in the name of the emperor; examples must not teach it.
IMPERIAL_WORDS = re.compile(r"朕|臣民|皇祖|皇宗|御名|御璽|聖旨|聖慮|叡|詔|勅")
SOURCE_YEAR = re.compile(r"（(\d{4})）$")


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


class VocabularyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.columns, self.rows = read_vocabulary()

    def test_columns(self) -> None:
        self.assertEqual(self.columns, VOCABULARY_COLUMNS)

    def test_table_is_not_empty(self) -> None:
        self.assertGreater(len(self.rows), 0)

    def test_words_are_unique(self) -> None:
        words = [row["word"] for row in self.rows]
        self.assertEqual(len(words), len(set(words)))

    def test_every_row_is_complete(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                for column in VOCABULARY_COLUMNS:
                    self.assertTrue(row[column], column)

    def test_example_contains_the_form(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertIn(row["example_form"], row["example"])

    def test_source_is_a_pre_1946_wikisource_revision(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertRegex(row["source_url"], SOURCE_URL.pattern + "$")
                year = SOURCE_YEAR.search(row["source"])
                self.assertIsNotNone(year)
                self.assertLessEqual(int(year.group(1)), 1945)

    def test_examples_do_not_use_imperial_wording(self) -> None:
        for row in self.rows:
            with self.subTest(word=row["word"]):
                self.assertNotRegex(row["example"], IMPERIAL_WORDS.pattern)


if __name__ == "__main__":
    unittest.main()
