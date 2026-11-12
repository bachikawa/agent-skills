"""Tests for ocr_confusions.py. They never use the network.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ocr_confusions  # noqa: E402
from ocr_confusions import Line, OcrDataError, PageRef, Response  # noqa: E402

COMPAT_SHA = "社"  # CJK compatibility ideograph of 社


def voc(*lines: tuple[str, tuple[int, int, int, int]], dontcare: int = 0) -> bytes:
    """Returns a PascalVOC annotation with the given lines (half scale)."""
    objects = "".join(
        "<object><name>text</name><bndbox>"
        f"<xmin>{x1}</xmin><ymin>{y1}</ymin><xmax>{x2}</xmax><ymax>{y2}</ymax>"
        "</bndbox><attributes><attribute><name>contents</name>"
        f"<value>{text}</value></attribute></attributes></object>"
        for text, (x1, y1, x2, y2) in lines
    )
    objects += "<object><name>dontcare</name><bndbox><xmin>0</xmin><ymin>0</ymin><xmax>1</xmax><ymax>1</ymax></bndbox></object>" * dontcare
    return f"<annotation>{objects}</annotation>".encode("utf-8")


def page_json(*lines: tuple[str, tuple[int, int, int, int]]) -> bytes:
    """Returns a page as the page API gives it (full scale)."""
    items = [
        {"id": i, "contenttext": text, "xmin": x1, "ymin": y1, "xmax": x2, "ymax": y2}
        for i, (text, (x1, y1, x2, y2)) in enumerate(lines)
    ]
    return json.dumps({"id": "1_1", "coordjson": json.dumps(items, ensure_ascii=False)}, ensure_ascii=False).encode("utf-8")


class FakeOpener:
    """Answers requests from a list of prepared responses or errors."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(responses: list[object]) -> tuple[ocr_confusions.NdlClient, FakeOpener, list[float]]:
    opener = FakeOpener(responses)
    waits: list[float] = []
    return ocr_confusions.NdlClient("test-agent", opener, waits.append), opener, waits


def range_response(start: int, data: bytes) -> Response:
    end = start + len(data) - 1
    return Response(
        206,
        {
            "Content-Range": f"bytes {start}-{end}/{ocr_confusions.ZIP_SIZE}",
            "ETag": ocr_confusions.ZIP_ETAG,
        },
        data,
    )


class NdlClientTest(unittest.TestCase):
    def test_waits_after_each_request_and_sends_the_user_agent(self) -> None:
        ndl, opener, waits = client([Response(200, {}, b"x")])
        self.assertEqual(ndl.get("https://example.invalid/").body, b"x")
        self.assertEqual(waits, [3.0])
        self.assertEqual(opener.requests[0].get_header("User-agent"), "test-agent")

    def test_retries_server_errors_and_network_errors(self) -> None:
        headers = Message()
        headers["Retry-After"] = "10"
        busy = urllib.error.HTTPError("u", 503, "busy", headers, None)
        ndl, _, waits = client([busy, OSError("reset"), Response(200, {}, b"ok")])
        self.assertEqual(ndl.get("https://example.invalid/").body, b"ok")
        self.assertEqual(waits, [10.0, 60.0, 3.0])

    def test_other_http_errors_stop(self) -> None:
        missing = urllib.error.HTTPError("u", 404, "missing", Message(), None)
        ndl, _, _ = client([missing])
        with self.assertRaises(OcrDataError):
            ndl.get("https://example.invalid/")

    def test_repeated_server_error_means_unavailable(self) -> None:
        error = urllib.error.HTTPError("u", 500, "error", Message(), None)
        ndl, opener, _ = client([error, error])
        with self.assertRaises(ocr_confusions.Unavailable):
            ndl.get("https://example.invalid/")
        self.assertEqual(len(opener.requests), 2)

    def test_one_server_error_is_retried(self) -> None:
        error = urllib.error.HTTPError("u", 500, "error", Message(), None)
        ndl, _, _ = client([error, Response(200, {}, b"ok")])
        self.assertEqual(ndl.get("https://example.invalid/").body, b"ok")

    def test_gives_up_after_the_attempts(self) -> None:
        ndl, _, _ = client([OSError("down")] * 6)
        with self.assertRaises(OcrDataError):
            ndl.get("https://example.invalid/")


