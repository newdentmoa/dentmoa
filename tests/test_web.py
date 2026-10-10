"""웹 대시보드: 로그인, CSRF, 화면, 설정 저장, 계정 정보, 관심·숨기기."""

from html import unescape
import re
import threading
from datetime import timedelta

import pytest

from dentmoa import config, db, settings_store, sources
from dentmoa.models import RawPosting
from dentmoa.sources.base import Source
from dentmoa.web import create_app
from dentmoa.web.security import LoginLimiter, safe_next

PASSWORD = "치과-비밀번호-1234"
TOKEN_RE = re.compile(r'name="_csrf" value="([^"]+)"')


class FakeSource(Source):
    def __init__(self, key, label):
        self.key, self.label = key, label

    def fetch(self, ctx):
        return iter(())


@pytest.fixture()
def app(tmp_db, monkeypatch):
    for env in ("DENTMOA_SECRET_KEY", "DENTMOA_SECURE_COOKIES", "DENTMOA_BEHIND_PROXY"):
        monkeypatch.delenv(env, raising=False)
    # 실제 수집기 모듈과 상관없이 출처 이름이 나오도록
    fakes = [FakeSource("moreden", "모어덴"), FakeSource("dentphoto", "덴트포토"), FakeSource("gojobs", "나라일터")]
    monkeypatch.setattr(sources, "all_sources", lambda: fakes)
    return create_app(testing=True)


def token(html: str) -> str:
    m = TOKEN_RE.search(html)
    assert m, "CSRF 입력칸이 없음"
    return m.group(1)


def login(client, password=PASSWORD, **kw):
    t = token(client.get("/login").get_data(as_text=True))
    return client.post("/login", data={"_csrf": t, "password": password}, **kw)


@pytest.fixture()
def client(app):
    settings_store.set_password(PASSWORD)
    c = app.test_client()
    r = login(c)
    assert r.status_code == 302
    return c


def csrf_of(client, path="/status") -> str:
    return token(client.get(path).get_data(as_text=True))


def add_posting(sid="1", title="[서울 강남구] 소아치과 전문의 봉직의 모십니다", body=None, source="moreden", days_ago=1, **extra):
    body = body if body is not None else (
        "서울 강남구 치과의원에서 소아치과 전문의 선생님을 모십니다.\n주 5일 풀타임 근무\n"
        f"마감: {(config.now() + timedelta(days=2)).date():%Y-%m-%d}"
    )
    raw = RawPosting(
        source=source, source_id=sid, url=f"https://example.com/{sid}", title=title, body=body,
        posted_at=config.now() - timedelta(days=days_ago), **extra,
    )
    pid, _ = db.save_raw(raw, source_kind="local_board", dentist_only=True, default_inst_type=None)
    return pid


# ──────────────────────────── 앱 설정 ────────────────────────────


def test_healthz_needs_no_login(app):
    c = app.test_client()
    r = c.get("/healthz")
    assert r.status_code == 200 and r.get_data(as_text=True) == "ok"
    settings_store.set_password(PASSWORD)
    assert c.get("/healthz").status_code == 200


def test_secret_key_and_cookie_flags(tmp_db, monkeypatch):
    for env in ("DENTMOA_SECRET_KEY", "DENTMOA_SECURE_COOKIES"):
        monkeypatch.delenv(env, raising=False)
    a = create_app(testing=True)
    stored = db.kv_get("flask_secret")
    assert stored and a.secret_key == stored
    assert create_app(testing=True).secret_key == stored  # 다시 시작해도 같은 키
    assert a.config["SESSION_COOKIE_HTTPONLY"] and a.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert not a.config["SESSION_COOKIE_SECURE"]
    assert a.permanent_session_lifetime == timedelta(days=30)

    monkeypatch.setenv("DENTMOA_SECRET_KEY", "env-key")
    monkeypatch.setenv("DENTMOA_SECURE_COOKIES", "1")
    b = create_app(testing=True)
    assert b.secret_key == "env-key" and b.config["SESSION_COOKIE_SECURE"]


# ──────────────────────────── 처음 설정·로그인 ────────────────────────────


