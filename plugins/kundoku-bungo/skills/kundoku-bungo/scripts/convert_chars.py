"""Mechanical character conversion for pre-war official Japanese text.

This script handles only conversions that follow a fixed table: kanji
forms (see references/kanji.tsv), kana script (hiragana and katakana),
voicing marks, small kana and punctuation. Rewriting grammar, choosing
words, historical kana spelling and kanji whose old form depends on the
word are left to the agent, because they depend on context.

Half-width katakana and punctuation other than the ideographic comma
and period (such as the full-width comma and full stop) are out of
scope and left as they are.

Usage:
    python convert_chars.py to-bungo [options] --input FILE
    python convert_chars.py to-modern [options] --input FILE

Without --input, the text is read from standard input.
"""

from __future__ import annotations

import argparse
import csv
import functools
import re
import sys
import unicodedata
from pathlib import Path

KANJI_TABLE = (
    Path(__file__).resolve().parent.parent / "references" / "kanji.tsv"
)
_COMPAT_KIND = "compat"

_HIRAGANA_START = 0x3041
_HIRAGANA_END = 0x3096
_HIRAGANA_BLOCK_END = 0x309F
_KATAKANA_BLOCK_START = 0x30A0
_KATAKANA_BLOCK_END = 0x30FF
_KANA_OFFSET = 0x60

# Small ヵ and ヶ are used as counters and in place names (三ヶ月,
# 霞ヶ関), not read as kana, so they stay katakana. The full-size ケ
# of pre-war text (三ケ月) cannot be told apart from the kana ケ by a
# table, so the agent reads it.
_KATAKANA_KEPT = {"ヵ", "ヶ"}

# Iteration marks are converted together with the kana they repeat.
_HIRAGANA_ITERATION = {"ゝ": "ヽ", "ゞ": "ヾ"}
_KATAKANA_ITERATION = {v: k for k, v in _HIRAGANA_ITERATION.items()}

_SMALL_TO_LARGE = str.maketrans(
    "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ",
    "あいうえおつやゆよわかけアイウエオツヤユヨワカケ",
)

_COMBINING_VOICED = "\u3099"
_COMBINING_SEMI_VOICED = "\u309a"
_SPACING_VOICED = "\u309b"
_SPACING_SEMI_VOICED = "\u309c"
_VOICING_MARKS = {
    _COMBINING_VOICED,
    _COMBINING_SEMI_VOICED,
    _SPACING_VOICED,
    _SPACING_SEMI_VOICED,
}

_COMMA = "、"
_PERIOD = "。"
_IDEOGRAPHIC_SPACE = "\u3000"
_CLOSING_BRACKETS = "」』）〕】》〉］｝)]}"
# Periods need no separator when a closing bracket, a space or the end
# of the line follows them.
_PERIODS_BEFORE_BREAK = re.compile(
    _PERIOD + "+(?=[" + re.escape(_CLOSING_BRACKETS) + r"]|\s|$)"
)
_PERIODS = re.compile(_PERIOD + "+")


def _is_kana(char: str) -> bool:
    """Returns whether the character is in the hiragana or katakana block."""
    code = ord(char)
    return (
        _HIRAGANA_START <= code <= _HIRAGANA_BLOCK_END
        or _KATAKANA_BLOCK_START <= code <= _KATAKANA_BLOCK_END
    )


_TABLE_COLUMNS = (
    "modern",
    "old",
    "old_codepoint",
    "kind",
    "one_to_many",
    "note",
    "evidence",
    "evidence_url",
)


class KanjiTableError(Exception):
    """Raised when the kanji table cannot be read or is malformed."""


def load_kanji_table(path: Path = KANJI_TABLE) -> list[dict[str, str]]:
    """Reads the table of modern and old kanji forms.

    Args:
        path: The TSV file with the columns modern, old, old_codepoint,
            kind, one_to_many, note, evidence and evidence_url.

    Returns:
        One dict per row, keyed by the column names. The list is new on
        every call, so callers may change it.

    Raises:
        KanjiTableError: The file cannot be read or lacks a column.
    """
    try:
        with open(path, encoding="utf-8", newline="") as source:
            reader = csv.DictReader(
                source, delimiter="\t", quoting=csv.QUOTE_NONE
            )
            rows = list(reader)
            columns = tuple(reader.fieldnames or ())
    except (OSError, UnicodeDecodeError) as error:
        raise KanjiTableError(f"cannot read {path}: {error}") from error
    if columns != _TABLE_COLUMNS:
        raise KanjiTableError(f"unexpected columns in {path}: {columns}")
    return rows


