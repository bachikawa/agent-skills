"""Tests for fetch_corpus.py. They never use the network.

Run from the repository root with:
    python -m unittest discover -s tools/kundoku-bungo/tests
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_corpus  # noqa: E402
from fetch_corpus import Page, Revision  # noqa: E402

HEADER = "{{Header\n|title=x\n|year=%d\n}}\n"


def law(year: int = 1900, text: str = "") -> str:
    """Returns a page with a header giving the year."""
    return HEADER % year + text


def law_with_notes(year: int, notes: str) -> str:
    """Returns a page whose header notes give the promulgation number."""
    return "{{Header\n|year=%d\n|notes=\n%s\n}}\n本文" % (year, notes)


def api_revision(revid: object, content: str, **extra: object) -> dict:
    """Returns a revision as the API gives it, with a matching SHA-1."""
    return dict(
        {
            "revid": revid,
            "sha1": fetch_corpus.content_sha1(content),
            "slots": {"main": {"content": content}},
        },
        **extra,
    )


def api_pages(*pages: dict) -> dict:
    """Returns a query response with the given pages."""
    return {"query": {"pages": list(pages)}}


class FakeApi:
    """Answers requests from a list of prepared responses."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, list[str]]] = []
        self.waits: list[float] = []

    def open(self, request) -> bytes:
        self.requests.append(urllib.parse.parse_qs(request.data.decode()))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, bytes):
            return response
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
        titles = ("教育ニ関スル勅語", "米國及英國ニ對スル宣戰ノ詔書", "大日本帝國憲法")
        for title in titles:
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, [], law(1890))
                self.assertEqual(usage, "kanji_only")

    def test_imperial_house_law_named_without_kotsurei_is_kanji_only(
        self,
    ) -> None:
        titles = (
            "皇室典範 (明治二十二年二月十一日)",
            "皇室婚嫁令",
            "華族令 (明治十七年宮内省達無号)",
            "天皇陛下崩御 (大正15年12月25日告示)",
            "御諱御名ノ文字ヲ闕画セシム",
        )
        for title in titles:
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, [], law(1889))
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
            ("all", "default"),
        )

    def test_notices_of_imperial_ceremonies_are_kanji_only(self) -> None:
        for title in ("親王御誕生", "大喪使官制", "來二十七日御卽位ヲ令ス"):
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, [], law(1912))
                self.assertEqual(usage, "kanji_only")

    def test_post_war_revision_with_an_old_header_year_is_left_out(
        self,
    ) -> None:
        titles = (
            "某法 (昭和二十一年十二月二十七日)",
            "某法 (昭和三十六年四月一日法律第五十六号)",
            "某法 (昭和四十年法律第一号)",
            "某令 (昭和23年政令第1号)",
            "某法 (平成十一年法律第百六十号)",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(
                    fetch_corpus.classify(title, [], law(1907)),
                    (None, "out-of-period"),
                )

    def test_showa_20_in_the_title_is_in_the_period(self) -> None:
        for title in ("公式令 (昭和20年勅令第664号による改正)", "某令 (昭和二十年)"):
            with self.subTest(title=title):
                self.assertEqual(
                    fetch_corpus.classify(title, [], law(1907)),
                    ("all", "default"),
                )

    def test_page_of_the_law_as_amended_today_is_left_out(self) -> None:
        notes = (
            "{{現行法令掲載}}",
            "*最終改正: 平成三〇年六月二〇日法律第五九号",
            "*最終改正：平成30年法律第29号",
            "*最終改正年月日:平成一六年六月九日法律第八四号",
        )
        for note in notes:
            with self.subTest(note=note):
                content = law_with_notes(1907, note)
                self.assertEqual(
                    fetch_corpus.classify("某法", [], content),
                    (None, "current-version"),
                )

    def test_law_quoting_a_rescript_is_used_everywhere(self) -> None:
        usage, rule = fetch_corpus.classify(
            "徵兵令詔書及ヒ徵兵吿諭", ["明治5年の詔勅"], law(1872)
        )
        self.assertEqual((usage, rule), ("all", "mixed"))

    def test_documents_with_foreign_governments_are_left_out(self) -> None:
        cases = {
            "日英同盟協約": ["明治35年の条約"],
            "政府組織法 (公布時)": ["満洲国の法令"],
            "日本の降伏文書": ["昭和20年の法令"],
            "日満議定書": [],
            "海上法要義ニ関スル宣言": [],
        }
        for title, categories in cases.items():
            with self.subTest(title=title):
                usage, _ = fetch_corpus.classify(title, categories, law(1932))
                self.assertIsNone(usage)

    def test_law_about_the_potsdam_declaration_is_kept(self) -> None:
        title = "「ポツダム」宣言ノ受諾ニ伴ヒ發スル命令ニ關スル件"
        categories = ["ポツダム宣言"]
        self.assertEqual(
            fetch_corpus.classify(title, categories, law(1945)),
            ("all", "default"),
        )

    def test_japanese_law_naming_manchuria_as_a_place_is_kept(self) -> None:
        title = "關東都督府郵便電信局官制施行ノ際滿州ニ現在スル件"
        self.assertEqual(
            fetch_corpus.classify(title, [], law(1906)), ("all", "default")
        )

    def test_link_text_saying_latest_revision_is_not_a_marker(self) -> None:
        content = law_with_notes(1884, "（[[法例#a1|最終改正版]]）")
        self.assertEqual(
            fetch_corpus.classify("爆發物取締罰則 (改正)", [], content),
            ("all", "default"),
        )

    def test_pages_without_a_year_or_out_of_period_are_left_out(self) -> None:
        cases = (("本文", "no-year"), (law(1947), "out-of-period"))
        for content, rule in cases:
            with self.subTest(rule=rule):
                self.assertEqual(
                    fetch_corpus.classify("法", [], content), (None, rule)
                )

    def test_year_outside_the_header_is_not_used(self) -> None:
        content = "{{Header\n|title=x\n}}\n{{Other|year=1900}}\n本文"
        self.assertEqual(
            fetch_corpus.classify("法", [], content), (None, "no-year")
        )

    def test_ordinary_law_in_the_period_is_used_everywhere(self) -> None:
        self.assertEqual(
            fetch_corpus.classify("刑法", ["明治13年の法令"], law(1880)),
            ("all", "default"),
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
                Revision(
                    "刑法", 100, pinned[0].sha1, 1880, "all", "pinned", "pinned"
                ),
                Revision(
                    "法例",
                    300,
                    listed[1].sha1,
                    1898,
                    "all",
                    "listed",
                    "default",
                ),
            ],
        )

    def test_kanji_only_wins_over_the_pinned_usage(self) -> None:
        pinned = [Page("王公家軌範/公布時", 1, law(1926), [])]
        listed = [Page("王公家軌範/公布時", 2, law(1926), ["大正15年の皇室令"])]
        rows, _ = fetch_corpus.build_revisions(pinned, {1: "all"}, listed)
        self.assertEqual(rows[0].oldid, 1)
        self.assertEqual(rows[0].usage, "kanji_only")

    def test_pinned_revision_left_out_by_the_rules_is_an_error(self) -> None:
        cases = {
            "no year": Page("某ノ勅語", 1, "本文", []),
            "treaty": Page("日英同盟協約", 2, law(1902), []),
            "current": Page("某法", 3, law_with_notes(1907, "{{現行法令掲載}}"), []),
        }
        for name, page in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(fetch_corpus.CorpusError):
                    fetch_corpus.build_revisions(
                        [page], {page.oldid: "all"}, []
                    )

    def test_pinned_speech_is_text_only(self) -> None:
        pinned = [Page("祝東京專門学校之開校", 4, law(1882), [])]
        rows, _ = fetch_corpus.build_revisions(pinned, {4: "all"}, [])
        self.assertEqual(
            (rows[0].usage, rows[0].rule), ("text_only", "non-government")
        )

    def test_pinned_page_only_in_examples_stays_examples_only(self) -> None:
        pinned = [Page("株熱の余症恐るべし", 5, law(1893), [])]
        usage = {5: "examples_only"}
        rows, _ = fetch_corpus.build_revisions(pinned, usage, [])
        self.assertEqual(rows[0].usage, "examples_only")

    def test_imperial_page_in_the_examples_becomes_kanji_only(self) -> None:
        pinned = [Page("某ノ詔書", 5, law(1900), [])]
        usage = {5: "examples_only"}
        rows, _ = fetch_corpus.build_revisions(pinned, usage, [])
        self.assertEqual(rows[0].usage, "kanji_only")

    def test_pinned_mixed_page_keeps_its_usage(self) -> None:
        pinned = [Page("徵兵令詔書及ヒ徵兵吿諭", 7, law(1872), [])]
        rows, _ = fetch_corpus.build_revisions(pinned, {7: "all"}, [])
        self.assertEqual((rows[0].usage, rows[0].rule), ("all", "mixed"))

    def test_left_out_pages_are_reported(self) -> None:
        listed = [Page("日英同盟協約", 9, law(1902), ["明治35年の条約"])]
        rows, left_out = fetch_corpus.build_revisions([], {}, listed)
        self.assertEqual(rows, [])
        self.assertEqual(left_out, [("日英同盟協約", "foreign-or-treaty")])


