"""Fetches the Wikisource pages and the Unihan data used to build the tables.

Run from the repository root. WORK must be outside the repository: the
fetched texts include editors' notes under CC BY-SA and are not committed.

Usage:
    python tools/kundoku-bungo/fetch_corpus.py list --work WORK
    python tools/kundoku-bungo/fetch_corpus.py revisions --work WORK
    python tools/kundoku-bungo/fetch_corpus.py reclassify --work WORK
    python tools/kundoku-bungo/fetch_corpus.py fetch --work WORK
    python tools/kundoku-bungo/fetch_corpus.py unihan --work WORK

list walks the yearly law categories and writes WORK/titles.json.
revisions fixes the revision of every page in corpus_revisions.tsv and
decides how each page may be used. reclassify decides the usages again
from the pages saved by revisions, without the network. fetch downloads
the revisions in
corpus_revisions.tsv into WORK/pages/. unihan downloads Unihan.zip.

To build the same tables as the repository, run only fetch and unihan:
list, revisions and reclassify are for widening the corpus, and they
change corpus_revisions.tsv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# python -I leaves the script's directory out of sys.path; the scripts
# are run that way because they read downloaded files.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clean_text  # noqa: E402

TOOLS = Path(__file__).resolve().parent
REPOSITORY = TOOLS.parent.parent
SKILL = REPOSITORY / "plugins/kundoku-bungo/skills/kundoku-bungo"
REFERENCES = SKILL / "references"
REVISIONS_FILE = TOOLS / "corpus_revisions.tsv"
REVISION_COLUMNS = ["title", "oldid", "year", "usage", "source", "rule"]
USAGES = ("all", "kanji_only", "examples_only")

API_URL = "https://ja.wikisource.org/w/api.php"
# A generic name: the requests must not carry personal details.
DEFAULT_USER_AGENT = "kundoku-bungo-corpus-tools/0.1 (Python urllib)"
BATCH_SIZE = 50
REQUEST_INTERVAL = 3.0
MAX_ATTEMPTS = 6
DEFAULT_RETRY_WAIT = 60.0

UNIHAN_URL = "https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip"
UNIHAN_SHA256 = (
    "4c93ea9c1f636451729a840978f1667a53886af37ba854fdcce109721c63d43e"
)

# Categories that are not walked: their pages are not used for the
# vocabulary table or the examples (see classify()).
SKIPPED_CATEGORY_WORDS = ("詔勅", "皇室令", "条約", "勅語")
CATEGORY_DEPTH = 2

# Pages of a law that quote a rescript. They are used everywhere, with the
# rescript removed by line_exclusions.tsv.
MIXED_PAGES = frozenset({"徵兵令詔書及ヒ徵兵吿諭"})
# Rescripts, the constitution, the imperial house laws (皇室典範,
# 皇室令, 華族令) and notices about the imperial family. Their wording is
# tied to the emperor even where no line has 朕, so they give kanji forms
# only.
IMPERIAL_TITLE = re.compile(
    "詔|勅語|敕語|勅諭|敕諭|憲法|皇室|華族令|皇族|天皇|皇后|皇太后|皇太子"
    "|皇男子|親王|崩御|大喪|即位|卽位|諡|陵墓|皇居|離宮"
)
IMPERIAL_CATEGORY = re.compile("詔勅|皇室令|勅語|敕語")
# Promulgation numbers of imperial house laws, as in 皇室令第6号 and
# 宮内省達甲第二号. Only the page header is searched: in the body, editors
# link titles of nobility such as 伯爵 to 華族令.
IMPERIAL_LAW_NUMBER = re.compile("皇室令第|宮内省達|宮內省達")
FOREIGN_OR_TREATY = re.compile("満洲|滿洲|満州|条約|條約")
# A date after 1945 in the title marks a later revision, even when the
# header still gives the year of the first version.
POST_WAR_TITLE = re.compile(
    "昭和(?:二十[一二三四五六七八九]|[三四五六]十|2[1-9]|[3-6][0-9])年|平成|令和"
)
HEADER_YEAR = re.compile(r"\|\s*year\s*=\s*(\d{4})")
FIRST_YEAR, LAST_YEAR = 1868, 1945

Opener = Callable[[urllib.request.Request], bytes]


class CorpusError(Exception):
    """Raised when the corpus cannot be fetched or checked."""


@dataclass(frozen=True)
class Revision:
    """One row of corpus_revisions.tsv.

    Attributes:
        title: The page title on Wikisource.
        oldid: The revision ID that the tables are built from.
        year: The year in the page header.
        usage: all, kanji_only or examples_only.
        source: pinned (used by the current tables) or listed (found in
            the categories).
        rule: The rule of classify() that decided the usage.
    """

    title: str
    oldid: int
    year: int
    usage: str
    source: str
    rule: str


def _urlopen(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


class WikisourceApi:
    """A small client for the MediaWiki API of Japanese Wikisource.

    It waits between requests, sends maxlag, and retries after HTTP 429,
    HTTP 503 and maxlag errors, as the Wikimedia API etiquette asks.

    Args:
        user_agent: The User-Agent header to send.
        opener: Sends a request and returns the body. Tests replace it.
        sleep: Waits for a number of seconds. Tests replace it.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        opener: Opener = _urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._user_agent = user_agent
        self._opener = opener
        self._sleep = sleep

    def query(self, params: dict[str, str | int]) -> dict:
        """Sends one POST request and returns the decoded JSON.

        Args:
            params: The API parameters, without format and maxlag.

        Returns:
            The JSON response.

        Raises:
            CorpusError: The request failed MAX_ATTEMPTS times or the API
                returned an error other than maxlag.
        """
        data = urllib.parse.urlencode(
            dict(params, format="json", formatversion=2, maxlag=5)
        ).encode("utf-8")
        request = urllib.request.Request(
            API_URL, data=data, headers={"User-Agent": self._user_agent}
        )
        for _ in range(MAX_ATTEMPTS):
            try:
                response = json.loads(self._opener(request))
            except urllib.error.HTTPError as error:
                if error.code not in (429, 503):
                    raise CorpusError(f"HTTP {error.code}") from error
                self._sleep(_retry_after(error.headers.get("Retry-After")))
                continue
            except urllib.error.URLError:
                self._sleep(DEFAULT_RETRY_WAIT)
                continue
            self._sleep(REQUEST_INTERVAL)
            error_info = response.get("error")
            if error_info is None:
                return response
            if error_info.get("code") != "maxlag":
                raise CorpusError(f"API error: {error_info.get('code')}")
            self._sleep(DEFAULT_RETRY_WAIT)
        raise CorpusError(f"gave up after {MAX_ATTEMPTS} attempts")

    def query_all(self, params: dict[str, str | int]) -> Iterator[dict]:
        """Yields every response of a query, following "continue".

        Args:
            params: The API parameters.

        Yields:
            Each response in turn.
        """
        cont: dict[str, str] = {}
        while True:
            response = self.query(dict(params, **cont))
            yield response
            if "continue" not in response:
                return
            cont = response["continue"]


