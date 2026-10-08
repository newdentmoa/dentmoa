import copy
import re
import smtplib
from datetime import date, datetime, timedelta
from email import message_from_bytes
from email.policy import default as default_policy
from html.parser import HTMLParser

import pytest
import requests

from dentmoa import config, settings_store
from dentmoa.models import Posting
from dentmoa.notify import format as fmt
from dentmoa.notify import mailer, telegram
from dentmoa.notify.errors import NotifyError
from dentmoa.pipeline import SourceReport
from dentmoa.regions import Region

NOW = datetime(2026, 10, 8, 12, 40, tzinfo=config.TZ)  # 목요일
TODAY = NOW.date()


def _posting(pid=1, **kw) -> Posting:
    base = dict(
        id=pid, source="moreden", source_id=str(pid), url=f"https://moreden.co.kr/recruit/doctor/{pid}",
        title=f"[서울 강남] 소아치과 전문의 모십니다 {pid}", body="", author="", region_hint="",
        institution_hint="", posted_at=NOW - timedelta(days=1), first_seen_at=NOW, last_seen_at=NOW,
        post_kind="hiring", is_dentist=True, inst_type="dental_clinic", institution="",
        positions=["staff_dentist"], specialties=["pedo"], specialty_hints=[], work_types=["fulltime"],
        regions=[Region("서울", "강남구")], deadline_kind="none", deadline=None, summary=[], uncertain=[],
        evidence={}, dup_of=None, starred=False, hidden=False, processed_at=None, notified_at=None,
        reminded_at=None, extra={},
    )
    base.update(kw)
    return Posting(**base)


def _settings(**notify):
    s = copy.deepcopy(settings_store.DEFAULT_SETTINGS)
    s["notify"].update(notify)
    return s


class _TgValidator(HTMLParser):
    """텔레그램이 받아들이는 HTML인지 (허용 태그만, 짝이 맞는지)."""

    ALLOWED = {"b", "i", "a", "code"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            self.errors.append(f"허용 안 된 태그 {tag}")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"짝 안 맞는 태그 {tag}")


def _assert_valid(chunk: str) -> None:
    assert len(chunk) <= telegram.LIMIT
    v = _TgValidator()
    v.feed(chunk)
    v.close()
    assert not v.errors and not v.stack, (v.errors, v.stack)
    assert not re.search(r"<(?!/?(?:b|i|a|code)\b)", chunk)
    assert not re.search(r"&(?!(?:lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);)", chunk)


@pytest.fixture(autouse=True)
def _no_base_url(monkeypatch):
    monkeypatch.setattr(config, "BASE_URL", "")


# ───────────── 메시지 형식 ─────────────


def test_escaping():
    p = _posting(
        title='<script>alert("x")</script> A&B 치과',
        institution='"OO" & <b>치과',
        summary=["<i>세후</i> 1500 & 숙소"],
        url="https://example.com/view?a=1&b=2",
    )
    chunks = fmt.telegram_digest([p], [], _settings(), now=NOW)
    assert len(chunks) == 1
    text = chunks[0]
    _assert_valid(text)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text and "A&amp;B" in text
    assert "&lt;i&gt;세후&lt;/i&gt; 1500 &amp; 숙소" in text
    assert 'href="https://example.com/view?a=1&amp;b=2"' in text

    subject, html, plain = fmt.email_digest([p], [], _settings(), now=NOW)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<script>alert("x")</script> A&B 치과' in plain
    assert subject.startswith("[덴트모아] 새 공고 1건")


def test_unsafe_url_not_linked():
    p = _posting(url="javascript:alert(1)")
    text = fmt.telegram_digest([p], [], _settings(), now=NOW)[0]
    assert "javascript:" not in text
    _, html, _ = fmt.email_digest([p], [], _settings(), now=NOW)
    assert "javascript:" not in html


