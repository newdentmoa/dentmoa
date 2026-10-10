"""수집 → 저장 → 중복 정리 → 알림 → 마감 알림 전체 흐름."""

from datetime import timedelta

import pytest

from dentmoa import config, db, notify, pipeline, settings_store
from dentmoa.models import RawPosting
from dentmoa.sources.base import LoginError, Source, SourceError


class FakeSource(Source):
    key = "moreden"
    label = "모어덴"
    source_kind = "local_board"
    dentist_only = True
    default_inst_type = "dental_clinic"

    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.contexts = []

    def fetch(self, ctx):
        self.contexts.append(ctx)
        if self.error:
            raise self.error
        for it in self.items:
            yield it


def raw(sid, title, body="", days_ago=0, source="moreden"):
    return RawPosting(source, sid, f"https://x/{sid}", title, body, posted_at=config.now() - timedelta(days=days_ago))


def one_person():
    """받는 사람을 첫 사람(SH) 한 명만 남긴다 — 한 사람 기준의 흐름 시험."""
    s = settings_store.load()
    s["people"] = s["people"][:1]
    return settings_store.save(s)


def first_filters(s):
    return s["people"][0]["filters"]


@pytest.fixture()
def sent(monkeypatch, tmp_db):
    one_person()
    box = {"digest": [], "reminders": []}

    def fake_digest(postings, warnings, settings, **kw):
        box["digest"].append((list(postings), list(warnings)))
        return {"텔레그램": "ok"}

    def fake_reminders(postings, settings, **kw):
        box["reminders"].append(list(postings))
        return {"텔레그램": "ok"}

    monkeypatch.setattr(notify, "send_digest", fake_digest)
    monkeypatch.setattr(notify, "send_reminders", fake_reminders)
    return box


def use_sources(monkeypatch, *sources):
    monkeypatch.setattr(pipeline, "all_sources", lambda: list(sources))


def test_catch_up_after_failed_days(monkeypatch, tmp_db):
    """며칠 동안 수집이 실패했으면 다음 성공 때 평소보다 많은 쪽을 읽어 따라잡는다."""
    s = settings_store.load()
    src = FakeSource()
    real_now = config.now()

    def ok_run_at(hours_ago):
        monkeypatch.setattr(config, "now", lambda: real_now - timedelta(hours=hours_ago))
        db.run_finish(db.run_start("collect", src.key), "ok")
        monkeypatch.setattr(config, "now", lambda: real_now)

    ok_run_at(14)  # 하루 3번: 전날 저녁 → 오늘 아침
    ctx = pipeline.make_context(src, s)
    assert ctx.max_pages == s["collect"]["max_pages"] == 2 and not ctx.backfill
    assert ctx.since == real_now - timedelta(hours=14) - timedelta(days=2)

    ok_run_at(25)  # 하루 1번으로 정해 둔 경우도 평소대로
    assert pipeline.make_context(src, s).max_pages == 2

    ok_run_at(24 * 4)  # 4일 동안 실패(마지막 성공이 4일 전) → 따라잡기
    ctx = pipeline.make_context(src, s)
    assert ctx.max_pages == s["collect"]["backfill_max_pages"] == 10 and not ctx.backfill
    assert ctx.since == real_now - timedelta(days=4) - timedelta(days=2)


def test_first_run_backfill_then_incremental(monkeypatch, sent):
    src = FakeSource([
        raw("1", "[서울 강남] 소아치과 전문의 모십니다", "주 5일", days_ago=1),
        raw("2", "[부산] 교정과 전문의 모십니다", "", days_ago=3),
        raw("3", "[대전] 오래된 공고 원장님 모십니다", "", days_ago=45),  # 30일보다 오래됨
    ])
    use_sources(monkeypatch, src)
    s = settings_store.load()
    first_filters(s)["specialties"] = ["pedo", "gp"]
    settings_store.save(s)

    out = pipeline.run_cycle()
    assert src.contexts[0].backfill is True
    assert out["collect"].new_count == 3
    postings, warnings = sent["digest"][0]
    assert [p.source_id for p in postings] == ["1"]  # 교정과는 조건 밖, 45일 전 글은 제외
    assert warnings == []

    # 두 번째 실행: 같은 글은 다시 알리지 않는다
    src.items.append(raw("4", "[서울 송파] GP 원장님 구합니다", "", days_ago=0))
    pipeline.run_cycle()
    assert src.contexts[1].backfill is False
    assert "1" in src.contexts[1].known_ids
    postings, _ = sent["digest"][1]
    assert [p.source_id for p in postings] == ["4"]


