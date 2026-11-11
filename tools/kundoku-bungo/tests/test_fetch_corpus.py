"""Tests for fetch_corpus.py. They never use the network.

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
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_corpus  # noqa: E402
from fetch_corpus import Page, Revision  # noqa: E402

HEADER = "{{Header\n|title=x\n|year=%d\n}}\n"


def law(year: int = 1900, text: str = "") -> str:
    return HEADER % year + text


def law_with_notes(year: int, notes: str) -> str:
    """Returns a page whose header notes give the promulgation number."""
    return "{{Header\n|year=%d\n|notes=\n%s\n}}\n本文" % (year, notes)


class FakeApi:
    """Answers requests from a list of prepared responses."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, list[str]]] = []
        self.waits: list[float] = []

    def open(self, request) -> bytes:
        import urllib.parse

        self.requests.append(urllib.parse.parse_qs(request.data.decode()))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response).encode("utf-8")

    def api(self) -> fetch_corpus.WikisourceApi:
        return fetch_corpus.WikisourceApi(
            "test-agent", opener=self.open, sleep=self.waits.append
        )


def http_error(code: int, retry_after: str | None = None) -> Exception:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        fetch_corpus.API_URL, code, "error", headers, io.BytesIO()
    )


class ClassifyTest(unittest.TestCase):
    def test_imperial_house_laws_are_kanji_only(self) -> None:
        cases = {
            "王公家軌範/公布時": ["大正15年の皇室令"],
            "皇統譜令/公布時 (大正15年皇室令第6号)": [],
            "華族令 (公布時)": ["明治40年の皇室令"],
        }
        for title, categories in cases.items():
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, categories, law(1926))
                self.assertEqual(usage, "kanji_only")

    def test_house_law_number_in_the_header_makes_it_kanji_only(self) -> None:
        content = law_with_notes(1907, "*明治40年皇室令第2号")
        usage, rule = fetch_corpus.classify("某規程", [], content)
        self.assertEqual((usage, rule), ("kanji_only", "imperial-number"))

    def test_rescripts_and_the_constitution_are_kanji_only(self) -> None:
        for title in ("教育ニ関スル勅語", "米國及英國ニ對スル宣戰ノ詔書", "大日本帝國憲法"):
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, [], law(1890))
                self.assertEqual(usage, "kanji_only")

    def test_imperial_house_law_named_without_kotsurei_is_kanji_only(
        self,
    ) -> None:
        cases = {
            "皇室典範 (明治二十二年二月十一日)": [],
            "皇室婚嫁令": [],
            "華族令 (明治十七年宮内省達無号)": [],
            "天皇陛下崩御 (大正15年12月25日告示)": [],
        }
        for title, categories in cases.items():
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, categories, law(1889))
                self.assertEqual(usage, "kanji_only")

    def test_imperial_household_notice_number_makes_it_kanji_only(
        self,
    ) -> None:
        content = law_with_notes(1894, "*明治二十七年宮内省達甲第二号")
        usage, rule = fetch_corpus.classify("某規則", [], content)
        self.assertEqual((usage, rule), ("kanji_only", "imperial-number"))

    def test_link_to_the_peerage_law_in_the_body_does_not_count(
        self,
    ) -> None:
        body = "法\n[[:w:華族令_(明治二十七年宮内省達甲第二号)|伯爵]]伊藤博文"
        self.assertEqual(
            fetch_corpus.classify("弁護士法", [], law(1893, body)),
            ("all", "law"),
        )

    def test_notices_of_imperial_ceremonies_are_kanji_only(self) -> None:
        for title in ("親王御誕生", "大喪使官制", "來二十七日御卽位ヲ令ス"):
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, [], law(1912))
                self.assertEqual(usage, "kanji_only")

    def test_post_war_revision_with_an_old_header_year_is_left_out(
        self,
    ) -> None:
        for title in ("某法 (昭和二十一年十二月二十七日)", "某令 (昭和23年政令第1号)"):
            with self.subTest(title=title):
                self.assertEqual(
                    fetch_corpus.classify(title, [], law(1907)),
                    (None, "out-of-period"),
                )

    def test_showa_20_in_the_title_is_in_the_period(self) -> None:
        title = "公式令 (昭和20年勅令第664号による改正)"
        self.assertEqual(
            fetch_corpus.classify(title, [], law(1907)), ("all", "law")
        )

    def test_law_quoting_a_rescript_is_used_everywhere(self) -> None:
        usage, rule = fetch_corpus.classify(
            "徵兵令詔書及ヒ徵兵吿諭", ["明治5年の詔勅"], law(1872)
        )
        self.assertEqual((usage, rule), ("all", "mixed"))

    def test_treaties_and_foreign_governments_are_left_out(self) -> None:
        cases = {
            "日英同盟協約": ["明治35年の条約"],
            "政府組織法 (公布時)": ["満洲国の法令"],
        }
        for title, categories in cases.items():
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, categories, law(1932))
                self.assertIsNone(usage)

    def test_pages_without_a_year_or_out_of_period_are_left_out(self) -> None:
        for content, rule in (("本文", "no-year"), (law(1947), "out-of-period")):
            with self.subTest(rule=rule):
                self.assertEqual(
                    fetch_corpus.classify("法", [], content), (None, rule)
                )

    def test_ordinary_law_in_the_period_is_used_everywhere(self) -> None:
        self.assertEqual(
            fetch_corpus.classify("刑法", ["明治13年の法令"], law(1880)),
            ("all", "law"),
        )