def test_item_layout():
    p = _posting(
        institution="해맑은소아치과",
        deadline_kind="date",
        deadline=date(2026, 10, 16),
        summary=["주 4.5일, 세후 1500", "소아 환자 많음", "숙소 제공", "넷째 줄은 빠짐"],
    )
    text = fmt.telegram_digest([p, _posting(2), _posting(3)], [], _settings(), now=NOW)[0]
    _assert_valid(text)
    assert text.startswith("🦷 <b>덴트모아</b> · 10월 8일(목) 12:40 · 새 공고 3건")
    assert "치과의원 · 봉직의 · 소아 · 풀타임 · 📍 서울 강남구" in text
    assert "🏥 해맑은소아치과" in text
    assert "⏰ 마감 10/16(금) · D-8" in text
    assert "• 주 4.5일, 세후 1500" in text and "넷째 줄은 빠짐" not in text
    assert '<a href="https://moreden.co.kr/recruit/doctor/1">원문</a>' in text
    assert "모어덴 · 10/7 게시" in text
    assert "대시보드" not in text  # BASE_URL 없음


def test_unknown_region_and_dashboard_link(monkeypatch):
    monkeypatch.setattr(config, "BASE_URL", "https://1-2-3-4.sslip.io")
    p = _posting(7, regions=[])
    text = fmt.telegram_digest([p], [], _settings(), now=NOW)[0]
    assert "📍 지역 미상" in text
    assert '<a href="https://1-2-3-4.sslip.io/posting/7">대시보드</a>' in text
    _, html, plain = fmt.email_digest([p], [], _settings(), now=NOW)
    assert "https://1-2-3-4.sslip.io/posting/7" in html and "https://1-2-3-4.sslip.io/posting/7" in plain


@pytest.mark.parametrize(
    "kind, day, expected",
    [
        ("date", date(2026, 10, 16), "마감 10/16(금) · D-8"),
        ("date", TODAY, "마감 10/8(목) · D-Day"),
        ("date", date(2026, 10, 1), "마감 10/1(목) · 지남"),
        ("until_filled", None, "채용시 마감"),
        ("none", None, ""),
    ],
)
def test_deadline_text(kind, day, expected):
    assert fmt.deadline_text(_posting(deadline_kind=kind, deadline=day), TODAY) == expected


def test_no_deadline_line_when_unknown():
    text = fmt.telegram_digest([_posting()], [], _settings(), now=NOW)[0]
    assert "⏰" not in text


def test_chunking_keeps_items_whole():
    long_summary = ["가" * 65 + " & <x>", "나" * 68, "다" * 69]
    posts = [
        _posting(i, title=f"공고{i:03d} " + "긴 제목 " * 15, summary=long_summary, institution=f"기관{i}")
        for i in range(1, 81)
    ]
    chunks = fmt.telegram_digest(posts, [], _settings(max_items=200), now=NOW)
    assert len(chunks) > 2
    for c in chunks:
        _assert_valid(c)
    assert chunks[1].startswith(f"<i>(2/{len(chunks)})</i>")
    for p in posts:
        holders = [c for c in chunks if f"공고{p.id:03d} " in c]
        assert len(holders) == 1  # 한 항목은 한 메시지 안에만
        assert f'href="{p.url}"' in holders[0]  # 제목과 링크가 같은 메시지


def test_split_html_on_line_boundaries():
    html = "\n".join(f"<b>줄 {i}</b> " + "가" * 50 for i in range(200))
    parts = telegram.split_html(html)
    assert len(parts) > 1
    for part in parts:
        _assert_valid(part)
    assert "\n".join(parts) == html


def test_split_html_very_long_line():
    html = "<b>" + "가&amp;" * 2000 + "</b>"
    parts = telegram.split_html(html)
    assert len(parts) >= 2
    for part in parts:
        _assert_valid(part)


def test_max_items_note():
    posts = [_posting(i) for i in range(1, 11)]
    chunks = fmt.telegram_digest(posts, [], _settings(max_items=5), now=NOW)
    text = "\n".join(chunks)
    assert "새 공고 10건" in text
    assert "외 5건은 대시보드에서 확인하세요" in text
    assert "모십니다 5</b>" in text and "모십니다 6</b>" not in text
    subject, html, plain = fmt.email_digest(posts, [], _settings(max_items=5), now=NOW)
    assert "외 5건은 대시보드에서 확인하세요" in html and "외 5건은 대시보드에서 확인하세요" in plain


