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


@pytest.fixture()
def sent(monkeypatch, tmp_db):
    box = {"digest": [], "reminders": []}

    def fake_digest(postings, warnings, settings):
        box["digest"].append((list(postings), list(warnings)))
        return {"텔레그램": "ok"}

    def fake_reminders(postings, settings):
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
    s["filters"]["specialties"] = ["pedo", "gp"]
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
    monkeypatch.setattr(notify, "send_digest", lambda *a: {})
    rep_cycle = pipeline.run_cycle()
    assert rep_cycle["digest"].skipped_reason == "알림 채널 미설정"
    assert len(db.unprocessed()) == 1  # 채널을 설정하면 그때 받는다


def test_failed_channel_retries_next_time(monkeypatch, tmp_db):
    use_sources(monkeypatch, FakeSource([raw("1", "[서울] GP 원장님 모십니다")]))
    monkeypatch.setattr(notify, "send_digest", lambda *a: {"텔레그램": "봇 토큰이 올바르지 않습니다"})
    pipeline.run_cycle()
    assert len(db.unprocessed()) == 1


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