class BuildRevisionsTest(unittest.TestCase):
    def test_pinned_revision_wins_over_the_listed_one(self) -> None:
        pinned = [Page("刑法", 100, law(1880), [])]
        listed = [
            Page("刑法", 200, law(1880), []),
            Page("法例", 300, law(1898), []),
        ]
        rows, _ = fetch_corpus.build_revisions(pinned, {100: "all"}, listed)
        self.assertEqual(
            rows,
            [
                Revision("刑法", 100, 1880, "all", "pinned", "law"),
                Revision("法例", 300, 1898, "all", "listed", "law"),
            ],
        )

    def test_kanji_only_wins_over_the_pinned_usage(self) -> None:
        pinned = [Page("王公家軌範/公布時", 1, law(1926), [])]
        listed = [Page("王公家軌範/公布時", 2, law(1926), ["大正15年の皇室令"])]
        rows, _ = fetch_corpus.build_revisions(pinned, {1: "all"}, listed)
        self.assertEqual(rows[0].oldid, 1)
        self.assertEqual(rows[0].usage, "kanji_only")

    def test_pinned_page_only_in_examples_stays_examples_only(self) -> None:
        pinned = [Page("株熱の余症恐るべし", 5, law(1893), [])]
        usage = {5: "examples_only"}
        rows, _ = fetch_corpus.build_revisions(pinned, usage, [])
        self.assertEqual(rows[0].usage, "examples_only")

    def test_left_out_pages_are_reported(self) -> None:
        listed = [Page("日英同盟協約", 9, law(1902), ["明治35年の条約"])]
        rows, left_out = fetch_corpus.build_revisions([], {}, listed)
        self.assertEqual(rows, [])
        self.assertEqual(left_out, [("日英同盟協約", "foreign-or-treaty")])


class RevisionsFileTest(unittest.TestCase):
    def test_rows_round_trip_with_lf_line_ends(self) -> None:
        rows = [Revision("刑法", 100, 1880, "all", "pinned", "law")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "corpus_revisions.tsv")
            fetch_corpus.write_revisions(rows, path)
            self.assertNotIn(b"\r", path.read_bytes())
            self.assertEqual(fetch_corpus.read_revisions(path), rows)

    def test_unknown_usage_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "corpus_revisions.tsv")
            path.write_text(
                "\t".join(fetch_corpus.REVISION_COLUMNS)
                + "\n刑法\t1\t1880\teverything\tpinned\tlaw\n",
                encoding="utf-8",
            )
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.read_revisions(path)


class PageIndexTest(unittest.TestCase):
    def test_saved_pages_load_back_with_their_categories(self) -> None:
        pinned = [Page("刑法", 1, law(1880), ["明治13年の法令"])]
        listed = [Page("華族令", 2, law(1884), ["明治17年の法令"])]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for page in pinned + listed:
                fetch_corpus.save_page(work, page)
            fetch_corpus.save_page_index(work, pinned, listed)
            self.assertEqual(
                fetch_corpus.load_page_index(work), (pinned, listed)
            )

    def test_loading_before_the_revisions_command_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.load_page_index(Path(directory))


class WorkDirTest(unittest.TestCase):
    def test_directory_inside_the_repository_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as repository:
            for inside in (Path(repository), Path(repository, "work")):
                with self.subTest(work=inside):
                    with self.assertRaises(fetch_corpus.CorpusError):
                        fetch_corpus.check_work_dir(inside, Path(repository))

    def test_directory_outside_the_repository_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as repository:
            with tempfile.TemporaryDirectory() as work:
                self.assertEqual(
                    fetch_corpus.check_work_dir(Path(work), Path(repository)),
                    Path(work).resolve(),
                )


