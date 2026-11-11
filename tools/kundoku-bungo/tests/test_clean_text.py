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

    def test_nested_templates_close_the_header_at_the_right_place(
        self,
    ) -> None:
        cases = {
            "{{Header|a={{X}}}}Z": "Z",
            "{{Header|a={{X|{{Y}}}}|n=1}}Z": "Z",
            "{{Header|a={{A|{{B}}}}\n}}Z": "Z",
        }
        for page, expected in cases.items():
            with self.subTest(page=page):
                self.assertEqual(clean_text.body(page), expected)

    def test_header_after_another_template_is_found(self) -> None:
        page = "{{Otheruses}}\n{{header\n|year=1907\n|notes=注\n}}\n本文"
        header, rest = clean_text.split_header(page)
        self.assertIn("year=1907", header)
        self.assertEqual(rest, "{{Otheruses}}\n\n本文")

    ORIGINAL = "凡ソ人タル者ハ其身ヲ修メ智ヲ開キ才藝ヲ長スルニ在リ"

    def test_sections_written_by_editors_are_removed(self) -> None:
        page = (
            f"== 原文 ==\n{self.ORIGINAL}\n=== 第一章 ===\n乙\n"
            "== 新字体 ==\n新\n=== 第一章 ===\n新二\n"
            "== 現代語訳 ==\n訳\n"
            "== 関連項目 ==\n* [[刑法]]\n"
            "== 附則 ==\n丙"
        )
        text = clean_text.body(page)
        for kept in (self.ORIGINAL, "乙", "丙", "第一章"):
            with self.subTest(kept=kept):
                self.assertIn(kept, text)
        for removed in ("新", "訳", "刑法", "関連項目"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, text)

    def test_new_form_section_is_kept_when_it_is_the_only_text(self) -> None:
        page = f"== 新字体 ==\n{self.ORIGINAL}\n== 関連項目 ==\n* [[刑法]]"
        text = clean_text.body(page)
        self.assertIn(self.ORIGINAL, text)
        self.assertNotIn("刑法", text)

    def test_unclosed_header_takes_the_rest_of_the_page(self) -> None:
        self.assertEqual(clean_text.body("前\n{{Header|notes=注\n本文"), "前\n")


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

    def test_nested_text_templates_keep_their_text(self) -> None:
        text = "陸{{r|{{異体字|&#xFA45;}}|かい}}軍"
        self.assertEqual(
            clean_text.strip_markup(text), "陸" + COMPAT_SEA + "軍"
        )

    def test_ref_with_a_slash_in_its_name_keeps_the_text_after_it(
        self,
    ) -> None:
        text = '甲<ref name="a/b">注</ref>乙<ref name="c/d"/>丙'
        self.assertEqual(clean_text.strip_markup(text), "甲乙丙")

    def test_br_becomes_a_line_break(self) -> None:
        text = "スヘシ<br />第二條　本文<BR>終"
        self.assertEqual(
            clean_text.strip_markup(text), "スヘシ\n第二條　本文\n終"
        )

    def test_links_to_files_and_other_languages_are_removed(self) -> None:
        text = "[[File:x.jpg|thumb|説明]]甲[[en:Title]]乙[[category:法]]丙"
        self.assertEqual(clean_text.strip_markup(text), "甲乙丙")

    def test_controls_from_character_references_are_removed(self) -> None:
        text = "甲&#9;乙&#x202E;丙&#x200B;丁&#7;"
        self.assertEqual(clean_text.strip_markup(text), "甲 乙丙丁")

    def test_text_removed_inside_a_line_can_be_marked(self) -> None:
        removed = clean_text.REMOVED
        cases = {
            "本法ハ'''公布'''ノ日ヨリ": f"本法ハ{removed}ノ日ヨリ",
            "本文<sup>編者注</sup>終": f"本文{removed}終",
            "本文<ref>閉じない注": f"本文{removed}",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    clean_text.strip_markup(text, mark_removed=True), expected
                )

    def test_bold_number_inside_a_span_at_the_line_start_is_removed(
        self,
    ) -> None:
        text = f"<span id=\"a2\">'''第２条'''</span>{SPACE}法律ニ正條ナキ者ハ"
        self.assertEqual(
            clean_text.strip_markup(text, mark_removed=True),
            f"{SPACE}法律ニ正條ナキ者ハ",
        )

    def test_unclosed_bold_does_not_join_lines(self) -> None:
        text = "甲'''乙\n丙\n丁'''戊"
        self.assertEqual(clean_text.strip_markup(text), "甲'乙\n丙\n丁'戊")

    def test_ref_in_capitals_and_br_with_attributes(self) -> None:
        text = '甲<REF>注</REF>乙<br clear="all">丙'
        self.assertEqual(clean_text.strip_markup(text), "甲乙\n丙")

    def test_removed_templates_can_be_marked(self) -> None:
        text = "本法ハ{{傍点|公布}}ノ日ヨリ"
        self.assertEqual(
            clean_text.strip_markup(text, mark_removed=True),
            "本法ハ" + clean_text.REMOVED + "ノ日ヨリ",
        )


