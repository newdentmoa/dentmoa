"""SQLite 저장소.

한 파일(data/dentmoa.sqlite3)에 공고, 실행 기록, 설정을 모두 저장한다.
웹 화면과 예약 작업이 동시에 쓰므로 호출마다 새 연결을 연다(WAL 모드).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from . import config
from .classify import CLASSIFIER_VERSION, Classification, classify
from .models import Posting, RawPosting

_db_path: Path = config.DB_PATH
_init_lock = threading.Lock()
_initialized: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    region_hint TEXT NOT NULL DEFAULT '',
    institution_hint TEXT NOT NULL DEFAULT '',
    posted_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    classifier_version INTEGER NOT NULL DEFAULT 0,
    post_kind TEXT,
    is_dentist INTEGER,
    inst_type TEXT,
    institution TEXT,
    positions TEXT,
    specialties TEXT,
    specialty_hints TEXT,
    work_types TEXT,
    regions TEXT,
    deadline_kind TEXT,
    deadline TEXT,
    summary TEXT,
    uncertain TEXT,
    evidence TEXT,
    dup_of INTEGER REFERENCES postings(id),
    starred INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT,
    notified_at TEXT,
    reminded_at TEXT,
    extra TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_posted ON postings(posted_at);
CREATE INDEX IF NOT EXISTS idx_postings_first_seen ON postings(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_postings_processed ON postings(processed_at);
CREATE INDEX IF NOT EXISTS idx_postings_deadline ON postings(deadline);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    found INTEGER NOT NULL DEFAULT 0,
    new INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def configure(path: Path | str) -> None:
    """DB 파일 위치 바꾸기 (테스트용)."""
    global _db_path
    _db_path = Path(path)


def path() -> Path:
    return _db_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = str(_db_path)
    if p not in _initialized:
        with _init_lock:
            if p not in _initialized:
                _db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(p)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                conn.commit()
                conn.close()
                _initialized.add(p)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v else None


# ──────────────────────────── 키-값 (설정) ────────────────────────────


def kv_get(key: str, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    return json.loads(row["value"])


def kv_set(key: str, value) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _iso(config.now())),
        )


# ──────────────────────────── 공고 저장·분류 ────────────────────────────


def _classification_values(c: Classification) -> dict:
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "post_kind": c.post_kind,
        "is_dentist": int(c.is_dentist),
        "inst_type": c.inst_type,
        "institution": c.institution,
        "positions": json.dumps(c.positions),
        "specialties": json.dumps(c.specialties),
        "specialty_hints": json.dumps(c.specialty_hints),
        "work_types": json.dumps(c.work_types),
        "regions": json.dumps([r.to_json() for r in c.regions], ensure_ascii=False),
        "deadline_kind": c.deadline_kind,
        "deadline": _iso(c.deadline),
        "summary": json.dumps(c.summary, ensure_ascii=False),
        "uncertain": json.dumps(c.uncertain),
        "evidence": json.dumps(c.evidence, ensure_ascii=False),
    }


def _classify_row(row) -> Classification:
    extra = json.loads(row["extra"] or "{}")
    posted = datetime.fromisoformat(row["posted_at"] or row["first_seen_at"]).date()
    return classify(
        row["title"],
        row["body"] or "",
        posted=posted,
        source_kind=extra.get("source_kind", "local_board"),
        dentist_only=bool(extra.get("dentist_only", False)),
        region_hint=row["region_hint"] or "",
        institution_hint=row["institution_hint"] or "",
        default_inst_type=extra.get("default_inst_type"),
    )


def save_raw(raw: RawPosting, *, source_kind: str, dentist_only: bool, default_inst_type: str | None) -> tuple[int, bool]:
    """공고를 저장하고 분류한다. 돌려주는 값: (id, 새 공고인지)"""
    now = _iso(config.now())
    extra = dict(raw.extra)
    extra.update(source_kind=source_kind, dentist_only=dentist_only, default_inst_type=default_inst_type)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM postings WHERE source=? AND source_id=?", (raw.source, raw.source_id)
        ).fetchone()
        if row:
            changed = (raw.body and raw.body != row["body"]) or raw.title != row["title"]
            conn.execute(
                "UPDATE postings SET last_seen_at=?, title=?, body=CASE WHEN ?<>'' THEN ? ELSE body END, "
                "url=?, region_hint=CASE WHEN ?<>'' THEN ? ELSE region_hint END, "
                "institution_hint=CASE WHEN ?<>'' THEN ? ELSE institution_hint END WHERE id=?",
                (
                    now, raw.title, raw.body, raw.body, raw.url,
                    raw.region_hint, raw.region_hint, raw.institution_hint, raw.institution_hint, row["id"],
                ),
            )
            if changed:
                fresh = conn.execute("SELECT * FROM postings WHERE id=?", (row["id"],)).fetchone()
                vals = _classification_values(_classify_row(fresh))
                conn.execute(
                    "UPDATE postings SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE id=?",
                    (*vals.values(), row["id"]),
                )
            return row["id"], False

        cur = conn.execute(
            "INSERT INTO postings(source, source_id, url, title, body, author, region_hint, institution_hint, "
            "posted_at, first_seen_at, last_seen_at, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                raw.source, raw.source_id, raw.url, raw.title, raw.body or "", raw.author or "",
                raw.region_hint or "", raw.institution_hint or "", _iso(raw.posted_at), now, now,
                json.dumps(extra, ensure_ascii=False, default=str),
            ),
        )
        pid = cur.lastrowid
        row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
        vals = _classification_values(_classify_row(row))
        conn.execute("UPDATE postings SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE id=?", (*vals.values(), pid))
        return pid, True


def reclassify(only_outdated: bool = True) -> int:
    """분류 규칙이 바뀌었을 때 저장된 공고를 다시 분류한다."""
    n = 0
    with connect() as conn:
        q = "SELECT * FROM postings"
        if only_outdated:
            q += f" WHERE classifier_version < {CLASSIFIER_VERSION}"
        for row in conn.execute(q).fetchall():
            vals = _classification_values(_classify_row(row))
            conn.execute("UPDATE postings SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE id=?", (*vals.values(), row["id"]))
            n += 1
    return n


def known_source_ids(source: str) -> set[str]:
    with connect() as conn:
        return {r[0] for r in conn.execute("SELECT source_id FROM postings WHERE source=?", (source,))}


def get_posting(pid: int) -> Posting | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM postings WHERE id=?", (pid,)).fetchone()
    return Posting.from_row(row) if row else None


def all_postings(*, since: datetime | None = None, include_hidden: bool = True) -> list[Posting]:
    q = "SELECT * FROM postings WHERE 1=1"
    args: list = []
    if since:
        q += " AND COALESCE(posted_at, first_seen_at) >= ?"
        args.append(_iso(since))
    if not include_hidden:
        q += " AND hidden=0"
    q += " ORDER BY COALESCE(posted_at, first_seen_at) DESC, id DESC"
    with connect() as conn:
        return [Posting.from_row(r) for r in conn.execute(q, args).fetchall()]


def set_flag(pid: int, flag: str, value: bool) -> None:
    if flag not in ("starred", "hidden"):
        raise ValueError(flag)
    with connect() as conn:
        conn.execute(f"UPDATE postings SET {flag}=? WHERE id=?", (int(value), pid))


def set_dup_of(pid: int, dup_of: int | None) -> None:
    with connect() as conn:
        conn.execute("UPDATE postings SET dup_of=? WHERE id=?", (dup_of, pid))


def unprocessed() -> list[Posting]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM postings WHERE processed_at IS NULL ORDER BY id").fetchall()
    return [Posting.from_row(r) for r in rows]


def mark_processed(ids: list[int], notified_ids: list[int]) -> None:
    now = _iso(config.now())
    notified = set(notified_ids)
    with connect() as conn:
        for pid in ids:
            conn.execute(
                "UPDATE postings SET processed_at=?, notified_at=CASE WHEN ? THEN ? ELSE notified_at END WHERE id=?",
                (now, int(pid in notified), now, pid),
            )


def due_reminders(today: date, days: int, starred_only: bool) -> list[Posting]:
    """마감이 오늘~days일 뒤이고, 알림을 보냈던 공고 중 아직 마감 알림을 안 보낸 것."""
    q = (
        "SELECT * FROM postings WHERE deadline IS NOT NULL AND deadline >= ? AND deadline <= ? "
        "AND reminded_at IS NULL AND hidden=0 AND dup_of IS NULL"
    )
    args: list = [today.isoformat(), (today + timedelta(days=days)).isoformat()]
    if starred_only:
        q += " AND starred=1"
    else:
        q += " AND (notified_at IS NOT NULL OR starred=1)"
    q += " ORDER BY deadline"
    with connect() as conn:
        return [Posting.from_row(r) for r in conn.execute(q, args).fetchall()]


def mark_reminded(ids: list[int]) -> None:
    now = _iso(config.now())
    with connect() as conn:
        conn.executemany("UPDATE postings SET reminded_at=? WHERE id=?", [(now, i) for i in ids])


def count_postings() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]


# ──────────────────────────── 실행 기록 ────────────────────────────


def run_start(kind: str, source: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs(kind, source, started_at) VALUES(?,?,?)", (kind, source, _iso(config.now()))
        )
        return cur.lastrowid


def run_finish(run_id: int, status: str, *, found: int = 0, new: int = 0, message: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, found=?, new=?, message=? WHERE id=?",
            (_iso(config.now()), status, found, new, message[:2000], run_id),
        )


def recent_runs(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def last_run(kind: str, source: str = "", status: str | None = None) -> dict | None:
    q = "SELECT * FROM runs WHERE kind=? AND source=?"
    args: list = [kind, source]
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT 1"
    with connect() as conn:
        row = conn.execute(q, args).fetchone()
    return dict(row) if row else None


def prune_runs(keep_days: int = 90) -> None:
    cutoff = _iso(config.now() - timedelta(days=keep_days))
    with connect() as conn:
        conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
