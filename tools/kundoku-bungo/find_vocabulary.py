"""Counts words in the corpus to find candidates for the vocabulary table.

Run from the repository root after fetch_corpus.py fetch:

    python -I tools/kundoku-bungo/find_vocabulary.py --work WORK

Writes WORK/vocabulary_candidates.tsv, which a person reads to choose the
words of vocabulary_entries.tsv. Two kinds of candidates are counted:

- words that start a clause: one or two kanji followed by katakana, at
  the start of a line or after 。, a space or a comma, and followed by a
  kanji. Adverbs and conjunctions of kanbun kundoku, such as 苟モ and
  加之, are found this way, mixed with verbs that a person leaves out.
- the words already in vocabulary_entries.tsv, so that their counts can
  be compared.

The count is the number of distinct texts a word appears in, not the
number of times: the corpus holds many amended versions of the same law,
and counting every occurrence would rank their wording too high.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from collections.abc import Iterable
from pathlib import Path

# python -I leaves the script's directory out of sys.path; the scripts
# are run that way because they read downloaded files.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_vocabulary  # noqa: E402
import fetch_corpus  # noqa: E402

KANJI = (
    f"々〻{chr(0x3400)}-{chr(0x4DBF)}{chr(0x4E00)}-{chr(0x9FFF)}"
    f"{chr(0xF900)}-{chr(0xFAFF)}"
)
CLAUSE_WORD = re.compile(
    f"(?:^|(?<=[。、 {chr(0x3000)}]))([{KANJI}]{{1,2}}[ァ-ヶ]{{1,3}})(?=[{KANJI}])"
)
CANDIDATES_FILE = "vocabulary_candidates.tsv"
TOP = 300
# A kanji stem followed by katakana anywhere in a line.
STEM_WITH_KANA = re.compile(f"([{KANJI}]{{1,2}})([ァ-ヶ]{{1,3}})")
# An adverb or conjunction keeps the same kana after its kanji (苟モ, 悉ク),
# while a noun takes many particles (本令ハ, 本令ニ, 本令ノ). A stem counts
# as fixed when one kana covers this share of its occurrences.
FIXED_SHARE = 0.9
# Particles that follow nouns; a stem whose fixed kana is one of them is a
# noun with its usual particle, not a word of kanbun kundoku.
NOUN_PARTICLES = frozenset("ノハヲトヘ")


def clause_words(lines: Iterable[str]) -> set[str]:
    """Returns the clause-initial words of a text.

    Args:
        lines: The lines of a page.

    Returns:
        The distinct words found.
    """
    found = set()
    for line in lines:
        found.update(m.group(1) for m in CLAUSE_WORD.finditer(line))
    return found


def count_texts(
    texts: Iterable[tuple[str, list[str]]],
) -> collections.Counter[str]:
    """Counts in how many distinct texts each clause-initial word appears.

    Args:
        texts: (key, lines) for each page; pages with the same key are
            counted once.

    Returns:
        The number of distinct texts for each word.
    """
    seen: dict[str, set[str]] = {}
    for key, lines in texts:
        for word in clause_words(lines):
            seen.setdefault(word, set()).add(key)
    return collections.Counter({w: len(keys) for w, keys in seen.items()})


def fixed_kana_words(
    texts: Iterable[tuple[str, list[str]]],
) -> collections.Counter[str]:
    """Counts the words whose kanji keep the same kana after them.

    Only stems that start a word (not preceded by a kanji) are counted.

    Args:
        texts: (key, lines) for each page; pages with the same key are
            counted once.

    Returns:
        The number of distinct texts for each fixed word, such as 悉ク.
    """
    kana: dict[str, collections.Counter[str]] = {}
    keys: dict[str, set[str]] = {}
    seen_texts: set[str] = set()
    for key, lines in texts:
        if key in seen_texts:
            continue
        seen_texts.add(key)
        for line in lines:
            for match in STEM_WITH_KANA.finditer(line):
                start = match.start()
                if start > 0 and re.match(f"[{KANJI}]", line[start - 1]):
                    continue
                stem, after = match.group(1), match.group(2)
                kana.setdefault(stem, collections.Counter())[after[0]] += 1
                keys.setdefault(stem, set()).add(key)
    words: collections.Counter[str] = collections.Counter()
    for stem, counter in kana.items():
        first, count = counter.most_common(1)[0]
        if first in NOUN_PARTICLES:
            continue
        if count / sum(counter.values()) < FIXED_SHARE:
            continue
        words[stem + first] = len(keys[stem])
    return words


def text_key(lines: list[str]) -> str:
    """Returns a key that is the same for pages with the same text."""
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Writes the candidates; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 on success, 1 on an error.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--top", type=int, default=TOP)
    args = parser.parse_args(argv)
    try:
        work = fetch_corpus.check_work_dir(args.work)
        rows = fetch_corpus.read_revisions()
        pages = build_vocabulary.text_pages(work, rows)
        counts = count_texts((text_key(p.lines), p.lines) for p in pages)
        fixed = fixed_kana_words((text_key(p.lines), p.lines) for p in pages)
        entries = build_vocabulary.read_entries()
        kanji = build_vocabulary.kanji_variants()
        known = {}
        for entry in entries:
            forms = build_vocabulary.forms_of(entry.word, kanji)
            keys = {
                text_key(p.lines)
                for p in pages
                if build_vocabulary.find_in(p.lines, forms, entry) is not None
            }
            known[entry.word] = len(keys)
        path = work / CANDIDATES_FILE
        with open(path, "w", encoding="utf-8", newline="\n") as target:
            target.write("kind\tword\ttexts\n")
            for word, count in sorted(known.items(), key=lambda i: -i[1]):
                target.write(f"entry\t{word}\t{count}\n")
            ranked = sorted(counts.items(), key=lambda i: (-i[1], i[0]))
            for word, count in ranked[: args.top]:
                target.write(f"clause\t{word}\t{count}\n")
            ranked = sorted(fixed.items(), key=lambda i: (-i[1], i[0]))
            for word, count in ranked[: args.top]:
                target.write(f"fixed\t{word}\t{count}\n")
    except (
        build_vocabulary.VocabularyError,
        fetch_corpus.CorpusError,
        OSError,
        ValueError,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{len(counts)} clause words, {len(known)} entries", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
