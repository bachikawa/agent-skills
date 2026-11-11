"""Turns Wikisource wikitext into the plain text of the original document.

Wikisource transcriptions mix the text of the original document with
material added by editors: notes in <ref> tags, numbers in bold, links,
templates and page headers. The editors' material is licensed under
CC BY-SA, so the tables of the skill must not take anything from it.
This module removes it and keeps the original text.

The functions are used by the scripts that build the tables:

- strip_markup() and body() give the text for the kanji table. Kanji
  written as character references (such as &#xFA45;) come back as the
  compatibility ideographs they name, because MediaWiki stores literal
  compatibility ideographs only after normalizing them to NFC.
- vocabulary_lines() gives the lines that may be quoted in the
  vocabulary table and the examples. When a line may hold anything other
  than the original text, the whole line is left out.
"""

from __future__ import annotations

import csv
import html
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

LINE_EXCLUSIONS_FILE = Path(__file__).resolve().parent / "line_exclusions.tsv"
LINE_EXCLUSION_COLUMNS = ["title", "start", "end"]
IDEOGRAPHIC_SPACE = chr(0x3000)
# Put in place of a template that strip_markup() removes from the middle
# of the text, so that vocabulary_lines() can leave the line out.
REMOVED = chr(0xFFFC)
# Wording used only in the name of the emperor; a line that has it is not
# quoted in the vocabulary table or the examples.
IMPERIAL_WORDS = re.compile(
    "朕|臣民|皇祖|皇宗|御名|御璽|聖旨|聖慮|叡|宸|休命|大御心|詔|勅|敕"
)
# Notes of stenographers in speeches, such as （拍手） and （大喝采）. A
# bracket is removed only when everything in it is such a note, so that
# brackets of the original text, such as （別紙）, are kept.
_NOTE_WORDS = (
    "拍手|喝采|喝釆|謹聴|謹聼|謹聽|ヒヤヒヤ|ノーノー|笑声|笑聲|大笑|"
    "異議ナシ|賛成|贊成|ノー|ヒヤ"
)
_NOTE_MODIFIERS = "大満滿場一同再三起ル起ルアリ者、・ " + IDEOGRAPHIC_SPACE
STENOGRAPHY_NOTE = re.compile(
    f"（(?:(?:{_NOTE_WORDS})|[{_NOTE_MODIFIERS}])+）"
)
_NOTE_WORD = re.compile(_NOTE_WORDS)
# Editors use half-width brackets for their notes, and some transcriptions
# use them for the brackets of the original text too. The two cannot be
# told apart, so a line with them is left out.
HALF_WIDTH_BRACKET = re.compile(r"[()\[\]]")
# Characters that mark editors' markup left after strip_markup(), or that
# the original documents never have.
_LEFTOVER_MARKUP = re.compile(f"[<>{{}}|={REMOVED}]")
_KANJI_NUMBER = "一二三四五六七八九十百千〇廿卅"
# Chapter and article numbers at the start of a line, with the space after.
# A number followed directly by text, as in 「第三條ノ規定ニ依リ」, is part
# of the sentence and is kept.
HEADING_NUMBER = re.compile(
    f"^(?:第[{_KANJI_NUMBER}]+[編章節款條条項](?:ノ[{_KANJI_NUMBER}]+)?"
    f"|[{_KANJI_NUMBER}]+章)(?:[ {IDEOGRAPHIC_SPACE}]+|$)"
)
# Variation selectors, C0 and C1 controls other than the line break, and
# bidirectional controls. Character references can bring them back.
_INVISIBLE = re.compile(
    f"[{chr(0xFE00)}-{chr(0xFE0F)}{chr(0xE0100)}-{chr(0xE01EF)}"
    f"{chr(0x00)}-{chr(0x09)}{chr(0x0B)}-{chr(0x1F)}{chr(0x7F)}-{chr(0x9F)}"
    f"{chr(0x200B)}-{chr(0x200F)}{chr(0x202A)}-{chr(0x202E)}"
    f"{chr(0x2066)}-{chr(0x2069)}{chr(0xFEFF)}]"
)
_HEADER_START = re.compile(r"\{\{\s*[Hh]eader\s*[|}]")
# Templates whose first argument is the text of the original document.
_TEXT_TEMPLATE = re.compile(
    r"\{\{\s*(?:[Ll]ang\s*\|[^|{}]*|異体字|[Rr]|[Rr]uby)\s*\|([^{}|]*)[^{}]*\}\}"
)
_ANY_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
# Links that show no text of the document: files, categories and links
# to other languages.
_HIDDEN_LINK = re.compile(
    r"\[\[\s*(?:[Ff]ile|[Ii]mage|ファイル|画像|[Cc]ategory|カテゴリ"
    r"|[a-z]{2,3}(?:-[a-z]+)?)\s*:[^\]]*\]\]"
)
_LINE_MARKS = f" {IDEOGRAPHIC_SPACE}*:#;"


def _template_end(text: str, start: int) -> int | None:
    """Returns the index just after the template that starts at start."""
    depth = 0
    position = start
    while position < len(text) - 1:
        pair = text[position : position + 2]
        if pair == "{{":
            depth += 1
            position += 2
        elif pair == "}}":
            depth -= 1
            position += 2
            if depth == 0:
                return position
        else:
            position += 1
    return None


