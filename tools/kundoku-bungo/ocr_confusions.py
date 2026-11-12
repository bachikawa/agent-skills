"""Counts OCR misreadings in pre-war books of the National Diet Library.

The ground truth is the PDM OCR training dataset of the National Diet
Library (ndl-lab/pdmocrdataset-part1): the text of each line of 2,713
page images, entered as seen. The OCR output is the text that the
Next Digital Library (次世代デジタルライブラリー) gives for the same
pages. Lines are paired by their boxes on the page, and the characters
of each pair are aligned to count which character the OCR wrote for
which.

The OCR was trained on this dataset, so the counts show which
misreadings occur, not how often the OCR misreads unseen pages. The
pages next to a dataset page were not in the dataset; fetch can get some
of them so that count can compare the two kinds of pages.

Run from the repository root. WORK must be outside the repository: the
texts are not committed (only the counts are).

Usage:
    python -I tools/kundoku-bungo/ocr_confusions.py annotations --work WORK
    python -I tools/kundoku-bungo/ocr_confusions.py fetch --work WORK
    python -I tools/kundoku-bungo/ocr_confusions.py count --work WORK

annotations takes only the annotation files out of the dataset zip with
HTTP range requests (about 12 MB of the 1.8 GB zip) and checks their
CRC-32. fetch downloads the OCR text of the selected pages into
WORK/ndl/pages/ and records each in ocr_pages.tsv; pages recorded there
are fetched again only when missing, and must have the same SHA-1.
count reads the saved files and writes ocr_confusions.tsv.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import difflib
import hashlib
import http.client
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

# python -I leaves the script's directory out of sys.path; the scripts
# are run that way because they read downloaded files.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_corpus  # noqa: E402

TOOLS = Path(__file__).resolve().parent
PAGES_FILE = TOOLS / "ocr_pages.tsv"
CONFUSIONS_FILE = TOOLS / "ocr_confusions.tsv"
PAGE_COLUMNS = ["pid", "frame", "role", "folder", "fetched", "sha1"]
CONFUSION_COLUMNS = [
    "kind",
    "expected",
    "found",
    "before_kind",
    "after_kind",
    "count",
    "pages",
    "books",
]
ROLES = ("dataset", "neighbor")

# The PascalVOC 1.1 form of the dataset. Its size and ETag are pinned
# so that a changed zip stops annotations.
ZIP_URL = (
    "https://lab.ndl.go.jp/dataset/pdm_ocr_dataset/line/"
    "tosho_all_pascalvoc1.1.zip"
)
ZIP_SIZE = 1879948576
ZIP_ETAG = '"035f1a63b89f2b644b85c287e30f9d83-110"'
# The central directory and the last folder fit in this many bytes at
# the end of the zip.
TAIL_SIZE = 4 * 1024 * 1024
# PascalVOC coordinates are half those of the page images.
VOC_SCALE = 2.0
PAGE_URL = "https://lab.ndl.go.jp/dl/api/page/{pid}_{frame}"
ANNOTATION_NAME = re.compile(r"(\d+)_R(\d{7})\.xml")
# Folder names in the zip, such as tosho_1920_bunkei. They become part of
# local paths and of ocr_pages.tsv, so anything else stops annotations.
FOLDER_NAME = re.compile(r"[A-Za-z0-9_]+")
# Upper bounds that keep a wrong answer from filling the memory: one
# range request, one response body, and one annotation file.
MAX_RANGE = 16 * 1024 * 1024
MAX_BODY = 32 * 1024 * 1024
MAX_ANNOTATION = 1024 * 1024

# A page counts as written in the pre-war official style when its
# ground truth has at least this many characters, nearly all of its
# kana are katakana and it has many kanji.
MIN_PAGE_CHARS = 200
MIN_KATAKANA_SHARE = 0.9
MIN_KANJI_SHARE = 0.4
DEFAULT_NEIGHBORS = 200
# Lines are paired when their boxes overlap this much (intersection
# over union), and compared only when this similar.
MIN_BOX_OVERLAP = 0.5
MIN_LINE_SIMILARITY = 0.8
# Replacements longer than this are not read as misread characters.
MAX_REPLACE = 2
# Longer sides of an "other" row are written as their length (<12>), so
# that the table does not carry runs of the page text.
MAX_OTHER = 4
GETA = "〓"
# The ground truth writes ▣ where no character was entered (the dataset
# README does not say why; the OCR often reads a plain kanji there).
PLACEHOLDER = "▣"
# A book whose page answers HTTP 500 this many times in a row is taken
# as not held by the Next Digital Library.
UNAVAILABLE_AFTER = 2
CHECKPOINT_EVERY = 50

Opener = Callable[[urllib.request.Request], "Response"]


class OcrDataError(Exception):
    """Raised when the data cannot be fetched or does not match."""


class Unavailable(OcrDataError):
    """Raised when the server keeps answering HTTP 500 for a URL.

    The Next Digital Library answers 500 for books it does not hold
    (it holds only part of the digitized books), so a repeated 500 means
    that the page is not available rather than that the server is busy.
    """


@dataclass(frozen=True)
class Response:
    """The parts of an HTTP response that the client uses."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class Line:
    """One line of text with its box on the page image."""

    text: str
    box: tuple[float, float, float, float]


