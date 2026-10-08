"""알림 보내기 (텔레그램·메일). 구현 예정."""

from __future__ import annotations


def send_digest(postings, warnings, settings) -> dict[str, str]:
    raise NotImplementedError


def send_reminders(postings, settings) -> dict[str, str]:
    raise NotImplementedError
