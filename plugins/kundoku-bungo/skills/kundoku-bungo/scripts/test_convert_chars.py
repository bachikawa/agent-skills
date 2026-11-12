"""Tests for convert_chars.py.

Run from the repository root with:
    python -m unittest discover \
        -s plugins/kundoku-bungo/skills/kundoku-bungo/scripts

Characters that look alike or are invisible (combining marks and CJK
compatibility ideographs) are written as escapes, so that an editor
that normalizes Unicode cannot silently change what a test checks.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

import convert_chars

SCRIPT = Path(__file__).with_name("convert_chars.py")

COMBINING_VOICED = "\u3099"
SPACING_VOICED = "\u309b"
OLD_FORM_OF_SHA = "\ufa4c"  # CJK compatibility ideograph of 社


EVIDENCE_URL = re.compile(
    r"https://(ja\.wikisource\.org/w/index\.php\?oldid=\d+"
    r"|dl\.ndl\.go\.jp/pid/[\d/]+)"
)
HAN = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff]")


class KanjiTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = convert_chars.load_kanji_table()

    def test_table_is_not_empty(self) -> None:
        self.assertGreater(len(self.rows), 0)

    def test_every_row_is_well_formed(self) -> None:
        for row in self.rows:
            with self.subTest(modern=row["modern"], old=row["old"]):
                self.assertRegex(row["modern"], HAN.pattern + "$")
                self.assertRegex(row["old"], HAN.pattern + "$")
                self.assertNotEqual(row["modern"], row["old"])
                self.assertEqual(
                    row["old_codepoint"], f"U+{ord(row['old']):04X}"
                )
                self.assertIn(row["kind"], {"joyo", "compat", "design"})
                self.assertIn(row["one_to_many"], {"0", "1"})
                self.assertTrue(row["evidence"])
                self.assertRegex(
                    row["evidence_url"], EVIDENCE_URL.pattern + "$"
                )

    def test_compat_rows_normalize_to_their_modern_form(self) -> None:
        compat = [row for row in self.rows if row["kind"] == "compat"]
        self.assertGreater(len(compat), 0)
        for row in compat:
            with self.subTest(modern=row["modern"], old=row["old"]):
                self.assertEqual(
                    unicodedata.normalize("NFC", row["old"]), row["modern"]
                )

    def test_other_rows_are_unchanged_by_normalization(self) -> None:
        others = [row for row in self.rows if row["kind"] != "compat"]
        for row in others:
            with self.subTest(modern=row["modern"], old=row["old"]):
                self.assertEqual(
                    unicodedata.normalize("NFC", row["old"]), row["old"]
                )

    def test_shared_modern_forms_are_marked_one_to_many(self) -> None:
        modern = [row["modern"] for row in self.rows]
        shared = [row for row in self.rows if modern.count(row["modern"]) > 1]
        for row in shared:
            with self.subTest(modern=row["modern"], old=row["old"]):
                self.assertEqual(row["one_to_many"], "1")

    def test_one_to_many_rows_have_notes_and_are_not_converted(self) -> None:
        one_to_many = [row for row in self.rows if row["one_to_many"] == "1"]
        self.assertGreater(len(one_to_many), 0)
        for row in one_to_many:
            with self.subTest(modern=row["modern"], old=row["old"]):
                self.assertTrue(row["note"])
                self.assertEqual(
                    convert_chars.to_kyujitai(row["modern"]), row["modern"]
                )

    def test_old_forms_are_unique_and_not_modern_forms(self) -> None:
        old = [row["old"] for row in self.rows]
        modern = {row["modern"] for row in self.rows}
        self.assertEqual(len(old), len(set(old)))
        self.assertEqual(set(old) & modern, set())


class LoadKanjiTableErrorTest(unittest.TestCase):
    def test_missing_file_raises_table_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory, "missing.tsv")
            with self.assertRaises(convert_chars.KanjiTableError):
                convert_chars.load_kanji_table(missing)

    def test_unexpected_columns_raise_table_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory, "kanji.tsv")
            table.write_text("modern\told\n学\t學\n", encoding="utf-8")
            with self.assertRaises(convert_chars.KanjiTableError):
                convert_chars.load_kanji_table(table)


class ToKyujitaiTest(unittest.TestCase):
    def test_one_to_one_kanji_become_old_forms(self) -> None:
        self.assertEqual(convert_chars.to_kyujitai("学生"), "學生")

    def test_kanji_whose_old_form_depends_on_the_word_are_kept(self) -> None:
        # 勅 stays: official documents of the period wrote 勅令, not 敕令.
        cases = {
            "弁護": "弁護",
            "台": "台",
            "民衆": "民衆",
            "勅令": "勅令",
            "政党": "政党",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(convert_chars.to_kyujitai(given), expected)

    def test_compatibility_ideographs_are_used_by_default(self) -> None:
        self.assertEqual(
            convert_chars.to_kyujitai("社会"), OLD_FORM_OF_SHA + "會"
        )

    def test_compatibility_ideographs_can_be_skipped(self) -> None:
        self.assertEqual(
            convert_chars.to_kyujitai("社会", compat=False), "社會"
        )


class ToShinjitaiTest(unittest.TestCase):
    def test_old_forms_become_modern(self) -> None:
        cases = {
            "辯護": "弁護",
            "辨別": "弁別",
            OLD_FORM_OF_SHA + "會": "社会",
            "有眾": "有衆",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(convert_chars.to_shinjitai(given), expected)


class ToKatakanaTest(unittest.TestCase):
    def test_hiragana_becomes_katakana(self) -> None:
        self.assertEqual(convert_chars.to_katakana("がくせい"), "ガクセイ")

    def test_kanji_and_katakana_are_kept(self) -> None:
        self.assertEqual(convert_chars.to_katakana("學生ハ"), "學生ハ")

    def test_iteration_marks_follow_the_script(self) -> None:
        self.assertEqual(convert_chars.to_katakana("こゝろ"), "コヽロ")


class ToHiraganaTest(unittest.TestCase):
    def test_katakana_becomes_hiragana(self) -> None:
        self.assertEqual(convert_chars.to_hiragana("スヘシ"), "すへし")

    def test_iteration_marks_follow_the_script(self) -> None:
        self.assertEqual(convert_chars.to_hiragana("コヽロ"), "こゝろ")

    def test_katakana_without_hiragana_counterpart_is_kept(self) -> None:
        self.assertEqual(convert_chars.to_hiragana("ヷー"), "ヷー")

    def test_counter_ke_is_kept(self) -> None:
        cases = {"三ヶ月": "三ヶ月", "一ヵ所": "一ヵ所"}
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(convert_chars.to_hiragana(given), expected)


class StripVoicingMarksTest(unittest.TestCase):
    def test_voiced_and_semi_voiced_kana_lose_marks(self) -> None:
        cases = {
            "ガクセイ": "カクセイ",
            "パン": "ハン",
            "ヴ": "ウ",
            "ぶどう": "ふとう",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(
                    convert_chars.strip_voicing_marks(given), expected
                )

    def test_combining_and_spacing_marks_are_removed(self) -> None:
        given = "カ" + COMBINING_VOICED + "キ" + SPACING_VOICED
        self.assertEqual(convert_chars.strip_voicing_marks(given), "カキ")

    def test_compatibility_ideographs_are_not_normalized(self) -> None:
        self.assertEqual(ord(OLD_FORM_OF_SHA), 0xFA4C)
        self.assertEqual(
            convert_chars.strip_voicing_marks(OLD_FORM_OF_SHA + "ガ"),
            OLD_FORM_OF_SHA + "カ",
        )


class EnlargeSmallKanaTest(unittest.TestCase):
    def test_small_kana_become_full_size(self) -> None:
        cases = {
            "アッタ": "アツタ",
            "キャク": "キヤク",
            "ちょっと": "ちよつと",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(
                    convert_chars.enlarge_small_kana(given), expected
                )


class RemovePunctuationTest(unittest.TestCase):
    def test_comma_is_removed_and_inner_period_becomes_space(self) -> None:
        self.assertEqual(
            convert_chars.remove_punctuation("之ヲ定ム。但シ、此ノ限ニ在ラス。"),
            "之ヲ定ム\u3000但シ此ノ限ニ在ラス",
        )

    def test_each_line_is_handled_separately(self) -> None:
        self.assertEqual(
            convert_chars.remove_punctuation("第一条。\n第二条。"),
            "第一条\n第二条",
        )

    def test_period_before_closing_bracket_is_removed(self) -> None:
        cases = {
            "「之ヲ定ム。」": "「之ヲ定ム」",
            "「之ヲ定ム。」ト云フ。": "「之ヲ定ム」ト云フ",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(
                    convert_chars.remove_punctuation(given), expected
                )

    def test_other_closing_brackets_are_handled(self) -> None:
        cases = {
            "〈之ヲ定ム。〉": "〈之ヲ定ム〉",
            "《之ヲ定ム。》": "《之ヲ定ム》",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(
                    convert_chars.remove_punctuation(given), expected
                )

    def test_consecutive_periods_count_as_one(self) -> None:
        cases = {
            "之ヲ定ム。。": "之ヲ定ム",
            "之ヲ定ム。。但シ": "之ヲ定ム\u3000但シ",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(
                    convert_chars.remove_punctuation(given), expected
                )

    def test_period_before_space_does_not_add_another_space(self) -> None:
        self.assertEqual(
            convert_chars.remove_punctuation("之ヲ定ム。\u3000但シ"),
            "之ヲ定ム\u3000但シ",
        )

    def test_period_before_trailing_space_or_cr_is_removed(self) -> None:
        cases = {
            "之ヲ定ム。 ": "之ヲ定ム ",
            "第一条。\r\n第二条。": "第一条\r\n第二条",
        }
        for given, expected in cases.items():
            with self.subTest(given=repr(given)):
                self.assertEqual(
                    convert_chars.remove_punctuation(given), expected
                )


class ToBungoTest(unittest.TestCase):
    def test_defaults_apply_every_conversion(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("がくせいは、あった。"), "カクセイハアツタ"
        )

    def test_historical_spelling_input_is_converted(self) -> None:
        self.assertEqual(convert_chars.to_bungo("がつかう"), "カツカウ")

    def test_voiced_iteration_mark_loses_its_mark(self) -> None:
        self.assertEqual(convert_chars.to_bungo("みすゞ"), "ミスヽ")

    def test_keep_voicing(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("がくせい", keep_voicing=True), "ガクセイ"
        )

    def test_keep_small_kana(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("あった", keep_small_kana=True), "アッタ"
        )

    def test_keep_punctuation(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("定む。", keep_punctuation=True), "定ム。"
        )

    def test_hiragana(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("さだむ", katakana=False), "さたむ"
        )

    def test_empty_text(self) -> None:
        self.assertEqual(convert_chars.to_bungo(""), "")

    def test_kanji_become_old_forms_by_default(self) -> None:
        self.assertEqual(convert_chars.to_bungo("学生は"), "學生ハ")

    def test_shinjitai(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("学生は", kyujitai=False), "学生ハ"
        )

    def test_compatibility_ideographs_survive_every_step(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("社会が"), OLD_FORM_OF_SHA + "會カ"
        )

    def test_no_compat(self) -> None:
        self.assertEqual(
            convert_chars.to_bungo("社会は", compat=False), "社會ハ"
        )


class ToModernTest(unittest.TestCase):
    def test_kana_script_changes(self) -> None:
        self.assertEqual(
            convert_chars.to_modern("之ヲ使用セシメタル者"),
            "之を使用せしめたる者",
        )

    def test_old_kanji_forms_become_modern(self) -> None:
        self.assertEqual(convert_chars.to_modern("學生ハ"), "学生は")

    def test_keep_kyujitai(self) -> None:
        self.assertEqual(
            convert_chars.to_modern("學生ハ", shinjitai=False), "學生は"
        )


class ExplainTest(unittest.TestCase):
    def _by_category(
        self, text: str, category: str, **options: bool
    ) -> list[dict[str, str]]:
        return [
            row
            for row in convert_chars.explain(text, **options)
            if row["category"] == category
        ]

    def test_every_row_has_the_columns(self) -> None:
        for row in convert_chars.explain("がっこうに学生、弁あり。"):
            with self.subTest(row=row):
                self.assertEqual(tuple(row), convert_chars.EXPLAIN_COLUMNS)

    def test_converted_kanji_has_the_evidence_of_the_table(self) -> None:
        table = {
            row["modern"]: row for row in convert_chars.load_kanji_table()
        }
        rows = self._by_category("学生の学", "kanji")
        self.assertEqual(
            rows,
            [
                {
                    "category": "kanji",
                    "from": "学",
                    "to": "學",
                    "count": "2",
                    "note": "",
                    "evidence": table["学"]["evidence"],
                    "evidence_url": table["学"]["evidence_url"],
                }
            ],
        )

    def test_kanji_rows_follow_the_order_of_the_text(self) -> None:
        rows = self._by_category("軽き処", "kanji")
        self.assertEqual([row["from"] for row in rows], ["軽", "処"])

    def test_kept_and_converted_kanji_follow_the_text_together(self) -> None:
        rows = convert_chars.explain("弁と学")
        self.assertEqual(
            [(row["category"], row["from"]) for row in rows[:3]],
            [("kept", "弁"), ("kept", "弁"), ("kanji", "学")],
        )

    def test_kanji_outside_the_table_has_no_row(self) -> None:
        self.assertEqual(self._by_category("山川", "kanji"), [])

    def test_compatibility_ideograph_is_noted(self) -> None:
        (row,) = self._by_category("社", "kanji")
        self.assertEqual(row["to"], OLD_FORM_OF_SHA)
        self.assertIn("U+FA4C", row["note"])

    def test_no_compat_drops_compatibility_ideographs_only(self) -> None:
        rows = self._by_category("社会", "kanji", compat=False)
        self.assertEqual([row["from"] for row in rows], ["会"])

    def test_one_to_many_kanji_is_kept_with_its_note(self) -> None:
        (row,) = self._by_category("舞台", "kept")
        self.assertEqual(
            (row["from"], row["to"], row["count"]), ("台", "臺", "1")
        )
        self.assertTrue(row["note"])
        self.assertTrue(row["evidence_url"])

    def test_kanji_with_two_old_forms_has_a_row_for_each(self) -> None:
        rows = self._by_category("弁護と弁別", "kept")
        self.assertEqual([row["to"] for row in rows], ["辨", "辯"])
        # Each row counts the modern kanji, not the uses of that old form.
        self.assertEqual([row["count"] for row in rows], ["2", "2"])

    def test_shinjitai_has_no_kanji_or_kept_rows(self) -> None:
        rows = convert_chars.explain("学生の弁", kyujitai=False)
        self.assertEqual(
            {row["category"] for row in rows}, {"notation"}
        )

    def test_notation_rows_count_what_changes(self) -> None:
        rows = self._by_category("がっこうにあり、また。", "notation")
        self.assertEqual(
            [(row["from"], row["to"], row["count"]) for row in rows],
            [
                ("ひらがな", "カタカナ", "9"),
                ("濁点・半濁点", "なし", "1"),
                ("小書きの仮名", "大きい仮名", "1"),
                ("、", "なし", "1"),
                ("。", "なし", "1"),
            ],
        )
        for row in rows:
            self.assertEqual(row["evidence"], "SKILL.md 選べる表記")
            self.assertEqual(row["evidence_url"], "")

    def test_periods_are_counted_as_to_bungo_treats_them(self) -> None:
        text = "あり。。\nなし。いや。」"
        rows = self._by_category(text, "notation", katakana=False)
        self.assertEqual(
            [(row["to"], row["count"]) for row in rows if row["from"] == "。"],
            [("全角の空白", "1"), ("なし", "3")],
        )
        converted = convert_chars.to_bungo(text, katakana=False)
        self.assertEqual(converted.count("\u3000"), 1)
        self.assertNotIn("。", converted)

    def test_combining_and_spacing_voicing_marks_are_counted(self) -> None:
        text = "か" + COMBINING_VOICED + "か" + SPACING_VOICED + "ぱ"
        (row,) = [
            row
            for row in self._by_category(text, "notation", katakana=False)
            if row["from"] == "濁点・半濁点"
        ]
        self.assertEqual(row["count"], "3")

    def test_each_notation_option_drops_its_rows(self) -> None:
        text = "がっこうにあり、また。"
        # Without options there are 5 rows: kana, voicing, small kana,
        # comma and one period row (the period ends the line).
        cases = {
            "katakana": (False, "ひらがな", 4),
            "keep_voicing": (True, "濁点・半濁点", 4),
            "keep_small_kana": (True, "小書きの仮名", 4),
            "keep_punctuation": (True, "、", 3),
        }
        for option, (value, source, expected) in cases.items():
            with self.subTest(option=option):
                rows = self._by_category(text, "notation", **{option: value})
                self.assertNotIn(source, [row["from"] for row in rows])
                self.assertEqual(len(rows), expected)

    def test_changes_that_do_not_occur_have_no_row(self) -> None:
        self.assertEqual(convert_chars.explain("山川"), [])

    def test_rows_match_what_to_bungo_changes(self) -> None:
        text = "此の規則に違反したる者は、三年以下の懲役に処す。"
        converted = convert_chars.to_bungo(text)
        for row in self._by_category(text, "kanji"):
            with self.subTest(kanji=row["from"]):
                self.assertNotIn(row["from"], converted)
                self.assertEqual(converted.count(row["to"]), int(row["count"]))

    def test_format_has_a_header_and_one_line_per_row(self) -> None:
        rows = convert_chars.explain("学生、")
        lines = convert_chars.format_explanation(rows).split("\n")
        self.assertEqual(lines[0], "\t".join(convert_chars.EXPLAIN_COLUMNS))
        self.assertEqual(len(lines), len(rows) + 2)
        self.assertEqual(lines[-1], "")
        self.assertEqual(lines[1].split("\t")[:4], ["kanji", "学", "學", "1"])

    def test_format_keeps_quotation_marks(self) -> None:
        row = dict.fromkeys(convert_chars.EXPLAIN_COLUMNS, "")
        row["note"] = 'a"b'
        lines = convert_chars.format_explanation([row]).split("\n")
        self.assertEqual(lines[1].split("\t")[4], 'a"b')

    def test_format_of_no_rows_is_the_header_only(self) -> None:
        self.assertEqual(
            convert_chars.format_explanation([]),
            "\t".join(convert_chars.EXPLAIN_COLUMNS) + "\n",
        )


class CommandLineTest(unittest.TestCase):
    def _run(self, *args: str, given: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=given.encode("utf-8"),
            capture_output=True,
        )

    def test_to_bungo_reads_stdin_and_writes_stdout(self) -> None:
        completed = self._run("to-bungo", given="がくせい")
        self.assertEqual(completed.stdout.decode("utf-8"), "カクセイ")

    def test_to_bungo_keep_options(self) -> None:
        completed = self._run(
            "to-bungo",
            "--keep-voicing",
            "--keep-small-kana",
            given="がっこう。",
        )
        self.assertEqual(completed.stdout.decode("utf-8"), "ガッコウ")

    def test_to_bungo_hiragana_and_keep_punctuation(self) -> None:
        completed = self._run(
            "to-bungo", "--hiragana", "--keep-punctuation", given="がくせい。"
        )
        self.assertEqual(completed.stdout.decode("utf-8"), "かくせい。")

    def test_input_file_is_read_as_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "input.txt")
            source.write_text("$(echo injected)がくせい", encoding="utf-8")
            completed = self._run("to-bungo", "--input", str(source))
        self.assertEqual(
            completed.stdout.decode("utf-8"), "$(echo injected)カクセイ"
        )

    def _temp_input(self, text: str, *, encoding: str = "utf-8") -> Path:
        handle, name = tempfile.mkstemp(
            prefix=convert_chars.REMOVABLE_PREFIX, suffix=".txt"
        )
        os.close(handle)
        path = Path(name)
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(text, encoding=encoding)
        return path

    def test_remove_input_deletes_the_temporary_file(self) -> None:
        source = self._temp_input("がくせい")
        completed = self._run(
            "to-bungo", "--input", str(source), "--remove-input"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.decode("utf-8"), "カクセイ")
        self.assertFalse(source.exists())

    def test_remove_input_works_for_to_modern(self) -> None:
        source = self._temp_input("學生ハ")
        completed = self._run(
            "to-modern", "--input", str(source), "--remove-input"
        )
        self.assertEqual(completed.stdout.decode("utf-8"), "学生は")
        self.assertFalse(source.exists())

    def test_explain_writes_a_table_instead_of_the_text(self) -> None:
        completed = self._run("explain", given="学生、")
        self.assertEqual(completed.returncode, 0)
        lines = completed.stdout.decode("utf-8").split("\n")
        self.assertEqual(lines[0], "\t".join(convert_chars.EXPLAIN_COLUMNS))
        self.assertEqual(
            [line.split("\t")[:4] for line in lines[1:-1]],
            [["kanji", "学", "學", "1"], ["notation", "、", "なし", "1"]],
        )

    def test_explain_takes_the_options_of_to_bungo(self) -> None:
        completed = self._run(
            "explain",
            "--shinjitai",
            "--hiragana",
            "--keep-voicing",
            "--keep-small-kana",
            "--keep-punctuation",
            given="がっこうの学生、",
        )
        self.assertEqual(
            completed.stdout.decode("utf-8"),
            "\t".join(convert_chars.EXPLAIN_COLUMNS) + "\n",
        )

    def test_explain_no_compat(self) -> None:
        completed = self._run("explain", "--no-compat", given="社会")
        lines = completed.stdout.decode("utf-8").split("\n")
        self.assertEqual(
            [line.split("\t")[1] for line in lines[1:-1]], ["会"]
        )

    def test_explain_keeps_the_input_for_the_conversion(self) -> None:
        source = self._temp_input("がくせい")
        self._run("explain", "--input", str(source))
        self.assertTrue(source.exists())
        completed = self._run(
            "to-bungo", "--input", str(source), "--remove-input"
        )
        self.assertEqual(completed.stdout.decode("utf-8"), "カクセイ")
        self.assertFalse(source.exists())

    def test_remove_input_works_for_explain(self) -> None:
        source = self._temp_input("がくせい")
        completed = self._run(
            "explain", "--input", str(source), "--remove-input"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertFalse(source.exists())

    def test_input_file_is_kept_without_remove_input(self) -> None:
        source = self._temp_input("がくせい")
        self._run("to-bungo", "--input", str(source))
        self.assertTrue(source.exists())

    def test_file_without_the_prefix_is_kept_with_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "input.txt")
            source.write_text("がくせい", encoding="utf-8")
            completed = self._run(
                "to-bungo", "--input", str(source), "--remove-input"
            )
            self.assertTrue(source.exists())
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.decode("utf-8"), "カクセイ")
        self.assertIn(b"not removed", completed.stderr)

    def test_unreadable_file_is_kept(self) -> None:
        source = self._temp_input("がくせい", encoding="shift_jis")
        completed = self._run(
            "to-bungo", "--input", str(source), "--remove-input"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn(b"cannot read", completed.stderr)
        self.assertTrue(source.exists())

    def test_remove_input_without_input_is_a_usage_error(self) -> None:
        completed = self._run("to-bungo", "--remove-input", given="がくせい")
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"--remove-input requires --input", completed.stderr)

    def test_input_file_with_bom_does_not_leak_the_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "input.txt")
            source.write_text("がくせい", encoding="utf-8-sig")
            completed = self._run("to-bungo", "--input", str(source))
        self.assertEqual(completed.stdout.decode("utf-8"), "カクセイ")

    def test_missing_input_file_fails_with_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory, "missing.txt"))
            completed = self._run("to-bungo", "--input", missing)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, b"")
        self.assertIn(b"cannot read", completed.stderr)

    def test_empty_input_path_fails_instead_of_reading_stdin(self) -> None:
        completed = self._run("to-bungo", "--input", "", given="がくせい")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, b"")

    def test_input_file_not_in_utf8_fails_with_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "input.txt")
            source.write_text("がくせい", encoding="shift_jis")
            completed = self._run("to-bungo", "--input", str(source))
        self.assertEqual(completed.returncode, 1)
        self.assertIn(b"cannot read", completed.stderr)

    def test_multiple_lines_are_kept(self) -> None:
        completed = self._run("to-bungo", given="さだむ。\nあり。\n")
        self.assertEqual(completed.stdout.decode("utf-8"), "サタム\nアリ\n")

    def test_to_modern(self) -> None:
        completed = self._run("to-modern", given="學生ハ")
        self.assertEqual(completed.stdout.decode("utf-8"), "学生は")

    def test_to_modern_keep_kyujitai(self) -> None:
        completed = self._run("to-modern", "--keep-kyujitai", given="學生ハ")
        self.assertEqual(completed.stdout.decode("utf-8"), "學生は")

    def test_to_bungo_shinjitai(self) -> None:
        completed = self._run("to-bungo", "--shinjitai", given="学生")
        self.assertEqual(completed.stdout.decode("utf-8"), "学生")

    def test_to_bungo_writes_compatibility_ideographs(self) -> None:
        completed = self._run("to-bungo", given="社会")
        self.assertEqual(
            completed.stdout.decode("utf-8"), OLD_FORM_OF_SHA + "會"
        )

    def test_to_bungo_no_compat(self) -> None:
        completed = self._run("to-bungo", "--no-compat", given="社会")
        self.assertEqual(completed.stdout.decode("utf-8"), "社會")

    def test_missing_direction_exits_with_usage_error(self) -> None:
        self.assertEqual(self._run().returncode, 2)

    def test_unknown_direction_exits_with_usage_error(self) -> None:
        self.assertEqual(self._run("to-latin").returncode, 2)


class RemovalProblemTest(unittest.TestCase):
    def test_prefixed_file_in_the_temporary_directory_may_be_removed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, convert_chars.REMOVABLE_PREFIX + "a.txt")
            path.write_text("x", encoding="utf-8")
            self.assertIsNone(
                convert_chars.removal_problem(str(path), directory)
            )

    def test_file_outside_the_temporary_directory_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with tempfile.TemporaryDirectory() as other:
                path = Path(directory, convert_chars.REMOVABLE_PREFIX + "a")
                path.write_text("x", encoding="utf-8")
                self.assertIsNotNone(
                    convert_chars.removal_problem(str(path), other)
                )

    def test_directory_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, convert_chars.REMOVABLE_PREFIX + "dir")
            path.mkdir()
            self.assertIsNotNone(
                convert_chars.removal_problem(str(path), directory)
            )

    def test_symbolic_link_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "target.txt")
            target.write_text("x", encoding="utf-8")
            link = Path(directory, convert_chars.REMOVABLE_PREFIX + "link")
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("creating symbolic links is not permitted")
            self.assertIsNotNone(
                convert_chars.removal_problem(str(link), directory)
            )


class MainInProcessTest(unittest.TestCase):
    """Runs main() in this process to replace parts that cannot fail
    on demand in a subprocess."""

    def _run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.object(sys, "stdout", stdout), mock.patch.object(
            sys, "stderr", stderr
        ):
            code = convert_chars.main(argv)
            stdout.flush()
            stderr.flush()
        return (
            code,
            stdout.buffer.getvalue().decode("utf-8"),
            stderr.buffer.getvalue().decode("utf-8"),
        )

    def _temp_input(self, text: str) -> Path:
        handle, name = tempfile.mkstemp(
            prefix=convert_chars.REMOVABLE_PREFIX, suffix=".txt"
        )
        os.close(handle)
        path = Path(name)
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_failed_removal_warns_and_still_converts(self) -> None:
        source = self._temp_input("がくせい")
        with mock.patch.object(
            convert_chars.os, "remove", side_effect=PermissionError("denied")
        ):
            code, out, err = self._run_main(
                ["to-bungo", "--input", str(source), "--remove-input"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(out, "カクセイ")
        self.assertIn("cannot remove", err)
        self.assertTrue(source.exists())

    def test_explain_with_a_broken_table_fails_and_keeps_input(
        self,
    ) -> None:
        source = self._temp_input("がくせい")
        with mock.patch.object(
            convert_chars,
            "load_kanji_table",
            side_effect=convert_chars.KanjiTableError("broken table"),
        ):
            code, out, err = self._run_main(
                ["explain", "--input", str(source), "--remove-input"]
            )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("broken table", err)
        self.assertTrue(source.exists())

    def test_failed_conversion_keeps_the_input(self) -> None:
        source = self._temp_input("がくせい")
        with mock.patch.object(
            convert_chars,
            "to_bungo",
            side_effect=convert_chars.KanjiTableError("broken table"),
        ):
            code, out, err = self._run_main(
                ["to-bungo", "--input", str(source), "--remove-input"]
            )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("broken table", err)
        self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
