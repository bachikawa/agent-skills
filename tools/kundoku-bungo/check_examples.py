"""Checks the quotations of examples.md against the pinned revisions.

Run from the repository root after fetch_corpus.py fetch:

    python -I tools/kundoku-bungo/check_examples.py --work WORK

examples.md is written by a person. Each section quotes a passage (文語)
and cites one revision of Wikisource. The check finds, for each section,
the cited revision in corpus_revisions.tsv and its saved copy in WORK,
and makes sure that the passage is in one line of the text that
clean_text.vocabulary_lines() keeps, so that the passage is the
transcription as it is, without editors' notes, stenographers' notes or
wording only for the emperor.

The exit code is 1 when a section fails.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# python -I leaves the script's directory out of sys.path; the scripts
# are run that way because they read downloaded files.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clean_text  # noqa: E402
import fetch_corpus  # noqa: E402

EXAMPLES_FILE = fetch_corpus.REFERENCES / "examples.md"
EXAMPLE_USAGES = frozenset({"all", "text_only", "examples_only"})
# Keep the form of a section in step with EXAMPLE_SECTION in the skill's
# scripts/test_references.py; the skill is shipped alone, so they cannot
# share the pattern.
SECTION = re.compile(
    r"^### (?P<id>[公演新]-\d+) .*?\n\n文語:\n\n```text\n(?P<bungo>.+?)\n```"
    r".*?(?P<notes>^- 出典: [^\n]*)",
    re.S | re.M,
)
OLDID = re.compile(r"oldid=(\d+)")


@dataclass(frozen=True)
class Example:
    """A section of examples.md.

    Attributes:
        id: The section ID, such as 公-1.
        bungo: The quoted passage.
        oldid: The cited revision ID.
    """

    id: str
    bungo: str
    oldid: int


def read_examples(text: str) -> list[Example]:
    """Reads the sections of examples.md.

    Args:
        text: The content of examples.md.

    Returns:
        The sections in the order of the file.

    Raises:
        ValueError: A section has no source line or not one revision ID,
            or a heading of a section is not matched.
    """
    headings = re.findall(r"^### ", text, re.M)
    examples = []
    for match in SECTION.finditer(text):
        oldids = OLDID.findall(match["notes"])
        if len(oldids) != 1:
            raise ValueError(f"{match['id']}: cite one revision")
        examples.append(Example(match["id"], match["bungo"], int(oldids[0])))
    if len(examples) != len(headings):
        raise ValueError("a section has no passage or no source line")
    return examples


def check(
    examples: Iterable[Example],
    rows: Iterable[fetch_corpus.Revision],
    work: Path,
    ranges: Mapping[str, Sequence[tuple[str, str]]] | None = None,
) -> list[str]:
    """Checks each passage against its cited revision.

    Args:
        examples: The sections of examples.md.
        rows: The rows of corpus_revisions.tsv.
        work: The work directory of fetch_corpus.py.
        ranges: The ranges of lines to remove, by page title
            (clean_text.read_line_exclusions() when None).

    Returns:
        A message for each section that fails; empty when all pass.
    """
    by_oldid = {row.oldid: row for row in rows}
    if ranges is None:
        ranges = clean_text.read_line_exclusions()
    errors = []
    for example in examples:
        row = by_oldid.get(example.oldid)
        if row is None or row.usage not in EXAMPLE_USAGES:
            errors.append(
                f"{example.id}: revision {example.oldid} is not pinned"
                " for the examples"
            )
            continue
        content = fetch_corpus.read_saved_page(work, row.oldid)
        if content is None or fetch_corpus.content_sha1(content) != row.sha1:
            errors.append(
                f"{example.id}: revision {row.oldid} is not fetched;"
                " run fetch_corpus.py fetch"
            )
            continue
        lines = clean_text.vocabulary_lines(content, ranges.get(row.title, ()))
        if not any(example.bungo in line for line in lines):
            errors.append(
                f"{example.id}: the passage is not in revision {row.oldid}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    """Checks examples.md; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 when every section passes, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        work = fetch_corpus.check_work_dir(args.work)
        examples = read_examples(EXAMPLES_FILE.read_text(encoding="utf-8"))
        errors = check(examples, fetch_corpus.read_revisions(), work)
    except (fetch_corpus.CorpusError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    for error in errors:
        print(error, file=sys.stderr)
    print(f"{len(examples)} sections, {len(errors)} failed", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
