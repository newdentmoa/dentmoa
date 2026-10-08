"""경로·시간대 등 기본 설정. 환경변수로 바꿀 수 있다."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Seoul")

DATA_DIR = Path(os.environ.get("DENTMOA_DATA", Path.cwd() / "data")).resolve()
DB_PATH = DATA_DIR / "dentmoa.sqlite3"
DEBUG_DIR = DATA_DIR / "debug"
BROWSER_DIR = DATA_DIR / "browser"
COOKIE_DIR = DATA_DIR / "cookies"

# 알림 메시지에 넣을 대시보드 주소 (예: https://1-2-3-4.sslip.io)
BASE_URL = os.environ.get("DENTMOA_BASE_URL", "").rstrip("/")

# 웹 서버
HOST = os.environ.get("DENTMOA_HOST", "0.0.0.0")
PORT = int(os.environ.get("DENTMOA_PORT", "8000"))


def now() -> datetime:
    return datetime.now(TZ)


def ensure_dirs() -> None:
    for d in (DATA_DIR, DEBUG_DIR, BROWSER_DIR, COOKIE_DIR):
        d.mkdir(parents=True, exist_ok=True)