@functools.cache
def _kyujitai_table(compat: bool) -> dict[int, str]:
    return str.maketrans(
        {
            row["modern"]: row["old"]
            for row in load_kanji_table()
            if row["one_to_many"] == "0"
            and (compat or row["kind"] != _COMPAT_KIND)
        }
    )


@functools.cache
def _shinjitai_table() -> dict[int, str]:
    return str.maketrans(
        {row["old"]: row["modern"] for row in load_kanji_table()}
    )


def to_kyujitai(text: str, *, compat: bool = True) -> str:
    """Converts modern kanji to their old forms where the table allows.

    Only rows whose modern form maps to a single old form are used.
    Kanji whose old form depends on the word (such as 弁 or 台) are left
    as they are for the agent to decide.

    Args:
        text: The text to convert.
        compat: Whether to use old forms that exist only as CJK
            compatibility ideographs (such as the old form of 社). Some
            software normalizes these back to the modern form.

    Returns:
        The text with convertible kanji in their old forms.

    Raises:
        KanjiTableError: The kanji table cannot be read.
    """
    return text.translate(_kyujitai_table(compat))


def to_shinjitai(text: str) -> str:
    """Converts old kanji forms in the table to their modern forms.

    Every old form has a single modern form, so this direction uses
    every row of the table.

    Args:
        text: The text to convert.

    Returns:
        The text with old kanji forms replaced by modern ones.

    Raises:
        KanjiTableError: The kanji table cannot be read.
    """
    return text.translate(_shinjitai_table())


def to_katakana(text: str) -> str:
    """Converts hiragana to katakana.

    Characters other than hiragana and the hiragana iteration marks are
    left as they are.

    Args:
        text: The text to convert.

    Returns:
        The text with every hiragana replaced by the matching katakana.
    """
    result = []
    for char in text:
        code = ord(char)
        if _HIRAGANA_START <= code <= _HIRAGANA_END:
            result.append(chr(code + _KANA_OFFSET))
        else:
            result.append(_HIRAGANA_ITERATION.get(char, char))
    return "".join(result)


def to_hiragana(text: str) -> str:
    """Converts katakana to hiragana.

    Katakana that have no hiragana counterpart (such as ヷ or the long
    vowel mark ー) are left as they are, and so are ヵ and ヶ, which are
    used as counters and in place names.

    Args:
        text: The text to convert.

    Returns:
        The text with every convertible katakana replaced by hiragana.
    """
    result = []
    for char in text:
        code = ord(char) - _KANA_OFFSET
        if char in _KATAKANA_KEPT:
            result.append(char)
        elif _HIRAGANA_START <= code <= _HIRAGANA_END:
            result.append(chr(code))
        else:
            result.append(_KATAKANA_ITERATION.get(char, char))
    return "".join(result)


def strip_voicing_marks(text: str) -> str:
    """Removes voiced and semi-voiced marks from kana.

    Precomposed kana such as ガ or パ become カ and ハ. Combining marks
    and the spacing voiced and semi-voiced marks are removed.
    Characters other than kana are left as they are.

    Args:
        text: The text to convert.

    Returns:
        The text without voicing marks.
    """
    result = []
    for char in text:
        if char in _VOICING_MARKS:
            continue
        if _is_kana(char):
            # Decompose kana only: normalizing the whole text would also
            # turn CJK compatibility ideographs (old forms such as the
            # U+FA4C form of 社) into their modern unified forms.
            result.append(unicodedata.normalize("NFD", char)[0])
        else:
            result.append(char)
    return "".join(result)


def enlarge_small_kana(text: str) -> str:
    """Writes small kana at full size, as in pre-war printing.

    For example, アッタ becomes アツタ and キャ becomes キヤ.

    Args:
        text: The text to convert.

    Returns:
        The text with every small kana replaced by its full-size form.
    """
    return text.translate(_SMALL_TO_LARGE)


def remove_punctuation(text: str) -> str:
    """Removes the Japanese comma and period.

    The comma is removed. Consecutive periods count as one. A period
    followed by a closing bracket, a space or the end of the line is
    removed. Any other period becomes an ideographic space so that
    sentences stay apart.

    Args:
        text: The text to convert.

    Returns:
        The text without 、 and 。.
    """
    lines = []
    for line in text.split("\n"):
        line = line.replace(_COMMA, "")
        line = _PERIODS_BEFORE_BREAK.sub("", line)
        lines.append(_PERIODS.sub(_IDEOGRAPHIC_SPACE, line))
    return "\n".join(lines)