def test_warnings_section():
    warnings = [
        SourceReport("moreden", "모어덴", ok=False, kind="credentials", message="아이디 없음"),
        SourceReport("dentphoto", "덴트포토", ok=False, kind="login", message="로그인 실패: 비밀번호 오류"),
        SourceReport("alio", "잡알리오", ok=False, kind="structure", message="목록을 찾지 못함"),
        SourceReport("hibrain", "하이브레인넷", ok=False, kind="network", message="접속 실패"),
        SourceReport("hospitals", "병원 채용게시판", ok=False, kind="error", message="예상하지 못한 오류: <ValueError>"),
    ]
    text = "\n".join(fmt.telegram_digest([], warnings, _settings(), now=NOW))
    for c in fmt.telegram_digest([], warnings, _settings(), now=NOW):
        _assert_valid(c)
    assert "⚠️ 확인이 필요해요" in text
    assert "모어덴: 아이디·비밀번호가 아직 설정되지 않았어요 (계정·연결 화면)" in text
    assert "덴트포토: 로그인에 실패했어요. 비밀번호가 바뀌었는지 확인해 주세요" in text
    assert "사이트 화면 구조가 바뀐 것 같아요. 프로그램 수리가 필요해요" in text
    assert "사이트에 접속하지 못했어요 (일시적일 수 있어요)" in text
    assert "병원 채용게시판: 예상하지 못한 오류" in text and "&lt;ValueError&gt;" in text
    subject, html, plain = fmt.email_digest([], warnings, _settings(), now=NOW)
    assert subject.startswith("[덴트모아] 확인이 필요해요")
    assert "로그인에 실패했어요" in html and "로그인에 실패했어요" in plain


def test_empty_digest():
    chunks = fmt.telegram_digest([], [], _settings(), now=NOW)
    assert len(chunks) == 1
    assert "새 공고 0건" in chunks[0] and "새로 올라온 조건에 맞는 공고가 없어요" in chunks[0]
    subject, html, plain = fmt.email_digest([], [], _settings(), now=NOW)
    assert subject.startswith("[덴트모아] 새 공고 없음")
    assert "새로 올라온 조건에 맞는 공고가 없어요" in html and "새로 올라온 조건에 맞는 공고가 없어요" in plain


def test_reminders_format():
    posts = [
        _posting(1, deadline_kind="date", deadline=TODAY + timedelta(days=2), institution="서울대학교치과병원",
                 title="소아치과 전임의 모집", starred=True),
        _posting(2, deadline_kind="date", deadline=TODAY),
    ]
    chunks = fmt.telegram_reminders(posts, _settings(), now=NOW)
    text = chunks[0]
    _assert_valid(text)
    assert text.startswith("⏰ <b>마감 임박 공고</b> · 10월 8일(목) 12:40 · 2건")
    assert "⭐ <b>소아치과 전임의 모집</b>" in text
    assert "마감 10/10(토) · D-2" in text and "D-Day" in text
    subject, html, plain = fmt.email_reminders(posts, _settings(), now=NOW)
    assert subject.startswith("[덴트모아] 마감 임박 공고 2건")
    assert "D-2" in html and "D-2" in plain


def test_source_labels_fallback():
    labels = fmt.source_labels()
    for key in ("moreden", "dentphoto", "alio", "hibrain", "hospitals"):
        assert labels[key]


# ───────────── 채널 고르기 ─────────────