def _retry_after(value: str | None) -> float:
    try:
        return max(float(value), 1.0) if value else DEFAULT_RETRY_WAIT
    except ValueError:
        return DEFAULT_RETRY_WAIT


def year_roots() -> list[str]:
    """Returns the yearly law categories from 明治元年 to 昭和20年."""

    def year(number: int) -> str:
        return "元" if number == 1 else str(number)

    eras = (("明治", 45), ("大正", 15), ("昭和", 20))
    return [
        f"{era}{year(n)}年の法令"
        for era, last in eras
        for n in range(1, last + 1)
    ]


def list_titles(
    api: WikisourceApi, roots: Iterable[str], depth: int = CATEGORY_DEPTH
) -> dict[str, list[str]]:
    """Walks categories and their subcategories and lists the pages.

    Subcategories whose names contain SKIPPED_CATEGORY_WORDS are not
    walked.

    Args:
        api: The API client.
        roots: The category names to start from, without "Category:".
        depth: How many levels of subcategories to walk.

    Returns:
        The page titles, each with the categories it was found in.
    """
    titles: dict[str, list[str]] = {}
    seen: set[str] = set()
    queue = [(root, 0) for root in roots]
    while queue:
        category, level = queue.pop(0)
        if category in seen:
            continue
        seen.add(category)
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:" + category,
            "cmlimit": 500,
            "cmtype": "page|subcat",
        }
        for response in api.query_all(params):
            for member in response.get("query", {}).get("categorymembers", []):
                if member["ns"] == 0:
                    titles.setdefault(member["title"], []).append(category)
                elif member["ns"] == 14 and level < depth:
                    sub = member["title"].split(":", 1)[1]
                    if not any(w in sub for w in SKIPPED_CATEGORY_WORDS):
                        queue.append((sub, level + 1))
    return titles