def to_bungo(
    text: str,
    *,
    kyujitai: bool = True,
    compat: bool = True,
    katakana: bool = True,
    keep_voicing: bool = False,
    keep_small_kana: bool = False,
    keep_punctuation: bool = False,
) -> str:
    """Applies the mechanical conversions toward pre-war notation.

    The defaults follow the notation of pre-war statutes: old kanji
    forms, katakana, no voicing marks, full-size kana and no
    punctuation.

    Args:
        text: Text whose grammar and kana spelling are already in the
            pre-war style.
        kyujitai: Whether to convert kanji to their old forms.
        compat: Whether old forms may use CJK compatibility ideographs.
        katakana: Whether to write kana in katakana.
        keep_voicing: Whether to keep voiced and semi-voiced marks.
        keep_small_kana: Whether to keep small kana small.
        keep_punctuation: Whether to keep 、 and 。.

    Returns:
        The converted text.
    """
    if kyujitai:
        text = to_kyujitai(text, compat=compat)
    if katakana:
        text = to_katakana(text)
    if not keep_voicing:
        text = strip_voicing_marks(text)
    if not keep_small_kana:
        text = enlarge_small_kana(text)
    if not keep_punctuation:
        text = remove_punctuation(text)
    return text


def to_modern(text: str, *, shinjitai: bool = True) -> str:
    """Applies the mechanical conversions toward modern notation.

    Old kanji forms in the table and the kana script are changed.
    Voicing marks, small kana and punctuation cannot be restored by a
    table, so the agent adds them.

    Args:
        text: Text in pre-war notation.
        shinjitai: Whether to convert old kanji forms to modern ones.

    Returns:
        The converted text.
    """
    if shinjitai:
        text = to_shinjitai(text)
    return to_hiragana(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="direction", required=True)

    bungo = sub.add_parser(
        "to-bungo", help="convert toward pre-war notation"
    )
    bungo.add_argument(
        "--shinjitai", action="store_true", help="keep modern kanji forms"
    )
    bungo.add_argument(
        "--no-compat",
        action="store_true",
        help="do not use CJK compatibility ideographs for old forms",
    )
    bungo.add_argument(
        "--hiragana", action="store_true", help="keep kana in hiragana"
    )
    bungo.add_argument(
        "--keep-voicing", action="store_true", help="keep voicing marks"
    )
    bungo.add_argument(
        "--keep-small-kana", action="store_true", help="keep small kana"
    )
    bungo.add_argument(
        "--keep-punctuation",
        action="store_true",
        help="keep the Japanese comma and period",
    )

    modern = sub.add_parser(
        "to-modern", help="convert old kanji forms and katakana"
    )
    modern.add_argument(
        "--keep-kyujitai", action="store_true", help="keep old kanji forms"
    )
    for subparser in (bungo, modern):
        subparser.add_argument(
            "--input",
            metavar="FILE",
            help="read the text from this UTF-8 file instead of stdin",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Reads text, converts it and writes it to standard output.

    Args:
        argv: Command-line arguments without the program name. Uses
            sys.argv when None.

    Returns:
        The exit code.
    """
    # The Windows console may not use UTF-8, and the text and the
    # arguments echoed in error messages are Japanese. Writing "\n" as
    # is keeps the line endings the same on every platform.
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)
    if args.input is not None:
        try:
            # utf-8-sig drops the BOM that some Windows editors add.
            with open(args.input, encoding="utf-8-sig") as source:
                text = source.read()
        except (OSError, UnicodeDecodeError) as error:
            print(f"cannot read {args.input}: {error}", file=sys.stderr)
            return 1
    else:
        sys.stdin.reconfigure(encoding="utf-8-sig")
        text = sys.stdin.read()
    try:
        if args.direction == "to-bungo":
            result = to_bungo(
                text,
                kyujitai=not args.shinjitai,
                compat=not args.no_compat,
                katakana=not args.hiragana,
                keep_voicing=args.keep_voicing,
                keep_small_kana=args.keep_small_kana,
                keep_punctuation=args.keep_punctuation,
            )
        else:
            result = to_modern(text, shinjitai=not args.keep_kyujitai)
    except KanjiTableError as error:
        print(error, file=sys.stderr)
        return 1
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
