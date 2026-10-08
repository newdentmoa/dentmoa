"""수집 → 분류·저장 → 중복 정리 → 알림 → 마감 알림 의 전체 흐름."""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config, db, dedup, settings_store
from .matching import match
from .models import Posting
from .sources import all_sources, get_source
from .sources.base import FetchContext, LoginError, MissingCredentials, Source, SourceError, StructureError

_cycle_lock = threading.Lock()


@dataclass
class SourceReport:
    key: str
    label: str
    ok: bool
    found: int = 0
    new: int = 0
    message: str = ""
    kind: str = ""  # login / structure / network / credentials / error


@dataclass
class CollectReport:
    sources: list[SourceReport] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return sum(s.new for s in self.sources)

    @property
    def warnings(self) -> list[SourceReport]:
        return [s for s in self.sources if not s.ok]


@dataclass
class DigestReport:
    considered: int = 0
    matched: list[Posting] = field(default_factory=list)
    sent: dict[str, str] = field(default_factory=dict)  # 채널 → 'ok' 또는 오류 메시지
    skipped_reason: str = ""


def _log(msg: str) -> None:
    print(f"[{config.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ──────────────────────────── 수집 ────────────────────────────


def make_context(source: Source, settings: dict, *, save_debug: bool = False, force_backfill: bool = False) -> FetchContext:
    col = settings["collect"]
    last_ok = db.last_run("collect", source.key, status="ok")
    now = config.now()
    backfill = force_backfill or last_ok is None
    if backfill:
        since = now - timedelta(days=col["backfill_days"])
        pages = col["backfill_max_pages"]
    else:
        since = datetime.fromisoformat(last_ok["started_at"]) - timedelta(days=2)
        pages = col["max_pages"]
    return FetchContext(
        since=since,
        known_ids=db.known_source_ids(source.key),
        max_pages=pages,
        min_delay=col["min_delay_sec"],
        max_delay=col["max_delay_sec"],
        secrets=settings_store.secrets(),
        log=lambda m, k=source.key: _log(f"[{k}] {m}"),
        backfill=backfill,
        save_debug=save_debug,
    )


def collect_source(source: Source, settings: dict, *, save_debug: bool = False) -> SourceReport:
    run_id = db.run_start("collect", source.key)
    rep = SourceReport(source.key, source.label, ok=True)
    try:
        ctx = make_context(source, settings, save_debug=save_debug)
        new_ids: list[int] = []
        for raw in source.fetch(ctx):
            if raw.posted_at and raw.posted_at.tzinfo is None:
                raw.posted_at = raw.posted_at.replace(tzinfo=config.TZ)
            rep.found += 1
            pid, is_new = db.save_raw(
                raw,
                source_kind=source.source_kind,
                dentist_only=source.dentist_only,
                default_inst_type=source.default_inst_type,
            )
            if is_new:
                rep.new += 1
                new_ids.append(pid)
        _dedup(new_ids)
        rep.message = f"{rep.found}건 확인, 새 글 {rep.new}건"
        db.run_finish(run_id, "ok", found=rep.found, new=rep.new, message=rep.message)
    except MissingCredentials as e:
        rep.ok, rep.kind, rep.message = False, "credentials", str(e)
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message)
    except LoginError as e:
        rep.ok, rep.kind, rep.message = False, "login", str(e)
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message)
    except StructureError as e:
        rep.ok, rep.kind, rep.message = False, "structure", str(e)
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message)
    except SourceError as e:
        rep.ok, rep.kind, rep.message = False, "network", str(e)
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message)
    except Exception as e:  # 예상 못 한 오류도 기록하고 다음 출처로 넘어간다
        rep.ok, rep.kind = False, "error"
        rep.message = f"예상하지 못한 오류: {e.__class__.__name__}: {e}"
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message + "\n" + traceback.format_exc()[-1500:])
    _log(f"[{source.key}] {'성공' if rep.ok else '실패'}: {rep.message}")
    return rep


def _dedup(new_ids: list[int]) -> None:
    if not new_ids:
        return
    recent = db.all_postings(since=config.now() - timedelta(days=dedup.WINDOW_DAYS + 5))
    by_id = {p.id: p for p in recent}
    for pid in new_ids:
        p = by_id.get(pid) or db.get_posting(pid)
        if p is None:
            continue
        orig = dedup.find_original(p, recent)
        if orig:
            db.set_dup_of(pid, orig.id)
            p.dup_of = orig.id


