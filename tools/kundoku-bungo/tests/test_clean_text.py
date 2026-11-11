"""Tests for clean_text.py.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests

The wikitext in these tests is written for the tests. The few words of
law text in them come from Japanese government documents in the public
domain; no editors' notes from Wikisource are copied.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clean_text  # noqa: E402

COMPAT_SEA = chr(0xFA45)
SPACE = clean_text.IDEOGRAPHIC_SPACE
CONSCRIPTION = "徵兵令詔書及ヒ徵兵吿諭"


class BodyTest(unittest.TestCase):
    def test_header_template_is_removed(self) -> None:
        page = "{{Header\n|title=法\n|notes={{異体字使用リスト|海}}\n}}\n本文"
        self.assertEqual(clean_text.body(page), "\n本文")

    def test_page_without_header_is_unchanged(self) -> None:
        self.assertEqual(clean_text.body("本文"), "本文")


class StripMarkupTest(unittest.TestCase):
    def test_ref_notes_are_removed(self) -> None:
        text = '本文<ref name="a">注</ref>ナリ<ref name="a"/>'
        self.assertEqual(clean_text.strip_markup(text), "本文ナリ")

    def test_bold_numbers_are_removed(self) -> None:
        self.assertEqual(clean_text.strip_markup("'''一'''法ハ"), "法ハ")

    def test_text_templates_keep_their_first_argument(self) -> None:
        text = "陸{{異体字|&#xFA45;}}軍{{Lang|ko|國}}{{r|學|がく}}{{Ruby|校|こう}}"
        self.assertEqual(
            clean_text.strip_markup(text), "陸" + COMPAT_SEA + "軍國學校"
        )

    def test_compatibility_ideograph_from_a_reference_survives(self) -> None:
        result = clean_text.strip_markup("&#64069;軍")
        self.assertEqual(result, COMPAT_SEA + "軍")

    def test_other_templates_and_tags_are_removed(self) -> None:
        text = '<div style="x">本文</div>{{デフォルトソート:ほん}}'
        self.assertEqual(clean_text.strip_markup(text), "本文")

    def test_links_keep_their_label_and_categories_are_removed(self) -> None:
        text = "[[刑法|此法]]ニ[[依ル]][[Category:明治13年の法令]]"
        self.assertEqual(clean_text.strip_markup(text), "此法ニ依ル")

    def test_variation_selectors_are_removed(self) -> None:
        text = "葛" + chr(0xE0100) + "城" + chr(0xFE00)
        self.assertEqual(clean_text.strip_markup(text), "葛城")


class StenographyNoteTest(unittest.TestCase):
    def test_note_of_a_stenographer_is_removed(self) -> None:
        text = "諸君ニ告ク（拍手）余ハ"
        self.assertEqual(clean_text.remove_stenography_notes(text), "諸君ニ告ク余ハ")

    def test_bracket_of_the_original_text_is_kept(self) -> None:
        text = "（別紙）詔書寫"
        self.assertEqual(clean_text.remove_stenography_notes(text), text)


class LineExclusionsTest(unittest.TestCase):
    def test_range_from_start_to_end_anchor_is_removed(self) -> None:
        lines = ["前文", "詔書寫", "兵農始テ分レ遂ニ", "明治五年", "徵兵吿諭", "明治五年"]
        result = clean_text.apply_line_exclusions(
            lines, [("詔書寫", "明治五年")]
        )
        self.assertEqual(result, ["前文", "徵兵吿諭", "明治五年"])

    def test_missing_anchor_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            clean_text.apply_line_exclusions(["本文"], [("詔書寫", "明治")])

    def test_conscription_rescript_is_removed_by_the_data_file(self) -> None:
        ranges = clean_text.read_line_exclusions()[CONSCRIPTION]
        lines = [
            "今般全國募兵ノ儀別紙",
            "詔書寫",
            "固ヨリ兵農ノ分ナシ中世以降兵權武門ニ歸シ兵農始テ分レ遂ニ封建ノ治ヲ成ス",
            "明治五年壬申十一月二十八日",
            "徵兵吿諭",
            "我" + SPACE + "朝上古ノ制",
        ]
        result = clean_text.apply_line_exclusions(lines, ranges)
        self.assertFalse(any("兵農始テ分レ遂ニ" in line for line in result))
        self.assertIn("我" + SPACE + "朝上古ノ制", result)

    def test_file_with_unexpected_columns_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "line_exclusions.tsv")
            path.write_text("title\tfrom\n法\t一\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                clean_text.read_line_exclusions(path)


class VocabularyLinesTest(unittest.TestCase):
    def test_lines_with_imperial_wording_are_left_out(self) -> None:
        page = "朕惟フニ\n汝等之ヲ守レ敕\n凡ソ人タル者ハ"
        self.assertEqual(clean_text.vocabulary_lines(page), ["凡ソ人タル者ハ"])

    def test_lines_with_half_width_brackets_are_left_out(self) -> None:
        page = "苟モ國アレハ(注)\n苟モ國アレハ"
        self.assertEqual(clean_text.vocabulary_lines(page), ["苟モ國アレハ"])

    def test_heading_numbers_and_the_space_after_are_removed(self) -> None:
        page = f"第一章{SPACE}總則\n第二條 法律ニ正條ナキ者ハ\n二章 罰則"
        self.assertEqual(
            clean_text.vocabulary_lines(page),
            ["總則", "法律ニ正條ナキ者ハ", "罰則"],
        )

    def test_list_marks_and_empty_lines_are_removed(self) -> None:
        page = "{{Header|year=1872}}\n*:人々自ラ其身ヲ立テ\n\n#" + SPACE
        self.assertEqual(clean_text.vocabulary_lines(page), ["人々自ラ其身ヲ立テ"])

    def test_ranges_are_removed_before_the_lines_are_filtered(self) -> None:
        page = "詔書寫\n兵農始テ分レ遂ニ\n明治五年\n苟モ國アレハ"
        result = clean_text.vocabulary_lines(page, [("詔書寫", "明治五年")])
        self.assertEqual(result, ["苟モ國アレハ"])


if __name__ == "__main__":
    unittest.main()
