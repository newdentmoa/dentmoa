"""수집 → 분류·저장 → 중복 정리 → 알림 → 마감 알림 의 전체 흐름."""

from __future__ import annotations

import hashlib
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


# 마지막 성공 수집 뒤로 이보다 오래 지났으면 '따라잡기' — 평소 최대 쪽 수(max_pages) 대신 backfill_max_pages 까지 읽는다.
# (하루 3번이면 실행 사이가 길어야 14시간, 하루 1번이면 24시간 남짓이라 평소에는 해당하지 않는다)
CATCH_UP_AFTER = timedelta(hours=36)


def make_context(source: Source, settings: dict, *, save_debug: bool = False, force_backfill: bool = False) -> FetchContext:
    col = settings["collect"]
    last_ok = db.last_run("collect", source.key, status="ok")
    now = config.now()
    backfill = force_backfill or last_ok is None
    if backfill:
        since = now - timedelta(days=col["backfill_days"])
        pages = col["backfill_max_pages"]
    else:
        last = datetime.fromisoformat(last_ok["started_at"])
        since = last - timedelta(days=2)
        pages = col["max_pages"]
        if now - last > CATCH_UP_AFTER:
            # 며칠 동안 이 사이트 수집이 실패했다(서버 메모리 부족·사이트 장애 등) → 그동안 올라온 글을 놓치지 않게 더 많이 읽는다.
            # 사이트별 수집기는 since 보다 오래된 글이 나오면 쪽 넘기기를 멈추므로, 필요한 만큼만 더 읽는다.
            pages = max(pages, col["backfill_max_pages"])
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


# 로그인 실패 뒤에는 같은 아이디·비밀번호로 이 시간 안에 다시 시도하지 않는다 — 틀린 비밀번호로 여러 번 시도하면
# 계정이 잠길 수 있다. 계정 화면에서 아이디·비밀번호를 바꾸면 바로 다시 시도한다.
LOGIN_RETRY_AFTER = timedelta(hours=23)
# 접속 실패·화면 구조 문제는 이만큼 연속으로 실패했을 때만 알린다 (하루 3번 수집이면 약 하루).
# 그사이 놓친 글은 다음에 성공할 때 따라잡는다 (make_context). 계정 문제(로그인·계정 정보 없음)는 바로 알린다.
WARN_AFTER_FAILURES = 3


def _credential_print(source: Source) -> str:
    """아이디·비밀번호가 바뀌었는지 알아보기 위한 지문 (비밀번호 자체는 저장하지 않는다)."""
    sec = settings_store.secrets()
    raw = f"{sec.get(source.key + '_id', '')}\n{sec.get(source.key + '_pw', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _login_paused(source: Source) -> str:
    entry = db.kv_get("login_failed", {}).get(source.key)
    if not entry or entry.get("creds") != _credential_print(source):
        return ""
    if config.now() - datetime.fromisoformat(entry["at"]) >= LOGIN_RETRY_AFTER:
        return ""
    return (f"로그인에 실패해서 계정 잠김을 막으려고 하루 한 번만 다시 시도합니다 (마지막 실패: {entry['at'][:16].replace('T', ' ')}). "
            f"대시보드의 '계정·연결'에서 아이디·비밀번호를 고치면 바로 다시 시도합니다. 처음 실패 내용: {entry.get('message', '')}")


def _remember_login(source: Source, failed_message: str | None) -> None:
    failed = db.kv_get("login_failed", {})
    if failed_message is None:
        if source.key in failed:
            failed.pop(source.key)
            db.kv_set("login_failed", failed)
        return
    failed[source.key] = {"at": config.now().isoformat(), "creds": _credential_print(source), "message": failed_message[:200]}
    db.kv_set("login_failed", failed)


def collect_source(source: Source, settings: dict, *, save_debug: bool = False) -> SourceReport:
    run_id = db.run_start("collect", source.key)
    rep = SourceReport(source.key, source.label, ok=True)
    if getattr(source, "requires_login", False):
        paused = _login_paused(source)
        if paused:
            rep.ok, rep.kind, rep.message = False, "login", paused
            db.run_finish(run_id, "error", message=paused)
            _log(f"[{source.key}] 건너뜀: {paused}")
            return rep
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
        if getattr(source, "requires_login", False):
            _remember_login(source, None)
    except MissingCredentials as e:
        rep.ok, rep.kind, rep.message = False, "credentials", str(e)
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message)
    except LoginError as e:
        rep.ok, rep.kind, rep.message = False, "login", str(e)
        db.run_finish(run_id, "error", found=rep.found, new=rep.new, message=rep.message)
        _remember_login(source, str(e))
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

    if dry_run:
        rep.skipped_reason = "미리보기"
        return rep
    if not matched and not warnings and not settings["notify"]["send_empty"]:
        db.mark_processed([p.id for p in pending], [])
        rep.skipped_reason = "새로 맞는 공고 없음"
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
    """알릴 만한 실패만: 계정 문제는 바로, 접속 실패·화면 구조 문제는 연속 WARN_AFTER_FAILURES 번 실패했을 때.
    같은 출처는 하루 한 번만 알린다."""
    if not report:
        return []
    warned = db.kv_get("warned", {})
    today = config.now().date().isoformat()
    out = []
    for w in report.warnings:
        if warned.get(w.key) == today:
            continue
        if w.kind not in ("login", "credentials") and _failures_in_a_row(w.key) < WARN_AFTER_FAILURES:
            continue
        out.append(w)
    return out


def _failures_in_a_row(source_key: str) -> int:
    n = 0
    for run in db.recent_collect_runs(source_key, WARN_AFTER_FAILURES + 1):
        if run["status"] != "error":
            break
        n += 1
    return n


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
