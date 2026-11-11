"""Builds the vocabulary table (vocabulary.tsv) of the skill kundoku-bungo.

Run from the repository root after fetch_corpus.py fetch:

    python -I tools/kundoku-bungo/build_vocabulary.py --work WORK
    python -I tools/kundoku-bungo/build_vocabulary.py --work WORK --check

The words, readings, kinds and meanings come from vocabulary_entries.tsv,
which a person writes. The script finds an example of each word in the
revisions of corpus_revisions.tsv whose usage is all or text_only, and
writes the example with its source.

The example is the first match in this order of pages: transcriptions in
the forms of the period before those whose kanji an editor replaced with
new forms (such as 学制, which has no other transcription), then year,
title and revision ID. The choice depends only on the corpus, not on the
table that was built before.

With --check the table is built in memory and compared with
vocabulary.tsv; the exit code is 1 when they differ.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# python -I leaves the script's directory out of sys.path; the scripts
# are run that way because they read downloaded files.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clean_text  # noqa: E402
import fetch_corpus  # noqa: E402

VOCABULARY_FILE = fetch_corpus.REFERENCES / "vocabulary.tsv"
KANJI_FILE = fetch_corpus.REFERENCES / "kanji.tsv"
ENTRIES_FILE = fetch_corpus.TOOLS / "vocabulary_entries.tsv"
VOCABULARY_COLUMNS = [
    "word",
    "reading",
    "kind",
    "meaning",
    "example",
    "example_form",
    "source",
    "source_url",
]
ENTRY_COLUMNS = [
    "word",
    "reading",
    "kind",
    "meaning",
    "exclude_prev",
    "exclude_next",
]
KINDS = frozenset(
    {
        "接続",
        "副詞",
        "構文",
        "名詞",
        "連体詞",
        "助動詞",
        "接続・副詞",
        "副詞・接続",
    }
)
TEXT_USAGES = frozenset({"all", "text_only"})
REVISION_URL = "https://ja.wikisource.org/w/index.php?oldid={}"
SPACE = chr(0x3000)
_HAN = re.compile(
    f"[{chr(0x3400)}-{chr(0x4DBF)}{chr(0x4E00)}-{chr(0x9FFF)}"
    f"{chr(0xF900)}-{chr(0xFAFF)}々〻]"
)
_DIGITS = re.compile("[0-9０-９]")
_CHAPTER_HEAD = re.compile("^第?[一二三四五六七八九十百]+章")
# Characters before the word, and after it for a construction (〜), that an
# example shows so that the word is read in its sentence.
BEFORE, AFTER, AFTER_CONSTRUCTION = 20, 20, 40
_SENTENCE_BREAKS = ("。", SPACE, chr(0x2003), " ")


class VocabularyError(Exception):
    """Raised when the vocabulary table cannot be built."""


@dataclass(frozen=True)
class Entry:
    """One row of vocabulary_entries.tsv.

    Attributes:
        word: The headword in new kanji and katakana without voicing
            marks; 〜 stands for the words around a construction.
        reading: The reading in modern hiragana.
        kind: The part of speech (see KINDS).
        meaning: The meaning today.
        exclude_prev: Characters that must not come right before the word,
            to keep apart a word of the same form (such as the verb in
            「〜ニ及ヒ」 for the conjunction 及ヒ).
        exclude_next: Characters that must not come right after the word.
    """

    word: str
    reading: str
    kind: str
    meaning: str
    exclude_prev: str
    exclude_next: str


@dataclass(frozen=True)
class TextPage:
    """A revision with the lines that may be quoted.

    Attributes:
        row: Its row of corpus_revisions.tsv.
        new_forms: Whether its only transcription is one in which an
            editor replaced the kanji with new forms.
        lines: Its lines (clean_text.vocabulary_lines()).
    """

    row: fetch_corpus.Revision
    new_forms: bool
    lines: list[str]


def read_entries(path: Path | None = None) -> list[Entry]:
    """Reads vocabulary_entries.tsv.

    Args:
        path: The file to read (ENTRIES_FILE when None).

    Returns:
        The entries in the order of the file.

    Raises:
        VocabularyError: The columns, a kind or a value are not as
            expected, or a word appears twice.
    """
    path = ENTRIES_FILE if path is None else path
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames != ENTRY_COLUMNS:
            raise VocabularyError(f"unexpected columns in {path}")
        entries = []
        seen = set()
        for row in reader:
            entry = Entry(**{c: row[c] or "" for c in ENTRY_COLUMNS})
            if not all((entry.word, entry.reading, entry.meaning)):
                raise VocabularyError(f"empty value: {entry.word}")
            if entry.kind not in KINDS:
                raise VocabularyError(f"unknown kind: {entry.word}")
            if entry.word in seen:
                raise VocabularyError(f"two entries for {entry.word}")
            seen.add(entry.word)
            entries.append(entry)
    return entries


def kanji_variants(path: Path | None = None) -> dict[str, list[str]]:
    """Reads the old forms of each modern form from kanji.tsv.

    Args:
        path: The file to read (KANJI_FILE when None).

    Returns:
        The old forms by modern form.
    """
    path = KANJI_FILE if path is None else path
    variants: dict[str, list[str]] = {}
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        for row in reader:
            variants.setdefault(row["modern"], []).append(row["old"])
    return variants


def one_to_one_pairs(path: Path | None = None) -> list[tuple[str, str]]:
    """Reads the pairs of kanji.tsv that are converted one to one.

    Args:
        path: The file to read (KANJI_FILE when None).

    Returns:
        (modern, old) for each joyo row whose one_to_many is 0.
    """
    path = KANJI_FILE if path is None else path
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        return [
            (row["modern"], row["old"])
            for row in reader
            if row["kind"] == "joyo" and row["one_to_many"] == "0"
        ]


def search_key(word: str) -> str:
    """Returns the part of a headword that must appear in its example.

    The fixed part before the first 〜 (after a leading 〜), without a
    leading スル before コト. The same rule as test_references.py.

    Args:
        word: A headword such as 「須ク〜スヘシ」 or 「〜スルコト能ハス」.

    Returns:
        The part to search for.
    """
    key = word.removeprefix("〜").split("〜")[0]
    return key.removeprefix("スル") if key.startswith("スルコト") else key


def forms_of(word: str, kanji: dict[str, list[str]]) -> list[str]:
    """Returns the spellings of a word in modern and old kanji forms.

    Args:
        word: A headword.
        kanji: The old forms by modern form (kanji_variants()).

    Returns:
        Every combination of modern and old forms of the search key.
    """
    options = [[ch, *kanji.get(ch, [])] for ch in search_key(word)]
    return ["".join(p) for p in itertools.product(*options)]


def text_pages(
    work: Path,
    rows: Iterable[fetch_corpus.Revision],
    one_to_one: Iterable[tuple[str, str]] = (),
) -> list[TextPage]:
    """Reads the revisions that may give examples, in the order to search.

    A page has new forms when its only transcription is in a 新字体
    section, or when its text looks written over in new forms
    (clean_text.looks_modernized()), as 政体 is without any heading.

    Args:
        work: The work directory of fetch_corpus.py.
        rows: The rows of corpus_revisions.tsv.
        one_to_one: (modern, old) pairs with one old form each
            (one_to_one_pairs()).

    Returns:
        The pages whose usage is all or text_only, sorted by whether the
        transcription has new forms, then year, title and revision ID.

    Raises:
        VocabularyError: A revision is not saved or has another SHA-1.
    """
    ranges = clean_text.read_line_exclusions()
    pages = []
    for row in rows:
        if row.usage not in TEXT_USAGES:
            continue
        content = fetch_corpus.read_saved_page(work, row.oldid)
        if content is None or fetch_corpus.content_sha1(content) != row.sha1:
            raise VocabularyError(
                f"revision {row.oldid} is not fetched;"
                " run fetch_corpus.py fetch"
            )
        rest = clean_text.split_header(content)[1]
        original = clean_text.remove_editor_sections(
            rest, keep_only_new_forms=False
        )
        lines = clean_text.vocabulary_lines(content, ranges.get(row.title, ()))
        new_forms = not clean_text.has_text(
            original
        ) or clean_text.looks_modernized("\n".join(lines), one_to_one)
        pages.append(TextPage(row, new_forms, lines))
    pages.sort(
        key=lambda p: (p.new_forms, p.row.year, p.row.title, p.row.oldid)
    )
    return pages


def _segment(line: str, position: int, form: str, entry: Entry) -> str:
    after = AFTER_CONSTRUCTION if "〜" in entry.word else AFTER
    start = max(0, position - BEFORE)
    end = min(len(line), position + len(form) + after)
    segment = line[start:end]
    offset = position - start
    cut = max(segment.rfind(ch, 0, offset) for ch in _SENTENCE_BREAKS)
    # An earlier match that was skipped (part of a longer word, or a word
    # of the same form) must not come first in the example, so that the
    # first occurrence of the form is the one chosen.
    earlier = segment.rfind(form, 0, offset)
    if earlier != -1:
        cut = max(cut, earlier + len(form) - 1)
    if cut != -1:
        segment = segment[cut + 1 :]
    return segment.strip(" " + SPACE)


def _matches(line: str, form: str, entry: Entry) -> Iterable[int]:
    position = line.find(form)
    while position != -1:
        before = line[position - 1] if position > 0 else ""
        after = line[position + len(form) : position + len(form) + 1]
        # A word that starts with a kanji must not be the end of a longer
        # kanji word, such as 故ニ in 事故ニ.
        inside = bool(_HAN.match(form[0]) and before and _HAN.match(before))
        excluded = (before and before in entry.exclude_prev) or (
            after and after in entry.exclude_next
        )
        if not inside and not excluded:
            yield position
        position = line.find(form, position + 1)


def find_in(
    lines: Iterable[str], forms: list[str], entry: Entry
) -> tuple[str, str] | None:
    """Finds the first usable example of a word in lines.

    Args:
        lines: The lines of a page.
        forms: The spellings to look for (forms_of()).
        entry: The entry, for its exclusions.

    Returns:
        (form, example), or None when the page has no usable example.
    """
    for line in lines:
        found = sorted(
            (position, form)
            for form in forms
            for position in _matches(line, form, entry)
        )
        for position, form in found:
            segment = _segment(line, position, form, entry)
            if len(segment) < len(form) + 4:
                continue
            if _DIGITS.search(segment) or _CHAPTER_HEAD.match(segment):
                continue
            return form, segment
    return None


def build_rows(
    entries: Iterable[Entry],
    pages: list[TextPage],
    kanji: dict[str, list[str]],
) -> tuple[list[list[str]], list[str]]:
    """Builds the rows of vocabulary.tsv.

    Args:
        entries: The rows of vocabulary_entries.tsv.
        pages: The pages from text_pages().
        kanji: The old forms by modern form.

    Returns:
        The rows in the order of the entries, and the words without an
        example.
    """
    rows = []
    missing = []
    for entry in entries:
        forms = forms_of(entry.word, kanji)
        for page in pages:
            found = find_in(page.lines, forms, entry)
            if found is None:
                continue
            form, example = found
            rows.append(
                [
                    entry.word,
                    entry.reading,
                    entry.kind,
                    entry.meaning,
                    example,
                    form,
                    f"{page.row.title}（{page.row.year}）",
                    REVISION_URL.format(page.row.oldid),
                ]
            )
            break
        else:
            missing.append(entry.word)
    return rows, missing


def render(rows: Iterable[list[str]]) -> str:
    """Returns the text of vocabulary.tsv with LF line ends.

    Args:
        rows: The rows from build_rows().

    Returns:
        The text of the file.

    Raises:
        VocabularyError: A value has a tab or a line break.
    """
    lines = ["\t".join(VOCABULARY_COLUMNS)]
    for row in rows:
        if any("\t" in v or "\n" in v or "\r" in v for v in row):
            raise VocabularyError(f"tab or line break in a value: {row[0]}")
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"


def write_atomically(text: str, path: Path) -> None:
    """Replaces a file at once, so that a failure keeps the old file.

    Args:
        text: The new content.
        path: The file to replace.
    """
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as target:
            target.write(text)
        # mkstemp makes the file readable by its owner only.
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Builds or checks vocabulary.tsv; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 on success, 1 on an error, when a word has no example, or when
        --check finds a difference.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        work = fetch_corpus.check_work_dir(args.work)
        revisions = fetch_corpus.read_revisions()
        pages = text_pages(work, revisions, one_to_one_pairs())
        rows, missing = build_rows(read_entries(), pages, kanji_variants())
        if missing:
            raise VocabularyError(f"words without an example: {missing}")
        text = render(rows)
        if args.check:
            if VOCABULARY_FILE.read_text(encoding="utf-8") != text:
                print("vocabulary.tsv differs from the build", file=sys.stderr)
                return 1
        else:
            write_atomically(text, VOCABULARY_FILE)
    except (
        VocabularyError,
        fetch_corpus.CorpusError,
        OSError,
        ValueError,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{len(rows)} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
