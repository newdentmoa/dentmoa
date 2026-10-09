"""실제 사이트(2026-10) 화면을 줄여 만든 tests/fixtures/sites 로 수집기를 시험한다 (네트워크 없이).

화면 모양이 바뀌면 이 테스트가 먼저 깨진다 → `python -m dentmoa probe <출처>` 로 새 화면을 받아 고친다.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

from dentmoa import config
from dentmoa.sources import htmlutil
from dentmoa.sources.base import FetchContext, LoginError

from test_sources import FakeWeb, fakeweb  # noqa: F401  (pytest 고정 장치)

FIX = Path(__file__).parent / "fixtures" / "sites"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=config.TZ)


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def ctx(**kw):
    base = dict(since=NOW - timedelta(days=60), min_delay=0, max_delay=0, log=lambda m: None)
    base.update(kw)
    return FetchContext(**base)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(config, "now", lambda: NOW)


def json_route(obj):
    def handler(session, method, url, kw):
        return 200, json.dumps(obj, ensure_ascii=False)
    return handler


# ───────────── 잡알리오 ─────────────


def test_alio_parse_list_real_layout():
    from dentmoa.sources.alio import AlioSource

    items = AlioSource().parse_list(fx("alio_list.html"), "https://job.alio.go.kr/recruit.do")
    by = {i.source_id: i for i in items}
    assert set(by) == {"305729", "305667", "305335"}
    it = by["305335"]
    # 링크 글자는 비어 있고 제목은 링크 뒤에 있다
    assert it.title == "[중앙보훈병원] 전문의(서울요양병원, 치과병원 치주과) 채용 공고"
    assert it.institution == "한국보훈복지의료공단" and it.region == "서울"
    assert it.posted_at == datetime(2026, 9, 23, tzinfo=config.TZ)
    assert it.deadline_text == "26.10.07"
    assert it.url == "https://job.alio.go.kr/recruitview.do?idx=305335"


def test_alio_fetch_posts_search_and_reads_detail(fakeweb):
    from dentmoa.sources import alio

    web = fakeweb([
        ("https://job.alio.go.kr/recruit.do", (200, fx("alio_list.html"))),
        ("https://job.alio.go.kr/recruitview.do?idx=", (200, fx("alio_view.html"))),
    ])
    got = list(alio.AlioSource().fetch(ctx(max_pages=1)))
    posts = [c for c in web.calls if c[0].upper() == "POST"]
    assert posts and posts[0][2]["keyword"] == "치과"  # 검색은 폼 POST
    assert {p[2].get("org_name") for p in posts[1:]} == set(alio.DENTAL_ORGS)
    assert len(got) == 3  # 같은 글은 여러 검색에 나와도 한 번만
    g = next(x for x in got if x.source_id == "305335")
    assert "치주과 전문의 자격증" in g.body
    assert "진행중인 채용공고" not in g.body  # 옆 광고 칸은 뺀다
    assert g.institution_hint == "한국보훈복지의료공단" and g.region_hint == "서울"
    assert g.extra["original_url"].startswith("https://www.bohun.or.kr/")


# ───────────── 하이브레인넷 ─────────────


def test_hibrain_list_ignores_side_ads_and_dept_names():
    from dentmoa.sources.hibrain import HibrainSource

    src = HibrainSource()
    items = src.parse_list(fx("hibrain_list.html"), "https://www.hibrain.net/recruitment/recruits")
    assert [i.source_id for i in items] == ["3600883", "3600605", "3600196", "3592631"]
    first = items[0]
    assert first.posted_at == datetime(2026, 10, 8, tzinfo=config.TZ)  # 등록일 (접수 시작일이 아님)
    assert first.deadline_text == "26.10.06 ~ 26.12.15"
    wanted = {i.source_id: src.wanted(i) for i in items}
    assert wanted == {"3600883": True, "3600605": False, "3600196": True, "3592631": True}  # 3600605 = 투자유치과


def test_hibrain_detail_and_members_only(fakeweb):
    from dentmoa.sources import hibrain

    fakeweb([
        ("https://www.hibrain.net/recruitment/recruits?", (200, fx("hibrain_list.html"))),
        ("https://www.hibrain.net/recruitment/recruits/3600883", (200, fx("hibrain_view.html"))),
        ("https://www.hibrain.net/recruitment/recruits/3592631", (200, fx("hibrain_view_members_only.html"))),
        ("https://www.hibrain.net/recruitment/recruits/3600196", (200, fx("hibrain_view_members_only.html"))),
    ])
    got = {g.source_id: g for g in hibrain.HibrainSource().fetch(ctx(since=NOW - timedelta(days=90), max_pages=1))}
    assert "3600605" not in got
    g = got["3600883"]
    assert g.institution_hint == "연세대학교 치과대학 구강생물학교실"
    assert g.region_hint == "서울"
    assert "박사후연구원" in g.body  # <meta description> 요약
    assert "접수기간" in g.body
    m = got["3592631"]
    assert m.title == "제주대학교병원 치과 임상/진료교수 모집"
    assert m.body == "접수기간: 상시" and m.extra.get("members_only")


# ───────────── 나라일터 ─────────────


def test_gojobs_keyword_search_and_hidden_textarea_body(fakeweb):
    from dentmoa.sources import gojobs

    web = fakeweb([
        ("https://www.gojobs.go.kr/apmList.do", lambda s, m, u, kw: (200, fx("gojobs_list.html") if kw["data"]["searchKeyword"] == "치과" else "<table></table>")),
        ("https://www.gojobs.go.kr/apmView.do?empmnsn=303189", (200, fx("gojobs_view.html"))),
        ("https://www.gojobs.go.kr/apmView.do?empmnsn=", (200, "<table id='apmViewTbl'><tr><th>공고명</th><td>다른 공고</td></tr></table>")),
    ])
    got = list(gojobs.GojobsSource().fetch(ctx(since=NOW - timedelta(days=90), max_pages=1)))
    assert [c[2]["searchKeyword"] for c in web.calls if c[0].upper() == "POST"] == ["치과", "구강"]
    g = next(x for x in got if x.source_id == "303189")
    assert g.title == "서대문구보건소 시간선택제임기제공무원(치과의사) 채용 공고"
    assert g.institution_hint == "서울특별시 서대문구 보건소"
    assert g.region_hint == "서울특별시 서대문구"
    assert "구강보건센터" in g.body  # 숨겨진 textarea 안의 공고 내용
    assert "02-000-0000" in g.body
    assert g.posted_at == datetime(2026, 9, 9, tzinfo=config.TZ)


# ───────────── 덴트포토 ─────────────

DP_LIST = """<table><tr><th>번호</th><th>지역</th><th>제목</th><th>글쓴이</th><th>날짜</th></tr>
<tr><td>9012</td><td>서울</td><td><a href="content.php?no=9012&page=1">[송파] 소아치과 원장님 모십니다</a></td><td>김원장</td><td>2026-10-08</td></tr>
<tr><td>9011</td><td>경기</td><td><a href="content.php?no=9011&page=1">수원 페이닥터 구인</a></td><td>이원장</td><td>2026-10-07</td></tr>
</table>"""


def test_dentphoto_follows_script_redirect_to_member_login(fakeweb):
    from dentmoa.sources import dentphoto

    state = {}

    def list_page(session, method, url, kw):
        return (200, DP_LIST) if state.get("ok") else (200, fx("dentphoto_list_logged_out.html"))

    def check(session, method, url, kw):
        assert method.upper() == "POST"
        assert kw["data"]["dp_id"] == "me" and kw["data"]["dp_password"] == "pa#ss$word"
        assert kw["data"]["login_url"]  # 원래 가려던 주소를 함께 보낸다
        state["ok"] = True
        return 200, "<script>location.replace('https://board.dentphoto.com/recruit_dental/list.php');</script>"

    fakeweb([
        ("https://board.dentphoto.com/recruit_dental/list.php", list_page),
        ("https://member.dentphoto.com/login/login.php", (200, fx("dentphoto_login.html"))),
        ("https://member.dentphoto.com/login/login_check.php", check),
        ("https://board.dentphoto.com/recruit_dental/content.php?no=", (200, "<div class='content'>소아치과 전문의, 주 4일</div>")),
    ])
    got = list(dentphoto.DentphotoSource().fetch(ctx(secrets={"dentphoto_id": "me", "dentphoto_pw": "pa#ss$word"}, max_pages=1)))
    assert state["ok"]
    assert [g.source_id for g in got] == ["9012", "9011"]
    assert got[0].url == "https://board.dentphoto.com/recruit_dental/content.php?no=9012&page=1"


def test_dentphoto_real_failure_message(fakeweb):
    from dentmoa.sources import dentphoto

    fakeweb([
        ("https://board.dentphoto.com/recruit_dental/list.php", (200, fx("dentphoto_list_logged_out.html"))),
        ("https://member.dentphoto.com/login/login.php", (200, fx("dentphoto_login.html"))),
        ("https://member.dentphoto.com/login/login_check.php", (200, fx("dentphoto_login_fail.html"))),
    ])
    with pytest.raises(LoginError, match="맞지 않습니다"):
        list(dentphoto.DentphotoSource().fetch(ctx(secrets={"dentphoto_id": "me", "dentphoto_pw": "bad"})))


# ───────────── 모어덴 ─────────────


def test_moreden_api_records():
    from dentmoa.sources import moreden

    a = json.loads(fx("moreden_article.json"))["article"]
    it = moreden.item_from_api(a)
    assert it["id"] == "73304" and it["url"] == "https://moreden.co.kr/recruit/doctor/73304"
    assert it["posted_at"] == datetime(2026, 10, 7, 10, 21, 30, tzinfo=config.TZ)
    assert it["institution"] == "예시치과의원 공덕점"
    body = moreden.body_from_article(a)
    assert body.splitlines()[:5] == [
        "위치: 서울특별시 마포구 마포대로 1 (공덕동) 2층",
        "병원: 예시치과의원 공덕점",
        "전공: 전공무관",
        "근무형태: 파트타임 근무",
        "급여: 일급 세후 30만원",
    ]
    assert "구강검진 및 일반 진료" in body
    assert moreden.majors_text("AE") == "전공무관 / 소아치과"
    assert moreden.wage_text({"wage_type": "monthly", "gross_wage": 15000000, "net_wage": None}) == "월급 세전 1500만원"
    assert moreden.wage_text({}) == "협의"


class _Page:
    def __init__(self, html=""):
        self.html = html

    def content(self):
        return self.html


def test_moreden_merge_uses_list_json_not_hot_items():
    from dentmoa.sources import moreden
    from dentmoa.sources.browser import BrowserSession, CapturedJSON

    a = json.loads(fx("moreden_article.json"))["article"]
    listing = {"board": [dict(a, bid=73304), dict(a, bid=73305, title="소아치과 원장님 구인", terminated_at="2026-10-08")],
               "page": "1", "pages": 3, "hot_items": [dict(a, bid=1, title="인기 글")]}
    sess = BrowserSession(page=_Page(), context=None,
                          captured=[CapturedJSON("https://public-api.moreden.co.kr/community/management/recruit/list?page=1", listing)])
    items: dict = {}
    moreden.MoredenSource()._merge(items, sess)
    assert set(items) == {"73304", "73305"}
    assert items["73305"]["closed"] is True
    assert not moreden.MoredenSource()._last_page(sess, 1) and moreden.MoredenSource()._last_page(sess, 3)


# ───────────── recruiter.co.kr · incruit (병원 채용 플랫폼) ─────────────


def test_recruiter_items_and_body(fakeweb):
    from dentmoa.sources import platforms
    from dentmoa.sources.base import PoliteSession

    web = fakeweb([("https://pnuh.recruiter.co.kr/app/jobnotice/list.json", json_route(json.loads(fx("recruiter_list.json"))))])
    http = PoliteSession("t", ctx(), persist_cookies=False)
    items = platforms.recruiter_items(http, "pnuh", "치과")
    assert web.calls[0][2]["keyword"] == "치과" and web.calls[0][2]["searchByNameOnly"] == "true"
    it = items[0]
    assert it.sid == "rc-pnuh:267982" and it.title == "영상의학과 전문의 모집 공고"
    assert it.url == "https://pnuh.recruiter.co.kr/app/jobnotice/view?systemKindCode=MRS2&jobnoticeSn=267982"
    assert it.posted_at == datetime(2026, 10, 1, 9, 0, tzinfo=config.TZ)
    assert it.period == "2026.10.01 09:00 ~ 2026.10.15 17:00" and it.category == "전문의"
    body = platforms.recruiter_body(fx("recruiter_view.html"), it)
    assert body.startswith("접수기간: 2026.10.01 09:00 ~ 2026.10.15 17:00\n구분: 전문의")
    assert "첨부: 붙임4_[서식] 제출서류(2025년도 기준).zip" in body
    assert platforms.recruiter_host("https://snudh.recruiter.co.kr/career/home") == "snudh"
    assert platforms.recruiter_host("https://www.snudh.org/") == ""


def test_hospitals_recruiter_board_shared_hints(fakeweb, monkeypatch):
    from dentmoa import institutions
    from dentmoa.institutions import Institution
    from dentmoa.sources import hospitals

    lst = json.loads(fx("recruiter_list.json"))
    lst["list"][0]["jobnoticeName"] = "치과진료센터 진료교수 모집"
    lst["list"][1]["jobnoticeName"] = "[일산백병원] 치과 계약직 치과위생사 모집"
    boards = [Institution("인제대학교 상계백병원", "univ_hospital_dept", "서울", "노원구",
                          recruit_url="https://paik.recruiter.co.kr/app/jobnotice/list", shared=True)]
    monkeypatch.setattr(institutions, "load", lambda: boards)
    fakeweb([
        ("https://paik.recruiter.co.kr/app/jobnotice/list.json", json_route(lst)),
        ("https://paik.recruiter.co.kr/app/jobnotice/view", (200, fx("recruiter_view.html"))),
    ])
    got = list(hospitals.HospitalBoardsSource().fetch(ctx(max_pages=1)))
    assert [g.title for g in got] == ["치과진료센터 진료교수 모집"]  # 위생사 글·치과 없는 글은 뺀다
    assert got[0].source_id == "rc-paik:267982"
    assert got[0].institution_hint == "" and got[0].region_hint == ""  # 여러 병원 공동 사이트


def test_incruit_list_titles_not_buttons():
    items = htmlutil.find_list_items(fx("incruit_list.html"), "https://recruit.incruit.com/khmc/job/", now=NOW)
    assert [i.title for i in items][:2] == ["계약직 신입직원 [간호본부 조무사] 공개채용 모집공고_", "계약직 신입직원 [사무직] 공개채용 모집"]
    assert items[0].source_id == "2610080014" and items[0].posted_at == datetime(2026, 10, 8, tzinfo=config.TZ)


def test_hospital_title_filter_dental():
    from dentmoa.institutions import Institution
    from dentmoa.sources.hospitals import is_relevant_title

    khmc = Institution("경희대학교치과병원", "dental_univ_hospital", "서울", "동대문구", title_filter="dental")
    assert not is_relevant_title("계약직 신입직원 [사무직] 공개채용 모집", khmc)
    assert is_relevant_title("[치과병원] 소아치과 임상교수 모집", khmc)


# ───────────── 게시판 일반 읽기 (htmlutil) ─────────────

MENU_AND_TABLE = """<div id="gnb"><ul>
<li><a href="/sub/intro.do">병원소개 안내</a></li><li><a href="/sub/greet.do">병원장 인사말</a></li>
<li><a href="/sub/vision.do">비전 및 미션</a></li><li><a href="/sub/recruit.do">채용공고 안내</a></li></ul></div>
<table class="board"><tbody>
<tr><td>683</td><td class="subject"><a href="view.do?menuNo=29060337&empSeq=6652">2026년 하반기 임상교수 채용 공고</a></td><td>2026-10-01 ~ 2026-10-15</td><td>접수중</td></tr>
<tr><td>682</td><td class="subject"><a href="view.do?menuNo=29060337&empSeq=6651&pageIndex=2">치과위생사 채용 공고</a></td><td>2026-09-30 ~ 2026-10-14</td><td>접수중</td></tr>
</tbody></table>"""

CARDS = """<div class="bbs-list">
<div class="bbs-item"><a class="inner" href="?mode=view&articleNo=59251"><div class="subject-area"><strong class="subject">2027년 임상교수 초빙 공고</strong></div><div class="info-area"><span class="date">2026-10-02</span></div></a></div>
<div class="bbs-item"><a class="inner" href="?mode=view&articleNo=59250"><div class="subject-area"><strong class="subject">2027년 연구교수 초빙 공고</strong></div><div class="info-area"><span class="date">2026-09-30</span></div></a></div>
</div>"""


def test_dated_rows_beat_menus_and_ids_ignore_paging():
    items = htmlutil.find_list_items(MENU_AND_TABLE, "https://www.snudh.org/portal/blindEmpmn/list.do", now=NOW)
    assert [i.title for i in items] == ["2026년 하반기 임상교수 채용 공고", "치과위생사 채용 공고"]
    assert [i.source_id for i in items] == ["empSeq=6652&menuNo=29060337", "empSeq=6651&menuNo=29060337"]
    assert items[0].posted_at == datetime(2026, 10, 1, tzinfo=config.TZ)


def test_card_list_with_date_inside_link():
    items = htmlutil.find_list_items(CARDS, "https://dentistry.yonsei.ac.kr/dentistry/news/recruit.do", now=NOW)
    assert [(i.title, i.source_id) for i in items] == [("2027년 임상교수 초빙 공고", "59251"), ("2027년 연구교수 초빙 공고", "59250")]
    assert items[1].posted_at == datetime(2026, 9, 30, tzinfo=config.TZ)


def test_menu_links_are_not_postings_and_empty_board():
    empty = '<div id="gnb"><a href="/a.do">채용공고 안내</a><a href="/b.do">병원 소개 안내</a><a href="/c.do">오시는 길 안내</a></div>' \
            '<table><tr><td>데이터가 없습니다.</td></tr></table>'
    assert htmlutil.find_list_items(empty, "https://x.example.com/list.do", now=NOW) == []
    assert htmlutil.looks_empty_board(empty)
    assert not htmlutil.looks_empty_board(MENU_AND_TABLE)


def test_future_date_in_list_is_a_deadline():
    html = """<ul><li><a href="view.jsp?adoptcnt=242">약사 채용 공고</a><span>마감일 2026.12.31</span></li>
    <li><a href="view.jsp?adoptcnt=241">치과 임상강사 채용 공고</a><span>마감일 2026.11.30</span></li></ul>"""
    items = htmlutil.find_list_items(html, "https://recruit.hallym.or.kr/index.jsp", now=NOW)
    assert items[0].posted_at is None and "2026.12.31" in items[0].deadline_text


def test_compact_date_and_js_redirect():
    assert htmlutil.parse_board_date("20261007102130", NOW) == datetime(2026, 10, 7, 10, 21, 30, tzinfo=config.TZ)
    assert htmlutil.parse_board_date("20261007", NOW) == datetime(2026, 10, 7, tzinfo=config.TZ)
    assert htmlutil.js_redirect_url(fx("dentphoto_list_logged_out.html")).startswith("https://member.dentphoto.com/login/login.php")
    assert htmlutil.js_redirect_url("<html>" + "x" * 5000 + "location.replace('/a')</html>") == ""


def test_dental_school_board_labels_only_dental_posts(fakeweb, monkeypatch):
    from dentmoa import institutions
    from dentmoa.institutions import Institution
    from dentmoa.sources import hospitals

    boards = [Institution("강원대학교 치과대학", "dental_school", "강원", "춘천시",
                          recruit_url="https://faculty.kangwon.ac.kr/bbs/board.php?bo_table=notice")]
    monkeypatch.setattr(institutions, "load", lambda: boards)
    board = """<table><tr><td>405</td><td><a href="board.php?bo_table=notice&wr_id=405">2026년 객원교수 공개채용 공고(철학과)</a></td><td>2026-09-30</td></tr>
    <tr><td>404</td><td><a href="board.php?bo_table=notice&wr_id=404">2026년 하반기 전임교원 공개채용 공고</a></td><td>2026-09-29</td></tr></table>"""
    fakeweb([
        ("https://faculty.kangwon.ac.kr/bbs/board.php?bo_table=notice&wr_id=405", (200, "<div class='content'>철학과 객원교수 1명</div>")),
        ("https://faculty.kangwon.ac.kr/bbs/board.php?bo_table=notice&wr_id=404", (200, "<div class='content'>치의학전문대학원 소아치과학 1명, 의과대학 2명</div>")),
        ("https://faculty.kangwon.ac.kr/bbs/board.php", (200, board)),
    ])
    got = {g.source_id.split(":")[1]: g for g in hospitals.HospitalBoardsSource().fetch(ctx(max_pages=1))}
    assert got["405"].institution_hint == "" and got["405"].region_hint == "강원 춘천시"
    assert got["404"].institution_hint == "강원대학교 치과대학"


def test_moreden_detail_reads_article_json():
    from dentmoa.sources import moreden
    from dentmoa.sources.browser import BrowserSession, CapturedJSON

    article = json.loads(fx("moreden_article.json"))

    class Page(_Page):
        url = ""

        def goto(self, url, **kw):
            self.url = url
            sess.captured.append(CapturedJSON("https://public-api.moreden.co.kr/community/management/recruit/article/73304", article))

        def wait_for_load_state(self, *a, **kw):
            pass

        def wait_for_timeout(self, ms):
            pass

    sess = BrowserSession(page=Page("<html></html>"), context=None)
    it = moreden.item_from_api(article["article"])
    raw = moreden.MoredenSource()._detail(sess, ctx(), it)
    assert raw.source_id == "73304" and raw.title == "10월 10일 토요일 근무 가능하신 선생님 모십니다."
    assert raw.region_hint.startswith("서울특별시 마포구") and raw.institution_hint == "예시치과의원 공덕점"
    assert raw.body.startswith("위치: 서울특별시 마포구") and raw.author == "글쓴이"
    assert raw.extra == {"major": "전공무관", "working_time": "파트타임 근무"}
