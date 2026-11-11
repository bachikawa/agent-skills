"""Counts words in the corpus to find candidates for the vocabulary table.

Run from the repository root after fetch_corpus.py fetch:

    python -I tools/kundoku-bungo/find_vocabulary.py --work WORK

Writes WORK/vocabulary_candidates.tsv, which a person reads to choose the
words of vocabulary_entries.tsv. Three kinds of rows are written:

- entry: the words already in vocabulary_entries.tsv, with the number of
  documents that give an example of them.
- fixed: one or two kanji whose following kana is the same in nine
  tenths of their uses or more (only the first kana is compared), not
  counting the particles that follow nouns. Adverbs and conjunctions of
  kanbun kundoku, such as 悉ク and 毫モ, rank high this way.
- clause: one or two kanji followed by katakana, at the start of a line
  or after 。, a space or a comma, and followed by a kanji. Most of them
  are nouns with a particle; a person picks the few useful ones.

The count is the number of documents a word appears in, not the number
of times: the corpus holds many amended versions of the same law (see
law_key()), and counting every version would rank their wording too high.
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


def law_key(title: str) -> str:
    """Returns a key that is the same for the versions of one document.

    Amended versions of a law have titles such as 「國債整理基金特別會計法
    (大正八年…)」 and 「地所質入書入規則/公布時」, so the part before 「 (」
    or 「/」 names the document.

    Args:
        title: A page title.

    Returns:
        The title without the part that tells the version.
    """
    return re.split(r" \(|/", title, maxsplit=1)[0]


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
        counts = count_texts((law_key(p.row.title), p.lines) for p in pages)
        fixed = fixed_kana_words(
            (law_key(p.row.title), p.lines) for p in pages
        )
        entries = build_vocabulary.read_entries()
        kanji = build_vocabulary.kanji_variants()
        known = {}
        for entry in entries:
            forms = build_vocabulary.forms_of(entry.word, kanji)
            keys = {
                law_key(p.row.title)
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