def test_no_channel_keeps_backlog(monkeypatch, tmp_db):
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] GP 원장님 모십니다")]))
    monkeypatch.setattr(notify, "send_digest", lambda *a, **k: {})
    rep_cycle = pipeline.run_cycle()
    assert [d.skipped_reason for d in rep_cycle["digest"].people] == ["알림 채널 미설정", "알림 채널 미설정"]
    assert len(db.unprocessed("p1")) == 1  # 채널을 설정하면 그때 받는다


def test_failed_channel_retries_next_time(monkeypatch, tmp_db):
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] GP 원장님 모십니다")]))
    monkeypatch.setattr(notify, "send_digest", lambda *a, **k: {"텔레그램": "봇 토큰이 올바르지 않습니다"})
    pipeline.run_cycle()
    assert len(db.unprocessed("p1")) == 1


def test_source_error_becomes_warning_once_per_day(monkeypatch, sent):
    bad = FakeSource(error=LoginError("모어덴 로그인에 실패했습니다."))
    use_sources(monkeypatch, bad)
    pipeline.run_cycle()
    _, warnings = sent["digest"][0]
    assert warnings[0].kind == "login"
    assert db.last_run("collect", "moreden")["status"] == "error"
    pipeline.run_cycle()
    assert len(sent["digest"]) == 1  # 같은 날 같은 경고는 다시 보내지 않음


def test_cross_site_duplicate_not_notified_twice(monkeypatch, sent):
    a = FakeSource([raw("1", "[부산 해운대] OO치과 봉직의 구합니다", days_ago=1)])
    b = FakeSource([raw("9", "[부산 해운대] OO치과 봉직의 구합니다", days_ago=0, source="dentphoto")])
    b.key, b.label = "dentphoto", "덴트포토"
    use_sources(monkeypatch, a, b)
    pipeline.run_cycle()
    postings, _ = sent["digest"][0]
    assert len(postings) == 1


def test_reminders(monkeypatch, sent):
    deadline = (config.now() + timedelta(days=2)).strftime("%Y.%m.%d")
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] 소아치과 진료교수 채용", f"접수 마감: {deadline}")]))
    pipeline.run_cycle()
    assert len(sent["reminders"]) == 1 and sent["reminders"][0][0].source_id == "1"
    pipeline.run_cycle()
    assert len(sent["reminders"]) == 1  # 한 번만


def test_disabled_source_skipped(monkeypatch, sent):
    src = FakeSource([raw("1", "[서울] GP 원장님 모십니다")])
    use_sources(monkeypatch, src)
    s = settings_store.load()
    s["collect"]["sources"]["moreden"] = False
    settings_store.save(s)
    pipeline.run_cycle()
    assert src.contexts == []


class LoginSource(FakeSource):
    requires_login = True


def test_network_failure_warns_only_after_failures_in_a_row(monkeypatch, sent):
    """접속 실패는 한 번으로는 알리지 않는다 — 놓친 글은 다음 성공 때 따라잡는다. 연속 3번이면 알린다."""
    src = FakeSource(error=SourceError("접속 실패: https://x (Read timed out)"))
    use_sources(monkeypatch, src)
    pipeline.run_cycle()
    pipeline.run_cycle()
    assert sent["digest"] == []  # 두 번 실패: 아직 조용히
    src.error = None
    pipeline.run_cycle()  # 성공하면 횟수는 처음부터
    src.error = SourceError("접속 실패: https://x (Read timed out)")
    pipeline.run_cycle()
    pipeline.run_cycle()
    assert sent["digest"] == []
    pipeline.run_cycle()  # 연속 세 번째
    _, warnings = sent["digest"][0]
    assert [w.kind for w in warnings] == ["network"]


def test_login_failure_waits_a_day_unless_password_changes(monkeypatch, sent):
    """틀린 비밀번호로 하루 3번씩 시도하면 계정이 잠길 수 있다 → 하루 한 번만, 비밀번호를 바꾸면 바로."""
    settings_store.update_secrets({"moreden_id": "dr", "moreden_pw": "old"})
    src = LoginSource(error=LoginError("모어덴 로그인에 실패했습니다."))
    use_sources(monkeypatch, src)
    pipeline.run_cycle()
    assert len(src.contexts) == 1
    _, warnings = sent["digest"][0]
    assert warnings[0].kind == "login"  # 계정 문제는 바로 알린다

    pipeline.run_cycle()  # 같은 날 다음 실행: 로그인하지 않고 건너뛴다
    assert len(src.contexts) == 1
    assert "하루 한 번만" in db.last_run("collect", "moreden")["message"]

    settings_store.update_secrets({"moreden_pw": "new"})  # 비밀번호를 고치면 바로 다시 시도
    src.error = None
    pipeline.run_cycle()
    assert len(src.contexts) == 2
    assert db.last_run("collect", "moreden")["status"] == "ok"
    assert "moreden" not in db.kv_get("login_failed", {})  # 성공하면 기록을 지운다


