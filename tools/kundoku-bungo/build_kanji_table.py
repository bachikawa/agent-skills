"""Builds the kanji table (kanji.tsv) of the skill kundoku-bungo.

Run from the repository root after fetch_corpus.py fetch and unihan:

    python -I tools/kundoku-bungo/build_kanji_table.py --work WORK
    python -I tools/kundoku-bungo/build_kanji_table.py --work WORK --check

The candidates are the pairs of a modern form and its old form in Unihan
(kJapaneseOldVariant). A pair goes into the table only when its old form
is found in the original text of a revision in corpus_revisions.tsv whose
usage is all or kanji_only. The evidence is the earliest such revision
(by year, then title, then revision ID), so the choice depends only on
the corpus, not on the table that was built before.

kanji_overrides.tsv gives what Unihan and the corpus cannot: which pairs
need a choice by word (one_to_many), the note for that choice, and pairs
of design variants checked on images (kind design).

With --check the table is built in memory and compared with kanji.tsv;
the exit code is 1 when they differ.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# python -I leaves the script's directory out of sys.path; the scripts
# are run that way because they read downloaded files.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clean_text  # noqa: E402
import fetch_corpus  # noqa: E402

KANJI_FILE = fetch_corpus.REFERENCES / "kanji.tsv"
OVERRIDES_FILE = fetch_corpus.TOOLS / "kanji_overrides.tsv"
KANJI_COLUMNS = [
    "modern",
    "old",
    "old_codepoint",
    "kind",
    "one_to_many",
    "note",
    "evidence",
    "evidence_url",
]
OVERRIDE_COLUMNS = [
    "modern",
    "old",
    "kind",
    "one_to_many",
    "note",
    "evidence",
    "evidence_url",
]
EVIDENCE_USAGES = frozenset({"all", "kanji_only"})
REVISION_URL = "https://ja.wikisource.org/w/index.php?oldid={}"


class TableError(Exception):
    """Raised when the kanji table cannot be built."""


@dataclass(frozen=True)
class Override:
    """One row of kanji_overrides.tsv.

    Attributes:
        modern: The modern form.
        old: The old form.
        kind: Empty to keep the kind from the code point, or design for a
            design variant that is not in Unihan.
        one_to_many: "1" when the old form must be chosen by word.
        note: How to choose, for a one_to_many row.
        evidence: The source of a design row.
        evidence_url: The URL of that source.
    """

    modern: str
    old: str
    kind: str
    one_to_many: str
    note: str
    evidence: str
    evidence_url: str


def unihan_pairs(unihan_zip: Path) -> list[tuple[str, str, bool]]:
    """Reads the modern and old forms from Unihan.

    Args:
        unihan_zip: Unihan.zip of UCD 18.0.0.

    Returns:
        (modern, old, has_several_old_forms) for each pair of
        kJapaneseOldVariant.
    """
    with zipfile.ZipFile(unihan_zip) as archive:
        text = archive.read("Unihan_Variants.txt").decode("utf-8")
    pairs = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        codepoint, field, value = line.split("\t", 2)
        if field != "kJapaneseOldVariant":
            continue
        modern = chr(int(codepoint[2:], 16))
        olds = [chr(int(v.split("<")[0][2:], 16)) for v in value.split()]
        pairs.extend((modern, old, len(olds) > 1) for old in olds)
    return pairs


def read_overrides(
    path: Path = OVERRIDES_FILE,
) -> dict[tuple[str, str], Override]:
    """Reads kanji_overrides.tsv.

    Args:
        path: The file to read.

    Returns:
        The overrides by (modern, old).

    Raises:
        TableError: The columns or a value are not as expected.
    """
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames != OVERRIDE_COLUMNS:
            raise TableError(f"unexpected columns in {path}")
        overrides = {}
        for row in reader:
            override = Override(**{c: row[c] or "" for c in OVERRIDE_COLUMNS})
            key = (override.modern, override.old)
            if key in overrides:
                raise TableError(f"two overrides for {key}")
            if override.kind not in ("", "design"):
                raise TableError(f"unknown kind: {override.kind}")
            if override.one_to_many not in ("", "0", "1"):
                raise TableError(f"unexpected one_to_many: {key}")
            if override.kind == "design" and not (
                override.evidence and override.evidence_url
            ):
                raise TableError(f"design row without evidence: {key}")
            overrides[key] = override
    return overrides


def evidence_pages(
    work: Path, rows: Iterable[fetch_corpus.Revision]
) -> list[tuple[fetch_corpus.Revision, str]]:
    """Reads the revisions that may attest kanji forms.

    Args:
        work: The work directory of fetch_corpus.py.
        rows: The rows of corpus_revisions.tsv.

    Returns:
        Each usable row with its lines joined, sorted by year, title and
        revision ID.

    Raises:
        TableError: A revision is not saved or has another SHA-1.
    """
    pages = []
    for row in rows:
        if row.usage not in EVIDENCE_USAGES:
            continue
        content = fetch_corpus.read_saved_page(work, row.oldid)
        if content is None or fetch_corpus.content_sha1(content) != row.sha1:
            raise TableError(
                f"revision {row.oldid} is not fetched;"
                " run fetch_corpus.py fetch"
            )
        pages.append((row, "\n".join(clean_text.kanji_lines(content))))
    pages.sort(key=lambda p: (p[0].year, p[0].title, p[0].oldid))
    return pages


def build_rows(
    pairs: Iterable[tuple[str, str, bool]],
    pages: list[tuple[fetch_corpus.Revision, str]],
    overrides: dict[tuple[str, str], Override],
) -> tuple[list[list[str]], list[tuple[str, str]]]:
    """Builds the rows of kanji.tsv.

    Args:
        pairs: The pairs from unihan_pairs().
        pages: The pages from evidence_pages().
        overrides: The rows of kanji_overrides.tsv.

    Returns:
        The rows sorted by the code points of the modern and the old form,
        and the pairs that were not found in the corpus.

    Raises:
        TableError: A one_to_many row has no note, or an override does not
            match any row.
    """
    rows = []
    missing = []
    used = set()
    for modern, old, several in pairs:
        page = next((p for p, text in pages if old in text), None)
        if page is None:
            missing.append((modern, old))
            continue
        override = overrides.get((modern, old))
        if override is not None:
            used.add((modern, old))
        kind = "compat" if 0xF900 <= ord(old) <= 0xFAFF else "joyo"
        one_to_many = "1" if several else "0"
        note = ""
        if override is not None:
            one_to_many = override.one_to_many or one_to_many
            note = override.note
        rows.append(
            [
                modern,
                old,
                f"U+{ord(old):04X}",
                kind,
                one_to_many,
                note,
                f"{page.title}（{page.year}）",
                REVISION_URL.format(page.oldid),
            ]
        )
    for key, override in overrides.items():
        if override.kind != "design":
            continue
        used.add(key)
        rows.append(
            [
                override.modern,
                override.old,
                f"U+{ord(override.old):04X}",
                "design",
                override.one_to_many or "1",
                override.note,
                override.evidence,
                override.evidence_url,
            ]
        )
    unused = sorted(set(overrides) - used)
    if unused:
        raise TableError(f"overrides that match no row: {unused}")
    without_note = [(r[0], r[1]) for r in rows if r[4] == "1" and not r[5]]
    if without_note:
        raise TableError(f"one_to_many rows without a note: {without_note}")
    rows.sort(key=lambda r: (ord(r[0]), ord(r[1])))
    return rows, sorted(missing, key=lambda p: (ord(p[0]), ord(p[1])))


def render(rows: Iterable[list[str]]) -> str:
    """Returns the text of kanji.tsv with LF line ends.

    Raises:
        TableError: A value has a tab or a line break.
    """
    lines = ["\t".join(KANJI_COLUMNS)]
    for row in rows:
        if any("\t" in v or "\n" in v or "\r" in v for v in row):
            raise TableError(f"tab or line break in a value: {row[:2]}")
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"


def write_atomically(text: str, path: Path) -> None:
    """Replaces a file at once, so that a failure keeps the old file."""
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
    """Builds or checks kanji.tsv; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 on success, 1 on an error or when --check finds a difference.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        work = fetch_corpus.check_work_dir(args.work)
        unihan = fetch_corpus.download_unihan(
            work, opener=_no_download
        )
        pages = evidence_pages(work, fetch_corpus.read_revisions())
        rows, missing = build_rows(
            unihan_pairs(unihan), pages, read_overrides()
        )
        text = render(rows)
    except (
        TableError,
        fetch_corpus.CorpusError,
        OSError,
        ValueError,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    if args.check:
        current = KANJI_FILE.read_text(encoding="utf-8")
        if current != text:
            print("kanji.tsv differs from the build", file=sys.stderr)
            return 1
    else:
        write_atomically(text, KANJI_FILE)
        with open(
            work / "kanji_missing.tsv", "w", encoding="utf-8", newline="\n"
        ) as target:
            for modern, old in missing:
                target.write(f"{modern}\t{old}\tU+{ord(old):04X}\n")
    print(f"{len(rows)} rows, {len(missing)} pairs not found", file=sys.stderr)
    return 0


def _no_download(request: object) -> bytes:
    raise fetch_corpus.CorpusError(
        "Unihan.zip is not fetched; run fetch_corpus.py unihan"
    )


if __name__ == "__main__":
    sys.exit(main())
