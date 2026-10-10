"""사용자 설정과 계정 정보.

- 설정: DB의 kv 'settings'
  - 'people': 알림을 받는 사람들(처음엔 SH·JY). 사람마다 받을 공고의 조건(filters), 알림 방법(alerts),
    텔레그램 채팅 ID·받는 메일 주소가 따로 있다. 대시보드도 고른 사람의 조건으로 보여 준다.
  - 'notify'(알림 시각·한 번에 보여 줄 수), 'collect'(수집)은 모두 함께 쓴다 — 공고는 한 번만 모은다.
- 계정 정보(사이트 아이디·비밀번호, 텔레그램 봇 토큰, 보내는 메일 계정): DB의 kv 'secrets'
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

# 받을 공고의 조건 (사람마다 따로)
DEFAULT_FILTERS: dict = {
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
}

# 알림 받는 방법 (사람마다 따로)
DEFAULT_ALERTS: dict = {
    "telegram": True,
    "email": True,
    "send_empty": False,  # 새 공고가 없어도 '없음' 알림 보내기
    "reminder_enabled": True,
    "reminder_days": 3,
    "reminder_starred_only": False,
    "warnings": True,  # 로그인 실패·사이트 접속 실패 같은 경고도 받기
}

# 처음 만들 때의 사람들 (사용자 요청 2026-10-10: 나=SH, 아내=JY). 이름은 대시보드에서 바꿀 수 있다.
# 키(p1, p2 …)는 DB의 사람별 기록에 쓰이므로 바뀌지 않는다. 사람별 기록이 생기기 전의 기록은 p1 것이다.
DEFAULT_PEOPLE = (("p1", "SH"), ("p2", "JY"))
PERSON_KEY_RE = re.compile(r"^p[1-9]\d{0,2}$")
MAX_PEOPLE = 5
NAME_MAX = 12
ALERT_KEYS = tuple(DEFAULT_ALERTS)

DEFAULT_SETTINGS: dict = {
    "people": [],  # 비어 있으면 load() 가 DEFAULT_PEOPLE 로 채운다
    "notify": {
        "times": ["08:10", "12:40", "18:20"],
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


def new_person(key: str, name: str, *, warnings: bool = True) -> dict:
    return {
        "key": key,
        "name": name,
        "filters": copy.deepcopy(DEFAULT_FILTERS),
        "alerts": {**DEFAULT_ALERTS, "warnings": warnings},
        "telegram_chat_id": "",  # 비어 있으면 첫 사람은 환경변수 TELEGRAM_CHAT_ID 를 쓴다
        "email_to": "",  # 받는 메일 주소 (여러 곳이면 쉼표). 첫 사람은 환경변수 EMAIL_TO 를 쓸 수 있다
    }


def default_people() -> list[dict]:
    return [new_person(k, name, warnings=i == 0) for i, (k, name) in enumerate(DEFAULT_PEOPLE)]


def _from_legacy(raw: dict) -> dict:
    """사람별 설정이 생기기 전(2026-10-10 이전)의 설정 → 첫 사람(SH)의 설정 + JY.

    예전 계정 화면에 저장한 텔레그램 채팅 ID·받는 메일 주소도 첫 사람에게 옮긴다.
    """
    people = default_people()
    first = people[0]
    if isinstance(raw.get("filters"), dict):
        first["filters"] = _merge(DEFAULT_FILTERS, raw["filters"])
    notify = raw.get("notify") if isinstance(raw.get("notify"), dict) else {}
    for key in ALERT_KEYS:
        if key in notify:
            first["alerts"][key] = notify[key]
    stored = db.kv_get("secrets", {}) or {}
    moved = False
    for key in ("telegram_chat_id", "email_to"):
        if stored.get(key):
            first[key] = stored.pop(key)
            moved = True
    if moved:
        db.kv_set("secrets", stored)
    out = {k: raw[k] for k in ("collect",) if k in raw}
    out["notify"] = {k: notify[k] for k in DEFAULT_SETTINGS["notify"] if k in notify}
    out["people"] = people
    return out


def load() -> dict:
    raw = db.kv_get("settings", {}) or {}
    if not raw.get("people"):
        raw = _from_legacy(raw)
        clean = validate(raw)
        db.kv_set("settings", clean)
        return clean
    return validate(raw)


def save(settings: dict) -> dict:
    clean = validate(settings)
    db.kv_set("settings", clean)
    return clean


# ──────────────────────────── 사람 ────────────────────────────


def person(settings: dict, key: str | None) -> dict:
    """키로 사람 찾기. 없으면 첫 사람."""
    for p in settings["people"]:
        if p["key"] == key:
            return p
    return settings["people"][0]


def is_first(settings: dict, p: dict) -> bool:
    return settings["people"][0]["key"] == p["key"]


def person_settings(settings: dict, p: dict) -> dict:
    """한 사람 몫의 설정: 그 사람의 조건(filters)과, 함께 쓰는 알림 설정 + 그 사람의 알림 방법(notify)."""
    return {
        "filters": p["filters"],
        "notify": {**settings["notify"], **p["alerts"]},
        "collect": settings["collect"],
        "person": p,
    }


def person_secrets(settings: dict, p: dict, sec: dict | None = None) -> dict:
    """이 사람에게 보낼 때 쓰는 계정 정보: 함께 쓰는 봇·메일 계정 + 이 사람의 채팅 ID·받는 주소."""
    out = dict(secrets() if sec is None else sec)
    first = is_first(settings, p)
    for key in ("telegram_chat_id", "email_to"):
        out[key] = p.get(key) or (out.get(key, "") if first else "")
    return out


def target_source(settings: dict, p: dict, key: str) -> str:
    """사람의 채팅 ID·받는 주소가 어디서 왔는지: 'db' / 'env' / ''"""
    if p.get(key):
        return "db"
    if is_first(settings, p) and os.environ.get(SECRET_FIELDS[key]):
        return "env"
    return ""


def clean_name(name) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:NAME_MAX]


def next_person_key(settings: dict) -> str:
    used = {int(p["key"][1:]) for p in settings["people"]}
    return f"p{max(used, default=0) + 1}"


def _validate_filters(f: dict) -> None:
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


def _validate_alerts(a: dict) -> None:
    a["reminder_days"] = _clamp_int(a["reminder_days"], 1, 30, 3)
    for key in ("telegram", "email", "send_empty", "reminder_enabled", "reminder_starred_only", "warnings"):
        a[key] = bool(a[key])


def _validate_people(people) -> list[dict]:
    out: list[dict] = []
    keys: set[str] = set()
    names: set[str] = set()
    for raw in people if isinstance(people, list) else []:
        if not isinstance(raw, dict) or len(out) >= MAX_PEOPLE:
            continue
        key = str(raw.get("key") or "")
        if not PERSON_KEY_RE.match(key) or key in keys:
            continue
        p = _merge(new_person(key, ""), raw)
        _validate_filters(p["filters"])
        _validate_alerts(p["alerts"])
        name = base = clean_name(p["name"]) or f"사람{len(out) + 1}"
        n = 2
        while name in names:  # 같은 이름이면 뒤에 숫자를 붙인다
            name = base[: NAME_MAX - len(str(n))] + str(n)
            n += 1
        p["name"] = name
        p["telegram_chat_id"] = str(p["telegram_chat_id"] or "").strip()[:40]
        p["email_to"] = str(p["email_to"] or "").strip()[:300]
        keys.add(key)
        names.add(name)
        out.append(p)
    return out or default_people()


def validate(settings: dict) -> dict:
    s = _merge(DEFAULT_SETTINGS, settings)
    s["people"] = _validate_people(s["people"])

    n = s["notify"]
    times = sorted({t.strip() for t in n["times"] if TIME_RE.match(t.strip())})
    n["times"] = times or list(DEFAULT_SETTINGS["notify"]["times"])
    n["max_items"] = _clamp_int(n["max_items"], 5, 200, 40)

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
        try:
            set_password(pw)
        except ValueError as e:
            print(f"경고: ADMIN_PASSWORD 를 쓰지 않았습니다 — {e} 처음 화면에서 비밀번호를 정해 주세요.")


def setup_code() -> str:
    """처음 비밀번호를 정할 때 필요한 설치 코드.

    서버를 켠 직후 주소를 먼저 알아낸 다른 사람이 비밀번호를 정해 버리지 못하게 한다.
    install.sh 가 deploy/.env 에 DENTMOA_SETUP_CODE 를 만들어 두고 화면에 보여준다.
    없으면 여기서 만들어 서버 기록(로그)에 남긴다.
    """
    env = (os.environ.get("DENTMOA_SETUP_CODE") or "").strip()
    if env:
        return env
    code = db.kv_get("setup_code")
    if not code:
        import secrets as _secrets

        code = f"{_secrets.randbelow(10**6):06d}"
        db.kv_set("setup_code", code)
    return code


def check_setup_code(value: str) -> bool:
    import hmac

    return hmac.compare_digest((value or "").strip().encode(), setup_code().encode())