class RevisionsFileTest(unittest.TestCase):
    ROW = Revision("刑法", 100, "a" * 40, 1880, "all", "pinned", "pinned")

    def write_lines(self, directory: str, *lines: str) -> Path:
        path = Path(directory, "corpus_revisions.tsv")
        text = "\t".join(fetch_corpus.REVISION_COLUMNS) + "\n"
        path.write_text(text + "".join(l + "\n" for l in lines), "utf-8")
        return path

    def test_rows_round_trip_with_lf_line_ends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "corpus_revisions.tsv")
            fetch_corpus.write_revisions([self.ROW], path)
            self.assertNotIn(b"\r", path.read_bytes())
            self.assertEqual(fetch_corpus.read_revisions(path), [self.ROW])

    def test_failed_write_keeps_the_old_file(self) -> None:
        bad = Revision("刑\t法", 1, "a" * 40, 1880, "all", "pinned", "pinned")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "corpus_revisions.tsv")
            fetch_corpus.write_revisions([self.ROW], path)
            before = path.read_bytes()
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.write_revisions([self.ROW, bad], path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_unexpected_values_are_errors(self) -> None:
        sha1 = "a" * 40
        cases = {
            "usage": f"刑法\t1\t{sha1}\t1880\teverything\tpinned\tpinned",
            "number": f"刑法\tone\t{sha1}\t1880\tall\tpinned\tpinned",
            "sha1": "刑法\t1\txyz\t1880\tall\tpinned\tpinned",
        }
        for name, line in cases.items():
            with self.subTest(value=name):
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_lines(directory, line)
                    with self.assertRaises(fetch_corpus.CorpusError):
                        fetch_corpus.read_revisions(path)


class PageIndexTest(unittest.TestCase):
    def test_saved_pages_load_back_with_their_categories(self) -> None:
        pinned = [Page("刑法", 1, law(1880, "本文\r\n"), ["明治13年の法令"])]
        listed = [
            Page("華族令", 2, law(1884), ["明治17年の法令"], "2020", "A", True)
        ]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for page in pinned + listed:
                fetch_corpus.save_page(work, page)
            fetch_corpus.save_page_index(work, pinned, listed)
            loaded = fetch_corpus.load_page_index(work)
            self.assertEqual(loaded, (pinned, listed))
            self.assertEqual(loaded[0][0].sha1, pinned[0].sha1)

    def test_loading_before_the_revisions_command_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.load_page_index(Path(directory))

    def test_saved_page_changed_after_fetching_is_an_error(self) -> None:
        page = Page("刑法", 1, law(1880), [])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, page)
            fetch_corpus.save_page_index(work, [page], [])
            fetch_corpus.save_page(work, Page("刑法", 1, "書き換えた", []))
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.load_page_index(work)

    def test_cut_file_reads_as_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            path = fetch_corpus.page_path(work, 1)
            path.parent.mkdir()
            path.write_bytes("本文".encode("utf-8")[:-1])
            self.assertIsNone(fetch_corpus.read_saved_page(work, 1))


class RecentEditsTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 12, tzinfo=datetime.timezone.utc)

    def test_recent_and_anonymous_edits_are_picked(self) -> None:
        pages = [
            Page("古い", 1, "", [], "2020-01-01T00:00:00Z", "A"),
            Page("新しい", 2, "", [], "2026-11-01T00:00:00Z", "B"),
            Page("匿名", 3, "", [], "2020-01-01T00:00:00Z", "", True),
            Page("不明", 4, "", [], ""),
        ]
        picked = fetch_corpus.recent_edits(pages, self.NOW)
        self.assertEqual([p.title for p in picked], ["不明", "匿名", "新しい"])

    def test_temporary_accounts_count_as_without_an_account(self) -> None:
        revision = api_revision(
            7,
            "本文",
            user="~2025-1",
            temp=True,
            timestamp="2020-01-01T00:00:00Z",
        )
        fake = FakeApi([api_pages({"title": "法", "revisions": [revision]})])
        pages = fetch_corpus.fetch_pages(fake.api(), revids=[7])
        self.assertTrue(pages[0].anonymous)
        self.assertEqual(fetch_corpus.recent_edits(pages, self.NOW), pages)


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

    def test_main_returns_1_for_a_directory_in_the_repository(self) -> None:
        work = fetch_corpus.REPOSITORY / "work-for-test"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = fetch_corpus.main(["fetch", "--work", str(work)])
        self.assertEqual(code, 1)
        self.assertIn("outside the repository", stderr.getvalue())
        self.assertFalse(work.exists())


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

    def test_default_user_agent_has_no_personal_details(self) -> None:
        self.assertEqual(
            fetch_corpus.DEFAULT_USER_AGENT, "kundoku-bungo-corpus-tools/0.1"
        )

    def test_http_429_waits_for_retry_after_and_retries(self) -> None:
        fake = FakeApi([http_error(429, "7"), {"query": {}}])
        self.assertEqual(fake.api().query({}), {"query": {}})
        self.assertEqual(fake.waits[0], 7.0)

    def test_retryable_failures_wait_the_default_and_retry(self) -> None:
        failures = {
            "429 without Retry-After": http_error(429),
            "502": http_error(502),
            "network": urllib.error.URLError("down"),
            "timeout": TimeoutError(),
            "not JSON": b"<html>busy</html>",
            "not an object": b"[1, 2]",
        }
        for name, failure in failures.items():
            with self.subTest(failure=name):
                fake = FakeApi([failure, {"query": {}}])
                self.assertEqual(fake.api().query({}), {"query": {}})
                wait = fake.waits[0]
                self.assertEqual(wait, fetch_corpus.DEFAULT_RETRY_WAIT)

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


