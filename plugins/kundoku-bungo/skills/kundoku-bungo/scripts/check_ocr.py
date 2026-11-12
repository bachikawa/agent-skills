"""Lists likely misreadings in OCR text of pre-war official Japanese.

The script never changes the text. It writes a tab-separated table of
places worth checking against the original image, each with the reason
it was listed. Whether a place is really a misreading is decided by the
agent from the context and by the user from the image.

The candidates come from fixed rules:

    vocabulary  a run that becomes a headword of references/vocabulary.tsv
                when look-alike characters are swapped (又八 -> 又ハ)
    numeral     katakana inside a number (第ニ條 -> 第二條)
    lookalike   a kanji that looks like katakana next to katakana
                (スル二依リ -> スルニ依リ); also flags correct text
    mixed-form  a modern kanji in text that mostly uses the old forms of
                references/kanji.tsv; also flags correct text
    mixed-kana  hiragana in katakana text, or the other way round
    unexpected  characters pre-war printing does not use (half-width
                letters, U+FFFD, private use, control characters, 〓)

Usage:
    python check_ocr.py --input FILE [--remove-input]

Without --input, the text is read from standard input. --remove-input
deletes the input file under the same conditions as convert_chars.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# The scripts ship together; make the sibling module importable even
# when the interpreter does not add the script directory (python -I).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert_chars  # noqa: E402

VOCABULARY_TABLE = (
    Path(__file__).resolve().parent.parent / "references" / "vocabulary.tsv"
)
COLUMNS = (
    "line",
    "column",
    "found",
    "suggestion",
    "kind",
    "basis",
    "context",
)
KINDS = (
    "vocabulary",
    "numeral",
    "lookalike",
    "mixed-form",
    "mixed-kana",
    "unexpected",
)
CONTEXT_WIDTH = 8

# Pairs of characters that print almost alike. The first of each pair is
# katakana. Hiragana pairs (ヘ・へ and リ・り) are covered by mixed-kana.
LOOKALIKE_PAIRS = (
    ("ニ", "二"),
    ("ハ", "八"),
    ("カ", "力"),
    ("ロ", "口"),
    ("エ", "工"),
    ("タ", "夕"),
    ("ト", "卜"),
    ("オ", "才"),
    ("ミ", "三"),
    ("チ", "千"),
    ("ー", "一"),
)
_LOOKALIKE = {frozenset(pair) for pair in LOOKALIKE_PAIRS}
# Katakana read in place of a numeral, and the numeral.
_KANA_FOR_NUMERAL = {"ニ": "二", "ハ": "八", "ミ": "三", "チ": "千", "ー": "一"}
# Kanji read in place of katakana, and the katakana. 三, 千 and 一 are
# left out: next to katakana they are far more often real numerals.
_KANJI_FOR_KANA = {
    "二": "ニ",
    "八": "ハ",
    "力": "カ",
    "口": "ロ",
    "工": "エ",
    "夕": "タ",
    "卜": "ト",
    "才": "オ",
}
_NUMERALS = set("〇一二三四五六七八九十百千万萬壱弐參拾")
_NUMBER_START = _NUMERALS | {"第"}
_NUMBER_END = _NUMERALS | set("条條項号號年月日")
# Counters that follow 二 and 八 used as numerals (二週, 八人). They
# only keep these kanji out of lookalike: katakana before them is often
# a particle, so they do not mark a number for numeral.
_COUNTERS = set("週人箇個回度名円圓銭錢里町尺寸分時歳級等倍")

_MIN_COUNTED = 10
_MIN_OLD_SHARE = 0.8
_MAX_MINORITY_SHARE = 0.1

# The basis column is read by Japanese users, like the notes of the
# tables, so it is written in Japanese.
_LOW = "確かさは低い"


class TableError(Exception):
    """Raised when a table in references/ cannot be read."""


@dataclass(frozen=True)
class Candidate:
    """A place in the text worth checking.

    Attributes:
        line: The line number, from 1.
        column: The position in the line in code points, from 1.
        found: The characters as they are in the text.
        suggestion: What they may have been; empty when unknown.
        kind: One of KINDS.
        basis: Why the place is listed.
    """

    line: int
    column: int
    found: str
    suggestion: str
    kind: str
    basis: str


def _is_hiragana(char: str) -> bool:
    return "ぁ" <= char <= "ゖ"


def _is_katakana(char: str) -> bool:
    # ァ to ヺ: ー, ヽ and ヾ are not counted as katakana letters.
    return "ァ" <= char <= "ヺ"


def load_headwords(path: Path = VOCABULARY_TABLE) -> list[str]:
    """Reads the headwords of the vocabulary table.

    Args:
        path: The vocabulary TSV file with a word column.

    Returns:
        The headwords in the order of the table.

    Raises:
        TableError: The file cannot be read or has no word column.
    """
    try:
        with open(path, encoding="utf-8", newline="") as source:
            reader = csv.DictReader(
                source, delimiter="\t", quoting=csv.QUOTE_NONE
            )
            words = [row["word"] for row in reader]
    except (OSError, UnicodeDecodeError, KeyError) as error:
        raise TableError(f"cannot read {path}: {error}") from error
    return words


def _search_parts(headwords: list[str]) -> list[tuple[str, str]]:
    """Splits headwords at 〜 into the parts that are searched for.

    Returns:
        Pairs of a part and its headword. Parts of one character are
        dropped: a single look-alike would match almost anywhere.
    """
    parts = []
    for word in headwords:
        for part in word.split("〜"):
            if len(part) > 1:
                parts.append((part, word))
    return parts


def search_copy(line: str) -> str:
    """Returns the form of a line that is compared with the headwords.

    Old kanji forms become modern, hiragana become katakana and kana
    with a precomposed voicing mark lose it. Every step replaces one
    character with one character, so positions stay the same. Combining
    voicing marks are kept for that reason.

    Args:
        line: One line of the text.

    Returns:
        The copy, as long as the line.
    """
    copy = convert_chars.to_katakana(convert_chars.to_shinjitai(line))
    return "".join(
        unicodedata.normalize("NFD", char)[0]
        if convert_chars._is_kana(char)
        else char
        for char in copy
    )


def _vocabulary(
    number: int, line: str, parts: list[tuple[str, str]]
) -> list[Candidate]:
    copy = search_copy(line)
    found = []
    for part, word in parts:
        size = len(part)
        for start in range(len(copy) - size + 1):
            window = copy[start : start + size]
            if window == part:
                continue
            differ = [
                index
                for index, (have, want) in enumerate(zip(window, part))
                if have != want
            ]
            if all(
                frozenset((window[index], part[index])) in _LOOKALIKE
                for index in differ
            ):
                original = line[start : start + size]
                suggestion = "".join(
                    part[index] if index in differ else char
                    for index, char in enumerate(original)
                )
                found.append(
                    Candidate(
                        number,
                        start + 1,
                        original,
                        suggestion,
                        "vocabulary",
                        f"語の表の見出し「{word}」",
                    )
                )
    return found


def _numeral(number: int, line: str) -> list[Candidate]:
    found = []
    for index, char in enumerate(line):
        if char not in _KANA_FOR_NUMERAL or index == 0:
            continue
        before = line[index - 1]
        after = line[index + 1] if index + 1 < len(line) else ""
        if before in _NUMBER_START and after in _NUMBER_END:
            found.append(
                Candidate(
                    number,
                    index + 1,
                    char,
                    _KANA_FOR_NUMERAL[char],
                    "numeral",
                    f"数の並びの中のカタカナ（前「{before}」、後「{after}」）",
                )
            )
    return found


def _lookalike(number: int, line: str) -> list[Candidate]:
    found = []
    for index, char in enumerate(line):
        if char not in _KANJI_FOR_KANA:
            continue
        before = line[index - 1] if index > 0 else ""
        after = line[index + 1] if index + 1 < len(line) else ""
        if not (_is_katakana(before) or _is_katakana(after)):
            continue
        # 二 and 八 inside a number are numerals.
        if char in "二八" and (
            before in _NUMBER_START
            or after in _NUMBER_END
            or after in _COUNTERS
        ):
            continue
        found.append(
            Candidate(
                number,
                index + 1,
                char,
                _KANJI_FOR_KANA[char],
                "lookalike",
                f"カタカナの隣の、カタカナに似た漢字。{_LOW}",
            )
        )
    return found


def _mixed_form(lines: list[str]) -> list[Candidate]:
    rows = [
        row
        for row in convert_chars.load_kanji_table()
        if row["kind"] == "joyo" and row["one_to_many"] == "0"
    ]
    old_of = {row["modern"]: row for row in rows}
    old = {row["old"] for row in rows}
    text = "".join(lines)
    old_count = sum(1 for char in text if char in old)
    new_count = sum(1 for char in text if char in old_of)
    total = old_count + new_count
    if (
        total < _MIN_COUNTED
        or new_count == 0
        or old_count / total < _MIN_OLD_SHARE
    ):
        return []
    share = round(100 * old_count / total)
    found = []
    for number, line in enumerate(lines, start=1):
        for index, char in enumerate(line):
            if char in old_of:
                row = old_of[char]
                found.append(
                    Candidate(
                        number,
                        index + 1,
                        char,
                        row["old"],
                        "mixed-form",
                        f"文の字の {share}% が旧字体。字の表の典拠: "
                        f"{row['evidence']}。{_LOW}",
                    )
                )
    return found


def _mixed_kana(lines: list[str]) -> list[Candidate]:
    text = "".join(lines)
    hiragana = sum(1 for char in text if _is_hiragana(char))
    katakana = sum(
        1
        for char in text
        if _is_katakana(char) and char not in convert_chars._KATAKANA_KEPT
    )
    total = hiragana + katakana
    if total < _MIN_COUNTED:
        return []
    if katakana > hiragana:
        is_minority, convert, major = (
            _is_hiragana,
            convert_chars.to_katakana,
            "カタカナ",
        )
        minority = hiragana
    else:
        is_minority, convert, major = (
            lambda char: _is_katakana(char)
            and char not in convert_chars._KATAKANA_KEPT,
            convert_chars.to_hiragana,
            "ひらがな",
        )
        minority = katakana
    if minority == 0 or minority / total >= _MAX_MINORITY_SHARE:
        return []
    share = round(100 * (total - minority) / total)
    found = []
    for number, line in enumerate(lines, start=1):
        for index, char in enumerate(line):
            if is_minority(char):
                found.append(
                    Candidate(
                        number,
                        index + 1,
                        char,
                        convert(char),
                        "mixed-kana",
                        f"文の仮名の {share}% が{major}。{_LOW}",
                    )
                )
    return found


def _is_unexpected(char: str) -> bool:
    code = ord(char)
    return (
        ("0" <= char <= "9")
        or ("A" <= char <= "Z")
        or ("a" <= char <= "z")
        or 0xFF61 <= code <= 0xFF9F
        or char in ("�", "〓")
        or 0xE000 <= code <= 0xF8FF
        or code >= 0xF0000
        or unicodedata.category(char) == "Cc"
    )


def _unexpected(number: int, line: str) -> list[Candidate]:
    return [
        Candidate(
            number,
            index + 1,
            char,
            "",
            "unexpected",
            f"当時の印刷にふつう出ない字（U+{ord(char):04X}）",
        )
        for index, char in enumerate(line)
        if _is_unexpected(char)
    ]


def split_lines(text: str) -> list[str]:
    """Splits the text into lines without their line breaks.

    Only "\\n" ends a line, with one "\\r" before it dropped, so that line
    numbers match those of an editor.

    Args:
        text: The whole text.

    Returns:
        The lines.
    """
    return [
        line[:-1] if line.endswith("\r") else line
        for line in text.split("\n")
    ]


def check(
    text: str, headwords: list[str] | None = None
) -> list[Candidate]:
    """Lists the places in the text that may be misread.

    Args:
        text: The OCR text. It is not changed or normalized.
        headwords: The vocabulary headwords; read from the table when
            None.

    Returns:
        The candidates ordered by line, column and kind. A lookalike
        candidate is left out where a vocabulary candidate covers the
        same character.

    Raises:
        TableError: The vocabulary table cannot be read.
        convert_chars.KanjiTableError: The kanji table cannot be read.
    """
    if headwords is None:
        headwords = load_headwords()
    parts = _search_parts(headwords)
    lines = split_lines(text)
    found = set()
    for number, line in enumerate(lines, start=1):
        vocabulary = _vocabulary(number, line, parts)
        covered = {
            (item.line, item.column + offset)
            for item in vocabulary
            for offset in range(len(item.found))
        }
        found.update(vocabulary)
        found.update(_numeral(number, line))
        found.update(
            item
            for item in _lookalike(number, line)
            if (item.line, item.column) not in covered
        )
        found.update(_unexpected(number, line))
    found.update(_mixed_form(lines))
    found.update(_mixed_kana(lines))
    return sorted(
        found,
        key=lambda item: (
            item.line,
            item.column,
            KINDS.index(item.kind),
            item.found,
            item.basis,
        ),
    )


def _visible(text: str) -> str:
    return text.replace("\t", "\\t")


def format_candidates(text: str, candidates: list[Candidate]) -> str:
    """Formats the candidates as a tab-separated table.

    Args:
        text: The text the candidates were found in, for the context.
        candidates: The candidates returned by check.

    Returns:
        The table with a header line. Every line ends with a newline.
    """
    lines = split_lines(text)
    output = ["\t".join(COLUMNS)]
    for item in candidates:
        line = lines[item.line - 1]
        start = item.column - 1
        context = line[
            max(0, start - CONTEXT_WIDTH) : start
            + len(item.found)
            + CONTEXT_WIDTH
        ]
        output.append(
            "\t".join(
                (
                    str(item.line),
                    str(item.column),
                    _visible(item.found),
                    _visible(item.suggestion),
                    item.kind,
                    item.basis,
                    _visible(context),
                )
            )
        )
    return "\n".join(output) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input",
        metavar="FILE",
        help="read the text from this UTF-8 file instead of stdin",
    )
    parser.add_argument(
        "--remove-input",
        action="store_true",
        help="delete the --input file after reading it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Reads OCR text and writes the candidates to standard output.

    Args:
        argv: Command-line arguments without the program name. Uses
            sys.argv when None.

    Returns:
        The exit code: 0 whether or not there are candidates, 1 when the
        text or a table cannot be read.
    """
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.remove_input and args.input is None:
        parser.error("--remove-input requires --input")
    if args.input is not None:
        try:
            # utf-8-sig drops the BOM that some Windows editors add.
            with open(args.input, encoding="utf-8-sig", newline="") as source:
                text = source.read()
        except (OSError, UnicodeDecodeError) as error:
            print(f"cannot read {args.input}: {error}", file=sys.stderr)
            return 1
    else:
        sys.stdin.reconfigure(encoding="utf-8-sig", newline="")
        text = sys.stdin.read()
    try:
        result = format_candidates(text, check(text))
    except (TableError, convert_chars.KanjiTableError) as error:
        print(error, file=sys.stderr)
        return 1
    # Remove only after the check succeeded, as convert_chars.py does.
    if args.remove_input:
        convert_chars.remove_input(args.input)
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
