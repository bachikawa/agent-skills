"""Fetches the Wikisource pages and the Unihan data used to build the tables.

Run from the repository root. WORK must be outside the repository: the
fetched texts include editors' notes under CC BY-SA and are not committed.

Usage:
    python -I tools/kundoku-bungo/fetch_corpus.py list --work WORK
    python -I tools/kundoku-bungo/fetch_corpus.py revisions --work WORK
    python -I tools/kundoku-bungo/fetch_corpus.py reclassify --work WORK
    python -I tools/kundoku-bungo/fetch_corpus.py fetch --work WORK
    python -I tools/kundoku-bungo/fetch_corpus.py unihan --work WORK

list walks the yearly law categories and writes WORK/titles.json.
revisions fixes the revision of every page in corpus_revisions.tsv and
decides how each page may be used. reclassify decides the usages again
from the pages saved by revisions, without the network. fetch downloads
the revisions in corpus_revisions.tsv into WORK/pages/ and checks their
SHA-1. unihan downloads Unihan.zip and checks its SHA-256.

To build the same tables as the repository, run only fetch and unihan:
list, revisions and reclassify are for widening the corpus, and they
change corpus_revisions.tsv.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import http.client
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
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
REVISION_COLUMNS = [
    "title",
    "oldid",
    "sha1",
    "year",
    "usage",
    "source",
    "rule",
]
USAGES = ("all", "kanji_only", "examples_only")
SHA1_PATTERN = re.compile("[0-9a-f]{40}")

API_URL = "https://ja.wikisource.org/w/api.php"
# A generic name: the requests must not carry personal details.
DEFAULT_USER_AGENT = "kundoku-bungo-corpus-tools/0.1"
BATCH_SIZE = 50
REQUEST_INTERVAL = 3.0
MAX_ATTEMPTS = 6
DEFAULT_RETRY_WAIT = 60.0
MAX_RETRY_WAIT = 300.0
RETRIED_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
# Revisions saved in this many days before running revisions are listed
# in WORK/recent_edits.tsv for a person to check for vandalism.
RECENT_DAYS = 90

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
    "|皇男子|親王|崩御|大喪|即位|卽位|諡|陵墓|皇居|離宮|御名|御諱|御紋|菊章"
    "|御影"
)
IMPERIAL_CATEGORY = re.compile("詔勅|皇室令|勅語|敕語")
# Promulgation numbers of imperial house laws, as in 皇室令第6号 and
# 宮内省達甲第二号. Only the page header is searched: in the body, editors
# link titles of nobility such as 伯爵 to 華族令.
IMPERIAL_LAW_NUMBER = re.compile("皇室令第|宮内省達|宮內省達")
# Treaties and other documents made with foreign governments, and
# documents of governments other than Japan's.
FOREIGN_OR_TREATY = re.compile(
    "満洲|滿洲|満州|滿州|条約|條約|協約|協定|議定書|覚書|覺書|降伏文書"
    "|宣言(?:/公布時)?$"
)
_KANJI_DIGIT = "一二三四五六七八九"
# A date after 1945 in the title marks a later revision, even when the
# header still gives the year of the first version.
POST_WAR_TITLE = re.compile(
    f"昭和(?:二十[{_KANJI_DIGIT}]|[三四五六]十[{_KANJI_DIGIT}]?"
    "|2[1-9]|[3-6][0-9])年|平成|令和"
)
# Pages that show the law as amended today, such as 刑法 (日本).
CURRENT_VERSION = re.compile("現行法令掲載|最終改正")
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
        sha1: The SHA-1 of the revision's wikitext in UTF-8, as the API
            gives it.
        year: The year in the page header.
        usage: all, kanji_only or examples_only.
        source: pinned (used by the current tables) or listed (found in
            the categories).
        rule: The rule that decided the usage (see classify()).
    """

    title: str
    oldid: int
    sha1: str
    year: int
    usage: str
    source: str
    rule: str