def test_setup_flow(app):
    c = app.test_client()
    for path in ("/", "/settings", "/accounts", "/status", "/login"):
        r = c.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/setup")

    t = token(c.get("/setup").get_data(as_text=True))
    code = settings_store.setup_code()
    r = c.post("/setup", data={"_csrf": t, "code": "000000" if code != "000000" else "111111", "password": PASSWORD, "password2": PASSWORD})
    assert r.status_code == 400 and "설치 코드" in r.get_data(as_text=True)
    assert not settings_store.has_password()
    r = c.post("/setup", data={"_csrf": t, "code": code, "password": "short", "password2": "short"})
    assert r.status_code == 400 and "8자 이상" in r.get_data(as_text=True)
    r = c.post("/setup", data={"_csrf": t, "code": code, "password": PASSWORD, "password2": PASSWORD + "x"})
    assert r.status_code == 400 and "서로 달라요" in r.get_data(as_text=True)
    assert not settings_store.has_password()

    r = c.post("/setup", data={"_csrf": t, "code": code, "password": PASSWORD, "password2": PASSWORD})
    assert r.status_code == 302 and r.headers["Location"].endswith("/accounts")
    assert settings_store.check_password(PASSWORD)
    assert c.get("/").status_code == 200  # 바로 로그인된 상태
    assert c.get("/setup").status_code == 302  # 비밀번호를 정한 뒤에는 다시 못 연다