@dataclass
class Page:
    """A fetched revision with the categories of its page."""

    title: str
    oldid: int
    content: str
    categories: list[str]


def fetch_pages(
    api: WikisourceApi,
    *,
    titles: Iterable[str] = (),
    revids: Iterable[int] = (),
) -> list[Page]:
    """Fetches the content and all categories of pages.

    Pages are asked for in batches of BATCH_SIZE, and "continue" is
    followed so that no category is lost. Missing pages are left out.

    Args:
        api: The API client.
        titles: Titles whose latest revisions are fetched.
        revids: Revision IDs to fetch.

    Returns:
        The fetched pages.
    """
    batches = [("titles", list(titles)), ("revids", [str(r) for r in revids])]
    # Keyed by title: a continued response may bring only categories.
    contents: dict[str, dict[int, str]] = {}
    categories: dict[str, list[str]] = {}
    for key, values in batches:
        for start in range(0, len(values), BATCH_SIZE):
            params = {
                "action": "query",
                "prop": "revisions|categories",
                "rvprop": "ids|content",
                "rvslots": "main",
                "cllimit": "max",
                key: "|".join(values[start : start + BATCH_SIZE]),
            }
            for response in api.query_all(params):
                for item in response.get("query", {}).get("pages", []):
                    if item.get("missing"):
                        continue
                    title = item["title"]
                    found = contents.setdefault(title, {})
                    for revision in item.get("revisions", []):
                        slot = revision["slots"]["main"]
                        found[revision["revid"]] = slot["content"]
                    names = categories.setdefault(title, [])
                    for category in item.get("categories", []):
                        name = category["title"].split(":", 1)[1]
                        if name not in names:
                            names.append(name)
    return [
        Page(title, oldid, content, list(categories.get(title, [])))
        for title, revisions in contents.items()
        for oldid, content in sorted(revisions.items())
    ]


def header_year(content: str) -> int | None:
    """Returns the year in the page header, if there is one."""
    match = HEADER_YEAR.search(content)
    return int(match.group(1)) if match else None


def classify(
    title: str, categories: Iterable[str], content: str
) -> tuple[str | None, str]:
    """Decides how a page may be used by the tables.

    When in doubt the page is not used for the vocabulary table and the
    examples: a page of an unknown year is left out, and an imperial
    rescript or house law is used only for kanji forms.

    Args:
        title: The page title.
        categories: All categories of the page.
        content: The wikitext of the page.

    Returns:
        The usage (None when the page is left out) and the rule that
        decided it.
    """
    categories = list(categories)
    if title in MIXED_PAGES:
        return "all", "mixed"
    if FOREIGN_OR_TREATY.search(title) or any(
        FOREIGN_OR_TREATY.search(c) for c in categories
    ):
        return None, "foreign-or-treaty"
    year = header_year(content)
    if year is None:
        return None, "no-year"
    if not FIRST_YEAR <= year <= LAST_YEAR or POST_WAR_TITLE.search(title):
        return None, "out-of-period"
    if IMPERIAL_TITLE.search(title):
        return "kanji_only", "imperial-title"
    if any(IMPERIAL_CATEGORY.search(c) for c in categories):
        return "kanji_only", "imperial-category"
    header = content[: len(content) - len(clean_text.body(content))]
    if IMPERIAL_LAW_NUMBER.search(title) or IMPERIAL_LAW_NUMBER.search(header):
        return "kanji_only", "imperial-number"
    return "all", "law"


def pinned_oldids(references: Path = REFERENCES) -> dict[int, str]:
    """Reads the revisions that the current tables cite.

    Args:
        references: The references directory of the skill.

    Returns:
        Each revision ID with the usage it needs: examples_only when only
        examples.md cites it, otherwise all (classify() may still make it
        kanji_only).
    """
    pattern = re.compile(r"oldid=(\d+)")
    tables = set()
    for name in ("kanji.tsv", "vocabulary.tsv"):
        text = (references / name).read_text(encoding="utf-8")
        tables.update(int(m) for m in pattern.findall(text))
    examples = {
        int(m)
        for m in pattern.findall(
            (references / "examples.md").read_text(encoding="utf-8")
        )
    }
    result = {oldid: "all" for oldid in tables}
    for oldid in examples - tables:
        result[oldid] = "examples_only"
    return result