@dataclass(frozen=True, order=True)
class PageRef:
    """A page of a book, by its PID and frame number."""

    pid: str
    frame: int
    folder: str = ""


@dataclass(frozen=True)
class Confusion:
    """One difference between the ground truth and the OCR output."""

    kind: str
    expected: str
    found: str
    before_kind: str
    after_kind: str


def _urlopen(request: urllib.request.Request) -> Response:
    with urllib.request.urlopen(request, timeout=300) as response:
        headers = dict(response.headers.items())
        # A server that ignores Range sends the whole 1.8 GB zip: stop
        # before reading it.
        if request.has_header("Range") and response.status != 206:
            return Response(response.status, headers, b"")
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise OcrDataError(
                f"response over {MAX_BODY} bytes: {request.full_url}"
            )
        return Response(response.status, headers, body)


class NdlClient:
    """Sends GET requests to the National Diet Library with care.

    It waits fetch_corpus.REQUEST_INTERVAL seconds after every request and
    retries after the same errors as the Wikisource client.

    Args:
        user_agent: The User-Agent header to send.
        opener: Sends a request and returns the response. Tests replace it.
        sleep: Waits for a number of seconds. Tests replace it.
    """

    def __init__(
        self,
        user_agent: str = fetch_corpus.DEFAULT_USER_AGENT,
        opener: Opener = _urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._user_agent = user_agent
        self._opener = opener
        self._sleep = sleep

    def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        """Sends one GET request.

        Args:
            url: The URL.
            headers: Extra headers, such as Range.

        Returns:
            The response.

        Raises:
            Unavailable: The server answered HTTP 500 UNAVAILABLE_AFTER
                times in a row.
            OcrDataError: The request failed fetch_corpus.MAX_ATTEMPTS
                times or the server refused it.
        """
        request = urllib.request.Request(
            url, headers=dict(headers or {}, **{"User-Agent": self._user_agent})
        )
        server_errors = 0
        for _ in range(fetch_corpus.MAX_ATTEMPTS):
            try:
                response = self._opener(request)
            except urllib.error.HTTPError as error:
                if error.code not in fetch_corpus.RETRIED_HTTP_CODES:
                    raise OcrDataError(f"HTTP {error.code}: {url}") from error
                server_errors = server_errors + 1 if error.code == 500 else 0
                if server_errors >= UNAVAILABLE_AFTER:
                    self._sleep(fetch_corpus.REQUEST_INTERVAL)
                    raise Unavailable(f"HTTP 500 again: {url}") from error
                # A 500 usually means a missing book, not a busy server,
                # so it is tried once more after the usual interval.
                self._sleep(
                    fetch_corpus.REQUEST_INTERVAL
                    if error.code == 500
                    else fetch_corpus.retry_wait(error.headers.get("Retry-After"))
                )
                continue
            except (OSError, http.client.HTTPException):
                self._sleep(fetch_corpus.DEFAULT_RETRY_WAIT)
                continue
            self._sleep(fetch_corpus.REQUEST_INTERVAL)
            return response
        raise OcrDataError(
            f"gave up after {fetch_corpus.MAX_ATTEMPTS} attempts: {url}"
        )

    def get_range(self, start: int, end: int) -> bytes:
        """Fetches bytes start to end (exclusive) of the dataset zip.

        Args:
            start: The first byte.
            end: The byte after the last one.

        Returns:
            The bytes.

        Raises:
            OcrDataError: The range is outside the zip or longer than
                MAX_RANGE, or the server did not answer with exactly that
                range of the pinned zip.
        """
        if not 0 <= start < end <= ZIP_SIZE or end - start > MAX_RANGE:
            raise OcrDataError(f"bad range {start}-{end}")
        response = self.get(ZIP_URL, {"Range": f"bytes={start}-{end - 1}"})
        headers = {key.lower(): value for key, value in response.headers.items()}
        expected = f"bytes {start}-{end - 1}/{ZIP_SIZE}"
        if response.status != 206 or headers.get("content-range") != expected:
            raise OcrDataError(
                f"the server did not send bytes {start}-{end - 1}"
                " of the pinned zip"
            )
        if headers.get("etag") != ZIP_ETAG:
            raise OcrDataError(f"the zip has changed: {headers.get('etag')}")
        if len(response.body) != end - start:
            raise OcrDataError(f"cut response for bytes {start}-{end - 1}")
        return response.body


class _Regions(io.RawIOBase):
    """A read-only file made of fetched ranges of the zip."""

    def __init__(self, regions: list[tuple[int, bytes]]) -> None:
        self._regions = regions
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position}.get(
            whence, ZIP_SIZE
        )
        self._position = base + offset
        return self._position

    def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
        for start, data in self._regions:
            if start <= self._position < start + len(data):
                chunk = data[self._position - start :][: len(buffer)]
                buffer[: len(chunk)] = chunk
                self._position += len(chunk)
                return len(chunk)
        if self._position >= ZIP_SIZE:
            return 0
        raise OcrDataError(f"byte {self._position} of the zip is not fetched")