class RetryWaitTest(unittest.TestCase):
    def test_wait_is_kept_between_the_interval_and_the_limit(self) -> None:
        cases = {
            "0": fetch_corpus.REQUEST_INTERVAL,
            "1": fetch_corpus.REQUEST_INTERVAL,
            "1e9": fetch_corpus.MAX_RETRY_WAIT,
            "inf": fetch_corpus.DEFAULT_RETRY_WAIT,
            "nan": fetch_corpus.DEFAULT_RETRY_WAIT,
            "Wed, 21 Oct 2026 07:28:00 GMT": fetch_corpus.DEFAULT_RETRY_WAIT,
            None: fetch_corpus.DEFAULT_RETRY_WAIT,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(fetch_corpus.retry_wait(value), expected)


class FetchPagesTest(unittest.TestCase):
    def test_categories_from_continued_responses_are_merged(self) -> None:
        first = {
            "continue": {"clcontinue": "1|b"},
            "query": {
                "pages": [
                    {
                        "title": "王公家軌範/公布時",
                        "revisions": [api_revision(9, "本文")],
                        "categories": [{"title": "Category:大正15年の法令"}],
                    }
                ]
            },
        }
        second = api_pages(
            {
                "title": "王公家軌範/公布時",
                "categories": [{"title": "Category:大正15年の皇室令"}],
            }
        )
        fake = FakeApi([first, second])
        pages = fetch_corpus.fetch_pages(fake.api(), revids=[9])
        self.assertEqual(
            pages,
            [
                Page(
                    "王公家軌範/公布時",
                    9,
                    "本文",
                    ["大正15年の法令", "大正15年の皇室令"],
                )
            ],
        )
        self.assertEqual(fake.requests[1]["clcontinue"], ["1|b"])

    def test_missing_pages_and_hidden_texts_are_left_out(self) -> None:
        hidden = {"revid": 5, "sha1hidden": True, "slots": {"main": {}}}
        fake = FakeApi(
            [
                api_pages(
                    {"title": "無い", "missing": True},
                    {"title": "隠す", "revisions": [hidden]},
                )
            ]
        )
        pages = fetch_corpus.fetch_pages(fake.api(), titles=["無い", "隠す"])
        self.assertEqual(pages, [])

    def test_unexpected_revision_ids_are_errors(self) -> None:
        for revid in ("../x", 0, -3, True):
            with self.subTest(revid=revid):
                response = api_pages(
                    {"title": "法", "revisions": [api_revision(revid, "本文")]}
                )
                fake = FakeApi([response])
                with self.assertRaises(fetch_corpus.CorpusError):
                    fetch_corpus.fetch_pages(fake.api(), titles=["法"])

    def test_sha1_that_does_not_match_the_text_is_an_error(self) -> None:
        revision = api_revision(9, "本文")
        revision["slots"]["main"]["content"] = "改竄"
        fake = FakeApi([api_pages({"title": "法", "revisions": [revision]})])
        with self.assertRaises(fetch_corpus.CorpusError):
            fetch_corpus.fetch_pages(fake.api(), revids=[9])

    def test_each_batch_is_handed_over_as_soon_as_it_is_fetched(self) -> None:
        titles = [f"法{n}" for n in range(fetch_corpus.BATCH_SIZE + 1)]
        first = api_pages(
            {"title": "法0", "revisions": [api_revision(1, "甲")]}
        )
        fake = FakeApi([first, http_error(404)])
        handed: list[list[int]] = []
        with self.assertRaises(fetch_corpus.CorpusError):
            fetch_corpus.fetch_pages(
                fake.api(),
                titles=titles,
                on_batch=lambda pages: handed.append([p.oldid for p in pages]),
            )
        self.assertEqual(handed, [[1]])
        self.assertEqual(fake.requests[1]["titles"], [titles[-1]])


class FetchCommandTest(unittest.TestCase):
    def test_changed_saved_file_is_fetched_again(self) -> None:
        good, changed = Page("甲", 1, "甲の本文", []), Page("乙", 2, "乙の本文", [])
        rows = [
            Revision(p.title, p.oldid, p.sha1, 1900, "all", "listed", "rule")
            for p in (good, changed)
        ]
        response = api_pages(
            {"title": "乙", "revisions": [api_revision(2, "乙の本文")]}
        )
        fake = FakeApi([response])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, good)
            fetch_corpus.save_page(work, Page("乙", 2, "書き換えた", []))
            with mock.patch.object(
                fetch_corpus, "read_revisions", return_value=rows
            ), contextlib.redirect_stderr(io.StringIO()):
                fetch_corpus._command_fetch(fake.api(), work)
            self.assertEqual(fake.requests[0]["revids"], ["2"])
            self.assertEqual(
                fetch_corpus.read_saved_page(work, 2), "乙の本文"
            )

    def test_renamed_page_gives_a_warning_only(self) -> None:
        page = Page("甲", 1, "本文", [])
        rows = [Revision("乙", 1, page.sha1, 1900, "all", "listed", "rule")]
        warnings = fetch_corpus.check_fetched(rows, [page])
        self.assertEqual(len(warnings), 1)
        self.assertIn("renamed", warnings[0])

    def test_fetched_revision_with_another_sha1_is_an_error(self) -> None:
        page = Page("甲", 1, "本文", [])
        rows = [Revision("甲", 1, "0" * 40, 1900, "all", "listed", "rule")]
        with self.assertRaises(fetch_corpus.CorpusError):
            fetch_corpus.check_fetched(rows, [page])