def split_header(wikitext: str) -> tuple[str, str]:
    """Splits the page header template (Header) from the rest of a page.

    Args:
        wikitext: The wikitext of a whole page.

    Returns:
        The header template and the wikitext without it. When there is no
        header, the first value is empty. When the header is not closed,
        the whole page is taken as the header, so that no editors' notes
        reach the text.
    """
    match = _HEADER_START.search(wikitext)
    if match is None:
        return "", wikitext
    end = _template_end(wikitext, match.start())
    if end is None:
        return wikitext[match.start() :], wikitext[: match.start()]
    header = wikitext[match.start() : end]
    return header, wikitext[: match.start()] + wikitext[end:]


def body(wikitext: str) -> str:
    """Removes the page header template (Header) from a page.

    Args:
        wikitext: The wikitext of a whole page.

    Returns:
        The wikitext without the header (see split_header()).
    """
    return split_header(wikitext)[1]


def strip_markup(wikitext: str, *, mark_removed: bool = False) -> str:
    """Removes the editors' markup and keeps the text of the document.

    Removes notes in <ref> tags, text in bold (numbers added by
    transcribers), templates other than those holding the text, links to
    files, categories and other languages, HTML tags, invisible
    characters and controls. Keeps the text of links and of the
    異体字・Lang・r・Ruby templates, also when they are nested. <br> becomes
    a line break. Character references are turned into characters without
    normalizing them, so compatibility ideographs survive.

    Args:
        wikitext: Wikitext, usually the result of body().
        mark_removed: Put REMOVED in place of each template that is
            removed, so that a caller can tell where text may be missing.

    Returns:
        The plain text, with the line breaks of the wikitext.
    """
    text = re.sub(r"<ref\b[^>]*/>", "", wikitext)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"'''[^']*'''", "", text)
    replacement = REMOVED if mark_removed else ""
    previous = None
    while previous != text:
        previous = text
        inner = None
        while inner != text:
            inner = text
            text = _TEXT_TEMPLATE.sub(r"\1", text)
        text = _ANY_TEMPLATE.sub(replacement, text)
    text = _HIDDEN_LINK.sub("", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("''", "")
    text = html.unescape(text).replace("\t", " ")
    return _INVISIBLE.sub("", text)


def remove_stenography_notes(text: str) -> str:
    """Removes stenographers' notes such as （拍手） from a speech.

    Args:
        text: Plain text from strip_markup().

    Returns:
        The text without the notes. A bracket that holds anything other
        than notes is kept.
    """

    def replace(match: re.Match[str]) -> str:
        return "" if _NOTE_WORD.search(match.group(0)) else match.group(0)

    return STENOGRAPHY_NOTE.sub(replace, text)


def apply_line_exclusions(
    lines: Sequence[str], ranges: Iterable[tuple[str, str]]
) -> list[str]:
    """Removes ranges of lines, such as the rescript inside a law.

    A range starts at the first line that contains its start anchor and
    ends at the next line, from there on, that contains its end anchor.
    Both lines are removed with the lines between them.

    Args:
        lines: The lines of a page.
        ranges: Pairs of (start anchor, end anchor).

    Returns:
        The lines outside every range.

    Raises:
        ValueError: An anchor is not found, which means that the page is
            not the revision the range was written for.
    """
    kept = list(lines)
    for start_anchor, end_anchor in ranges:
        start = next(
            (i for i, line in enumerate(kept) if start_anchor in line), None
        )
        if start is None:
            raise ValueError(f"start anchor not found: {start_anchor}")
        end = next(
            (i for i in range(start, len(kept)) if end_anchor in kept[i]),
            None,
        )
        if end is None:
            raise ValueError(f"end anchor not found: {end_anchor}")
        del kept[start : end + 1]
    return kept


def read_line_exclusions(
    path: Path = LINE_EXCLUSIONS_FILE,
) -> dict[str, list[tuple[str, str]]]:
    """Reads line_exclusions.tsv.

    Args:
        path: The file to read.

    Returns:
        The ranges of lines to remove, by page title.

    Raises:
        ValueError: The columns are not the expected ones or a value is
            empty.
    """
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames != LINE_EXCLUSION_COLUMNS:
            raise ValueError(f"unexpected columns in {path}")
        ranges: dict[str, list[tuple[str, str]]] = {}
        for row in reader:
            if not all(row[column] for column in LINE_EXCLUSION_COLUMNS):
                raise ValueError(f"empty value in {path}")
            ranges.setdefault(row["title"], []).append(
                (row["start"], row["end"])
            )
    return ranges


def vocabulary_lines(
    wikitext: str, ranges: Iterable[tuple[str, str]] = ()
) -> list[str]:
    """Returns the lines of a page that may be quoted as examples.

    Takes the text of the page (see body() and strip_markup()), removes
    stenographers' notes and the given ranges, and trims list marks and
    heading numbers. Leaves out lines that are empty, that are headings
    (starting with =), that use wording only for the emperor, or that may
    hold editors' material: half-width brackets, leftovers of markup, or
    a removed template.

    Args:
        wikitext: The wikitext of a whole page.
        ranges: Ranges of lines to remove (see apply_line_exclusions()).

    Returns:
        The lines in the order of the page.
    """
    text = strip_markup(body(wikitext), mark_removed=True)
    text = remove_stenography_notes(text)
    lines = apply_line_exclusions(text.split("\n"), ranges)
    result = []
    for line in lines:
        if line.lstrip().startswith(("=", "{|", "|")):
            continue
        line = HEADING_NUMBER.sub("", line.strip(_LINE_MARKS))
        if not line or IMPERIAL_WORDS.search(line):
            continue
        if HALF_WIDTH_BRACKET.search(line) or _LEFTOVER_MARKUP.search(line):
            continue
        result.append(line)
    return result
