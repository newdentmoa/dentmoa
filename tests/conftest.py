import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """테스트마다 빈 DB를 쓴다."""
    from dentmoa import config, db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(config, "COOKIE_DIR", tmp_path / "cookies")
    monkeypatch.setattr(config, "BROWSER_DIR", tmp_path / "browser")
    for env in ("MOREDEN_ID", "MOREDEN_PW", "DENTPHOTO_ID", "DENTPHOTO_PW", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO",
                "ADMIN_PASSWORD"):
        monkeypatch.delenv(env, raising=False)
    db.configure(tmp_path / "test.sqlite3")
    yield tmp_path
    db.configure(config.DB_PATH)
