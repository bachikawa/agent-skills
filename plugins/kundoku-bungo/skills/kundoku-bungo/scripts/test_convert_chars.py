"""Tests for convert_chars.py.

Run from this directory or with:
    python -m unittest discover -s plugins/kundoku-bungo/skills/kundoku-bungo/scripts
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import convert_chars

SCRIPT = Path(__file__).with_name("convert_chars.py")


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

    def test_long_vowel_mark_is_kept(self) -> None:
        self.assertEqual(convert_chars.to_hiragana("ラヂオー"), "らぢおー")


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
                self.assertEqual(convert_chars.strip_voicing_marks(given), expected)

    def test_combining_and_spacing_marks_are_removed(self) -> None:
        self.assertEqual(convert_chars.strip_voicing_marks("ガキ゛"), "カキ")

    def test_compatibility_ideographs_are_not_normalized(self) -> None:
        old_form_of_sha = "社"
        self.assertEqual(
            convert_chars.strip_voicing_marks(old_form_of_sha + "ガ"), old_form_of_sha + "カ"
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
                self.assertEqual(convert_chars.enlarge_small_kana(given), expected)


class RemovePunctuationTest(unittest.TestCase):
    def test_comma_is_removed_and_inner_period_becomes_space(self) -> None:
        self.assertEqual(
            convert_chars.remove_punctuation("之ヲ定ム。但シ、此ノ限ニ在ラス。"),
            "之ヲ定ム　但シ此ノ限ニ在ラス",
        )

    def test_each_line_is_handled_separately(self) -> None:
        self.assertEqual(convert_chars.remove_punctuation("第一条。\n第二条。"), "第一条\n第二条")


class ToBungoTest(unittest.TestCase):
    def test_defaults_apply_every_conversion(self) -> None:
        self.assertEqual(convert_chars.to_bungo("がくせいは、あった。"), "カクセイハアツタ")

    def test_historical_spelling_input_is_converted(self) -> None:
        self.assertEqual(convert_chars.to_bungo("がつかう"), "カツカウ")

    def test_keep_voicing(self) -> None:
        self.assertEqual(convert_chars.to_bungo("がくせい", keep_voicing=True), "ガクセイ")

    def test_keep_small_kana(self) -> None:
        self.assertEqual(convert_chars.to_bungo("あった", keep_small_kana=True), "アッタ")

    def test_keep_punctuation(self) -> None:
        self.assertEqual(convert_chars.to_bungo("定む。", keep_punctuation=True), "定ム。")

    def test_hiragana(self) -> None:
        self.assertEqual(convert_chars.to_bungo("さだむ", katakana=False), "さたむ")


class ToModernTest(unittest.TestCase):
    def test_only_kana_script_changes(self) -> None:
        self.assertEqual(convert_chars.to_modern("之ヲ使用セシメタル者"), "之を使用せしめたる者")


class CommandLineTest(unittest.TestCase):
    def _run(self, *args: str, given: str) -> str:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=given.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        return completed.stdout.decode("utf-8")

    def test_to_bungo_reads_stdin_and_writes_stdout(self) -> None:
        self.assertEqual(self._run("to-bungo", given="がくせい"), "カクセイ")

    def test_to_bungo_options(self) -> None:
        self.assertEqual(
            self._run("to-bungo", "--keep-voicing", "--keep-small-kana", given="がっこう。"),
            "ガッコウ",
        )

    def test_to_modern(self) -> None:
        self.assertEqual(self._run("to-modern", given="スヘシ"), "すへし")


if __name__ == "__main__":
    unittest.main()
