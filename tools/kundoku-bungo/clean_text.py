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
  vocabulary table and the examples.
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
# Wording used only in the name of the emperor; a line that has it is not
# quoted in the vocabulary table or the examples.
IMPERIAL_WORDS = re.compile(
    "朕|臣民|皇祖|皇宗|御名|御璽|聖旨|聖慮|叡|休命|大御心|詔|勅|敕"
)
# Notes of stenographers in speeches, such as （拍手） and （ヒヤヒヤ）.
# Brackets of the original text, such as （別紙）, are kept.
STENOGRAPHY_NOTE = re.compile(
    "（[^（）]*(?:拍手|喝采|謹聴|謹聼|ヒヤヒヤ|ヒヤ|ノーノー|笑声|大笑)[^（）]*）"
)
# Half-width brackets are written by editors (notes and readings), not by
# the original documents, which use full-width ones.
HALF_WIDTH_BRACKET = re.compile(r"[()\[\]]")
_KANJI_NUMBER = "一二三四五六七八九十百千〇"
# Chapter and article numbers at the start of a line, with the space after.
HEADING_NUMBER = re.compile(
    f"^(?:第[{_KANJI_NUMBER}]+[編章節款條条項]|[{_KANJI_NUMBER}]+章)"
    f"[ {IDEOGRAPHIC_SPACE}]*"
)
_VARIATION_SELECTORS = re.compile(
    f"[{chr(0xFE00)}-{chr(0xFE0F)}{chr(0xE0100)}-{chr(0xE01EF)}]"
)
# Templates whose first argument is the text of the original document.
_TEXT_TEMPLATE = re.compile(
    r"\{\{\s*(?:[Ll]ang\s*\|[^|{}]*|異体字|[Rr]|[Rr]uby)\s*\|([^{}|]*)[^{}]*\}\}"
)
_ANY_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_LINE_MARKS = f" {IDEOGRAPHIC_SPACE}*:#"


def body(wikitext: str) -> str:
    """Removes the page header template at the top of a page.

    Args:
        wikitext: The wikitext of a whole page.

    Returns:
        The wikitext after the first template, when the page starts with
        one; otherwise the wikitext unchanged.
    """
    text = wikitext.lstrip()
    if not text.startswith("{{"):
        return wikitext
    depth = 0
    for position in range(len(text) - 1):
        pair = text[position : position + 2]
        if pair == "{{":
            depth += 1
        elif pair == "}}":
            depth -= 1
            if depth == 0:
                return text[position + 2 :]
    return ""


def strip_markup(wikitext: str) -> str:
    """Removes the editors' markup and keeps the text of the document.

    Removes notes in <ref> tags, text in bold (numbers added by
    transcribers), templates other than those holding the text, category
    links, HTML tags and variation selectors. Keeps the text of links and
    of the 異体字・Lang・r・Ruby templates. Character references are
    turned into characters without normalizing them, so compatibility
    ideographs survive.

    Args:
        wikitext: Wikitext, usually the result of body().

    Returns:
        The plain text, with the line breaks of the wikitext.
    """
    text = re.sub(r"<ref[^>/]*/>", "", wikitext)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"'''[^']*'''", "", text)
    previous = None
    while previous != text:
        previous = text
        text = _TEXT_TEMPLATE.sub(r"\1", text)
        text = _ANY_TEMPLATE.sub("", text)
    text = re.sub(r"\[\[(?:Category|カテゴリ):[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("''", "")
    text = html.unescape(text)
    return _VARIATION_SELECTORS.sub("", text)


def remove_stenography_notes(text: str) -> str:
    """Removes stenographers' notes such as （拍手） from a speech.

    Args:
        text: Plain text from strip_markup().

    Returns:
        The text without the notes. Other brackets are kept.
    """
    return STENOGRAPHY_NOTE.sub("", text)


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
            (
                i
                for i in range(start, len(kept))
                if end_anchor in kept[i]
            ),
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
    stenographers' notes and the given ranges, trims list marks and
    heading numbers, and leaves out lines that are empty, use wording
    only for the emperor, or have half-width brackets.

    Args:
        wikitext: The wikitext of a whole page.
        ranges: Ranges of lines to remove (see apply_line_exclusions()).

    Returns:
        The lines in the order of the page.
    """
    text = remove_stenography_notes(strip_markup(body(wikitext)))
    lines = apply_line_exclusions(text.split("\n"), ranges)
    result = []
    for line in lines:
        line = HEADING_NUMBER.sub("", line.strip(_LINE_MARKS))
        if not line or IMPERIAL_WORDS.search(line):
            continue
        if HALF_WIDTH_BRACKET.search(line):
            continue
        result.append(line)
    return result
