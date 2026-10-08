"""사용자 설정과 계정 정보.

- 설정(조건·알림·수집): DB의 kv 'settings'
- 계정 정보(사이트 아이디·비밀번호, 텔레그램, 메일): DB의 kv 'secrets'
  같은 이름의 환경변수가 있으면 환경변수가 우선한다.
"""

from __future__ import annotations

import copy
import os
import re

from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .regions import is_valid_selection
from .taxonomy import INST_TYPES, POSITIONS, POST_KINDS, SPECIALTIES, WORK_TYPES

SOURCE_KEYS = ["moreden", "dentphoto", "alio", "hibrain", "gojobs", "hospitals"]

DEFAULT_SETTINGS: dict = {
    "filters": {
        "post_kinds": ["hiring"],
        "dentist_only": True,
        "inst_types": list(INST_TYPES),
        "positions": [k for k in POSITIONS if k not in ("resident", "other")],
        "specialties": list(SPECIALTIES),
        "work_types": list(WORK_TYPES),
        "regions": ["전국"],
        "include_uncertain_region": True,  # 지역을 알 수 없는 공고도 받기
        "include_hints": False,  # '교정 가능자 우대'처럼 참고 분야만 맞아도 받기
        "include_keywords": [],  # 하나라도 있으면 다른 조건과 상관없이 받기
        "exclude_keywords": [],  # 하나라도 있으면 받지 않기
    },
    "notify": {
        "telegram": True,
        "email": True,
        "times": ["08:10", "12:40", "18:20"],
        "send_empty": False,  # 새 공고가 없어도 '없음' 알림 보내기
        "reminder_enabled": True,
        "reminder_days": 3,
        "reminder_starred_only": False,
        "max_items": 40,
    },
    "collect": {
        "backfill_days": 30,
        "jitter_minutes": 15,
        "min_delay_sec": 3.0,
        "max_delay_sec": 8.0,
        "max_pages": 2,
        "backfill_max_pages": 10,
        "sources": {k: True for k in SOURCE_KEYS},
    },
}

SECRET_FIELDS: dict[str, str] = {
    # 키: 환경변수 이름
    "moreden_id": "MOREDEN_ID",
    "moreden_pw": "MOREDEN_PW",
    "dentphoto_id": "DENTPHOTO_ID",
    "dentphoto_pw": "DENTPHOTO_PW",
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "email_to": "EMAIL_TO",
}
SECRET_DEFAULTS = {"smtp_host": "smtp.gmail.com", "smtp_port": "587"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def load() -> dict:
    return _merge(DEFAULT_SETTINGS, db.kv_get("settings", {}))


def save(settings: dict) -> dict:
    clean = validate(settings)
    db.kv_set("settings", clean)
    return clean


def validate(settings: dict) -> dict:
    s = _merge(DEFAULT_SETTINGS, settings)
    f = s["filters"]
    f["post_kinds"] = [k for k in f["post_kinds"] if k in POST_KINDS]
    f["inst_types"] = [k for k in f["inst_types"] if k in INST_TYPES]
    f["positions"] = [k for k in f["positions"] if k in POSITIONS]
    f["specialties"] = [k for k in f["specialties"] if k in SPECIALTIES]
    f["work_types"] = [k for k in f["work_types"] if k in WORK_TYPES]
    f["regions"] = list(dict.fromkeys(r.strip() for r in f["regions"] if is_valid_selection(r.strip()))) or ["전국"]
    if "전국" in f["regions"]:
        f["regions"] = ["전국"]
    for key in ("include_keywords", "exclude_keywords"):
        f[key] = _keywords(f[key])
    for key in ("dentist_only", "include_uncertain_region", "include_hints"):
        f[key] = bool(f[key])

    n = s["notify"]
    times = sorted({t.strip() for t in n["times"] if TIME_RE.match(t.strip())})
    n["times"] = times or list(DEFAULT_SETTINGS["notify"]["times"])
    n["reminder_days"] = _clamp_int(n["reminder_days"], 1, 30, 3)
    n["max_items"] = _clamp_int(n["max_items"], 5, 200, 40)
    for key in ("telegram", "email", "send_empty", "reminder_enabled", "reminder_starred_only"):
        n[key] = bool(n[key])

    c = s["collect"]
    c["backfill_days"] = _clamp_int(c["backfill_days"], 1, 90, 30)
    c["jitter_minutes"] = _clamp_int(c["jitter_minutes"], 0, 60, 15)
    c["max_pages"] = _clamp_int(c["max_pages"], 1, 10, 2)
    c["backfill_max_pages"] = _clamp_int(c["backfill_max_pages"], 1, 30, 10)
    c["min_delay_sec"] = _clamp_float(c["min_delay_sec"], 1.0, 60.0, 3.0)
    c["max_delay_sec"] = max(c["min_delay_sec"], _clamp_float(c["max_delay_sec"], 1.0, 120.0, 8.0))
    c["sources"] = {k: bool(c["sources"].get(k, True)) for k in SOURCE_KEYS}
    return s


def _keywords(value) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    return list(dict.fromkeys(k.strip() for k in value if k and k.strip()))[:100]


def _clamp_int(v, lo, hi, default) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _clamp_float(v, lo, hi, default) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


# ──────────────────────────── 계정 정보 ────────────────────────────


def secrets() -> dict[str, str]:
    stored = db.kv_get("secrets", {}) or {}
    out = {}
    for key, env in SECRET_FIELDS.items():
        out[key] = os.environ.get(env) or stored.get(key) or SECRET_DEFAULTS.get(key, "")
    return out


def secret_sources() -> dict[str, str]:
    """각 항목이 어디서 왔는지: 'env' / 'db' / ''"""
    stored = db.kv_get("secrets", {}) or {}
    out = {}
    for key, env in SECRET_FIELDS.items():
        out[key] = "env" if os.environ.get(env) else ("db" if stored.get(key) else "")
    return out


def update_secrets(values: dict[str, str]) -> None:
    """빈 값은 '바꾸지 않음'. 지우려면 '__clear__'."""
    stored = db.kv_get("secrets", {}) or {}
    for key, val in values.items():
        if key not in SECRET_FIELDS or val is None:
            continue
        val = val.strip()
        if val == "__clear__":
            stored.pop(key, None)
        elif val:
            stored[key] = val
    db.kv_set("secrets", stored)


# ──────────────────────────── 대시보드 비밀번호 ────────────────────────────


def has_password() -> bool:
    return bool(db.kv_get("auth", {}).get("hash"))


def set_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    db.kv_set("auth", {"hash": generate_password_hash(password)})


def check_password(password: str) -> bool:
    h = db.kv_get("auth", {}).get("hash")
    return bool(h) and check_password_hash(h, password)


def init_password_from_env() -> None:
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw and not has_password():
        set_password(pw)