def test_login_retried_after_a_day(monkeypatch, sent):
    src = LoginSource(error=LoginError("모어덴 로그인에 실패했습니다."))
    use_sources(monkeypatch, src)
    pipeline.run_cycle()
    real_now = config.now()
    monkeypatch.setattr(config, "now", lambda: real_now + pipeline.LOGIN_RETRY_AFTER - timedelta(minutes=1))
    pipeline.run_cycle()
    assert len(src.contexts) == 1
    monkeypatch.setattr(config, "now", lambda: real_now + pipeline.LOGIN_RETRY_AFTER)
    pipeline.run_cycle()
    assert len(src.contexts) == 2


# ───────────── 받는 사람이 둘 (SH·JY) ─────────────


def two_people(sh_regions, jy_regions):
    s = settings_store.load()
    assert [p["name"] for p in s["people"]] == ["SH", "JY"]  # 처음부터 두 사람
    s["people"][0]["filters"]["regions"] = sh_regions
    s["people"][1]["filters"]["regions"] = jy_regions
    s["people"][0]["telegram_chat_id"] = "111"
    s["people"][1]["telegram_chat_id"] = "222"
    return settings_store.save(s)


@pytest.fixture()
def outbox(monkeypatch, tmp_db):
    """보낸 알림: (받는 채팅 ID, 이름, 공고 제목들, 경고 출처들)"""
    box = {"digest": [], "reminders": []}

    def fake_digest(postings, warnings, settings, *, secrets=None, who=""):
        box["digest"].append((secrets["telegram_chat_id"], who, [p.title for p in postings], [w.key for w in warnings]))
        return {"텔레그램": "ok"} if secrets["telegram_chat_id"] else {}

    def fake_reminders(postings, settings, *, secrets=None, who=""):
        box["reminders"].append((secrets["telegram_chat_id"], who, [p.title for p in postings]))
        return {"텔레그램": "ok"}

    monkeypatch.setattr(notify, "send_digest", fake_digest)
    monkeypatch.setattr(notify, "send_reminders", fake_reminders)
    return box


def test_each_person_gets_own_matches_on_own_chat(monkeypatch, outbox):
    two_people(["서울"], ["부산"])
    use_sources(monkeypatch, FakeSource([
        raw("1", "[서울 강남] 소아치과 전문의 모십니다", days_ago=1),
        raw("2", "[부산 해운대] GP 원장님 모십니다", days_ago=1),
        raw("3", "[대구] 원장님 모십니다", days_ago=1),
    ]))
    out = pipeline.run_cycle()
    assert outbox["digest"] == [
        ("111", "SH", ["[서울 강남] 소아치과 전문의 모십니다"], []),
        ("222", "JY", ["[부산 해운대] GP 원장님 모십니다"], []),
    ]
    assert out["digest"].summary() == "SH 1건 · JY 1건"
    assert "알림 SH 1건 · JY 1건" in out["message"]

    pipeline.run_cycle()  # 같은 글은 다시 보내지 않는다
    assert len(outbox["digest"]) == 2


def test_person_without_channel_keeps_own_backlog(monkeypatch, outbox):
    s = two_people(["전국"], ["전국"])
    s["people"][1]["telegram_chat_id"] = ""  # JY 는 아직 텔레그램을 연결하지 않음
    settings_store.save(s)
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] GP 원장님 모십니다", days_ago=1)]))
    out = pipeline.run_cycle()
    assert [d.skipped_reason for d in out["digest"].people] == ["", "알림 채널 미설정"]
    assert db.unprocessed("p1") == [] and len(db.unprocessed("p2")) == 1

    s = settings_store.load()
    s["people"][1]["telegram_chat_id"] = "222"
    settings_store.save(s)
    pipeline.digest()  # JY 가 연결하면 그동안 모인 공고를 받는다
    assert outbox["digest"][-1][:3] == ("222", "JY", ["[서울] GP 원장님 모십니다"])


