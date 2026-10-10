"""알림 보내기 (텔레그램·메일).

채널은 설정에서 켜져 있고(settings['notify']['telegram'/'email']) 계정 정보가 입력되어 있을 때만 쓴다.
받는 사람이 여럿이면 pipeline 이 사람마다 그 사람 몫의 설정(settings_store.person_settings)과
계정 정보(settings_store.person_secrets — 그 사람의 채팅 ID·받는 주소)를 넘기고, who(이름)를 제목에 붙인다.
돌려주는 값: {'텔레그램': 'ok' 또는 한글 오류 메시지, '메일': …}. 시도한 채널이 없으면 {}.
"""

from __future__ import annotations

from typing import Callable

from .. import settings_store
from . import format as fmt
from . import mailer, telegram
from .errors import NotifyError

TELEGRAM = "텔레그램"
EMAIL = "메일"

__all__ = ["NotifyError", "TELEGRAM", "EMAIL", "send_digest", "send_reminders", "channels", "send_test"]


def channels(settings: dict, secrets: dict | None = None) -> list[str]:
    """지금 보낼 수 있는 채널 이름 목록 (켜져 있고 설정이 끝난 것)."""
    sec = settings_store.secrets() if secrets is None else secrets
    n = settings.get("notify", {})
    out = []
    if n.get("telegram") and telegram.is_configured(sec):
        out.append(TELEGRAM)
    if n.get("email") and mailer.is_configured(sec):
        out.append(EMAIL)
    return out


def _attempt(fn: Callable[[], None]) -> str:
    try:
        fn()
        return "ok"
    except NotifyError as e:
        return str(e)
    except Exception as e:  # 형식 오류 등 예상 못 한 문제도 다른 채널에 영향 주지 않게
        return f"예상하지 못한 오류: {e.__class__.__name__}: {e}"


def _deliver(settings: dict, sec: dict | None, tg_chunks: Callable[[], list[str]],
             mail: Callable[[], tuple[str, str, str]]) -> dict[str, str]:
    sec = settings_store.secrets() if sec is None else sec
    result: dict[str, str] = {}
    for ch in channels(settings, sec):
        if ch == TELEGRAM:
            result[ch] = _attempt(
                lambda: telegram.send_chunks(sec["telegram_token"], sec["telegram_chat_id"], tg_chunks())
            )
        else:
            result[ch] = _attempt(lambda: mailer.send_email(sec, *mail()))
    return result


def send_digest(postings, warnings, settings: dict, *, secrets: dict | None = None, who: str = "") -> dict[str, str]:
    """새 공고 모음 알림."""
    return _deliver(
        settings,
        secrets,
        lambda: fmt.telegram_digest(postings, warnings, settings, who=who),
        lambda: fmt.email_digest(postings, warnings, settings, who=who),
    )


def send_reminders(postings, settings: dict, *, secrets: dict | None = None, who: str = "") -> dict[str, str]:
    """마감 임박 알림."""
    return _deliver(
        settings,
        secrets,
        lambda: fmt.telegram_reminders(postings, settings, who=who),
        lambda: fmt.email_reminders(postings, settings, who=who),
    )


def send_test(channel: str, secrets: dict | None = None) -> None:
    """설정 화면의 '테스트 보내기'. channel: '텔레그램'/'telegram' 또는 '메일'/'email'. 실패하면 NotifyError."""
    sec = settings_store.secrets() if secrets is None else secrets
    if channel in (TELEGRAM, "telegram"):
        telegram.send_test(sec)
    elif channel in (EMAIL, "email"):
        mailer.send_test(sec)
    else:
        raise NotifyError(f"알 수 없는 알림 채널: {channel}")