def annotation_ranges(
    members: Iterable[zipfile.ZipInfo],
) -> list[tuple[int, int]]:
    """Finds the byte ranges that hold the annotation files, by folder.

    Args:
        members: The members of the zip, from its central directory.

    Returns:
        One (start, end) range per folder, from the local header of its
        first annotation to the next member after its last one. Ranges
        inside the tail, which is fetched with the central directory,
        are left out.

    Raises:
        OcrDataError: The annotations of a folder are not next to each
            other in the zip, or a folder has an unexpected name.
    """
    ordered = sorted(members, key=lambda info: info.header_offset)
    by_folder: dict[str, list[int]] = collections.defaultdict(list)
    for index, info in enumerate(ordered):
        if info.filename.endswith(".xml"):
            by_folder[_folder(info.filename)].append(index)
    tail = ZIP_SIZE - TAIL_SIZE
    ranges = []
    for folder, indexes in by_folder.items():
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise OcrDataError(f"the annotations of {folder} are scattered")
        start = ordered[indexes[0]].header_offset
        if start >= tail:
            continue
        after = indexes[-1] + 1
        end = ordered[after].header_offset if after < len(ordered) else tail
        ranges.append((start, min(end, tail)))
    return ranges


def _folder(filename: str) -> str:
    parts = filename.split("/")
    if len(parts) < 3 or not FOLDER_NAME.fullmatch(parts[1]):
        raise OcrDataError(f"unexpected name in the zip: {filename!r}")
    return parts[1]