@dataclass
class Page:
    """A fetched revision with the categories of its page.

    Attributes:
        title: The page title.
        oldid: The revision ID.
        content: The wikitext of the revision.
        categories: All categories of the page, without "Category:".
        timestamp: When the revision was saved, as the API gives it.
        user: Who saved the revision; empty when unknown.
        anonymous: Whether the revision was saved without an account.
    """

    title: str
    oldid: int
    content: str
    categories: list[str]
    timestamp: str = ""
    user: str = ""
    anonymous: bool = False
    sha1: str = field(init=False)

    def __post_init__(self) -> None:
        self.sha1 = content_sha1(self.content)


def content_sha1(content: str) -> str:
    """Returns the SHA-1 that MediaWiki gives for a revision's wikitext.

    Args:
        content: The wikitext.

    Returns:
        The SHA-1 of the wikitext in UTF-8, in hexadecimal.
    """
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _urlopen(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


class WikisourceApi:
    """A small client for the MediaWiki API of Japanese Wikisource.

    It waits between requests, sends maxlag, and retries after HTTP 429
    and 5xx errors, network errors, broken responses and maxlag errors, as
    the Wikimedia API etiquette asks.

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
            CorpusError: The request failed MAX_ATTEMPTS times, the server
                refused it, or the API returned an error other than maxlag.
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
                if error.code not in RETRIED_HTTP_CODES:
                    raise CorpusError(f"HTTP {error.code}") from error
                self._sleep(retry_wait(error.headers.get("Retry-After")))
                continue
            except (OSError, http.client.HTTPException, ValueError):
                # Network errors, cut responses and bodies that are not
                # JSON (such as an HTML error page) are worth a retry.
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


def retry_wait(value: str | None) -> float:
    """Returns how long to wait after a Retry-After header.

    Args:
        value: The header value, if any.

    Returns:
        The seconds to wait, between REQUEST_INTERVAL and MAX_RETRY_WAIT.
        DEFAULT_RETRY_WAIT when the value is missing or not a number.
    """
    try:
        seconds = float(value) if value else DEFAULT_RETRY_WAIT
    except ValueError:
        return DEFAULT_RETRY_WAIT
    if not math.isfinite(seconds):
        return DEFAULT_RETRY_WAIT
    return min(max(seconds, REQUEST_INTERVAL), MAX_RETRY_WAIT)


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


def fetch_pages(
    api: WikisourceApi,
    *,
    titles: Iterable[str] = (),
    revids: Iterable[int] = (),
    on_batch: Callable[[list[Page]], None] | None = None,
) -> list[Page]:
    """Fetches the content and all categories of pages.

    Pages are asked for in batches of BATCH_SIZE, and "continue" is
    followed so that no category is lost. Missing pages and revisions
    whose text is hidden are left out.

    Args:
        api: The API client.
        titles: Titles whose latest revisions are fetched.
        revids: Revision IDs to fetch.
        on_batch: Called with the pages of each batch as soon as it is
            fetched, so that a later failure does not lose them.

    Returns:
        The fetched pages.

    Raises:
        CorpusError: The API gave a revision ID that is not a positive
            integer, or a SHA-1 that does not match the text.
    """
    batches = [("titles", list(titles)), ("revids", [str(r) for r in revids])]
    result: list[Page] = []
    for key, values in batches:
        for start in range(0, len(values), BATCH_SIZE):
            params = {
                "action": "query",
                "prop": "revisions|categories",
                "rvprop": "ids|sha1|timestamp|user|content",
                "rvslots": "main",
                "cllimit": "max",
                key: "|".join(values[start : start + BATCH_SIZE]),
            }
            pages = _collect_pages(api.query_all(params))
            if on_batch is not None:
                on_batch(pages)
            result.extend(pages)
    return result


def _collect_pages(responses: Iterable[dict]) -> list[Page]:
    # Keyed by title: a continued response may bring only categories.
    revisions: dict[str, dict[int, dict]] = {}
    categories: dict[str, list[str]] = {}
    for response in responses:
        for item in response.get("query", {}).get("pages", []):
            if item.get("missing"):
                continue
            title = item["title"]
            found = revisions.setdefault(title, {})
            for revision in item.get("revisions", []):
                oldid = revision.get("revid")
                if not isinstance(oldid, int) or isinstance(oldid, bool):
                    raise CorpusError(f"unexpected revision ID: {oldid!r}")
                if oldid <= 0:
                    raise CorpusError(f"unexpected revision ID: {oldid!r}")
                if "content" in revision.get("slots", {}).get("main", {}):
                    found[oldid] = revision
            names = categories.setdefault(title, [])
            for category in item.get("categories", []):
                name = category["title"].split(":", 1)[1]
                if name not in names:
                    names.append(name)
    pages = []
    for title, found in revisions.items():
        for oldid, revision in sorted(found.items()):
            page = Page(
                title,
                oldid,
                revision["slots"]["main"]["content"],
                list(categories.get(title, [])),
                revision.get("timestamp", ""),
                revision.get("user", ""),
                bool(revision.get("anon")),
            )
            if revision.get("sha1") != page.sha1:
                raise CorpusError(f"SHA-1 does not match: {oldid}")
            pages.append(page)
    return pages


def header_year(content: str) -> int | None:
    """Returns the year in the page header.

    Args:
        content: The wikitext of a page.

    Returns:
        The year in the Header template, or None when there is none.
    """
    header, _ = clean_text.split_header(content)
    match = HEADER_YEAR.search(header)
    return int(match.group(1)) if match else None


def imperial_rule(
    title: str, categories: Iterable[str], content: str
) -> str | None:
    """Tells whether a page is a rescript or an imperial house law.

    Args:
        title: The page title.
        categories: All categories of the page.
        content: The wikitext of the page.

    Returns:
        The rule that matched, or None.
    """
    if IMPERIAL_TITLE.search(title):
        return "imperial-title"
    if any(IMPERIAL_CATEGORY.search(c) for c in categories):
        return "imperial-category"
    header, _ = clean_text.split_header(content)
    if IMPERIAL_LAW_NUMBER.search(title) or IMPERIAL_LAW_NUMBER.search(header):
        return "imperial-number"
    return None


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
    header, _ = clean_text.split_header(content)
    if CURRENT_VERSION.search(header):
        return None, "current-version"
    year = header_year(content)
    if year is None:
        return None, "no-year"
    if not FIRST_YEAR <= year <= LAST_YEAR or POST_WAR_TITLE.search(title):
        return None, "out-of-period"
    rule = imperial_rule(title, categories, content)
    if rule is not None:
        return "kanji_only", rule
    return "all", "default"


def pinned_oldids(references: Path = REFERENCES) -> dict[int, str]:
    """Reads the revisions that the current tables cite.

    Args:
        references: The references directory of the skill.

    Returns:
        Each revision ID with the usage it needs: examples_only when only
        examples.md cites it, otherwise all (an imperial page still
        becomes kanji_only; see build_revisions()).
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


def _revision(page: Page, usage: str, source: str, rule: str) -> Revision:
    year = header_year(page.content) or 0
    return Revision(
        page.title, page.oldid, page.sha1, year, usage, source, rule
    )


def build_revisions(
    pinned: list[Page], pinned_usage: dict[int, str], listed: list[Page]
) -> tuple[list[Revision], list[tuple[str, str]]]:
    """Decides the rows of corpus_revisions.tsv.

    A pinned revision is always kept, so that the current tables keep
    their sources; when the same page is also listed, the pinned revision
    wins. kanji_only wins over the other usages. A pinned page is checked
    for imperial wording apart from its year, so that a rescript without
    a year in its header still becomes kanji_only.

    Args:
        pinned: The revisions the current tables cite.
        pinned_usage: The usage each pinned revision needs.
        listed: The latest revisions of the pages found in the categories.

    Returns:
        The rows sorted by title, and the (title, rule) of each listed
        page that was left out.

    Raises:
        CorpusError: Two pinned revisions belong to the same page.
    """
    rows: dict[str, Revision] = {}
    for page in pinned:
        if page.title in rows:
            raise CorpusError(f"two pinned revisions of {page.title}")
        rule = imperial_rule(page.title, page.categories, page.content)
        if page.title in MIXED_PAGES:
            usage, rule = pinned_usage[page.oldid], "mixed"
        elif rule is not None:
            usage = "kanji_only"
        else:
            usage, rule = pinned_usage[page.oldid], "pinned"
        rows[page.title] = _revision(page, usage, "pinned", rule)
    left_out = []
    for page in listed:
        usage, rule = classify(page.title, page.categories, page.content)
        if page.title in rows:
            old = rows[page.title]
            if usage == "kanji_only" and old.usage != usage:
                rows[page.title] = Revision(
                    old.title,
                    old.oldid,
                    old.sha1,
                    old.year,
                    usage,
                    old.source,
                    rule,
                )
            continue
        if usage is None:
            left_out.append((page.title, rule))
            continue
        rows[page.title] = _revision(page, usage, "listed", rule)
    return sorted(rows.values(), key=lambda r: r.title), sorted(left_out)


def write_revisions(rows: Iterable[Revision], path: Path) -> None:
    """Writes corpus_revisions.tsv with LF line ends.

    All rows are checked before anything is written, and the file is
    replaced at once, so that a failure leaves the old file as it was.

    Args:
        rows: The rows to write.
        path: The file to write.

    Raises:
        CorpusError: A value has a tab or a line break.
    """
    lines = ["\t".join(REVISION_COLUMNS)]
    for row in rows:
        values = [
            row.title,
            str(row.oldid),
            row.sha1,
            str(row.year),
            row.usage,
            row.source,
            row.rule,
        ]
        if any("\t" in v or "\n" in v or "\r" in v for v in values):
            raise CorpusError(f"tab or line break in a value: {row.title}")
        lines.append("\t".join(values))
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as target:
            target.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_revisions(path: Path = REVISIONS_FILE) -> list[Revision]:
    """Reads corpus_revisions.tsv.

    Args:
        path: The file to read.

    Returns:
        The rows in the order of the file.

    Raises:
        CorpusError: The columns, a usage, a number or a SHA-1 is not as
            expected.
    """
    with open(path, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames != REVISION_COLUMNS:
            raise CorpusError(f"unexpected columns in {path}")
        rows = []
        for line in reader:
            if line["usage"] not in USAGES:
                raise CorpusError(f"unknown usage: {line['usage']}")
            if not SHA1_PATTERN.fullmatch(line["sha1"] or ""):
                raise CorpusError(f"unexpected SHA-1: {line['title']}")
            try:
                oldid, year = int(line["oldid"]), int(line["year"])
            except (TypeError, ValueError) as error:
                raise CorpusError(f"not a number: {line['title']}") from error
            rows.append(
                Revision(
                    line["title"],
                    oldid,
                    line["sha1"],
                    year,
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


def page_path(work: Path, oldid: int) -> Path:
    """Returns WORK/pages/<oldid>.wiki."""
    return work / "pages" / f"{oldid}.wiki"


def save_page(work: Path, page: Page) -> None:
    """Saves a revision as WORK/pages/<oldid>.wiki.

    Args:
        work: The work directory.
        page: The revision to save.
    """
    path = page_path(work, page.oldid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page.content, encoding="utf-8", newline="")


def read_saved_page(work: Path, oldid: int) -> str | None:
    """Reads a saved revision exactly as it was saved.

    Args:
        work: The work directory.
        oldid: The revision ID.

    Returns:
        The wikitext, or None when it has not been saved.
    """
    path = page_path(work, oldid)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", newline="")


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


def save_page_index(
    work: Path, pinned: list[Page], listed: list[Page]
) -> None:
    """Saves the titles, categories and authors of the fetched pages.

    With WORK/pages.json the reclassify command can decide the usages
    again without fetching the pages.

    Args:
        work: The work directory.
        pinned: The pinned revisions.
        listed: The listed revisions.
    """

    def entry(page: Page) -> dict:
        return {
            "title": page.title,
            "oldid": page.oldid,
            "categories": page.categories,
            "timestamp": page.timestamp,
            "user": page.user,
            "anonymous": page.anonymous,
        }

    index = {
        "pinned": [entry(p) for p in pinned],
        "listed": [entry(p) for p in listed],
    }
    (work / "pages.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def load_page_index(work: Path) -> tuple[list[Page], list[Page]]:
    """Loads WORK/pages.json and the saved contents of its pages.

    Args:
        work: The work directory.

    Returns:
        The pinned and the listed revisions.

    Raises:
        CorpusError: The revisions command has not been run, or a saved
            page is missing.
    """
    path = work / "pages.json"
    if not path.exists():
        raise CorpusError("run the revisions command first")
    index = json.loads(path.read_text(encoding="utf-8"))

    def pages(key: str) -> list[Page]:
        result = []
        for entry in index[key]:
            content = read_saved_page(work, entry["oldid"])
            if content is None:
                raise CorpusError(f"saved page not found: {entry['oldid']}")
            result.append(
                Page(
                    entry["title"],
                    entry["oldid"],
                    content,
                    entry["categories"],
                    entry.get("timestamp", ""),
                    entry.get("user", ""),
                    entry.get("anonymous", False),
                )
            )
        return result

    return pages("pinned"), pages("listed")


def recent_edits(
    pages: Iterable[Page], now: datetime.datetime, days: int = RECENT_DAYS
) -> list[Page]:
    """Picks the revisions a person should check for vandalism.

    Args:
        pages: The listed revisions.
        now: The current time, with a time zone.
        days: How recent a revision must be to be picked.

    Returns:
        The revisions saved without an account or within the last days,
        or with an unknown time, sorted by title.
    """
    limit = now - datetime.timedelta(days=days)
    picked = []
    for page in pages:
        try:
            saved = datetime.datetime.fromisoformat(page.timestamp)
        except ValueError:
            picked.append(page)
            continue
        if page.anonymous or saved >= limit:
            picked.append(page)
    return sorted(picked, key=lambda p: p.title)


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

    def save_all(pages: list[Page]) -> None:
        for page in pages:
            save_page(work, page)

    pinned = fetch_pages(api, revids=sorted(usage), on_batch=save_all)
    missing = set(usage) - {page.oldid for page in pinned}
    if missing:
        raise CorpusError(f"pinned revisions not found: {sorted(missing)}")
    listed = fetch_pages(api, titles=sorted(titles), on_batch=save_all)
    save_page_index(work, pinned, listed)
    _write_classification(work, usage, pinned, listed)
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = recent_edits(listed, now)
    with open(
        work / "recent_edits.tsv", "w", encoding="utf-8", newline="\n"
    ) as target:
        for page in recent:
            user = "(anonymous)" if page.anonymous else page.user
            target.write(
                f"{page.title}\t{page.oldid}\t{page.timestamp}\t{user}\n"
            )
    print(f"{len(recent)} recent edits to check", file=sys.stderr)


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


def check_fetched(rows: Iterable[Revision], pages: Iterable[Page]) -> None:
    """Checks fetched revisions against corpus_revisions.tsv.

    Args:
        rows: The rows of corpus_revisions.tsv.
        pages: The fetched revisions.

    Raises:
        CorpusError: A revision has another title or SHA-1 than its row.
    """
    by_oldid = {row.oldid: row for row in rows}
    for page in pages:
        row = by_oldid.get(page.oldid)
        if row is None or row.title != page.title or row.sha1 != page.sha1:
            raise CorpusError(f"fetched revision does not match: {page.oldid}")


def _command_fetch(api: WikisourceApi, work: Path) -> None:
    rows = read_revisions()
    wanted = []
    for row in rows:
        content = read_saved_page(work, row.oldid)
        # A saved file that was changed or cut is fetched again.
        if content is None or content_sha1(content) != row.sha1:
            wanted.append(row.oldid)

    def check_and_save(pages: list[Page]) -> None:
        check_fetched(rows, pages)
        for page in pages:
            save_page(work, page)

    pages = fetch_pages(api, revids=wanted, on_batch=check_and_save)
    missing = set(wanted) - {page.oldid for page in pages}
    if missing:
        raise CorpusError(f"revisions not found: {sorted(missing)}")
    print(f"{len(pages)} fetched, {len(rows)} in total", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Runs one command; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The exit code: 0 on success, 1 on an error.
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