class StenographyNoteTest(unittest.TestCase):
    def test_note_of_a_stenographer_is_removed(self) -> None:
        text = "諸君ニ告ク（拍手）余ハ"
        self.assertEqual(clean_text.remove_stenography_notes(text), "諸君ニ告ク余ハ")

    def test_bracket_of_the_original_text_is_kept(self) -> None:
        text = "（別紙）詔書寫"
        self.assertEqual(clean_text.remove_stenography_notes(text), text)

    def test_notes_written_in_old_kanji_forms_are_removed(self) -> None:
        cases = {
            "終ニ（大喝采）": "終ニ",
            "余ハ（謹聽）": "余ハ",
            "ナリ（笑聲）": "ナリ",
            "述フ（拍手起ル）": "述フ",
            "諸君（「ヒヤヒヤ」）": "諸君（「ヒヤヒヤ」）",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    clean_text.remove_stenography_notes(text), expected
                )

    def test_bracket_with_other_words_is_kept(self) -> None:
        for text in ("（ヒヤリト感ス）", "（大）", "（大正元年）"):
            with self.subTest(text=text):
                self.assertEqual(
                    clean_text.remove_stenography_notes(text), text
                )


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

    def test_start_anchor_on_two_lines_is_an_error(self) -> None:
        lines = ["別紙詔書寫ノ通", "明治五年", "詔書寫", "明治五年"]
        with self.assertRaises(ValueError):
            clean_text.apply_line_exclusions(lines, [("詔書寫", "明治五年")])

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

    def test_number_followed_by_text_is_kept(self) -> None:
        page = "第三條ノ規定ニ依リ\n第十二條中「甲」ヲ改ム\n第二條第一項ノ者"
        self.assertEqual(
            clean_text.vocabulary_lines(page),
            ["第三條ノ規定ニ依リ", "第十二條中「甲」ヲ改ム", "第二條第一項ノ者"],
        )

    def test_branch_number_with_a_space_is_removed(self) -> None:
        page = f"第一條ノ二{SPACE}本文\n第廿條 本文"
        self.assertEqual(clean_text.vocabulary_lines(page), ["本文", "本文"])

    def test_other_ways_of_writing_numbers_are_removed(self) -> None:
        page = "第壹條 甲\n第1条 乙\n第一條ノ二ノ三 丙\n第二條&nbsp;丁"
        self.assertEqual(
            clean_text.vocabulary_lines(page), ["甲", "乙", "丙", "丁"]
        )

    def test_page_layout_lines_are_left_out(self) -> None:
        page = "! 見出シ\n----\n__NOTOC__\n乙ハ丙ナリ"
        self.assertEqual(clean_text.vocabulary_lines(page), ["乙ハ丙ナリ"])

    def test_lines_that_may_hold_editors_material_are_left_out(self) -> None:
        page = "\n".join(
            [
                "本法ハ{{傍点|公布}}ノ日ヨリ",
                "==第一章==",
                "{|",
                "|-",
                "| 表ノ中",
                "&lt;b&gt;甲",
                "乙ハ丙ナリ",
            ]
        )
        self.assertEqual(clean_text.vocabulary_lines(page), ["乙ハ丙ナリ"])

    def test_definition_list_mark_is_removed_before_the_number(self) -> None:
        self.assertEqual(clean_text.vocabulary_lines(";第一條 本文"), ["本文"])

    def test_list_marks_and_empty_lines_are_removed(self) -> None:
        page = "{{Header|year=1872}}\n*:人々自ラ其身ヲ立テ\n\n#" + SPACE
        self.assertEqual(clean_text.vocabulary_lines(page), ["人々自ラ其身ヲ立テ"])

    def test_ranges_are_removed_before_the_lines_are_filtered(self) -> None:
        page = "詔書寫\n兵農始テ分レ遂ニ\n明治五年\n苟モ國アレハ"
        result = clean_text.vocabulary_lines(page, [("詔書寫", "明治五年")])
        self.assertEqual(result, ["苟モ國アレハ"])


if __name__ == "__main__":
    unittest.main()