def fetch_annotations(work: Path, client: NdlClient) -> int:
    """Saves the annotation files of the dataset in WORK/ndl/annotations/.

    Args:
        work: The work directory.
        client: The HTTP client.

    Returns:
        The number of files saved.

    Raises:
        OcrDataError: A request failed, the zip changed, a file has a
            wrong CRC-32 or is larger than MAX_ANNOTATION, or a name is
            not the expected folder and file name.
    """
    tail_start = ZIP_SIZE - TAIL_SIZE
    regions = [(tail_start, client.get_range(tail_start, ZIP_SIZE))]
    members = zipfile.ZipFile(
        io.BufferedReader(_Regions(regions))
    ).infolist()
    for start, end in annotation_ranges(members):
        regions.append((start, client.get_range(start, end)))
    archive = zipfile.ZipFile(io.BufferedReader(_Regions(regions)))
    saved = 0
    for info in archive.infolist():
        name = Path(info.filename).name
        if not ANNOTATION_NAME.fullmatch(name):
            continue
        if info.file_size > MAX_ANNOTATION:
            raise OcrDataError(f"{info.filename}: {info.file_size} bytes")
        try:
            data = archive.read(info)
        except zipfile.BadZipFile as error:
            raise OcrDataError(f"{info.filename}: {error}") from error
        target = annotation_dir(work) / _folder(info.filename) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        saved += 1
    return saved


def annotation_dir(work: Path) -> Path:
    """Returns WORK/ndl/annotations."""
    return work / "ndl" / "annotations"


def page_file(work: Path, ref: PageRef) -> Path:
    """Returns WORK/ndl/pages/<pid>_<frame>.json."""
    return work / "ndl" / "pages" / f"{ref.pid}_{ref.frame}.json"


def read_annotation(data: bytes) -> list[Line]:
    """Reads the text lines of one PascalVOC annotation file.

    Args:
        data: The XML.

    Returns:
        The lines with text, with boxes at the scale of the page image.
        Regions marked dontcare are left out.
    """
    root = ET.fromstring(data)
    lines = []
    for item in root.iter("object"):
        if item.findtext("name") != "text":
            continue
        text = next(
            (
                attribute.findtext("value") or ""
                for attribute in item.findall("attributes/attribute")
                if attribute.findtext("name") == "contents"
            ),
            "",
        )
        box = item.find("bndbox")
        if not text or box is None:
            continue
        lines.append(
            Line(
                text,
                tuple(
                    float(box.findtext(key) or 0) * VOC_SCALE
                    for key in ("xmin", "ymin", "xmax", "ymax")
                ),
            )
        )
    return lines