class GetRangeTest(unittest.TestCase):
    def test_returns_the_range(self) -> None:
        ndl, opener, _ = client([range_response(10, b"abc")])
        self.assertEqual(ndl.get_range(10, 13), b"abc")
        self.assertEqual(opener.requests[0].get_header("Range"), "bytes=10-12")

    def test_refuses_a_range_outside_the_zip(self) -> None:
        ndl, opener, _ = client([])
        for start, end in ((-1, 3), (5, 5), (0, ocr_confusions.ZIP_SIZE + 1)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(OcrDataError):
                    ndl.get_range(start, end)
        self.assertEqual(opener.requests, [])

    def test_a_whole_file_answer_stops(self) -> None:
        ndl, _, _ = client([Response(200, {"ETag": ocr_confusions.ZIP_ETAG}, b"abc")])
        with self.assertRaises(OcrDataError):
            ndl.get_range(0, 3)

    def test_a_changed_zip_stops(self) -> None:
        changed = range_response(0, b"abc")
        changed.headers["ETag"] = '"other"'
        ndl, _, _ = client([changed])
        with self.assertRaisesRegex(OcrDataError, "changed"):
            ndl.get_range(0, 3)

    def test_a_cut_answer_stops(self) -> None:
        cut = Response(206, range_response(0, b"abc").headers, b"ab")
        ndl, _, _ = client([cut])
        with self.assertRaises(OcrDataError):
            ndl.get_range(0, 3)


class AnnotationRangesTest(unittest.TestCase):
    def _info(self, name: str, offset: int) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name)
        info.header_offset = offset
        return info

    def test_one_range_per_folder_up_to_the_next_member(self) -> None:
        members = [
            self._info("r/a/Annotations/1_R0000001.xml", 100),
            self._info("r/a/Annotations/1_R0000002.xml", 200),
            self._info("r/a/JPEGImages/1_R0000001.jpg", 300),
            self._info("r/b/Annotations/2_R0000001.xml", 1000),
            self._info("r/b/JPEGImages/2_R0000001.jpg", 1100),
        ]
        self.assertEqual(
            ocr_confusions.annotation_ranges(members), [(100, 300), (1000, 1100)]
        )

    def test_scattered_annotations_stop(self) -> None:
        members = [
            self._info("r/a/Annotations/1_R0000001.xml", 100),
            self._info("r/a/JPEGImages/1_R0000001.jpg", 200),
            self._info("r/a/Annotations/1_R0000002.xml", 300),
        ]
        with self.assertRaises(OcrDataError):
            ocr_confusions.annotation_ranges(members)

    def test_folders_inside_the_tail_are_left_out(self) -> None:
        tail = ocr_confusions.ZIP_SIZE - ocr_confusions.TAIL_SIZE
        members = [self._info("r/z/Annotations/9_R0000001.xml", tail + 10)]
        self.assertEqual(ocr_confusions.annotation_ranges(members), [])


