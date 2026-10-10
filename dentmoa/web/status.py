"""상태 화면: 다음 예약, 출처별 마지막 수집, 실행 기록, 지금 실행, 알림 미리보기."""

from __future__ import annotations

import threading
from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, url_for

from .. import db, settings_store
from ..matching import match
from ..settings_store import SOURCE_KEYS
from .helpers import Card, current_person, fmt_dt, source_label

bp = Blueprint("status", __name__)

RUN_KINDS = {"collect": "수집", "cycle": "수집·알림", "digest": "알림", "reminders": "마감 알림", "reminder": "마감 알림"}
RUN_STATUS = {
    "ok": ("성공", "st-ok"),
    "error": ("실패", "st-error"),
    "partial": ("일부 실패", "st-warn"),
    "running": ("실행 중", "st-running"),
}


def _fmt_any(v) -> str:
    """scheduler.next_runs() 가 어떤 모양을 돌려주든 글자로 바꾼다."""
    if isinstance(v, datetime):
        return fmt_dt(v)
    if isinstance(v, date):
        return f"{v.month}월 {v.day}일"
    if isinstance(v, dict):
        return " · ".join(_fmt_any(x) for x in v.values() if x not in (None, ""))
    if isinstance(v, (list, tuple)):
        return " · ".join(_fmt_any(x) for x in v if x not in (None, ""))
    return str(v)


def next_runs() -> list[str]:
    try:
        from dentmoa import scheduler

        return [_fmt_any(r) for r in (scheduler.next_runs() or [])]
    except Exception:
        return []


def run_view(run: dict | None) -> dict | None:
    if not run:
        return None
    text, cls = RUN_STATUS.get(run["status"], (run["status"], ""))
    msg = run.get("message") or ""
    first, _, rest = msg.partition("\n")
    return {
        **run,
        "kind_label": RUN_KINDS.get(run["kind"], run["kind"]),
        "source_label": source_label(run["source"]) if run.get("source") else "",
        "status_label": text,
        "status_cls": cls,
        "started": fmt_dt(run.get("started_at")),
        "finished": fmt_dt(run.get("finished_at")),
        "summary": first,
        "detail": rest.strip(),
    }


def board_status() -> dict | None:
    """병원 게시판 수집기가 남긴 게시판별 결과 (실패한 곳 먼저)."""
    status = db.kv_get("board_status") or {}
    if not isinstance(status, dict):
        return None
    rows = sorted(
        ({"name": name, **v} for name, v in status.items() if isinstance(v, dict)),
        key=lambda r: (bool(r.get("ok")), r["name"]),
    )
    if not rows:
        return None
    return {"rows": rows, "ok": sum(1 for r in rows if r.get("ok")), "total": len(rows)}


def cycle_running() -> bool:
    try:
        from dentmoa import pipeline

        lock = getattr(pipeline, "_cycle_lock", None)
        return bool(lock and lock.locked())
    except Exception:
        return False


@bp.get("/status")
def index():
    settings = settings_store.load()
    enabled = settings["collect"]["sources"]
    sources = [
        {
            "key": k,
            "label": source_label(k),
            "enabled": enabled.get(k, True),
            "run": run_view(db.last_run("collect", k)),
            "last_ok": run_view(db.last_run("collect", k, status="ok")),
        }
        for k in SOURCE_KEYS
    ]
    return render_template(
        "status.html",
        next_runs=next_runs(),
        times=settings["notify"]["times"],
        sources=sources,
        boards=board_status(),
        runs=[run_view(r) for r in db.recent_runs(30)],
        total=db.count_postings(),
        running=cycle_running(),
    )


@bp.post("/status/run")
def run_now():
    if cycle_running():
        flash("이미 수집·알림을 실행하고 있어요. 잠시 뒤 새로고침해 보세요.", "info")
        return redirect(url_for("status.index"))
    try:
        from dentmoa import pipeline

        threading.Thread(target=pipeline.run_cycle, name="dentmoa-run-now", daemon=True).start()
    except Exception as e:
        flash(f"실행하지 못했어요: {e}", "error")
    else:
        flash("수집·알림을 시작했어요. 사이트를 천천히 읽기 때문에 몇 분 걸릴 수 있어요. 잠시 뒤 새로고침해 보세요.", "ok")
    return redirect(url_for("status.index"))


@bp.get("/status/preview")
def preview():
    """보는 사람(SH·JY)에게 지금 알림을 보낸다면 들어갈 공고."""
    settings = settings_store.load()
    me = current_person(settings)
    error = None
    pending: list = []
    matched: list = []
    try:
        from dentmoa import pipeline

        pending, matched = pipeline.matched_pending(settings, me)
    except Exception as e:
        error = str(e) or e.__class__.__name__
    return render_template(
        "preview.html",
        who=me["name"] if len(settings["people"]) > 1 else "",
        cards=[Card(p, match(p, me["filters"])) for p in matched],
        considered=len(pending),
        max_items=settings["notify"]["max_items"],
        error=error,
    )