def read_ocr_page(data: bytes) -> list[Line]:
    """Reads the lines of a page from the page API of the library.

    Args:
        data: The JSON the API returned.

    Returns:
        The lines with their boxes.

    Raises:
        OcrDataError: The JSON does not have the expected shape.
    """
    try:
        page = json.loads(data)
        items = json.loads(page["coordjson"])
        return [
            Line(
                str(item["contenttext"]),
                tuple(
                    float(item[key])
                    for key in ("xmin", "ymin", "xmax", "ymax")
                ),
            )
            for item in items
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise OcrDataError(f"unexpected page JSON: {error}") from error


def is_bungo_page(text: str) -> bool:
    """Returns whether a page reads as kanji and katakana text.

    Args:
        text: The ground truth of the page.

    Returns:
        True when the page has MIN_PAGE_CHARS characters, katakana make
        up MIN_KATAKANA_SHARE of its kana and kanji MIN_KANJI_SHARE of
        its characters.
    """
    if len(text) < MIN_PAGE_CHARS:
        return False
    katakana = sum(1 for char in text if char_kind(char) == "katakana")
    hiragana = sum(1 for char in text if char_kind(char) == "hiragana")
    kanji = sum(1 for char in text if char_kind(char) == "kanji")
    return (
        katakana > 0
        and katakana / (katakana + hiragana) >= MIN_KATAKANA_SHARE
        and kanji / len(text) >= MIN_KANJI_SHARE
    )


def select_pages(work: Path) -> list[PageRef]:
    """Lists the dataset pages written in the pre-war official style.

    Args:
        work: The work directory with the annotations.

    Returns:
        The pages, sorted by PID and frame.
    """
    pages = []
    for path in sorted(annotation_dir(work).glob("*/*.xml")):
        match = ANNOTATION_NAME.fullmatch(path.name)
        if match is None:
            continue
        lines = read_annotation(path.read_bytes())
        if is_bungo_page("".join(line.text for line in lines)):
            pages.append(
                PageRef(match.group(1), int(match.group(2)), path.parent.name)
            )
    return sorted(pages)


def neighbor_pages(
    selected: list[PageRef], dataset: set[tuple[str, int]], limit: int
) -> list[PageRef]:
    """Lists pages next to selected ones that are not in the dataset.

    Args:
        selected: The selected dataset pages, sorted.
        dataset: Every (PID, frame) in the dataset.
        limit: How many selected pages get their neighbors.

    Returns:
        The frames before and after the first `limit` selected pages,
        leaving out frame 0 and the frames in the dataset.
    """
    found = []
    for ref in selected[:limit]:
        for frame in (ref.frame - 1, ref.frame + 1):
            if frame > 0 and (ref.pid, frame) not in dataset:
                found.append(PageRef(ref.pid, frame, ref.folder))
    return sorted(set(found))


def read_pages_file(path: Path = PAGES_FILE) -> dict[tuple[str, int], dict]:
    """Reads ocr_pages.tsv, keyed by (PID, frame).

    Raises:
        OcrDataError: The file has other columns or an unknown role.
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as source:
        rows = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
        if rows.fieldnames != PAGE_COLUMNS:
            raise OcrDataError(f"unexpected columns in {path}")
        result = {}
        for row in rows:
            if row["role"] not in ROLES:
                raise OcrDataError(f"unknown role in {path}: {row['role']!r}")
            result[(row["pid"], int(row["frame"]))] = row
        return result


def write_tsv(path: Path, columns: list[str], rows: Iterable[dict]) -> None:
    """Writes a tab-separated file with LF line endings.

    Values are written as they are, like the other tables of the tools,
    so that a reader with csv.QUOTE_NONE gets them back unchanged.

    Raises:
        OcrDataError: A value holds a tab or a line break.
    """
    lines = ["\t".join(columns)]
    for row in rows:
        values = [str(row[column]) for column in columns]
        if any(char in value for value in values for char in "\t\r\n"):
            raise OcrDataError(f"a tab or line break in a row of {path.name}")
        lines.append("\t".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")


def fetch_pages(
    work: Path,
    client: NdlClient,
    refs: Iterable[tuple[PageRef, str]],
    pinned: dict[tuple[str, int], dict],
    today: str,
    checkpoint: Callable[[dict[tuple[str, int], dict]], None] | None = None,
    retry_unavailable: bool = False,
) -> dict[tuple[str, int], dict]:
    """Downloads the OCR text of pages that are not saved yet.

    Args:
        work: The work directory.
        client: The HTTP client.
        refs: The pages with their roles (dataset or neighbor).
        pinned: The rows of ocr_pages.tsv.
        today: The date to record for newly fetched pages.
        checkpoint: Called with the rows after every CHECKPOINT_EVERY
            new pages and when the run stops for any reason (an error or
            Ctrl-C), so that an interrupted run keeps its records.
        retry_unavailable: Request again the pages recorded with an
            empty sha1, such as after a passing server fault.

    Returns:
        The rows for ocr_pages.tsv, keyed by (PID, frame). A page of a
        book that the library does not hold has an empty sha1; once a
        page of a book is found missing, the other pages of the book are
        not requested in this run. A page fetched again with the same
        SHA-1 keeps its first fetched date.

    Raises:
        OcrDataError: A request failed, or a page recorded in
            ocr_pages.tsv now has another SHA-1.
    """
    rows = dict(pinned)
    fetched = 0
    missing_books: set[str] = set()
    try:
        for ref, role in refs:
            key = (ref.pid, ref.frame)
            path = page_file(work, ref)
            row = rows.get(key)
            if row and row["sha1"] == "" and not retry_unavailable:
                continue
            if row and path.exists() and _sha1(path.read_bytes()) == row["sha1"]:
                continue
            if ref.pid in missing_books:
                sha1 = ""
            else:
                try:
                    body = client.get(
                        PAGE_URL.format(pid=ref.pid, frame=ref.frame)
                    ).body
                except Unavailable:
                    missing_books.add(ref.pid)
                    body = None
                if body is None:
                    sha1 = ""
                else:
                    read_ocr_page(body)
                    sha1 = _sha1(body)
            if row and row["sha1"] and row["sha1"] != sha1:
                raise OcrDataError(
                    f"page {ref.pid}_{ref.frame} has changed since {row['fetched']}"
                )
            if sha1:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            rows[key] = {
                "pid": ref.pid,
                "frame": str(ref.frame),
                "role": role,
                "folder": ref.folder,
                "fetched": (
                    row["fetched"] if row and row["sha1"] == sha1 else today
                ),
                "sha1": sha1,
            }
            fetched += 1
            if checkpoint is not None and fetched % CHECKPOINT_EVERY == 0:
                checkpoint(rows)
    except BaseException:
        if checkpoint is not None:
            checkpoint(rows)
        raise
    return rows


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def char_kind(char: str) -> str:
    """Names the kind of a character for the context columns.

    Args:
        char: One character, or "" for the edge of a line.

    Returns:
        "edge", "kanji", "katakana", "hiragana", "digit" or "other".
    """
    if not char:
        return "edge"
    code = ord(char)
    if 0x3041 <= code <= 0x3096:
        return "hiragana"
    if 0x30A1 <= code <= 0x30FA:
        return "katakana"
    if char.isdigit():
        return "digit"
    if (
        0x3400 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3FFFF
        or char in "々〆"
    ):
        return "kanji"
    return "other"


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inner = width * height
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
    return inner / (area - inner)


def pair_lines(truth: list[Line], ocr: list[Line]) -> list[tuple[str, str]]:
    """Pairs ground-truth lines with OCR lines by the overlap of their boxes.

    Pairs are taken greedily, the largest overlap first, and every line
    is used at most once.

    Args:
        truth: The ground-truth lines.
        ocr: The OCR lines.

    Returns:
        (ground truth, OCR) text pairs whose boxes overlap at least
        MIN_BOX_OVERLAP, in the order of the ground truth.
    """
    candidates = sorted(
        (
            (_overlap(a.box, b.box), i, j)
            for i, a in enumerate(truth)
            for j, b in enumerate(ocr)
        ),
        reverse=True,
    )
    used_truth: set[int] = set()
    used_ocr: set[int] = set()
    pairs = {}
    for overlap, i, j in candidates:
        if overlap < MIN_BOX_OVERLAP:
            break
        if i in used_truth or j in used_ocr:
            continue
        used_truth.add(i)
        used_ocr.add(j)
        pairs[i] = (truth[i].text, ocr[j].text)
    return [pairs[i] for i in sorted(pairs)]


def line_confusions(expected: str, found: str) -> list[Confusion]:
    """Lists the characters the OCR got wrong in one pair of lines.

    Args:
        expected: The ground truth.
        found: The OCR output.

    Returns:
        Nothing when the lines are less similar than
        MIN_LINE_SIMILARITY. Otherwise one Confusion per character: kind
        "substitution" for a replacement of up to MAX_REPLACE characters
        by as many ("geta" when the OCR wrote 〓, and "variant" when the
        two are the same character under NFKC, such as a CJK
        compatibility ideograph and its unified form), and
        "insertion" or "deletion" for a character the OCR added or left
        out. A ground-truth ▣ replaced or left out is "placeholder",
        since the right character is not known. Longer or uneven replacements are "other". before_kind and
        after_kind are the kinds of the ground-truth characters around.
    """
    matcher = difflib.SequenceMatcher(None, expected, found, autojunk=False)
    if matcher.ratio() < MIN_LINE_SIMILARITY:
        return []
    result = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = _kind_at(expected, i1 - 1)
        after = _kind_at(expected, i2)
        if tag == "replace" and i2 - i1 == j2 - j1 <= MAX_REPLACE:
            for offset, (old, new) in enumerate(
                zip(expected[i1:i2], found[j1:j2])
            ):
                result.append(
                    Confusion(
                        _substitution_kind(old, new),
                        old,
                        new,
                        _kind_at(expected, i1 + offset - 1),
                        _kind_at(expected, i1 + offset + 1),
                    )
                )
        elif tag == "delete":
            for index in range(i1, i2):
                old = expected[index]
                kind = "placeholder" if old == PLACEHOLDER else "deletion"
                result.append(
                    Confusion(
                        kind,
                        old,
                        "",
                        _kind_at(expected, index - 1),
                        _kind_at(expected, index + 1),
                    )
                )
        elif tag == "insert":
            for new in found[j1:j2]:
                result.append(Confusion("insertion", "", new, before, after))
        else:
            result.append(
                Confusion(
                    "other",
                    _shortened(expected[i1:i2]),
                    _shortened(found[j1:j2]),
                    before,
                    after,
                )
            )
    return result


def _kind_at(text: str, index: int) -> str:
    return char_kind(text[index] if 0 <= index < len(text) else "")


def _shortened(text: str) -> str:
    return text if len(text) <= MAX_OTHER else f"<{len(text)}>"


def _substitution_kind(expected: str, found: str) -> str:
    if expected == PLACEHOLDER:
        return "placeholder"
    if found == GETA:
        return "geta"
    if unicodedata.normalize("NFKC", expected) == unicodedata.normalize(
        "NFKC", found
    ):
        return "variant"
    return "substitution"


def count_confusions(
    work: Path, rows: dict[tuple[str, int], dict]
) -> list[dict[str, str]]:
    """Counts the confusions of every dataset page in ocr_pages.tsv.

    Args:
        work: The work directory with the annotations and pages.
        rows: The rows of ocr_pages.tsv.

    Returns:
        The rows for ocr_confusions.tsv, most frequent first. Pages the
        library does not hold (empty sha1) are left out.

    Raises:
        OcrDataError: A page is missing or has another SHA-1.
    """
    counts: collections.Counter[Confusion] = collections.Counter()
    pages: dict[Confusion, set] = collections.defaultdict(set)
    books: dict[Confusion, set] = collections.defaultdict(set)
    for key, row in sorted(rows.items()):
        if row["role"] != "dataset" or not row["sha1"]:
            continue
        ref = PageRef(row["pid"], int(row["frame"]), row["folder"])
        ocr = read_ocr_page(_checked_page(work, ref, row))
        annotation = (
            annotation_dir(work)
            / ref.folder
            / f"{ref.pid}_R{ref.frame:07d}.xml"
        )
        truth = read_annotation(annotation.read_bytes())
        for expected, found in pair_lines(truth, ocr):
            for confusion in line_confusions(expected, found):
                counts[confusion] += 1
                pages[confusion].add(key)
                books[confusion].add(ref.pid)
    ordered = sorted(
        counts,
        key=lambda c: (-counts[c], c.kind, c.expected, c.found, c.before_kind, c.after_kind),
    )
    return [
        {
            "kind": c.kind,
            "expected": c.expected,
            "found": c.found,
            "before_kind": c.before_kind,
            "after_kind": c.after_kind,
            "count": str(counts[c]),
            "pages": str(len(pages[c])),
            "books": str(len(books[c])),
        }
        for c in ordered
    ]


def _checked_page(work: Path, ref: PageRef, row: dict) -> bytes:
    path = page_file(work, ref)
    if not path.exists():
        raise OcrDataError(f"page {ref.pid}_{ref.frame} is not fetched")
    data = path.read_bytes()
    if _sha1(data) != row["sha1"]:
        raise OcrDataError(f"page {ref.pid}_{ref.frame} has another SHA-1")
    return data


def geta_rates(
    work: Path, rows: dict[tuple[str, int], dict]
) -> dict[str, tuple[int, int]]:
    """Counts 〓 in the OCR text of dataset and neighbor pages.

    The OCR writes 〓 for a character it cannot read, so a higher share
    on neighbor pages suggests that the counts of dataset pages are low
    because the OCR was trained on them.

    Args:
        work: The work directory.
        rows: The rows of ocr_pages.tsv.

    Returns:
        For each role, the number of 〓 and of all characters.
    """
    totals = {role: [0, 0] for role in ROLES}
    for row in rows.values():
        if not row["sha1"]:
            continue
        ref = PageRef(row["pid"], int(row["frame"]), row["folder"])
        text = "".join(
            line.text for line in read_ocr_page(_checked_page(work, ref, row))
        )
        totals[row["role"]][0] += text.count(GETA)
        totals[row["role"]][1] += len(text)
    return {role: (geta, chars) for role, (geta, chars) in totals.items()}


def _write_pages(rows: dict[tuple[str, int], dict]) -> None:
    write_tsv(PAGES_FILE, PAGE_COLUMNS, [rows[key] for key in sorted(rows)])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("annotations", "take the annotation files out of the dataset zip"),
        ("fetch", "download the OCR text of the selected pages"),
        ("count", "count the confusions into ocr_confusions.tsv"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--work", type=Path, required=True)
        if name != "count":
            command.add_argument(
                "--user-agent", default=fetch_corpus.DEFAULT_USER_AGENT
            )
        if name == "fetch":
            command.add_argument(
                "--limit",
                type=int,
                help="fetch only the first LIMIT selected pages",
            )
            command.add_argument(
                "--neighbors",
                type=int,
                default=DEFAULT_NEIGHBORS,
                help="how many selected pages get their neighbor pages",
            )
            command.add_argument(
                "--retry-unavailable",
                action="store_true",
                help="request again the pages recorded with an empty sha1",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Runs one command.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 on success, 1 on an error.
    """
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)
    try:
        work = fetch_corpus.check_work_dir(args.work)
        if args.command == "annotations":
            saved = fetch_annotations(work, NdlClient(args.user_agent))
            print(f"{saved} annotation files saved", file=sys.stderr)
        elif args.command == "fetch":
            selected = select_pages(work)[: args.limit]
            dataset = {
                (match.group(1), int(match.group(2)))
                for path in annotation_dir(work).glob("*/*.xml")
                if (match := ANNOTATION_NAME.fullmatch(path.name))
            }
            refs = [(ref, "dataset") for ref in selected] + [
                (ref, "neighbor")
                for ref in neighbor_pages(selected, dataset, args.neighbors)
            ]
            today = datetime.date.today().isoformat()
            rows = fetch_pages(
                work,
                NdlClient(args.user_agent),
                refs,
                read_pages_file(),
                today,
                checkpoint=_write_pages,
                retry_unavailable=args.retry_unavailable,
            )
            _write_pages(rows)
            empty = [row for row in rows.values() if not row["sha1"]]
            books = len({row["pid"] for row in empty})
            print(
                f"{len(rows)} pages recorded, {len(empty)} of them not held"
                f" by the library ({books} books)",
                file=sys.stderr,
            )
        else:
            rows = read_pages_file()
            write_tsv(
                CONFUSIONS_FILE,
                CONFUSION_COLUMNS,
                count_confusions(work, rows),
            )
            for role, (geta, chars) in geta_rates(work, rows).items():
                rate = 1000 * geta / chars if chars else 0.0
                print(
                    f"{role}: {geta} {GETA} in {chars} characters"
                    f" ({rate:.2f} per 1,000)"
                )
    except (fetch_corpus.CorpusError, OcrDataError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