class FetchAnnotationsTest(unittest.TestCase):
    def test_saves_the_annotations_from_a_zip_at_the_end(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("root/tosho_1870_bunkei/labelmap.txt", "x")
            archive.writestr(
                "root/tosho_1870_bunkei/Annotations/p/tosho_1870_bunkei/1_R0000002.xml",
                voc(("ノ", (0, 0, 1, 1))),
            )
        data = buffer.getvalue()
        # A small zip at the end of the pinned size lies inside the tail
        # that annotations fetches first; zipfile finds an archive that
        # has other bytes before it.
        start = ocr_confusions.ZIP_SIZE - ocr_confusions.TAIL_SIZE
        tail = b"\0" * (ocr_confusions.TAIL_SIZE - len(data)) + data
        ndl, opener, _ = client([range_response(start, tail)])
        with tempfile.TemporaryDirectory() as work:
            saved = ocr_confusions.fetch_annotations(Path(work), ndl)
            files = [
                path.relative_to(work).as_posix()
                for path in Path(work).rglob("*.xml")
            ]
        self.assertEqual(saved, 1)
        self.assertEqual(
            files, ["ndl/annotations/tosho_1870_bunkei/1_R0000002.xml"]
        )
        self.assertEqual(len(opener.requests), 1)

    def test_a_wrong_crc_stops(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("root/f/Annotations/1_R0000002.xml", b"<a/>")
        data = bytearray(buffer.getvalue())
        position = data.index(b"<a/>")
        data[position] = ord("<") ^ 1
        start = ocr_confusions.ZIP_SIZE - ocr_confusions.TAIL_SIZE
        tail = b"\0" * (ocr_confusions.TAIL_SIZE - len(data)) + bytes(data)
        ndl, _, _ = client([range_response(start, tail)])
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(OcrDataError):
                ocr_confusions.fetch_annotations(Path(work), ndl)


class ReadTest(unittest.TestCase):
    def test_annotation_lines_are_scaled_and_dontcare_is_skipped(self) -> None:
        lines = ocr_confusions.read_annotation(
            voc(("其ノ", (1, 2, 3, 4)), dontcare=1)
        )
        self.assertEqual(lines, [Line("其ノ", (2.0, 4.0, 6.0, 8.0))])

    def test_ocr_page_lines(self) -> None:
        lines = ocr_confusions.read_ocr_page(page_json(("其ノ", (2, 4, 6, 8))))
        self.assertEqual(lines, [Line("其ノ", (2.0, 4.0, 6.0, 8.0))])

    def test_unexpected_page_json_stops(self) -> None:
        for data in (b"{}", b"[]", b"not json", json.dumps({"coordjson": "[{}]"}).encode()):
            with self.subTest(data=data):
                with self.assertRaises(OcrDataError):
                    ocr_confusions.read_ocr_page(data)


class SelectTest(unittest.TestCase):
    def test_kanji_and_katakana_page(self) -> None:
        self.assertTrue(ocr_confusions.is_bungo_page("此ノ規則ニ違反シタル者ハ罰金ニ處ス" * 20))

    def test_short_hiragana_or_kana_heavy_pages_are_left_out(self) -> None:
        cases = {
            "short": "此ノ規則ニ違反ス",
            "hiragana": "此の規則に違反したる者は罰金に處す" * 20,
            "kana heavy": "コレハテストノブンデスヨ規" * 20,
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertFalse(ocr_confusions.is_bungo_page(text))

    def test_neighbors_leave_out_dataset_frames_and_frame_zero(self) -> None:
        selected = [PageRef("1", 1, "f"), PageRef("2", 5, "f"), PageRef("3", 9, "f")]
        dataset = {("1", 1), ("2", 5), ("2", 6), ("3", 9)}
        self.assertEqual(
            ocr_confusions.neighbor_pages(selected, dataset, limit=2),
            [PageRef("1", 2, "f"), PageRef("2", 4, "f")],
        )


class FetchPagesTest(unittest.TestCase):
    def test_saves_new_pages_and_skips_pinned_ones(self) -> None:
        body = page_json(("ノ", (0, 0, 1, 1)))
        ref = PageRef("1", 2, "f")
        with tempfile.TemporaryDirectory() as work:
            ndl, opener, _ = client([Response(200, {}, body)])
            rows = ocr_confusions.fetch_pages(Path(work), ndl, [(ref, "dataset")], {}, "2026-11-13")
            self.assertEqual(
                opener.requests[0].full_url, "https://lab.ndl.go.jp/dl/api/page/1_2"
            )
            again, opener2, _ = client([])
            rows2 = ocr_confusions.fetch_pages(Path(work), again, [(ref, "dataset")], rows, "2026-11-14")
        self.assertEqual(rows[("1", 2)]["fetched"], "2026-11-13")
        self.assertEqual(rows2, rows)
        self.assertEqual(opener2.requests, [])

    def test_a_changed_page_stops(self) -> None:
        ref = PageRef("1", 2, "f")
        pinned = {("1", 2): {"pid": "1", "frame": "2", "role": "dataset", "folder": "f", "fetched": "2026-11-13", "sha1": "0" * 40}}
        with tempfile.TemporaryDirectory() as work:
            ndl, _, _ = client([Response(200, {}, page_json())])
            with self.assertRaisesRegex(OcrDataError, "changed"):
                ocr_confusions.fetch_pages(Path(work), ndl, [(ref, "dataset")], pinned, "2026-11-14")

    def test_books_the_library_does_not_hold_are_recorded_empty(self) -> None:
        error = urllib.error.HTTPError("u", 500, "error", Message(), None)
        refs = [
            (PageRef("1", 2, "f"), "dataset"),
            (PageRef("1", 5, "f"), "dataset"),
            (PageRef("2", 1, "f"), "dataset"),
        ]
        with tempfile.TemporaryDirectory() as work:
            ndl, opener, _ = client([error, error, Response(200, {}, page_json())])
            rows = ocr_confusions.fetch_pages(Path(work), ndl, refs, {}, "d")
            saved = sorted(p.name for p in Path(work).rglob("*.json"))
            again, opener2, _ = client([])
            ocr_confusions.fetch_pages(Path(work), again, refs, rows, "e")
        self.assertEqual(
            [rows[key]["sha1"] == "" for key in sorted(rows)], [True, True, False]
        )
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(saved, ["2_1.json"])
        self.assertEqual(opener2.requests, [])

    def test_an_error_still_writes_the_checkpoint(self) -> None:
        refs = [(PageRef("1", n, "f"), "dataset") for n in (1, 2)]
        missing = urllib.error.HTTPError("u", 404, "missing", Message(), None)
        calls = []
        with tempfile.TemporaryDirectory() as work:
            ndl, _, _ = client([Response(200, {}, page_json()), missing])
            with self.assertRaises(OcrDataError):
                ocr_confusions.fetch_pages(Path(work), ndl, refs, {}, "d", checkpoint=calls.append)
        self.assertEqual([list(rows) for rows in calls], [[("1", 1)]])

    def test_checkpoint_is_called(self) -> None:
        refs = [(PageRef("1", n, "f"), "dataset") for n in range(1, 51)]
        calls = []
        with tempfile.TemporaryDirectory() as work:
            ndl, _, _ = client([Response(200, {}, page_json())] * 50)
            ocr_confusions.fetch_pages(Path(work), ndl, refs, {}, "d", checkpoint=calls.append)
        self.assertEqual(len(calls), 1)


class PairLinesTest(unittest.TestCase):
    def test_pairs_by_overlap_in_the_order_of_the_truth(self) -> None:
        truth = [Line("甲", (0, 0, 10, 100)), Line("乙", (20, 0, 30, 100))]
        ocr = [Line("乙'", (21, 0, 31, 100)), Line("甲'", (0, 1, 10, 101))]
        self.assertEqual(
            ocr_confusions.pair_lines(truth, ocr), [("甲", "甲'"), ("乙", "乙'")]
        )

    def test_lines_that_barely_overlap_are_not_paired(self) -> None:
        truth = [Line("甲", (0, 0, 10, 100))]
        ocr = [Line("乙", (8, 0, 18, 100))]
        self.assertEqual(ocr_confusions.pair_lines(truth, ocr), [])

    def test_each_line_is_used_once(self) -> None:
        truth = [Line("甲", (0, 0, 10, 100)), Line("乙", (0, 0, 10, 99))]
        ocr = [Line("甲", (0, 0, 10, 100))]
        self.assertEqual(len(ocr_confusions.pair_lines(truth, ocr)), 1)


class LineConfusionsTest(unittest.TestCase):
    def _kinds(self, expected: str, found: str) -> list[tuple[str, str, str]]:
        return [
            (c.kind, c.expected, c.found)
            for c in ocr_confusions.line_confusions(expected, found)
        ]

    def test_substitution_with_its_context(self) -> None:
        (confusion,) = ocr_confusions.line_confusions(
            "不都合ニ付候事", "不都合二付候事"
        )
        self.assertEqual(
            confusion,
            ocr_confusions.Confusion("substitution", "ニ", "二", "kanji", "kanji"),
        )

    def test_geta_variant_insertion_and_deletion(self) -> None:
        self.assertEqual(self._kinds("此ノ規則ニ違反ス", "此ノ規〓ニ違反ス"), [("geta", "則", "〓")])
        self.assertEqual(
            self._kinds("此ノ會" + COMPAT_SHA + "ニ違反ス", "此ノ會社ニ違反ス"),
            [("variant", COMPAT_SHA, "社")],
        )
        self.assertEqual(self._kinds("後ルヽ時ハ之ヲ罰ス", "後ル時ハ之ヲ罰ス"), [("deletion", "ヽ", "")])
        self.assertEqual(self._kinds("後ル時ハ之ヲ罰ス", "後ル丶時ハ之ヲ罰ス"), [("insertion", "", "丶")])

    def test_long_or_uneven_replacements_are_other(self) -> None:
        line = "此ノ規則ニ違反シタル者ハ三年以下ノ懲役ニ處シ又ハ罰金ニ處ス"
        self.assertEqual(
            self._kinds(line, line.replace("タル者", "夕儿老")),
            [("other", "タル者", "夕儿老")],
        )
        self.assertEqual(
            self._kinds(line, line.replace("又ハ", "叉ハ", 1)),
            [("substitution", "又", "叉")],
        )

    def test_dissimilar_lines_give_nothing(self) -> None:
        self.assertEqual(ocr_confusions.line_confusions("此ノ規則", "全ク別ノ行"), [])

    def test_char_kinds(self) -> None:
        cases = {"": "edge", "ノ": "katakana", "の": "hiragana", "二": "kanji", "3": "digit", "、": "other", COMPAT_SHA: "kanji"}
        for char, kind in cases.items():
            with self.subTest(char=char):
                self.assertEqual(ocr_confusions.char_kind(char), kind)


class CountTest(unittest.TestCase):
    def _work(self, work: Path, pages: dict[PageRef, tuple[str, bytes, bytes | None]]) -> dict:
        rows = {}
        for ref, (role, ocr, truth) in pages.items():
            path = ocr_confusions.page_file(work, ref)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ocr)
            if truth is not None:
                annotation = ocr_confusions.annotation_dir(work) / ref.folder / f"{ref.pid}_R{ref.frame:07d}.xml"
                annotation.parent.mkdir(parents=True, exist_ok=True)
                annotation.write_bytes(truth)
            rows[(ref.pid, ref.frame)] = {
                "pid": ref.pid, "frame": str(ref.frame), "role": role, "folder": ref.folder,
                "fetched": "2026-11-13", "sha1": ocr_confusions._sha1(ocr),
            }
        return rows

    def test_counts_dataset_pages_by_page_and_book(self) -> None:
        line = "不都合ニ付候事"
        truth = voc((line, (0, 0, 10, 100)))
        ocr = page_json(("不都合二付候事", (0, 0, 20, 200)))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            rows = self._work(work, {
                PageRef("9", 1, "f"): ("dataset", b"", truth),
                PageRef("1", 1, "f"): ("dataset", ocr, truth),
                PageRef("1", 3, "f"): ("dataset", ocr, truth),
                PageRef("2", 1, "f"): ("dataset", ocr, truth),
                PageRef("1", 2, "f"): ("neighbor", page_json(("〓", (0, 0, 1, 1))), None),
            })
            rows[("9", 1)]["sha1"] = ""
            result = ocr_confusions.count_confusions(work, rows)
            rates = ocr_confusions.geta_rates(work, rows)
        self.assertEqual(
            result,
            [{"kind": "substitution", "expected": "ニ", "found": "二", "before_kind": "kanji",
              "after_kind": "kanji", "count": "3", "pages": "3", "books": "2"}],
        )
        self.assertEqual(rates, {"dataset": (0, 21), "neighbor": (1, 1)})

    def test_a_page_with_another_sha1_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            rows = self._work(work, {PageRef("1", 1, "f"): ("dataset", page_json(), voc())})
            rows[("1", 1)]["sha1"] = "0" * 40
            with self.assertRaises(OcrDataError):
                ocr_confusions.count_confusions(work, rows)


class PagesFileTest(unittest.TestCase):
    def test_round_trip_and_columns(self) -> None:
        row = {"pid": "1", "frame": "2", "role": "dataset", "folder": "f", "fetched": "d", "sha1": "a" * 40}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "pages.tsv")
            ocr_confusions.write_tsv(path, ocr_confusions.PAGE_COLUMNS, [row])
            self.assertEqual(ocr_confusions.read_pages_file(path), {("1", 2): row})
            path.write_text("a\tb\n", encoding="utf-8")
            with self.assertRaises(OcrDataError):
                ocr_confusions.read_pages_file(path)

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(ocr_confusions.read_pages_file(Path(tempfile.gettempdir(), "no-such-file.tsv")), {})


if __name__ == "__main__":
    unittest.main()