def test_warnings_only_to_people_who_want_them(monkeypatch, outbox):
    two_people(["전국"], ["전국"])
    use_sources(monkeypatch, FakeSource(error=LoginError("모어덴 로그인에 실패했습니다.")))
    pipeline.run_cycle()
    assert [(chat, warns) for chat, _, _, warns in outbox["digest"]] == [("111", ["moreden"])]  # JY 는 기본으로 끔


def test_one_person_alert_has_no_name_tag(monkeypatch, outbox):
    one_person()
    s = settings_store.load()
    s["people"][0]["telegram_chat_id"] = "111"
    settings_store.save(s)
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] GP 원장님 모십니다", days_ago=1)]))
    pipeline.run_cycle()
    assert outbox["digest"][0][1] == ""


def test_reminders_per_person(monkeypatch, outbox):
    two_people(["서울"], ["부산"])
    deadline = (config.now() + timedelta(days=2)).strftime("%Y.%m.%d")
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] 소아치과 진료교수 채용", f"접수 마감: {deadline}")]))
    pipeline.run_cycle()
    # SH 에게만 알림이 갔으므로 마감 알림도 SH 에게만. JY 가 관심(★) 표시하면 JY 도 받는다
    assert [(chat, who) for chat, who, _ in outbox["reminders"]] == [("111", "SH")]
    db.set_star("p2", db.all_postings()[0].id, True)
    pipeline.reminders()
    assert [(chat, who) for chat, who, _ in outbox["reminders"]] == [("111", "SH"), ("222", "JY")]
    pipeline.reminders()
    assert len(outbox["reminders"]) == 2  # 사람마다 한 번만


def test_legacy_one_person_data_becomes_sh(tmp_db):
    """사람별 기능 전의 설정·알림 기록·관심 표시는 첫 사람(SH) 것으로 옮긴다."""
    legacy_filters = {**settings_store.DEFAULT_FILTERS, "regions": ["서울 강남구"], "specialties": ["pedo"]}
    db.kv_set("settings", {"filters": legacy_filters,
                           "notify": {"telegram": False, "email": True, "times": ["09:00"], "reminder_days": 5}})
    db.kv_set("secrets", {"telegram_token": "123:abc", "telegram_chat_id": "777", "email_to": "me@gmail.com"})
    s = settings_store.load()
    sh, jy = s["people"]
    assert (sh["key"], sh["name"], jy["key"], jy["name"]) == ("p1", "SH", "p2", "JY")
    assert sh["filters"]["regions"] == ["서울 강남구"] and sh["filters"]["specialties"] == ["pedo"]
    assert sh["alerts"]["telegram"] is False and sh["alerts"]["reminder_days"] == 5
    assert (sh["telegram_chat_id"], sh["email_to"]) == ("777", "me@gmail.com")
    assert jy["filters"]["regions"] == ["전국"] and jy["telegram_chat_id"] == ""
    assert s["notify"]["times"] == ["09:00"]
    sec = db.kv_get("secrets")
    assert "telegram_chat_id" not in sec and sec["telegram_token"] == "123:abc"  # 봇 토큰은 함께 쓴다
    assert settings_store.load() == s  # 한 번만 옮긴다


def test_first_person_uses_env_chat_id(tmp_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    s = settings_store.load()
    sh, jy = s["people"]
    assert settings_store.person_secrets(s, sh)["telegram_chat_id"] == "999"
    assert settings_store.person_secrets(s, jy)["telegram_chat_id"] == ""
    sh["telegram_chat_id"] = "111"
    assert settings_store.person_secrets(s, sh)["telegram_chat_id"] == "111"


def test_legacy_posting_records_move_to_first_person(tmp_db):
    pid, _ = db.save_raw(raw("1", "[서울] GP 원장님 모십니다"), source_kind="local_board", dentist_only=True,
                         default_inst_type=None)
    now = config.now().isoformat()
    with db.connect() as conn:  # 사람별 기록이 생기기 전의 DB 흉내
        conn.execute("UPDATE postings SET processed_at=?, notified_at=?, starred=1 WHERE id=?", (now, now, pid))
        conn.execute("DELETE FROM kv WHERE key='migrated_person_postings'")
        db._migrate(conn)
    assert db.unprocessed("p1") == [] and [p.id for p in db.unprocessed("p2")] == [pid]
    assert db.starred_ids("p1") == {pid} and db.starred_ids("p2") == set()
    assert db.person_records(pid)["p1"]["notified_at"] is not None