class ReclassifyCommandTest(unittest.TestCase):
    def test_index_from_older_tables_is_an_error(self) -> None:
        page = Page("刑法", 1, law(1880), [])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, page)
            fetch_corpus.save_page_index(work, [page], [])
            with mock.patch.object(
                fetch_corpus, "_write_classification"
            ) as write, self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus._command_reclassify(work)
            write.assert_not_called()

    def test_index_without_sha1_asks_for_revisions_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fetch_corpus.save_page(work, Page("刑法", 1, law(1880), []))
            index = {"pinned": [{"title": "刑法", "oldid": 1}], "listed": []}
            Path(work, "pages.json").write_text(json.dumps(index), "utf-8")
            with self.assertRaisesRegex(fetch_corpus.CorpusError, "SHA-1"):
                fetch_corpus.load_page_index(work)

    def test_index_of_another_form_is_a_corpus_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            Path(work, "pages.json").write_text('{"pinned": 1}', "utf-8")
            with self.assertRaises(fetch_corpus.CorpusError):
                fetch_corpus.load_page_index(work)


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
        self.assertEqual(
            titles, {"刑法": ["明治13年の法令", "明治13年の太政官布告"]}
        )
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
    def test_only_revisions_cited_by_examples_alone_are_examples_only(
        self,
    ) -> None:
        pinned = fetch_corpus.pinned_oldids()
        self.assertGreater(len(pinned), 0)
        # 218223 (株熱の余症恐るべし) is no longer cited, but a pinned row
        # stays until a person removes it.
        self.assertEqual(
            sorted(o for o, u in pinned.items() if u == "examples_only"),
            [201608, 218223, 218225, 236790, 236821],
        )


