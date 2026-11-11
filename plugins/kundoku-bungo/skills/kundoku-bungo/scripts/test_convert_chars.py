"""Tests for convert_chars.py.

Run from the repository root with:
    python -m unittest discover \
        -s plugins/kundoku-bungo/skills/kundoku-bungo/scripts

Characters that look alike or are invisible (combining marks and CJK
compatibility ideographs) are written as escapes, so that an editor
that normalizes Unicode cannot silently change what a test checks.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

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
        cases = {"弁護": "弁護", "台": "台", "民衆": "民衆"}
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


if __name__ == "__main__":
    unittest.main()
