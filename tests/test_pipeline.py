"""수집 → 저장 → 중복 정리 → 알림 → 마감 알림 전체 흐름."""

from datetime import timedelta

import pytest

from dentmoa import config, db, notify, pipeline, settings_store
from dentmoa.models import RawPosting
from dentmoa.sources.base import LoginError, Source


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