@pytest.fixture()
def senders(monkeypatch):
    calls = {"telegram": [], "email": []}
    behaviour = {"telegram": None, "email": None}

    def fake_chunks(token, chat_id, chunks, **kw):
        chunks = list(chunks)
        calls["telegram"].append((token, chat_id, chunks))
        if behaviour["telegram"]:
            raise behaviour["telegram"]
        return len(chunks)

    def fake_email(secrets, subject, html, text):
        calls["email"].append((subject, html, text))
        if behaviour["email"]:
            raise behaviour["email"]

    monkeypatch.setattr(telegram, "send_chunks", fake_chunks)
    monkeypatch.setattr(mailer, "send_email", fake_email)
    return calls, behaviour


def _secrets(monkeypatch, **values):
    sec = {k: "" for k in settings_store.SECRET_FIELDS}
    sec.update(smtp_host="smtp.gmail.com", smtp_port="587")
    sec.update(values)
    monkeypatch.setattr(settings_store, "secrets", lambda: dict(sec))


TG = dict(telegram_token="123:abc", telegram_chat_id="42")
MAIL = dict(smtp_user="me@gmail.com", smtp_password="app-pass", email_to="me@gmail.com")


def test_send_digest_only_configured_channel(monkeypatch, senders):
    from dentmoa.notify import send_digest

    calls, _ = senders
    _secrets(monkeypatch, **TG)
    assert send_digest([_posting()], [], _settings()) == {"텔레그램": "ok"}
    assert calls["telegram"][0][:2] == ("123:abc", "42") and not calls["email"]


def test_send_digest_respects_switches(monkeypatch, senders):
    from dentmoa.notify import send_digest

    calls, _ = senders
    _secrets(monkeypatch, **TG, **MAIL)
    assert send_digest([_posting()], [], _settings(telegram=False)) == {"메일": "ok"}
    assert not calls["telegram"] and calls["email"][0][0].startswith("[덴트모아] 새 공고 1건")
    assert send_digest([_posting()], [], _settings(telegram=False, email=False)) == {}


def test_send_digest_nothing_configured(monkeypatch, senders):
    from dentmoa.notify import send_digest

    calls, _ = senders
    _secrets(monkeypatch, telegram_token="123:abc", smtp_user="me@gmail.com")  # 반쪽 설정
    assert send_digest([_posting()], [], _settings()) == {}
    assert not calls["telegram"] and not calls["email"]


def test_send_digest_errors_per_channel(monkeypatch, senders):
    from dentmoa.notify import send_digest, send_reminders

    calls, behaviour = senders
    _secrets(monkeypatch, **TG, **MAIL)
    behaviour["telegram"] = NotifyError("봇 토큰이 올바르지 않습니다")
    behaviour["email"] = RuntimeError("boom")
    result = send_digest([_posting()], [], _settings())
    assert result == {"텔레그램": "봇 토큰이 올바르지 않습니다", "메일": "예상하지 못한 오류: RuntimeError: boom"}

    behaviour["telegram"] = behaviour["email"] = None
    due = [_posting(deadline_kind="date", deadline=date.today() + timedelta(days=1))]
    assert send_reminders(due, _settings()) == {"텔레그램": "ok", "메일": "ok"}
    assert "마감 임박 공고" in calls["telegram"][-1][2][0]


# ───────────── 텔레그램 API ─────────────


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def tg_api(monkeypatch):
    state = {"responses": [], "calls": [], "sleeps": []}

    def fake_post(url, json=None, timeout=None):
        state["calls"].append((url, json))
        resp = state["responses"].pop(0) if state["responses"] else _Resp(200, {"ok": True, "result": {}})
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    monkeypatch.setattr(telegram.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def test_send_message_ok(tg_api):
    assert telegram.send_message("123:abc", "42", "<b>안녕</b>") == 1
    url, payload = tg_api["calls"][0]
    assert url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert payload == {"chat_id": "42", "text": "<b>안녕</b>", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}


def test_send_message_bad_token(tg_api):
    tg_api["responses"] = [_Resp(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
    with pytest.raises(NotifyError, match="봇 토큰이 올바르지 않습니다"):
        telegram.send_message("bad", "42", "hi")


def test_send_message_chat_not_found(tg_api):
    tg_api["responses"] = [_Resp(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})]
    with pytest.raises(NotifyError, match="채팅 ID가 올바르지 않거나 봇에게 먼저 /start 를 보내지 않았습니다"):
        telegram.send_message("123:abc", "999", "hi")


def test_send_message_retries_on_429(tg_api):
    too_many = _Resp(429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 3}})
    tg_api["responses"] = [too_many, _Resp(200, {"ok": True, "result": {}})]
    assert telegram.send_message("123:abc", "42", "hi") == 1
    assert len(tg_api["calls"]) == 2 and tg_api["sleeps"] == [3.0]

    tg_api["calls"].clear()
    tg_api["responses"] = [too_many, too_many, too_many, too_many]
    with pytest.raises(NotifyError, match="잠시 후 다시"):
        telegram.send_message("123:abc", "42", "hi")
    assert len(tg_api["calls"]) == 3  # 처음 1번 + 다시 2번


