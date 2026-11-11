"""Tests for convert_chars.py.

Run from the repository root with:
    python -m unittest discover \
        -s plugins/kundoku-bungo/skills/kundoku-bungo/scripts

Characters that look alike or are invisible (combining marks and CJK
compatibility ideographs) are written as escapes, so that an editor
that normalizes Unicode cannot silently change what a test checks.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import convert_chars

SCRIPT = Path(__file__).with_name("convert_chars.py")

COMBINING_VOICED = "゙"
SPACING_VOICED = "゛"
OLD_FORM_OF_SHA = "社"  # CJK compatibility ideograph of 社


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
            "之ヲ定ム　但シ此ノ限ニ在ラス",
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


class ToModernTest(unittest.TestCase):
    def test_only_kana_script_changes(self) -> None:
        self.assertEqual(
            convert_chars.to_modern("之ヲ使用セシメタル者"),
            "之を使用せしめたる者",
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

    def test_multiple_lines_are_kept(self) -> None:
        completed = self._run("to-bungo", given="さだむ。\nあり。\n")
        self.assertEqual(completed.stdout.decode("utf-8"), "サタム\nアリ\n")

    def test_to_modern(self) -> None:
        completed = self._run("to-modern", given="スヘシ")
        self.assertEqual(completed.stdout.decode("utf-8"), "すへし")

    def test_missing_direction_exits_with_usage_error(self) -> None:
        self.assertEqual(self._run().returncode, 2)

    def test_unknown_direction_exits_with_usage_error(self) -> None:
        self.assertEqual(self._run("to-latin").returncode, 2)


if __name__ == "__main__":
    unittest.main()