def build_revisions(
    pinned: list[Page], pinned_usage: dict[int, str], listed: list[Page]
) -> tuple[list[Revision], list[tuple[str, str]]]:
    """Decides the rows of corpus_revisions.tsv.

    A pinned revision is always kept, so that the current tables keep
    their sources; when the same page is also listed, the pinned revision
    wins. kanji_only wins over the other usages.

    Args:
        pinned: The revisions the current tables cite.
        pinned_usage: The usage each pinned revision needs.
        listed: The latest revisions of the pages found in the categories.

    Returns:
        The rows sorted by title, and the (title, rule) of each listed
        page that was left out.
    """
    rows: dict[str, Revision] = {}
    for page in pinned:
        usage, rule = classify(page.title, page.categories, page.content)
        if usage != "kanji_only":
            usage = pinned_usage[page.oldid]
        if page.title in rows:
            raise CorpusError(f"two pinned revisions of {page.title}")
        year = header_year(page.content) or 0
        rows[page.title] = Revision(
            page.title, page.oldid, year, usage, "pinned", rule
        )
    left_out = []
    for page in listed:
        usage, rule = classify(page.title, page.categories, page.content)
        if page.title in rows:
            if usage == "kanji_only" and rows[page.title].usage != usage:
                old = rows[page.title]
                rows[page.title] = Revision(
                    old.title, old.oldid, old.year, usage, old.source, rule
                )
            continue
        if usage is None:
            left_out.append((page.title, rule))
            continue
        year = header_year(page.content) or 0
        rows[page.title] = Revision(
            page.title, page.oldid, year, usage, "listed", rule
        )
    return sorted(rows.values(), key=lambda r: r.title), sorted(left_out)


def write_revisions(rows: Iterable[Revision], path: Path) -> None:
    """Writes corpus_revisions.tsv with LF line ends."""
    with open(path, "w", encoding="utf-8", newline="\n") as target:
        target.write("\t".join(REVISION_COLUMNS) + "\n")
        for row in rows:
            values = [
                row.title,
                str(row.oldid),
                str(row.year),
                row.usage,
                row.source,
                row.rule,
            ]
            if any("\t" in v or "\n" in v for v in values):
                raise CorpusError(f"tab or newline in a value: {row.title}")
            target.write("\t".join(values) + "\n")


def read_revisions(path: Path = REVISIONS_FILE) -> list[Revision]:
    """Reads corpus_revisions.tsv.

    Raises:
        CorpusError: The columns or a usage are not the expected ones.
    """
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames != REVISION_COLUMNS:
            raise CorpusError(f"unexpected columns in {path}")
        rows = []
        for line in reader:
            if line["usage"] not in USAGES:
                raise CorpusError(f"unknown usage: {line['usage']}")
            rows.append(
                Revision(
                    line["title"],
                    int(line["oldid"]),
                    int(line["year"]),
                    line["usage"],
                    line["source"],
                    line["rule"],
                )
            )
    return rows


def check_work_dir(work: Path, repository: Path = REPOSITORY) -> Path:
    """Makes sure that the work directory is outside the repository.

    Args:
        work: The directory for fetched files.
        repository: The repository root.

    Returns:
        The resolved work directory.

    Raises:
        CorpusError: The directory is the repository or inside it.
    """
    resolved = work.resolve()
    root = repository.resolve()
    if resolved == root or root in resolved.parents:
        raise CorpusError(
            f"the work directory must be outside the repository: {work}"
        )
    return resolved


def save_page(work: Path, page: Page) -> None:
    """Saves a revision as WORK/pages/<oldid>.wiki."""
    folder = work / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{page.oldid}.wiki"
    path.write_text(page.content, encoding="utf-8", newline="\n")