def collect(keys: list[str] | None = None, *, save_debug: bool = False) -> CollectReport:
    settings = settings_store.load()
    enabled = settings["collect"]["sources"]
    report = CollectReport()
    for src in all_sources():
        if keys is not None and src.key not in keys:
            continue
        if keys is None and not enabled.get(src.key, True):
            continue
        report.sources.append(collect_source(src, settings, save_debug=save_debug))
    return report


# ──────────────────────────── 알림 ────────────────────────────


def matched_pending(settings: dict) -> tuple[list[Posting], list[Posting]]:
    """(알림 판단할 공고 전체, 그중 조건에 맞는 것)"""
    pending = db.unprocessed()
    cutoff = config.now() - timedelta(days=settings["collect"]["backfill_days"])
    matched = []
    for p in pending:
        if p.dup_of is not None or p.hidden:
            continue
        if p.effective_date < cutoff:
            continue
        if match(p, settings["filters"]).ok:
            matched.append(p)
    matched.sort(key=lambda p: p.effective_date, reverse=True)
    return pending, matched


def digest(collect_report: CollectReport | None = None, *, dry_run: bool = False) -> DigestReport:
    from .notify import send_digest  # 순환 import 방지

    settings = settings_store.load()
    pending, matched = matched_pending(settings)
    rep = DigestReport(considered=len(pending), matched=matched)
    warnings = _fresh_warnings(collect_report)

    if not matched and not warnings and not settings["notify"]["send_empty"]:
        db.mark_processed([p.id for p in pending], [])
        rep.skipped_reason = "새로 맞는 공고 없음"
        return rep
    if dry_run:
        rep.skipped_reason = "미리보기"
        return rep

    rep.sent = send_digest(matched, warnings, settings)
    if not rep.sent:
        # 텔레그램·메일이 아직 설정되지 않음 → 처리 완료로 표시하지 않고 남겨 둔다.
        # (설정을 마치면 그동안 모인 최근 공고를 한 번에 받는다)
        rep.skipped_reason = "알림 채널 미설정"
        return rep
    if any(v == "ok" for v in rep.sent.values()):
        shown = matched[: settings["notify"]["max_items"]]
        db.mark_processed([p.id for p in pending], [p.id for p in shown])
        if warnings:
            db.kv_set("warned", {**db.kv_get("warned", {}), **{w.key: config.now().date().isoformat() for w in warnings}})
    return rep


def _fresh_warnings(report: CollectReport | None) -> list[SourceReport]:
    """같은 출처의 같은 문제는 하루 한 번만 알린다."""
    if not report:
        return []
    warned = db.kv_get("warned", {})
    today = config.now().date().isoformat()
    return [w for w in report.warnings if warned.get(w.key) != today]


def reminders() -> int:
    from .notify import send_reminders

    settings = settings_store.load()
    n = settings["notify"]
    if not n["reminder_enabled"]:
        return 0
    due = db.due_reminders(config.now().date(), n["reminder_days"], n["reminder_starred_only"])
    due = [p for p in due if p.starred or match(p, settings["filters"]).ok]
    if not due:
        return 0
    sent = send_reminders(due, settings)
    if any(v == "ok" for v in sent.values()):
        db.mark_reminded([p.id for p in due])
        return len(due)
    return 0


# ──────────────────────────── 한 번 돌리기 ────────────────────────────


def run_cycle(*, keys: list[str] | None = None) -> dict:
    """예약 시각마다 실행: 수집 → 알림 → 마감 알림. 동시에 두 번 돌지 않게 잠근다."""
    if not _cycle_lock.acquire(blocking=False):
        return {"skipped": "이미 실행 중"}
    run_id = db.run_start("cycle")
    try:
        col = collect(keys)
        dig = digest(col)
        rem = reminders()
        msg = f"새 글 {col.new_count}건, 알림 {len(dig.matched)}건, 마감알림 {rem}건"
        if dig.sent:
            msg += " / " + ", ".join(f"{k}:{v}" for k, v in dig.sent.items())
        status = "ok" if not col.warnings else "partial"
        db.run_finish(run_id, status, new=col.new_count, message=msg)
        db.prune_runs()
        return {"collect": col, "digest": dig, "reminders": rem, "message": msg}
    except Exception as e:
        db.run_finish(run_id, "error", message=f"{e}\n{traceback.format_exc()[-1500:]}")
        raise
    finally:
        _cycle_lock.release()


def check_source(key: str) -> str:
    """설정 화면의 연결 테스트."""
    src = get_source(key)
    settings = settings_store.load()
    ctx = make_context(src, settings)
    ctx.max_pages = 1
    ctx.min_delay, ctx.max_delay = 1.0, 2.0
    return src.check(ctx)