class ApiTest(unittest.TestCase):
    def test_request_sends_maxlag_and_the_user_agent(self) -> None:
        fake = FakeApi([{"query": {}}])
        captured = []
        api = fetch_corpus.WikisourceApi(
            "test-agent",
            opener=lambda r: captured.append(r) or fake.open(r),
            sleep=fake.waits.append,
        )
        api.query({"action": "query"})
        self.assertEqual(captured[0].get_header("User-agent"), "test-agent")
        self.assertEqual(fake.requests[0]["maxlag"], ["5"])
        self.assertEqual(fake.waits, [fetch_corpus.REQUEST_INTERVAL])

    def test_http_429_waits_for_retry_after_and_retries(self) -> None:
        fake = FakeApi([http_error(429, "7"), {"query": {}}])
        self.assertEqual(fake.api().query({}), {"query": {}})
        self.assertEqual(fake.waits[0], 7.0)

    def test_http_429_without_retry_after_waits_the_default(self) -> None:
        fake = FakeApi([http_error(429), {"query": {}}])
        fake.api().query({})
        self.assertEqual(fake.waits[0], fetch_corpus.DEFAULT_RETRY_WAIT)

    def test_maxlag_error_is_retried(self) -> None:
        fake = FakeApi([{"error": {"code": "maxlag"}}, {"query": {}}])
        self.assertEqual(fake.api().query({}), {"query": {}})

    def test_other_http_errors_stop_at_once(self) -> None:
        fake = FakeApi([http_error(404)])
        with self.assertRaises(fetch_corpus.CorpusError):
            fake.api().query({})

    def test_repeated_failures_give_up(self) -> None:
        fake = FakeApi([http_error(429)] * fetch_corpus.MAX_ATTEMPTS)
        with self.assertRaises(fetch_corpus.CorpusError):
            fake.api().query({})


class FetchPagesTest(unittest.TestCase):
    def test_categories_from_continued_responses_are_merged(self) -> None:
        first = {
            "continue": {"clcontinue": "1|b"},
            "query": {
                "pages": [
                    {
                        "title": "王公家軌範/公布時",
                        "revisions": [
                            {"revid": 9, "slots": {"main": {"content": "本文"}}}
                        ],
                        "categories": [{"title": "Category:大正15年の法令"}],
                    }
                ]
            },
        }
        second = {
            "query": {
                "pages": [
                    {
                        "title": "王公家軌範/公布時",
                        "categories": [{"title": "Category:大正15年の皇室令"}],
                    }
                ]
            }
        }
        fake = FakeApi([first, second])
        pages = fetch_corpus.fetch_pages(fake.api(), revids=[9])
        self.assertEqual(
            pages,
            [Page("王公家軌範/公布時", 9, "本文", ["大正15年の法令", "大正15年の皇室令"])],
        )
        self.assertEqual(fake.requests[1]["clcontinue"], ["1|b"])

    def test_missing_pages_are_left_out(self) -> None:
        missing = {"title": "無い", "missing": True}
        fake = FakeApi([{"query": {"pages": [missing]}}])
        pages = fetch_corpus.fetch_pages(fake.api(), titles=["無い"])
        self.assertEqual(pages, [])

    def test_titles_are_asked_for_in_batches(self) -> None:
        titles = [f"法{n}" for n in range(fetch_corpus.BATCH_SIZE + 1)]
        fake = FakeApi([{"query": {"pages": []}}, {"query": {"pages": []}}])
        fetch_corpus.fetch_pages(fake.api(), titles=titles)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.requests[1]["titles"], [titles[-1]])


class ListTitlesTest(unittest.TestCase):
    def test_skipped_subcategories_are_not_walked(self) -> None:
        root = {
            "query": {
                "categorymembers": [
                    {"ns": 0, "title": "刑法"},
                    {"ns": 14, "title": "Category:明治13年の太政官布告"},
                    {"ns": 14, "title": "Category:明治13年の詔勅"},
                ]
            }
        }
        sub = {"query": {"categorymembers": [{"ns": 0, "title": "刑法"}]}}
        fake = FakeApi([root, sub])
        titles = fetch_corpus.list_titles(fake.api(), ["明治13年の法令"])
        self.assertEqual(titles, {"刑法": ["明治13年の法令", "明治13年の太政官布告"]})
        self.assertEqual(len(fake.requests), 2)

    def test_year_roots_cover_meiji_1_to_showa_20(self) -> None:
        roots = fetch_corpus.year_roots()
        self.assertEqual(roots[0], "明治元年の法令")
        self.assertEqual(roots[-1], "昭和20年の法令")
        self.assertEqual(len(roots), 45 + 15 + 20)


class UnihanTest(unittest.TestCase):
    def test_file_with_another_hash_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.download_unihan(Path(work), opener=lambda r: b"x")
            self.assertFalse(Path(work, "Unihan.zip").exists())


class PinnedOldidsTest(unittest.TestCase):
    def test_current_tables_cite_37_revisions(self) -> None:
        pinned = fetch_corpus.pinned_oldids()
        self.assertEqual(len(pinned), 37)
        self.assertEqual(
            sorted(o for o, u in pinned.items() if u == "examples_only"),
            [218223, 218225],
        )


if __name__ == "__main__":
    unittest.main()