def download_unihan(
    work: Path, opener: Opener = _urlopen, user_agent: str = DEFAULT_USER_AGENT
) -> Path:
    """Downloads Unihan.zip and checks its SHA-256.

    Args:
        work: The work directory.
        opener: Sends a request and returns the body. Tests replace it.
        user_agent: The User-Agent header to send.

    Returns:
        The path of the saved file.

    Raises:
        CorpusError: The file does not have the expected SHA-256.
    """
    path = work / "Unihan.zip"
    if path.exists() and _sha256(path.read_bytes()) == UNIHAN_SHA256:
        return path
    request = urllib.request.Request(
        UNIHAN_URL, headers={"User-Agent": user_agent}
    )
    data = opener(request)
    if _sha256(data) != UNIHAN_SHA256:
        raise CorpusError("Unihan.zip does not have the expected SHA-256")
    path.write_bytes(data)
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _command_list(api: WikisourceApi, work: Path) -> None:
    titles = list_titles(api, year_roots())
    (work / "titles.json").write_text(
        json.dumps(titles, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    print(f"{len(titles)} pages", file=sys.stderr)


def _command_revisions(api: WikisourceApi, work: Path) -> None:
    titles_path = work / "titles.json"
    if not titles_path.exists():
        raise CorpusError("run the list command first")
    titles = json.loads(titles_path.read_text(encoding="utf-8"))
    usage = pinned_oldids()
    pinned = fetch_pages(api, revids=sorted(usage))
    missing = set(usage) - {page.oldid for page in pinned}
    if missing:
        raise CorpusError(f"pinned revisions not found: {sorted(missing)}")
    listed = fetch_pages(api, titles=sorted(titles))
    for page in pinned + listed:
        save_page(work, page)
    save_page_index(work, pinned, listed)
    _write_classification(work, usage, pinned, listed)


def save_page_index(
    work: Path, pinned: list[Page], listed: list[Page]
) -> None:
    """Saves the titles and categories of the fetched pages.

    With WORK/pages.json the reclassify command can decide the usages
    again without fetching the pages.
    """
    index = {
        "pinned": [[p.title, p.oldid, p.categories] for p in pinned],
        "listed": [[p.title, p.oldid, p.categories] for p in listed],
    }
    (work / "pages.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def load_page_index(work: Path) -> tuple[list[Page], list[Page]]:
    """Loads WORK/pages.json and the saved contents of its pages.

    Raises:
        CorpusError: The revisions command has not been run.
    """
    path = work / "pages.json"
    if not path.exists():
        raise CorpusError("run the revisions command first")
    index = json.loads(path.read_text(encoding="utf-8"))

    def pages(key: str) -> list[Page]:
        result = []
        for title, oldid, categories in index[key]:
            text = (work / "pages" / f"{oldid}.wiki").read_text(
                encoding="utf-8"
            )
            result.append(Page(title, oldid, text, categories))
        return result

    return pages("pinned"), pages("listed")


def _command_reclassify(work: Path) -> None:
    pinned, listed = load_page_index(work)
    _write_classification(work, pinned_oldids(), pinned, listed)


def _write_classification(
    work: Path, usage: dict[int, str], pinned: list[Page], listed: list[Page]
) -> None:
    rows, left_out = build_revisions(pinned, usage, listed)
    write_revisions(rows, REVISIONS_FILE)
    with open(
        work / "left_out.tsv", "w", encoding="utf-8", newline="\n"
    ) as target:
        for title, rule in left_out:
            target.write(f"{title}\t{rule}\n")
    print(f"{len(rows)} rows, {len(left_out)} left out", file=sys.stderr)


def _command_fetch(api: WikisourceApi, work: Path) -> None:
    rows = read_revisions()
    wanted = [
        row.oldid
        for row in rows
        if not (work / "pages" / f"{row.oldid}.wiki").exists()
    ]
    pages = fetch_pages(api, revids=wanted)
    for page in pages:
        save_page(work, page)
    missing = set(wanted) - {page.oldid for page in pages}
    if missing:
        raise CorpusError(f"revisions not found: {sorted(missing)}")
    print(f"{len(pages)} fetched, {len(rows)} in total", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Runs one command; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        choices=("list", "revisions", "reclassify", "fetch", "unihan"),
    )
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args(argv)
    try:
        work = check_work_dir(args.work)
        work.mkdir(parents=True, exist_ok=True)
        api = WikisourceApi(args.user_agent)
        if args.command == "list":
            _command_list(api, work)
        elif args.command == "revisions":
            _command_revisions(api, work)
        elif args.command == "reclassify":
            _command_reclassify(work)
        elif args.command == "fetch":
            _command_fetch(api, work)
        else:
            download_unihan(work, user_agent=args.user_agent)
    except CorpusError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