def test_network_error_hides_token(tg_api):
    tg_api["responses"] = [requests.ConnectionError("Max retries exceeded with url: /bot123:SECRET/sendMessage")]
    with pytest.raises(NotifyError) as exc:
        telegram.send_message("123:SECRET", "42", "hi")
    assert "SECRET" not in str(exc.value) and "접속하지 못했습니다" in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_long_message_sent_in_parts(tg_api):
    html = "\n\n".join(f"<b>공고 {i}</b>\n" + "가" * 300 for i in range(40))
    n = telegram.send_message("123:abc", "42", html)
    assert n == len(tg_api["calls"]) > 1
    for _, payload in tg_api["calls"]:
        _assert_valid(payload["text"])
    assert len(tg_api["sleeps"]) == n - 1


def test_find_chat_id(tg_api):
    tg_api["responses"] = [_Resp(200, {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 111, "type": "private"}, "text": "/start"}},
        {"update_id": 2, "message": {"chat": {"id": 222, "type": "private"}, "text": "안녕"}},
        {"update_id": 3, "message": {"chat": {"id": -100, "type": "group"}, "text": "단체방"}},
    ]})]
    assert telegram.find_chat_id("123:abc") == "222"
    assert tg_api["calls"][0][0].endswith("/getUpdates")
    tg_api["responses"] = [_Resp(200, {"ok": True, "result": []})]
    assert telegram.find_chat_id("123:abc") is None


def test_telegram_send_test(tg_api):
    with pytest.raises(NotifyError):
        telegram.send_test({"telegram_token": "123:abc", "telegram_chat_id": ""})
    telegram.send_test({"telegram_token": "123:abc", "telegram_chat_id": "42"})
    assert tg_api["calls"][0][1]["text"] == "덴트모아 테스트 메시지입니다 ✅"


# ───────────── 메일 ─────────────


class _FakeSMTP:
    instances: list = []
    fail_login = False
    ssl = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.log, self.sent = [], []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append("quit")

    def ehlo(self):
        self.log.append("ehlo")

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user, password))
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append((msg, from_addr, to_addrs))


@pytest.fixture()
def fake_smtp(monkeypatch):
    class Plain(_FakeSMTP):
        instances = []

    class Secure(_FakeSMTP):
        instances = []

    monkeypatch.setattr(mailer.smtplib, "SMTP", Plain)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", Secure)
    return Plain, Secure


SECRETS = {
    "smtp_host": "smtp.gmail.com", "smtp_port": "587", "smtp_user": "me@gmail.com",
    "smtp_password": "abcd efgh ijkl mnop", "email_to": "me@gmail.com, wife@naver.com",
}