def test_login_required_and_next(app):
    settings_store.set_password(PASSWORD)
    c = app.test_client()
    r = c.get("/settings")
    assert r.status_code == 302 and "/login?next=/settings" in r.headers["Location"]
    assert c.get("/posting/1").status_code == 302
    assert c.get("/static/style.css").status_code == 200

    t = token(c.get("/login?next=/settings").get_data(as_text=True))
    r = c.post("/login", data={"_csrf": t, "password": PASSWORD, "next": "/settings"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/settings")
    assert c.get("/settings").status_code == 200

    t = csrf_of(c)
    assert c.post("/logout", data={"_csrf": t}).status_code == 302
    assert c.get("/settings").status_code == 302


def test_login_rejects_external_next(app):
    settings_store.set_password(PASSWORD)
    c = app.test_client()
    t = token(c.get("/login").get_data(as_text=True))
    r = c.post("/login", data={"_csrf": t, "password": PASSWORD, "next": "//evil.example.com/"})
    assert r.headers["Location"] == "/"
    for bad in ("//evil.com", "https://evil.com", "/\t/evil.com", "/\\evil.com", "javascript:alert(1)", ""):
        assert safe_next(bad, "/") == "/"
    assert safe_next("/?view=all&q=소아", "/") == "/?view=all&q=소아"


def test_wrong_password_lockout(app):
    settings_store.set_password(PASSWORD)
    clock = [1000.0]
    app.extensions["dentmoa_login_limiter"] = LoginLimiter(clock=lambda: clock[0])
    c = app.test_client()
    for _ in range(4):
        r = login(c, "틀린-비밀번호")
        assert r.status_code == 401 and "비밀번호가 틀렸어요" in r.get_data(as_text=True)
    r = login(c, "틀린-비밀번호")
    assert r.status_code == 429 and "10분" in r.get_data(as_text=True)

    # 잠긴 동안에는 맞는 비밀번호도 거절
    r = login(c, PASSWORD)
    assert r.status_code == 429 and "너무 많이 실패" in r.get_data(as_text=True)
    # 다른 IP는 영향 없음
    other = app.test_client()
    assert login(other, PASSWORD, environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 302

    clock[0] += 601
    assert login(c, PASSWORD).status_code == 302


def test_change_password(client):
    t = csrf_of(client)
    r = client.post("/password", data={"_csrf": t, "current": "틀림", "password": "새-비밀번호-5678", "password2": "새-비밀번호-5678"})
    assert r.status_code == 302 and settings_store.check_password(PASSWORD)
    client.post("/password", data={"_csrf": t, "current": PASSWORD, "password": "새-비밀번호-5678", "password2": "새-비밀번호-5678"})
    assert settings_store.check_password("새-비밀번호-5678")
    assert client.get("/").status_code == 200  # 바꾼 기기는 로그인 유지


def test_password_change_logs_out_other_devices(app, client):
    other = app.test_client()
    assert login(other).status_code == 302
    t = csrf_of(client)
    client.post("/password", data={"_csrf": t, "current": PASSWORD, "password": "새-비밀번호-5678", "password2": "새-비밀번호-5678"})
    assert other.get("/").status_code == 302


# ──────────────────────────── CSRF ────────────────────────────


def test_csrf_rejected(app, client):
    pid = add_posting()
    assert client.post("/settings", data={"times": ["09:00"]}).status_code == 400
    assert client.post("/settings", data={"_csrf": "wrong", "times": ["09:00"]}).status_code == 400
    r = client.post(f"/posting/{pid}/star", data={"_csrf": "wrong"})
    assert r.status_code == 400 and "새로고침" in r.get_data(as_text=True)
    assert not db.get_posting(pid).starred
    assert client.post("/accounts", data={"section": "moreden", "moreden_id": "x"}).status_code == 400
    assert settings_store.secret_sources()["moreden_id"] == ""

    settings_store.set_password(PASSWORD)
    fresh = app.test_client()
    assert fresh.post("/login", data={"password": PASSWORD}).status_code == 400


def test_every_post_form_has_csrf(client):
    pid = add_posting()
    for path in ("/", f"/posting/{pid}", "/settings", "/accounts", "/status"):
        html = client.get(path).get_data(as_text=True)
        forms = re.findall(r'<form[^>]*method="post".*?</form>', html, flags=re.S)
        assert forms, path
        for f in forms:
            assert 'name="_csrf"' in f, (path, f[:120])


# ──────────────────────────── 공고 목록·상세 ────────────────────────────


def test_first_run_empty_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "아직 수집된 공고가 없어요" in html and "지금 수집·알림 실행" in html


def test_list_shows_saved_posting(client):
    pid = add_posting(title="[서울 강남구] 소아치과 전문의 봉직의 모십니다 <script>alert(1)</script>")
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "소아치과 전문의 봉직의 모십니다" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert f'href="/posting/{pid}' in html
    assert "서울 강남구" in html and "모어덴" in html
    assert "D-2" in html and "badge-urgent" in html
    assert 'target="_blank" rel="noopener' in html and "https://example.com/1" in html
    assert "소아" in html  # 짧은 분과 꼬리표

    assert "소아치과 전문의" in client.get("/?view=all").get_data(as_text=True)
    assert "소아치과 전문의" in client.get("/?q=강남 소아").get_data(as_text=True)
    assert "소아치과 전문의" not in client.get("/?view=all&q=교정과").get_data(as_text=True)
    assert "소아치과 전문의" not in client.get("/?view=all&sido=부산").get_data(as_text=True)
    assert "소아치과 전문의" in client.get("/?view=all&sido=서울&spec=pedo&src=moreden").get_data(as_text=True)
    assert "소아치과 전문의" not in client.get("/?view=all&src=dentphoto").get_data(as_text=True)


def test_default_view_follows_alert_filters(client):
    add_posting("1", "[부산 해운대구] 교정과 전문의 모십니다", "부산 해운대구 치과 교정과 전문의 주5일")
    add_posting("2", "[서울 강남구] 소아치과 전문의 모십니다", "서울 강남구 소아치과 전문의 주5일")
    s = settings_store.load()
    s["people"][0]["filters"]["regions"] = ["서울"]
    settings_store.save(s)
    html = client.get("/").get_data(as_text=True)
    assert "소아치과 전문의" in html and "교정과 전문의" not in html
    html = client.get("/?view=all").get_data(as_text=True)
    assert "소아치과 전문의" in html and "교정과 전문의" in html


def test_bad_query_params_do_not_crash(client):
    add_posting()
    for qs in ("days=abc&page=-3", "page=999", "view=nope&days=12", "inst=nope&sido=화성&src=x", "q=" + "가" * 500):
        assert client.get("/?" + qs).status_code == 200, qs


def test_pagination(client):
    for i in range(55):
        add_posting(str(i), f"[서울 강남구] 소아치과 전문의 모십니다 {i}번", days_ago=0)
    html = client.get("/").get_data(as_text=True)
    assert "1 / 2" in html and html.count('class="card posting') == 50
    html = client.get("/?page=2").get_data(as_text=True)
    assert html.count('class="card posting') == 5


def test_duplicate_note(client):
    first = add_posting("1", source="moreden", days_ago=2)
    dup = add_posting("9", source="dentphoto", days_ago=1)
    db.set_dup_of(dup, first)
    html = client.get("/").get_data(as_text=True)
    assert "다른 출처에도 있음: 덴트포토" in html
    assert html.count('class="card posting') == 1  # 같은 공고는 한 장만
    detail = client.get(f"/posting/{first}").get_data(as_text=True)
    assert "같은 공고" in detail and f"/posting/{dup}" in detail


def test_star_and_hide_toggle(client):
    pid = add_posting()
    t = csrf_of(client)
    r = client.post(f"/posting/{pid}/star", data={"_csrf": t, "next": "/?view=all"})
    assert r.status_code == 302 and r.headers["Location"] == "/?view=all"
    assert db.starred_ids("p1") == {pid} and db.starred_ids("p2") == set()  # 관심(★)은 보는 사람(SH)에게만
    assert "소아치과 전문의" in client.get("/?view=star").get_data(as_text=True)
    r = client.post(f"/posting/{pid}/star", data={"_csrf": t}, headers={"X-Requested-With": "fetch"})
    assert r.get_json() == {"ok": True, "value": False} and db.starred_ids("p1") == set()

    client.post(f"/posting/{pid}/hide", data={"_csrf": t})
    assert db.get_posting(pid).hidden
    assert "소아치과 전문의" not in client.get("/").get_data(as_text=True)
    assert "소아치과 전문의" in client.get("/?hidden=1").get_data(as_text=True)
    client.post(f"/posting/{pid}/hide", data={"_csrf": t})
    assert not db.get_posting(pid).hidden

    assert client.post("/posting/999/star", data={"_csrf": t}).status_code == 404
    r = client.post(f"/posting/{pid}/star", data={"_csrf": t, "next": "https://evil.com"})
    assert r.headers["Location"].endswith(f"/posting/{pid}")


def test_star_tab_shows_every_starred_post(client):
    pid = add_posting("1", "구직합니다 봉직 자리 구해요", "부산에서 봉직 자리 구합니다")
    assert db.get_posting(pid).post_kind == "seeking"
    db.set_star("p1", pid, True)
    assert "봉직 자리 구해요" not in client.get("/?view=all").get_data(as_text=True)
    assert "봉직 자리 구해요" in client.get("/?view=star").get_data(as_text=True)


def test_fetch_errors_are_korean_json(client):
    pid = add_posting()
    r = client.post(f"/posting/{pid}/star", data={"_csrf": "wrong"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400 and r.get_json()["ok"] is False and "새로고침" in r.get_json()["error"]
    t = csrf_of(client)
    r = client.post("/posting/999/star", data={"_csrf": t}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 404 and "찾을 수 없어요" in r.get_json()["error"]


def test_huge_posting_id_is_404(client):
    big = "9" * 25
    assert client.get(f"/posting/{big}").status_code == 404
    assert client.post(f"/posting/{big}/hide", data={"_csrf": csrf_of(client)}).status_code == 404


def test_detail_shows_body_and_evidence(client):
    pid = add_posting(body="서울 강남구 치과의원\n소아치과 전문의 모십니다\n주 5일 <b>근무</b>")
    s = settings_store.load()
    s["people"][0]["filters"]["regions"] = ["부산"]
    s["people"][0]["filters"]["include_uncertain_region"] = False
    settings_store.save(s)

    html = client.get(f"/posting/{pid}").get_data(as_text=True)
    assert "왜 이렇게 분류됐나요?" in html
    assert "&lt;b&gt;근무&lt;/b&gt;" in html and "<b>근무</b>" not in html
    p = db.get_posting(pid)
    for snippet in p.evidence.get("specialties", []):
        assert snippet in html
    assert "소아치과" in html and "봉직의 (페이닥터)" in html
    assert "알림 조건과 비교" in html and "지역: 서울 강남구" in html
    assert "원문 보기" in html and f"/posting/{pid}/star" in html and f"/posting/{pid}/hide" in html
    assert client.get("/posting/999").status_code == 404


def test_region_uncertain_marker(client):
    add_posting("1", "소아치과 전문의 모십니다", "소아치과 전문의 선생님 모십니다. 주5일")
    s = settings_store.load()
    s["people"][0]["filters"]["regions"] = ["서울 강남구"]
    settings_store.save(s)
    html = client.get("/").get_data(as_text=True)
    assert "지역 확인 필요" in html


# ──────────────────────────── 알림 조건 ────────────────────────────


def settings_form(t: str, **over) -> dict:
    data = {
        "_csrf": t,
        "post_kinds": ["hiring"],
        "dentist_only": "1",
        "inst_types": ["dental_clinic", "dental_univ_hospital"],
        "positions": ["staff_dentist", "faculty"],
        "specialties": ["pedo", "gp"],
        "work_types": ["fulltime", "parttime"],
        "include_hints": "1",
        "regions": ["서울 강남구", "부산"],
        "include_keywords": "서울대학교치과병원, 소아\n어린이",
        "exclude_keywords": "양도\n동업",
        "times": ["07:30", "19:05", "", ""],
        "telegram": "1",
        "reminder_enabled": "1",
        "reminder_days": "5",
        "source_moreden": "1",
        "source_hospitals": "1",
        "backfill_days": "14",
        "min_delay_sec": "4",
        "max_delay_sec": "9",
    }
    data.update(over)
    return data


def test_settings_save_round_trip(client, monkeypatch):
    from dentmoa import scheduler

    calls = []
    monkeypatch.setattr(scheduler, "reschedule", lambda: calls.append(1))
    t = csrf_of(client, "/settings")
    r = client.post("/settings", data=settings_form(t))
    assert r.status_code == 302 and calls == [1]

    s = settings_store.load()
    f, a, n, c = s["people"][0]["filters"], s["people"][0]["alerts"], s["notify"], s["collect"]
    assert f["regions"] == ["서울 강남구", "부산"]
    assert f["include_keywords"] == ["서울대학교치과병원", "소아", "어린이"]
    assert f["exclude_keywords"] == ["양도", "동업"]
    assert f["specialties"] == ["pedo", "gp"] and f["include_hints"] and not f["include_uncertain_region"]
    assert n["times"] == ["07:30", "19:05"] and a["telegram"] and not a["email"] and a["reminder_days"] == 5
    assert s["people"][1]["filters"]["regions"] == ["전국"]  # JY 의 조건은 그대로
    assert c["sources"]["moreden"] and c["sources"]["hospitals"] and not c["sources"]["dentphoto"]
    assert c["backfill_days"] == 14 and c["min_delay_sec"] == 4.0 and c["max_delay_sec"] == 9.0

    html = client.get("/settings").get_data(as_text=True)
    assert "SH의 조건을 저장했습니다" in html
    assert 'value="서울 강남구" data-sigungu-of="서울" checked' in html
    assert 'value="부산" data-sido-all="부산" checked' in html
    assert 'value="전국" data-region-all checked' not in html
    assert "서울대학교치과병원\n소아\n어린이" in html
    assert 'value="07:30"' in html and 'value="19:05"' in html
    assert "현재 조건으로 최근 30일 공고 중" in html
    assert "나라일터" in html and "치과의사 커뮤니티 구인 게시판" in html
    assert 'value="세종" data-sido-all="세종"' in html and "세종 전체" not in html  # 구·시·군이 없는 곳


def test_settings_region_normalization(client):
    t = csrf_of(client, "/settings")
    client.post("/settings", data=settings_form(t, regions=["부산", "부산 해운대구", "서울 강남구", "없는 곳"]))
    assert settings_store.load()["people"][0]["filters"]["regions"] == ["부산", "서울 강남구"]
    client.post("/settings", data=settings_form(t, regions=["전국", "서울"]))
    assert settings_store.load()["people"][0]["filters"]["regions"] == ["전국"]


def test_settings_validation_errors(client):
    before = settings_store.load()
    t = csrf_of(client, "/settings")
    r = client.post("/settings", data=settings_form(t, times=["", ""], regions=[], specialties=[]))
    html = r.get_data(as_text=True)
    assert r.status_code == 400
    assert "알림 시각을 하나 이상" in html and "지역을 하나 이상" in html and "분과를 하나 이상" in html
    assert settings_store.load() == before
    r = client.post("/settings", data=settings_form(t, times=["06:00", "09:00", "12:00", "15:00", "18:00"]))
    assert r.status_code == 400 and "4개까지" in r.get_data(as_text=True)


def test_settings_preview_count(client):
    add_posting("1")
    add_posting("2", "[부산 해운대구] 교정과 전문의 모십니다", "부산 해운대구 교정과 주5일")
    t = csrf_of(client, "/settings")
    client.post("/settings", data=settings_form(t, regions=["서울"], include_keywords="", specialties=list(settings_store.DEFAULT_FILTERS["specialties"]), inst_types=list(settings_store.DEFAULT_FILTERS["inst_types"])))
    html = client.get("/settings").get_data(as_text=True)
    assert "최근 30일 공고 중 <strong>1건</strong>" in html


# ──────────────────────────── 계정·연결 ────────────────────────────


def test_accounts_never_echo_secrets(client, monkeypatch):
    t = csrf_of(client, "/accounts")
    client.post("/accounts", data={"_csrf": t, "section": "moreden", "moreden_id": "kimdds77", "moreden_pw": "SuperSecret!9"})
    client.post("/accounts", data={"_csrf": t, "section": "telegram", "telegram_token": "123456:ABCDEF_secret_token"})
    client.post("/accounts/person/p1", data={"_csrf": t, "section": "telegram", "telegram_chat_id": "987654"})
    client.post("/accounts", data={
        "_csrf": t, "section": "email", "smtp_user": "me@gmail.com", "smtp_password": "abcd efgh ijkl mnop",
    })
    client.post("/accounts/person/p1", data={"_csrf": t, "section": "email", "email_to": "me@gmail.com"})
    sec = settings_store.secrets()
    assert sec["moreden_id"] == "kimdds77" and sec["moreden_pw"] == "SuperSecret!9"
    sh = settings_store.load()["people"][0]
    assert sec["smtp_password"] == "abcdefghijklmnop" and sh["telegram_chat_id"] == "987654" and sh["email_to"] == "me@gmail.com"

    html = client.get("/accounts").get_data(as_text=True)
    for value in ("kimdds77", "SuperSecret!9", "123456:ABCDEF_secret_token", "987654", "abcdefghijklmnop", "me@gmail.com"):
        assert value not in html
    assert "저장됨 ✓" in html and "@BotFather" in html and "앱 비밀번호" in html

    # 빈칸은 그대로 두기, '지우기'는 지우기
    client.post("/accounts", data={"_csrf": t, "section": "moreden", "moreden_id": "", "moreden_pw": ""})
    assert settings_store.secrets()["moreden_pw"] == "SuperSecret!9"
    client.post("/accounts", data={"_csrf": t, "section": "moreden", "clear": ["moreden_pw"]})
    assert settings_store.secrets()["moreden_pw"] == "" and settings_store.secrets()["moreden_id"] == "kimdds77"

    monkeypatch.setenv("DENTPHOTO_PW", "from-env-pw")
    html = client.get("/accounts").get_data(as_text=True)
    assert "환경변수로 설정됨" in html and "from-env-pw" not in html


def test_app_password_spaces_removed(client):
    t = csrf_of(client, "/accounts")
    # 휴대폰에서 복사하면 줄바꿈 없는 공백(U+00A0)이 섞여 온다
    client.post("/accounts", data={"_csrf": t, "section": "email", "smtp_password": "abcd\u00a0efgh ijkl\u00a0mnop"})
    assert settings_store.secrets()["smtp_password"] == "abcdefghijklmnop"


def test_accounts_validation(client):
    t = csrf_of(client, "/accounts")
    r = client.post("/accounts", data={"_csrf": t, "section": "email", "smtp_port": "abc"}, follow_redirects=True)
    assert "포트는 숫자로" in r.get_data(as_text=True)
    assert settings_store.secret_sources()["smtp_port"] == ""
    r = client.post("/accounts", data={"_csrf": t, "section": "nope"}, follow_redirects=True)
    assert "알 수 없는 항목" in r.get_data(as_text=True)


def test_account_test_buttons(client, monkeypatch):
    from dentmoa import pipeline
    from dentmoa.notify import mailer, telegram
    from dentmoa.notify.errors import NotifyError

    settings_store.update_secrets({"telegram_token": "123:abc"})
    t = csrf_of(client, "/accounts")

    chats = [("55555", "김선생"), ("66666", "지영")]  # 가장 최근에 봇에게 말을 건 순서 (최근 것부터)

    def fake_find(token, exclude=()):
        assert token == "123:abc"
        return next((c for c in chats if c[0] not in exclude), None)

    monkeypatch.setattr(telegram, "find_chat", fake_find)
    r = client.post("/accounts/telegram/find-chat", data={"_csrf": t, "person": "p1"}, follow_redirects=True)
    assert "'김선생'의 채팅 ID를 찾아서 SH에게 저장했어요" in unescape(r.get_data(as_text=True))
    # JY 가 아이폰에서 '시작'을 누른 뒤: SH 의 채팅은 건너뛰고 JY 의 채팅을 찾는다
    r = client.post("/accounts/telegram/find-chat", data={"_csrf": t, "person": "p2"}, follow_redirects=True)
    assert "'지영'의 채팅 ID를 찾아서 JY에게 저장했어요" in unescape(r.get_data(as_text=True))
    assert [p["telegram_chat_id"] for p in settings_store.load()["people"]] == ["55555", "66666"]

    def fail(_secrets):
        raise NotifyError("텔레그램 봇 토큰이 올바르지 않습니다")

    monkeypatch.setattr(telegram, "send_test", fail)
    r = client.post("/accounts/telegram/test", data={"_csrf": t, "person": "p1"}, follow_redirects=True)
    assert "SH 텔레그램 테스트 실패: 텔레그램 봇 토큰이 올바르지 않습니다" in r.get_data(as_text=True)

    sent = []
    monkeypatch.setattr(telegram, "send_test", lambda sec: sent.append(sec["telegram_chat_id"]))
    client.post("/accounts/telegram/test", data={"_csrf": t, "person": "p2"})
    assert sent == ["66666"]  # JY 의 채팅으로

    mails = []
    s = settings_store.load()
    s["people"][1]["email_to"] = "jy@icloud.com"
    settings_store.save(s)
    monkeypatch.setattr(mailer, "send_test", lambda sec: mails.append(sec["email_to"]))
    r = client.post("/accounts/email/test", data={"_csrf": t, "person": "p2"}, follow_redirects=True)
    assert mails == ["jy@icloud.com"] and "JY에게 테스트 메일을 보냈어요" in r.get_data(as_text=True)

    checked = []

    def fake_check(key):
        checked.append(key)
        if key == "dentphoto":
            raise RuntimeError("로그인 실패 — 아이디·비밀번호를 확인해 주세요")
        return "연결 성공"

    monkeypatch.setattr(pipeline, "check_source", fake_check)
    html = client.get("/accounts").get_data(as_text=True)
    for key in settings_store.SOURCE_KEYS:
        assert f"/accounts/source/{key}/check" in html
        r = client.post(f"/accounts/source/{key}/check", data={"_csrf": t}, follow_redirects=True)
        assert r.status_code == 200
    assert checked == settings_store.SOURCE_KEYS
    r = client.post("/accounts/source/dentphoto/check", data={"_csrf": t}, follow_redirects=True)
    assert "덴트포토 연결 테스트 실패: 로그인 실패" in r.get_data(as_text=True)
    r = client.post("/accounts/source/nope/check", data={"_csrf": t}, follow_redirects=True)
    assert "알 수 없는 출처" in r.get_data(as_text=True) and "nope" not in checked


# ──────────────────────────── 상태 ────────────────────────────


def test_status_page(client):
    run = db.run_start("collect", "moreden")
    db.run_finish(run, "error", message="로그인 실패\n자세한 기록")
    db.kv_set("board_status", {"서울대치과병원": {"ok": True, "message": "3건"}, "어느병원": {"ok": False, "message": "게시판 접속 실패"}})
    add_posting()
    html = client.get("/status").get_data(as_text=True)
    assert "예약 정보 없음" in html
    assert "모어덴" in html and "실패" in html and "로그인 실패" in html
    assert "모아 둔 공고 <strong>1건</strong>" in html
    assert "병원별 결과 (성공 1곳 / 전체 2곳)" in html and "게시판 접속 실패" in html
    assert "대시보드 비밀번호 바꾸기" in html


def test_status_shows_scheduler_next_runs(client, monkeypatch):
    from dentmoa import scheduler

    when = config.now().replace(hour=8, minute=10) + timedelta(days=1)
    monkeypatch.setattr(scheduler, "next_runs", lambda: [when])
    html = client.get("/status").get_data(as_text=True)
    assert "예약 정보 없음" not in html and "08:10" in html


def test_run_now_starts_background_cycle(client, monkeypatch):
    from dentmoa import pipeline

    done = threading.Event()
    monkeypatch.setattr(pipeline, "run_cycle", lambda **kw: done.set())
    t = csrf_of(client)
    r = client.post("/status/run", data={"_csrf": t}, follow_redirects=True)
    assert done.wait(5)
    assert "수집·알림을 시작했어요" in r.get_data(as_text=True)


def test_preview_lists_pending_matches_without_sending(client, monkeypatch):
    from dentmoa import notify

    monkeypatch.setattr(notify, "send_digest", lambda *a, **k: pytest.fail("미리보기에서 알림을 보내면 안 됨"))
    add_posting()
    html = client.get("/status/preview").get_data(as_text=True)
    assert "알림 미리보기" in html and "소아치과 전문의" in html and "<strong>1건</strong>" in html
    assert db.get_posting(1).processed_at is None


def test_error_pages_are_korean(client):
    r = client.get("/없는-주소")
    assert r.status_code == 404 and "찾을 수 없어요" in r.get_data(as_text=True)
    r = client.get("/posting/1/star")
    assert r.status_code == 405 and "버튼을 눌러서" in r.get_data(as_text=True)


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"


# ──────────────────────────── 받는 사람 둘 (SH·JY) ────────────────────────────


def set_regions(sh, jy):
    s = settings_store.load()
    s["people"][0]["filters"]["regions"] = sh
    s["people"][1]["filters"]["regions"] = jy
    settings_store.save(s)


def switch(client, key, next_url="/"):
    t = csrf_of(client)
    return client.post(f"/person/{key}", data={"_csrf": t, "next": next_url})


def test_person_switch_changes_list_tags_and_stars(client):
    seoul = add_posting("1")
    busan = add_posting("2", "[부산 해운대구] 소아치과 봉직의 모십니다", "부산 해운대구 소아치과 주5일")
    set_regions(["서울"], ["부산"])

    html = client.get("/").get_data(as_text=True)
    assert "SH 조건에 맞는 공고" in html and 'class="on" aria-pressed="true">SH<' in html
    assert "[서울 강남구]" in html and "[부산 해운대구]" not in html

    r = switch(client, "p2", "/?view=all")
    assert r.status_code == 302 and r.headers["Location"] == "/?view=all"
    html = client.get("/").get_data(as_text=True)
    assert "JY 조건에 맞는 공고" in html and "[부산 해운대구]" in html and "[서울 강남구]" not in html

    # 전체 공고: 카드마다 조건에 맞는 사람 이름
    html = client.get("/?view=all").get_data(as_text=True)
    assert 'class="tag tag-person" title="SH의 알림 조건에 맞아요">SH<' in html
    assert 'class="tag tag-person" title="JY의 알림 조건에 맞아요">JY<' in html

    t = csrf_of(client)
    client.post(f"/posting/{busan}/star", data={"_csrf": t})
    assert db.starred_ids("p2") == {busan} and db.starred_ids("p1") == set()
    switch(client, "p1")
    assert "[부산 해운대구]" not in client.get("/?view=star").get_data(as_text=True)  # SH 의 관심 목록은 비어 있음

    detail = client.get(f"/posting/{seoul}").get_data(as_text=True)
    assert "SH 조건에 맞음" in detail and "JY 조건 밖" in detail
    assert switch(client, "nobody").status_code == 302  # 없는 사람은 무시
    assert "SH 조건에 맞는 공고" in client.get("/").get_data(as_text=True)


def test_person_choice_survives_login(app, client):
    switch(client, "p2")
    t = csrf_of(client)
    client.post("/logout", data={"_csrf": t})
    login(client)
    assert "JY 조건에 맞는 공고" in client.get("/").get_data(as_text=True)


def test_settings_edit_one_person_only(client):
    switch(client, "p2")
    t = csrf_of(client, "/settings")
    html = client.get("/settings").get_data(as_text=True)
    assert "JY의 알림 조건" in html and 'name="person" value="p2"' in html and "모두 함께 쓰는 설정" in html
    r = client.post("/settings", data=settings_form(t, person="p2", name="지영", regions=["경기"], telegram="", email="1"))
    assert r.status_code == 302
    s = settings_store.load()
    sh, jy = s["people"]
    assert jy["name"] == "지영" and jy["filters"]["regions"] == ["경기"] and jy["alerts"]["email"] and not jy["alerts"]["telegram"]
    assert sh["name"] == "SH" and sh["filters"]["regions"] == ["전국"] and sh["alerts"]["telegram"]
    assert s["notify"]["times"] == ["07:30", "19:05"]  # 알림 시각은 함께 쓴다

    r = client.post("/settings", data=settings_form(t, person="p2", name="SH"))
    assert r.status_code == 400 and "이미 다른 사람의 이름" in unescape(r.get_data(as_text=True))
    r = client.post("/settings", data=settings_form(t, person="p2", name="  "))
    assert r.status_code == 400 and "이름을 적어 주세요" in r.get_data(as_text=True)


def test_add_and_delete_person(client):
    pid = add_posting()
    t = csrf_of(client, "/settings")
    client.post("/settings/people/add", data={"_csrf": t})
    s = settings_store.load()
    assert [(p["key"], p["name"]) for p in s["people"]] == [("p1", "SH"), ("p2", "JY"), ("p3", "사람3")]
    assert not s["people"][2]["alerts"]["warnings"]
    assert "사람3의 알림 조건" in client.get("/settings").get_data(as_text=True)  # 새 사람으로 바뀜

    db.set_star("p3", pid, True)
    client.post("/settings/people/p3/delete", data={"_csrf": t})
    assert [p["key"] for p in settings_store.load()["people"]] == ["p1", "p2"]
    assert db.starred_ids("p3") == set()

    client.post("/settings/people/p2/delete", data={"_csrf": t})
    r = client.post("/settings/people/p1/delete", data={"_csrf": t}, follow_redirects=True)
    assert "한 명뿐이라 지울 수 없어요" in r.get_data(as_text=True)
    html = client.get("/").get_data(as_text=True)
    assert "내 조건에 맞는 공고" in html and "person-switch" not in html  # 한 사람이면 예전 화면 그대로


def test_accounts_per_person_targets(client):
    t = csrf_of(client, "/accounts")
    html = client.get("/accounts").get_data(as_text=True)
    assert "SH 채팅 ID" in html and "JY 채팅 ID" in html and "JY 받는 주소" in html
    r = client.post("/accounts/person/p2", data={"_csrf": t, "section": "email", "email_to": "jy@icloud.com, sh@gmail.com"})
    assert r.status_code == 302 and r.headers["Location"].endswith("#email")
    r = client.post("/accounts/person/p2", data={"_csrf": t, "section": "telegram", "telegram_chat_id": "abc"}, follow_redirects=True)
    assert "채팅 ID는 숫자" in r.get_data(as_text=True)
    jy = settings_store.load()["people"][1]
    assert jy["email_to"] == "jy@icloud.com, sh@gmail.com" and jy["telegram_chat_id"] == ""
    html = client.get("/accounts").get_data(as_text=True)
    assert "jy@icloud.com" not in html
    client.post("/accounts/person/p2", data={"_csrf": t, "section": "email", "clear": ["email_to"]})
    assert settings_store.load()["people"][1]["email_to"] == ""
