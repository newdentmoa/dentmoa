"""알림 오류."""

from __future__ import annotations


class NotifyError(Exception):
    """알림 전송 실패 (사용자에게 그대로 보여줄 한글 메시지)."""