def test_mail_starttls(fake_smtp):
    plain, secure = fake_smtp
    mailer.send_email(SECRETS, "[덴트모아] 새 공고 1건", "<p>안녕하세요 &amp;</p>", "안녕하세요 &")
    assert not secure.instances
    server = plain.instances[0]
    assert (server.host, server.port, server.timeout) == ("smtp.gmail.com", 587, 30)
    assert server.log[:3] == ["ehlo", "starttls", "ehlo"]
    assert ("login", "me@gmail.com", "abcdefghijklmnop") in server.log  # 앱 비밀번호 띄어쓰기 제거
    msg, from_addr, to_addrs = server.sent[0]
    assert from_addr == "me@gmail.com" and to_addrs == ["me@gmail.com", "wife@naver.com"]

    parsed = message_from_bytes(msg.as_bytes(), policy=default_policy)
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed["Subject"] == "[덴트모아] 새 공고 1건"
    assert "me@gmail.com" in parsed["From"] and parsed["To"] == "me@gmail.com, wife@naver.com"
    plain_part = parsed.get_body(("plain",))
    html_part = parsed.get_body(("html",))
    assert plain_part.get_content_charset() == "utf-8" and html_part.get_content_charset() == "utf-8"
    assert plain_part.get_content().strip() == "안녕하세요 &"
    assert "<p>안녕하세요 &amp;</p>" in html_part.get_content()


def test_mail_ssl_port(fake_smtp):
    plain, secure = fake_smtp
    mailer.send_email({**SECRETS, "smtp_port": "465"}, "제목", "<p>x</p>", "x")
    assert not plain.instances
    assert secure.instances[0].port == 465 and "starttls" not in secure.instances[0].log


def test_mail_auth_failure(fake_smtp, monkeypatch):
    plain, _ = fake_smtp
    monkeypatch.setattr(plain, "fail_login", True)
    with pytest.raises(NotifyError, match="앱 비밀번호"):
        mailer.send_email(SECRETS, "제목", "<p>x</p>", "x")


def test_mail_connection_failure(monkeypatch):
    def refuse(*a, **kw):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(mailer.smtplib, "SMTP", refuse)
    with pytest.raises(NotifyError, match="접속하지 못했습니다"):
        mailer.send_email(SECRETS, "제목", "<p>x</p>", "x")


def test_mail_missing_settings_and_test(fake_smtp):
    plain, _ = fake_smtp
    with pytest.raises(NotifyError):
        mailer.send_email({**SECRETS, "smtp_password": ""}, "제목", "x", "x")
    with pytest.raises(NotifyError, match="포트"):
        mailer.send_email({**SECRETS, "smtp_port": "abc"}, "제목", "x", "x")
    mailer.send_test(SECRETS)
    msg = plain.instances[-1].sent[0][0]
    assert msg["Subject"] == "덴트모아 테스트 메일"


# ───────────── 예약 ─────────────


def test_scheduler_start_reschedule_shutdown(tmp_db):
    from dentmoa import scheduler

    s = settings_store.load()
    s["notify"]["times"] = ["18:20", "08:10"]
    s["collect"]["jitter_minutes"] = 0
    settings_store.save(s)
    try:
        scheduler.start()
        scheduler.start()  # 두 번 불러도 괜찮아야 함
        assert scheduler.is_running()
        runs = scheduler.next_runs()
        assert len(runs) == 2 and runs == sorted(runs)
        assert {(r.hour, r.minute) for r in runs} == {(8, 10), (18, 20)}
        assert all(r.utcoffset() == timedelta(hours=9) for r in runs)
        ids = {j.id for j in scheduler.scheduler.get_jobs()}
        assert ids == {"cycle-0810", "cycle-1820", "maintenance"}
        job = scheduler.scheduler.get_job("cycle-0810")
        assert job.coalesce and job.max_instances == 1 and job.misfire_grace_time == 3600

        s["notify"]["times"] = ["07:05", "12:40", "21:00"]
        s["collect"]["jitter_minutes"] = 15
        settings_store.save(s)
        runs = scheduler.reschedule()
        assert len(runs) == 3
        assert {j.id for j in scheduler.scheduler.get_jobs()} == {"cycle-0705", "cycle-1240", "cycle-2100", "maintenance"}
        assert scheduler.scheduler.get_job("cycle-1240").trigger.jitter == 900
    finally:
        scheduler.shutdown()
    assert not scheduler.is_running()
    assert scheduler.next_runs() == []
    assert scheduler.reschedule() == []  # 꺼져 있으면 아무것도 안 함
