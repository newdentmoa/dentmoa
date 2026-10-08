"""사이트 수집기 테스트 — 실제 사이트 대신 흔한 게시판 모양의 가짜 HTML 로 시험한다."""

from datetime import datetime, timedelta

import pytest
import requests

from dentmoa import config
from dentmoa.sources import htmlutil
from dentmoa.sources.base import FetchContext, LoginError, MissingCredentials, StructureError
from dentmoa.sources.browser import find_record, records_from_json

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=config.TZ)


def ctx(**kw):
    base = dict(since=NOW - timedelta(days=30), min_delay=0, max_delay=0, log=lambda m: None)
    base.update(kw)
    return FetchContext(**base)


# ───────────── 가짜 웹 ─────────────


class FakeWeb:
    """URL(앞부분 일치) → (상태코드, HTML) 또는 함수(method, url, kwargs) -> (상태, HTML)"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, session, method, url, *args, **kwargs):
        params = kwargs.get("params")
        if params:
            url = requests.Request("GET", url, params=params).prepare().url
        self.calls.append((method, url, kwargs.get("data")))
        for prefix, handler in self.routes:
            if url.startswith(prefix):
                status, html = handler(session, method, url, kwargs) if callable(handler) else handler
                r = requests.Response()
                r.status_code = status
                r._content = html.encode("utf-8")
                r.encoding = "utf-8"
                r.url = url
                r.headers["Content-Type"] = "text/html; charset=utf-8"
                return r
        raise AssertionError(f"예상하지 못한 요청: {method} {url}")


@pytest.fixture()
def fakeweb(monkeypatch, tmp_db):
    holder = {}

    def install(routes):
        web = FakeWeb(routes)
        monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: web(self, m, u, *a, **k))
        holder["web"] = web
        return web

    return install


# ───────────── htmlutil ─────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-10-08", datetime(2026, 10, 8, tzinfo=config.TZ)),
        ("2026.10.07 14:30", datetime(2026, 10, 7, 14, 30, tzinfo=config.TZ)),
        ("26.10.05", datetime(2026, 10, 5, tzinfo=config.TZ)),
        ("10-03", datetime(2026, 10, 3, tzinfo=config.TZ)),
        ("12-20", datetime(2025, 12, 20, tzinfo=config.TZ)),
        ("09:15", datetime(2026, 10, 8, 9, 15, tzinfo=config.TZ)),
        ("3시간 전", NOW - timedelta(hours=3)),
        ("2일 전", NOW - timedelta(days=2)),
        ("", None),
        ("조회 123", None),
    ],
)
def test_parse_board_date(text, expected):
    assert htmlutil.parse_board_date(text, NOW) == expected


GNU_LIST = """
<html><body><div id="header"><a href="/bbs/login.php">로그인</a><a href="/about">사이트 소개</a></div>
<table class="board_list">
<tr><th>번호</th><th>지역</th><th>제목</th><th>글쓴이</th><th>날짜</th></tr>
<tr class="notice"><td>공지</td><td></td><td><a href="view.php?no=1">게시판 이용 규칙 안내</a></td><td>운영자</td><td>2026-01-01</td></tr>
<tr><td>303</td><td>서울 강남</td><td><a href="view.php?no=303&page=1">[강남] 교정과 전문의 원장님 모십니다</a></td><td>김원장</td><td>10-07</td></tr>
<tr><td>302</td><td>경기 분당</td><td><a href="view.php?no=302&page=1">분당 GP 페이닥터 구합니다</a></td><td>이원장</td><td>2026-10-05</td></tr>
<tr><td>301</td><td>부산</td><td><a href="view.php?no=301&page=1">부산 서면 대진 원장님 구해요</a></td><td>박원장</td><td>2026-08-01</td></tr>
</table></body></html>
"""


def test_find_list_items_table():
    items = htmlutil.find_list_items(GNU_LIST, "https://board.example.com/recruit/list.php", now=NOW)
    ids = [i.source_id for i in items]
    assert ids == ["1", "303", "302", "301"]
    assert items[0].is_notice
    assert items[1].title == "[강남] 교정과 전문의 원장님 모십니다"
    assert items[1].url == "https://board.example.com/recruit/view.php?no=303&page=1"
    assert items[1].posted_at == datetime(2026, 10, 7, tzinfo=config.TZ)
    assert items[2].posted_at == datetime(2026, 10, 5, tzinfo=config.TZ)


def test_find_list_items_li_and_js_links():
    html = """<ul class="list">
    <li class="item"><a href="javascript:fn_view('5501')">2026년 하반기 임상강사(전임의) 채용 공고</a><span>2026.10.01</span></li>
    <li class="item"><a href="javascript:fn_view('5500')">2026년 치과위생사 채용 공고</a><span>2026.09.28</span></li>
    <li class="item"><a href="javascript:fn_view('5499')">진료교수(소아치과) 초빙 공고</a><span>2026.09.20</span></li>
    </ul>"""
    items = htmlutil.find_list_items(html, "https://hosp.example.com/recruit/list.do", now=NOW)
    assert [i.source_id for i in items] == ["5501", "5500", "5499"]
    assert items[0].posted_at == datetime(2026, 10, 1, tzinfo=config.TZ)


def test_extract_main_text_and_labels():
    html = """<html><body><nav>메뉴 메뉴 메뉴</nav>
    <div class="view_content"><p>근무지역 : 서울 강남구 역삼동</p><p>근무: 주 4.5일</p><p>급여: 세후 1,500</p></div>
    <footer>회사 정보</footer></body></html>"""
    text = htmlutil.extract_main_text(html)
    assert "근무지역 : 서울 강남구 역삼동" in text
    assert "메뉴" not in text
    assert htmlutil.find_label_value(html, ["근무지역"]) == "서울 강남구 역삼동"


def test_find_login_form():
    html = """<form action="/bbs/login_check.php" method="post">
    <input type="hidden" name="url" value="">
    <input type="text" name="mb_id"><input type="password" name="mb_password">
    <input type="checkbox" name="auto_login"><input type="submit" value="로그인"></form>"""
    f = htmlutil.find_login_form(html, "https://board.example.com/bbs/login.php")
    assert f.action == "https://board.example.com/bbs/login_check.php"
    assert f.user_field == "mb_id" and f.pass_field == "mb_password"
    assert "auto_login" not in f.fields
    assert htmlutil.looks_like_login_page(html)
    assert not htmlutil.looks_like_login_page(GNU_LIST)


def test_records_from_json():
    data = {"data": {"items": [
        {"id": 11, "title": "[서울 송파] 소아치과 전문의 모십니다", "region": {"sido": "서울", "gu": "송파구"}, "createdAt": "2026-10-07T10:00:00"},
        {"id": 12, "title": "보존과 원장님 구인", "region": "경기 수원", "createdAt": "2026-10-06T10:00:00"},
    ]}}
    recs = records_from_json(data)
    assert [r["id"] for r in recs] == ["11", "12"]
    assert "송파구" in recs[0]["region"]
    assert find_record({"result": {"id": 12, "title": "보존과 원장님 구인", "content": "<p>본문</p>"}}, "12")["body"] == "<p>본문</p>"


# ───────────── 덴트포토 ─────────────

LOGIN_PAGE = """<html><body><p>로그인 후 이용 가능합니다.</p>
<form action="/login_check.php" method="post"><input type="hidden" name="url" value="">
<input type="text" name="user_id"><input type="password" name="user_pw"></form></body></html>"""

DETAIL_303 = """<html><body><div class="view_content">
[강남] 교정과 전문의 원장님 모십니다
근무지 : 서울 강남구 역삼동
근무: 주 4.5일 / 급여: 세후 1,500
마감: 채용시</div></body></html>"""


def _dentphoto_routes(state):
    def list_page(session, method, url, kw):
        return (200, GNU_LIST) if state.get("logged_in") else (200, LOGIN_PAGE)

    def login(session, method, url, kw):
        assert method.upper() == "POST"
        assert kw["data"]["user_id"] == "me" and kw["data"]["user_pw"] == "secret"
        state["logged_in"] = True
        return 200, "<script>location.href='/recruit_dental/list.php'</script>"

    return [
        ("https://board.dentphoto.com/recruit_dental/list.php", list_page),
        ("https://board.dentphoto.com/login_check.php", login),
        ("https://board.dentphoto.com/recruit_dental/view.php?no=303", (200, DETAIL_303)),
        ("https://board.dentphoto.com/recruit_dental/view.php?no=302", (200, "<div class='view_content'>분당 GP 구합니다. 주5일</div>")),
    ]


def test_dentphoto_login_and_fetch(fakeweb, monkeypatch):
    from dentmoa.sources import dentphoto

    monkeypatch.setattr(htmlutil.config, "now", lambda: NOW)
    state = {}
    web = fakeweb(_dentphoto_routes(state))
    c = ctx(secrets={"dentphoto_id": "me", "dentphoto_pw": "secret"}, max_pages=1)
    got = list(dentphoto.DentphotoSource().fetch(c))
    assert state["logged_in"]
    ids = [g.source_id for g in got]
    assert ids == ["303", "302"]  # 공지 제외, 30일보다 오래된 301 제외
    first = got[0]
    assert "세후 1,500" in first.body
    assert first.region_hint.startswith("서울 강남구")

    # 이미 본 글은 본문을 다시 열지 않는다
    web.calls.clear()
    c2 = ctx(secrets={"dentphoto_id": "me", "dentphoto_pw": "secret"}, max_pages=1, known_ids={"303", "302"})
    got2 = list(dentphoto.DentphotoSource().fetch(c2))
    assert [g.source_id for g in got2] == ["303", "302"]
    assert not any("view.php" in u for _, u, _ in web.calls)


def test_dentphoto_missing_credentials(tmp_db):
    from dentmoa.sources import dentphoto

    with pytest.raises(MissingCredentials):
        list(dentphoto.DentphotoSource().fetch(ctx(secrets={})))


def test_dentphoto_wrong_password(fakeweb, monkeypatch):
    from dentmoa.sources import dentphoto

    fakeweb([
        ("https://board.dentphoto.com/recruit_dental/list.php", (200, LOGIN_PAGE)),
        ("https://board.dentphoto.com/login_check.php", (200, "<script>alert('비밀번호가 일치하지 않습니다.');history.back();</script>")),
    ])
    with pytest.raises(LoginError):
        list(dentphoto.DentphotoSource().fetch(ctx(secrets={"dentphoto_id": "me", "dentphoto_pw": "bad"})))


# ───────────── 잡알리오 ─────────────

ALIO_LIST = """<table class="tbl"><tr><th>번호</th><th>기관명</th><th>제목</th><th>근무지</th><th>등록일</th><th>마감일</th></tr>
<tr><td>10</td><td>서울대학교치과병원</td><td><a href="/recruitview.do?idx=777&pageNo=1">2026년 제5차 직원(임상교수 등) 채용 공고</a></td><td>서울</td><td>2026.10.02</td><td>2026.10.16</td></tr>
<tr><td>9</td><td>서울대학교치과병원</td><td><a href="/recruitview.do?idx=776&pageNo=1">2026년 치과위생사 채용</a></td><td>서울</td><td>2026.09.30</td><td>2026.10.10</td></tr>
<tr><td>8</td><td>부산대학교치과병원</td><td><a href="/recruitview.do?idx=700&pageNo=1">전임의 채용</a></td><td>경남</td><td>2026.09.29</td><td>2026.10.12</td></tr>
</table>"""


def test_alio_fetch(fakeweb):
    from dentmoa.sources import alio

    fakeweb([
        ("https://job.alio.go.kr/recruit.do", (200, ALIO_LIST)),
        ("https://job.alio.go.kr/recruitview.do?idx=777", (200, "<div class='recruitView'>채용분야: 소아치과 임상교수 1명\n접수기간: 2026.10.02 ~ 2026.10.16</div>")),
        ("https://job.alio.go.kr/recruitview.do?idx=776", (200, "<div class='recruitView'>치과위생사 2명</div>")),
        ("https://job.alio.go.kr/recruitview.do?idx=700", (200, "<div class='recruitView'>구강악안면외과 전임의 1명</div>")),
    ])
    got = list(alio.AlioSource().fetch(ctx(max_pages=1)))
    assert {g.source_id for g in got} == {"777", "776", "700"}
    g = next(x for x in got if x.source_id == "777")
    assert g.institution_hint == "서울대학교치과병원"
    assert "소아치과 임상교수" in g.body


def test_alio_structure_error(fakeweb):
    from dentmoa.sources import alio

    fakeweb([("https://job.alio.go.kr/recruit.do", (200, "<html><body>점검 중입니다</body></html>"))])
    with pytest.raises(StructureError):
        list(alio.AlioSource().fetch(ctx(max_pages=1)))


# ───────────── 병원 게시판 ─────────────


def test_hospital_boards(fakeweb, monkeypatch):
    from dentmoa import institutions
    from dentmoa.institutions import Institution
    from dentmoa.sources import hospitals

    boards = [
        Institution("가나대학교치과병원", "dental_univ_hospital", "서울", "종로구", recruit_url="https://dent.example.ac.kr/recruit/list"),
        Institution("다라병원", "general_hospital", "부산", "해운대구", recruit_url="https://dara.example.com/board"),
        Institution("고장난병원", "general_hospital", "부산", "해운대구", recruit_url="https://broken.example.com/"),
    ]
    monkeypatch.setattr(institutions, "load", lambda: boards)
    board1 = """<table><tr><td><a href="/recruit/view?idx=31">2026년 진료교수(소아치과) 초빙 공고</a></td><td>2026-10-01</td></tr>
    <tr><td><a href="/recruit/view?idx=30">2026년 간호사 채용</a></td><td>2026-09-30</td></tr>
    <tr><td><a href="/recruit/view?idx=29">2026년 하반기 직원 채용 공고</a></td><td>2026-09-29</td></tr></table>"""
    board2 = """<ul><li><a href="/board/read?no=5">내과 전문의 채용</a> 2026-10-02</li>
    <li><a href="/board/read?no=4">치과 전문의 채용 (구강악안면외과)</a> 2026-10-01</li>
    <li><a href="/board/read?no=3">행정직 채용</a> 2026-09-01</li></ul>"""
    fakeweb([
        ("https://dent.example.ac.kr/recruit/list", (200, board1)),
        ("https://dent.example.ac.kr/recruit/view?idx=31", (200, "<div class='board_view'>소아치과 진료교수 1명, 접수 ~10.15</div>")),
        ("https://dent.example.ac.kr/recruit/view?idx=29", (200, "<div class='board_view'>치과위생사, 행정직, 임상강사 모집</div>")),
        ("https://dara.example.com/board", (200, board2)),
        ("https://dara.example.com/board/read?no=4", (200, "<div class='board_view'>치과 구강악안면외과 전문의 1명</div>")),
        ("https://broken.example.com/", (500, "error")),
    ])
    got = list(hospitals.HospitalBoardsSource().fetch(ctx(max_pages=1)))
    titles = [g.title for g in got]
    assert "2026년 진료교수(소아치과) 초빙 공고" in titles
    assert "2026년 하반기 직원 채용 공고" in titles  # 치과 기관의 일반 채용 공고는 본문까지 본다
    assert "2026년 간호사 채용" not in titles
    assert "치과 전문의 채용 (구강악안면외과)" in titles
    assert "내과 전문의 채용" not in titles  # 일반 병원은 제목에 치과 관련 단어가 있어야 함
    g = next(x for x in got if "소아치과" in x.title)
    assert g.institution_hint == "가나대학교치과병원" and g.region_hint == "서울 종로구"
    from dentmoa import db

    status = db.kv_get("board_status")
    assert status["고장난병원"]["ok"] is False and status["다라병원"]["ok"] is True


# ───────────── 하이브레인넷 ─────────────

HIBRAIN_LIST = """<ul class="recruitList">
<li class="row"><a href="/recruitment/recruits/3600299?listType=ING">2027학년도 1학기 전임교원 초빙</a><span class="org">경북대학교</span><span>2026.10.06</span></li>
<li class="row"><a href="/recruitment/recruits/3600298?listType=ING">기계공학과 연구교수 초빙</a><span class="org">한국공과대학교</span><span>2026.10.06</span></li>
<li class="row"><a href="/recruitment/recruits/3600297?listType=ING">치의학전문대학원 기금교수 채용</a><span class="org">부산대학교</span><span>2026.10.05</span></li>
<li class="row"><a href="/recruitment/recruits/3600296?listType=ING">영어영문학과 강사 채용</a><span class="org">서울대학교</span><span>2026.10.05</span></li>
</ul>"""


def test_hibrain_filters_titles(fakeweb):
    from dentmoa.sources import hibrain

    web = fakeweb([
        ("https://www.hibrain.net/recruitment/recruits?", (200, HIBRAIN_LIST)),
        ("https://www.hibrain.net/recruitment/recruits/3600299", (200, "<div class='content'>치과대학 소아치과학 분야 1명</div>")),
        ("https://www.hibrain.net/recruitment/recruits/3600297", (200, "<div class='content'>구강악안면외과학 기금교수</div>")),
    ])
    got = list(hibrain.HibrainSource().fetch(ctx(max_pages=1)))
    assert {g.source_id for g in got} == {"3600299", "3600297"}
    assert not any("3600298" in u or "3600296" in u for _, u, _ in web.calls)


# ───────────── 나라일터 ─────────────

GOJOBS_LIST = """<table><tr><th>번호</th><th>제목</th><th>기관</th><th>등록일</th><th>마감일</th><th>조회</th></tr>
<tr><td>3</td><td><a href="#" onclick="fn_apmView('020','305296');return false;">보건소 치과의사(임기제공무원) 채용 공고</a></td><td>서울특별시 강남구</td><td>2026.10.07</td><td>2026.10.17</td><td>12</td></tr>
<tr><td>2</td><td><a href="#" onclick="fn_apmView('020','305295');return false;">기간제 행정직 채용</a></td><td>OO시청</td><td>2026.10.07</td><td>2026.10.14</td><td>5</td></tr>
</table>"""


def test_gojobs(fakeweb):
    from dentmoa.sources import gojobs

    fakeweb([
        ("https://www.gojobs.go.kr/apmList.do?menuNo=401&pageIndex=1", (200, GOJOBS_LIST)),
        ("https://www.gojobs.go.kr/apmList.do", (200, "<table></table>")),
        ("https://www.gojobs.go.kr/apmView.do?empmnsn=305296", (200, "<div class='content'>근무지 : 서울특별시 강남구 보건소\n치과의사 1명</div>")),
    ])
    got = list(gojobs.GojobsSource().fetch(ctx(max_pages=1)))
    assert [g.source_id for g in got] == ["305296"]
    assert got[0].institution_hint == "서울특별시 강남구"
