"""예약 작업 (APScheduler).

- 설정의 알림 시각(notify.times)마다 수집 → 알림 (pipeline.run_cycle)
  사이트에 매번 같은 시각에 접속하지 않도록 jitter_minutes 안에서 무작위로 늦춘다.
- 매일 03:20 정리 작업: 분류 규칙이 바뀐 공고 다시 분류, 오래된 실행 기록 지우기
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, db, settings_store

CYCLE_PREFIX = "cycle-"
MAINTENANCE_ID = "maintenance"
JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}

_lock = threading.RLock()


def _new_scheduler() -> BackgroundScheduler:
    return BackgroundScheduler(timezone=config.TZ)


scheduler = _new_scheduler()


def _log(msg: str) -> None:
    print(f"[{config.now():%Y-%m-%d %H:%M:%S}] [예약] {msg}", flush=True)


# ──────────────────────────── 작업 ────────────────────────────


def _cycle_job() -> None:
    from . import pipeline  # 수집기 import 는 실행할 때

    try:
        result = pipeline.run_cycle()
        _log(result.get("message") or result.get("skipped") or "완료")
    except Exception as e:
        _log(f"실행 실패: {e.__class__.__name__}: {e}\n{traceback.format_exc()[-1500:]}")


def _maintenance_job() -> None:
    try:
        n = db.reclassify(only_outdated=True)
        db.prune_runs()
        if n:
            _log(f"정리: 공고 {n}건 다시 분류")
    except Exception as e:
        _log(f"정리 작업 실패: {e.__class__.__name__}: {e}")


def _add_cycle_jobs(settings: dict) -> None:
    jitter = int(settings["collect"].get("jitter_minutes", 0)) * 60 or None
    for t in settings["notify"]["times"]:
        hh, mm = t.split(":")
        scheduler.add_job(
            _cycle_job,
            CronTrigger(hour=int(hh), minute=int(mm), timezone=config.TZ, jitter=jitter),
            id=f"{CYCLE_PREFIX}{hh}{mm}",
            name=f"수집·알림 {t}",
            replace_existing=True,
            **JOB_DEFAULTS,
        )


# ──────────────────────────── 공개 함수 ────────────────────────────


def start() -> None:
    """예약 시작. 이미 돌고 있으면 아무것도 하지 않는다."""
    with _lock:
        if scheduler.running:
            return
        settings = settings_store.load()
        scheduler.start()
        _add_cycle_jobs(settings)
        scheduler.add_job(
            _maintenance_job,
            CronTrigger(hour=3, minute=20, timezone=config.TZ),
            id=MAINTENANCE_ID,
            name="정리 작업",
            replace_existing=True,
            **JOB_DEFAULTS,
        )
    runs = next_runs()
    _log("시작 — 다음 실행: " + (", ".join(f"{d:%m/%d %H:%M}" for d in runs[:3]) or "없음"))


def reschedule() -> list[datetime]:
    """설정의 알림 시각이 바뀌었을 때 다시 예약한다. 예약이 꺼져 있으면 아무것도 하지 않는다."""
    with _lock:
        if not scheduler.running:
            return []
        for job in scheduler.get_jobs():
            if job.id.startswith(CYCLE_PREFIX):
                job.remove()
        _add_cycle_jobs(settings_store.load())
    return next_runs()


def next_runs() -> list[datetime]:
    """다음 수집·알림 시각들 (빠른 순)."""
    if not scheduler.running:
        return []
    return sorted(
        j.next_run_time for j in scheduler.get_jobs() if j.id.startswith(CYCLE_PREFIX) and j.next_run_time
    )


def is_running() -> bool:
    return scheduler.running


def shutdown() -> None:
    """예약 멈추기. 다시 start() 할 수 있게 새 스케줄러로 바꿔 둔다."""
    global scheduler
    with _lock:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        scheduler = _new_scheduler()