class PinnedOldidsUnionTest(unittest.TestCase):
    def test_revision_pinned_before_stays_pinned(self) -> None:
        rows = [
            Revision("某ノ詔書", 9, "a" * 40, 1900, "kanji_only", "pinned", "x"),
            Revision("株", 8, "b" * 40, 1893, "examples_only", "pinned", "x"),
            Revision("法", 7, "c" * 40, 1900, "all", "listed", "x"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            references = Path(directory, "references")
            references.mkdir()
            for name in ("kanji.tsv", "vocabulary.tsv", "examples.md"):
                Path(references, name).write_text("", encoding="utf-8")
            Path(references, "kanji.tsv").write_text(
                "https://ja.wikisource.org/w/index.php?oldid=5\n", "utf-8"
            )
            path = Path(directory, "corpus_revisions.tsv")
            fetch_corpus.write_revisions(rows, path)
            pinned = fetch_corpus.pinned_oldids(references, path)
        self.assertEqual(pinned, {5: "all", 9: "all", 8: "examples_only"})

    def test_pinned_revision_cited_only_by_examples_stays_all(self) -> None:
        rows = [Revision("告諭", 8, "a" * 40, 1936, "all", "pinned", "x")]
        with tempfile.TemporaryDirectory() as directory:
            references = Path(directory, "references")
            references.mkdir()
            for name in ("kanji.tsv", "vocabulary.tsv"):
                Path(references, name).write_text("", encoding="utf-8")
            Path(references, "examples.md").write_text(
                "https://ja.wikisource.org/w/index.php?oldid=8\n", "utf-8"
            )
            path = Path(directory, "corpus_revisions.tsv")
            fetch_corpus.write_revisions(rows, path)
            pinned = fetch_corpus.pinned_oldids(references, path)
        self.assertEqual(pinned, {8: "all"})


class ReclassifyPinnedTest(unittest.TestCase):
    def test_cited_revision_is_pinned_and_an_old_pin_stays(self) -> None:
        cited = Page("刑法", 1, law(1880), [])
        other = Page("法例", 2, law(1898), [])
        old_pin = Page("古い", 3, law(1890), [])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory, "work")
            references = Path(directory, "references")
            references.mkdir()
            Path(references, "kanji.tsv").write_text(
                "https://ja.wikisource.org/w/index.php?oldid=1\n", "utf-8"
            )
            for name in ("vocabulary.tsv", "examples.md"):
                Path(references, name).write_text("", encoding="utf-8")
            revisions = Path(directory, "corpus_revisions.tsv")
            fetch_corpus.write_revisions(
                [
                    Revision(
                        "古い", 3, old_pin.sha1, 1890, "all", "pinned", "pinned"
                    )
                ],
                revisions,
            )
            for page in (cited, other, old_pin):
                fetch_corpus.save_page(work, page)
            fetch_corpus.save_page_index(work, [old_pin], [cited, other])
            with mock.patch.object(
                fetch_corpus, "REFERENCES", references
            ), mock.patch.object(
                fetch_corpus, "REVISIONS_FILE", revisions
            ), contextlib.redirect_stderr(io.StringIO()):
                fetch_corpus._command_reclassify(work)
            rows = fetch_corpus.read_revisions(revisions)
        self.assertEqual(
            {row.oldid: row.source for row in rows},
            {1: "pinned", 2: "listed", 3: "pinned"},
        )


if __name__ == "__main__":
    unittest.main()
