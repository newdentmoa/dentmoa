"""알림 메시지 만들기 — 텔레그램(HTML 조각 목록)과 메일(제목, HTML, 글).

사이트에서 가져온 글은 모두 escape 해서 넣는다.
텔레그램에는 <b>, <i>, <a> 태그만 쓰고, 태그는 항상 한 줄 안에서 닫는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape

from .. import config
from ..models import Posting
from ..taxonomy import short
from .telegram import LIMIT, pack

WEEKDAYS = "월화수목금토일"
EMPTY_TEXT = "새로 올라온 조건에 맞는 공고가 없어요"
MORE_TEXT = "외 {n}건은 대시보드에서 확인하세요"
WARN_TITLE = "⚠️ 확인이 필요해요"

FALLBACK_SOURCE_LABELS = {
    "moreden": "모어덴",
    "dentphoto": "덴트포토",
    "alio": "잡알리오",
    "hibrain": "하이브레인넷",
    "hospitals": "병원 채용게시판",
}

WARNING_TEXT = {
    "credentials": "아이디·비밀번호가 아직 설정되지 않았어요 (계정·연결 화면)",
    "login": "로그인에 실패했어요. 비밀번호가 바뀌었는지 확인해 주세요",
    "structure": "사이트 화면 구조가 바뀐 것 같아요. 프로그램 수리가 필요해요",
    "network": "사이트에 접속하지 못했어요 (일시적일 수 있어요)",
    "error": "예상하지 못한 오류",
}

_SAFE_URL_RE = re.compile(r"^https?://", re.I)


# ──────────────────────────── 날짜·라벨 ────────────────────────────


def when_text(dt: datetime) -> str:
    """'10월 8일(목) 12:40'"""
    return f"{dt.month}월 {dt.day}일({WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"


def short_date(d: date) -> str:
    """'10/16(금)'"""
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def dday(d: date, today: date) -> str:
    n = (d - today).days
    if n > 0:
        return f"D-{n}"
    return "D-Day" if n == 0 else "지남"


def deadline_text(p: Posting, today: date) -> str:
    """'마감 10/16(금) · D-8' / '채용시 마감' / '' (정보 없음)"""
    if p.deadline_kind == "date" and p.deadline:
        return f"마감 {short_date(p.deadline)} · {dday(p.deadline, today)}"
    if p.deadline_kind == "until_filled":
        return "채용시 마감"
    return ""


def warning_text(kind: str) -> str:
    return WARNING_TEXT.get(kind, WARNING_TEXT["error"])


def source_labels() -> dict[str, str]:
    labels = dict(FALLBACK_SOURCE_LABELS)
    try:
        from ..sources import all_sources

        labels.update({s.key: s.label for s in all_sources() if s.key and s.label})
    except Exception:  # 수집기 파일이 아직 없거나 불러오기 실패 → 기본 이름
        pass
    return labels


def _tags(p: Posting) -> str:
    parts = ["기타 기관" if p.inst_type == "other" else short(p.inst_type)] if p.inst_type else []
    parts += ["기타 직위" if k == "other" else short(k) for k in p.positions]
    parts += [short(k) for k in p.specialties]
    parts += [f"{short(k)}(참고)" for k in p.specialty_hints if k not in p.specialties]
    parts += [short(k) for k in p.work_types]
    return " · ".join(dict.fromkeys(parts))


def _regions(p: Posting) -> str:
    labels = p.region_labels
    if not labels:
        return "지역 미상"
    text = ", ".join(labels[:3])
    return text + (f" 외 {len(labels) - 3}곳" if len(labels) > 3 else "")


def _safe_url(url: str) -> str:
    return url if url and _SAFE_URL_RE.match(url) else ""


# ──────────────────────────── 항목 ────────────────────────────


@dataclass
class _Item:
    title: str
    tags: str
    regions: str
    institution: str
    deadline: str
    summary: list[str] = field(default_factory=list)
    url: str = ""
    dash_url: str = ""
    source: str = ""
    starred: bool = False


def _item(p: Posting, today: date, labels: dict[str, str]) -> _Item:
    title = (p.title or "").strip() or "(제목 없음)"
    inst = (p.institution or p.institution_hint or "").strip()
    if inst and inst in title:
        inst = ""  # 제목에 이미 있으면 생략
    source = labels.get(p.source, p.source)
    if p.posted_at:
        source += f" · {p.posted_at.month}/{p.posted_at.day} 게시"
    return _Item(
        title=title,
        tags=_tags(p),
        regions=_regions(p),
        institution=inst,
        deadline=deadline_text(p, today),
        summary=[s.strip() for s in p.summary[:3] if s and s.strip()],
        url=_safe_url(p.url),
        dash_url=f"{config.BASE_URL}/posting/{p.id}" if config.BASE_URL else "",
        source=source,
        starred=p.starred,
    )


def _limit(postings: list[Posting], settings: dict) -> tuple[list[Posting], int]:
    try:
        n = int(settings["notify"]["max_items"])
    except (KeyError, TypeError, ValueError):
        n = 40
    n = max(1, n)
    return postings[:n], max(0, len(postings) - n)


def _now(now: datetime | None) -> datetime:
    return now or config.now()


def _warnings(warnings) -> list[tuple[str, str, str]]:
    """(출처 이름, 설명, 자세한 내용)"""
    out = []
    for w in warnings or []:
        kind = getattr(w, "kind", "") or "error"
        label = getattr(w, "label", "") or getattr(w, "key", "")
        detail = (getattr(w, "message", "") or "").strip().split("\n")[0]
        if kind == "credentials":
            detail = ""
        out.append((label, warning_text(kind), detail[:150]))
    return out


# ──────────────────────────── 텔레그램 ────────────────────────────


def _e(text: str) -> str:
    return escape(text or "", quote=False)


def _a(url: str, label: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{_e(label)}</a>'


def _tg_item(it: _Item, *, compact: bool = False) -> str:
    lines = [("⭐ " if it.starred else "") + f"<b>{_e(it.title)}</b>"]
    if compact:
        if it.deadline:
            lines.append(f"⏰ {_e(it.deadline)}")
        place = " · ".join(x for x in (it.institution, it.regions) if x)
        lines.append(f"📍 {_e(place)}")
    else:
        lines.append(f"{_e(it.tags)} · 📍 {_e(it.regions)}" if it.tags else f"📍 {_e(it.regions)}")
        if it.institution:
            lines.append(f"🏥 {_e(it.institution)}")
        if it.deadline:
            lines.append(f"⏰ {_e(it.deadline)}")
        lines += [f"• {_e(s)}" for s in it.summary]
    links = []
    if it.url:
        links.append(_a(it.url, "원문"))
    if it.dash_url:
        links.append(_a(it.dash_url, "대시보드"))
    links.append(_e(it.source))
    lines.append(" · ".join(links))
    return "\n".join(lines)


def _tg_more(more: int) -> str:
    text = _e(MORE_TEXT.format(n=more))
    if config.BASE_URL:
        text += " 👉 " + _a(config.BASE_URL + "/", "열기")
    return text


def _tg_warnings(warnings) -> str:
    lines = [f"<b>{WARN_TITLE}</b>"]
    for label, text, detail in _warnings(warnings):
        lines.append(f"• {_e(label)}: {_e(text)}")
        if detail:
            lines.append(f"  <i>{_e(detail)}</i>")
    return "\n".join(lines)


def _number(chunks: list[str]) -> list[str]:
    """여러 개로 나뉘면 두 번째부터 (2/3) 표시."""
    n = len(chunks)
    return [c if i == 0 else f"<i>({i + 1}/{n})</i>\n{c}" for i, c in enumerate(chunks)]


def _who(who: str) -> str:
    """제목에 넣을 받는 사람 이름 ('SH · ') — 받는 사람이 한 명이면 비어 있다."""
    return f"{who} · " if who else ""


def telegram_digest(postings: list[Posting], warnings, settings: dict, *, now: datetime | None = None,
                    who: str = "") -> list[str]:
    now = _now(now)
    labels = source_labels()
    shown, more = _limit(postings, settings)
    blocks = [f"🦷 <b>덴트모아</b> · {_e(_who(who))}{when_text(now)} · 새 공고 {len(postings)}건"]
    if not postings:
        blocks.append(EMPTY_TEXT)
    blocks += [_tg_item(_item(p, now.date(), labels)) for p in shown]
    if more:
        blocks.append(_tg_more(more))
    if warnings:
        blocks.append(_tg_warnings(warnings))
    return _number(pack(blocks, LIMIT - 20))


def telegram_reminders(postings: list[Posting], settings: dict, *, now: datetime | None = None,
                       who: str = "") -> list[str]:
    now = _now(now)
    labels = source_labels()
    shown, more = _limit(postings, settings)
    blocks = [f"⏰ <b>마감 임박 공고</b> · {_e(_who(who))}{when_text(now)} · {len(postings)}건"]
    blocks += [_tg_item(_item(p, now.date(), labels), compact=True) for p in shown]
    if more:
        blocks.append(_tg_more(more))
    return _number(pack(blocks, LIMIT - 20))


# ──────────────────────────── 메일 ────────────────────────────

_FONT = "-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif"
_MUTED = "color:#57606a;font-size:13px;margin:2px 0"


def _h(text: str) -> str:
    return escape(text or "", quote=True)


def _mail_item(it: _Item, *, compact: bool = False) -> str:
    title = _h(it.title)
    if it.url:
        title = f'<a href="{_h(it.url)}" style="color:#1f2328;text-decoration:none">{title}</a>'
    rows = [f'<div style="font-size:16px;font-weight:700;margin:0 0 4px">{"⭐ " if it.starred else ""}{title}</div>']
    if not compact and it.tags:
        rows.append(f'<div style="{_MUTED}">{_h(it.tags)}</div>')
    place = " · ".join(x for x in (it.institution if compact else "", it.regions) if x)
    rows.append(f'<div style="{_MUTED}">📍 {_h(place)}</div>')
    if not compact and it.institution:
        rows.append(f'<div style="{_MUTED}">🏥 {_h(it.institution)}</div>')
    if it.deadline:
        rows.append(f'<div style="color:#b42318;font-size:13px;font-weight:600;margin:2px 0">⏰ {_h(it.deadline)}</div>')
    if not compact and it.summary:
        lis = "".join(f'<li style="margin:2px 0">{_h(s)}</li>' for s in it.summary)
        rows.append(f'<ul style="margin:6px 0 4px;padding-left:18px;font-size:14px;color:#1f2328">{lis}</ul>')
    links = []
    if it.url:
        links.append(f'<a href="{_h(it.url)}" style="color:#0969da">원문 보기</a>')
    if it.dash_url:
        links.append(f'<a href="{_h(it.dash_url)}" style="color:#0969da">대시보드</a>')
    links.append(f'<span style="color:#57606a">{_h(it.source)}</span>')
    rows.append(f'<div style="font-size:13px;margin:6px 0 0">{" · ".join(links)}</div>')
    return (
        '<div style="background:#ffffff;border:1px solid #e3e6ea;border-radius:10px;'
        f'padding:12px 14px;margin:0 0 10px">{"".join(rows)}</div>'
    )


def _mail_page(heading: str, body: str) -> str:
    footer = "덴트모아가 보낸 알림이에요. 조건과 알림 시각은 대시보드에서 바꿀 수 있어요."
    if config.BASE_URL:
        footer += f' <a href="{_h(config.BASE_URL)}/" style="color:#0969da">대시보드 열기</a>'
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f4f5f7">'
        f'<div style="max-width:640px;margin:0 auto;padding:16px 12px;font-family:{_FONT};'
        'color:#1f2328;line-height:1.5;word-break:keep-all;overflow-wrap:anywhere">'
        f'<div style="font-size:18px;font-weight:700;margin:0 0 12px">{_h(heading)}</div>'
        f"{body}"
        f'<div style="color:#8c959f;font-size:12px;margin:16px 0 0">{footer}</div>'
        "</div></body></html>"
    )


def _mail_note(text: str) -> str:
    return f'<div style="color:#57606a;font-size:14px;margin:4px 0 12px">{_h(text)}</div>'


def _mail_warnings(warnings) -> str:
    items = []
    for label, text, detail in _warnings(warnings):
        d = f'<br><span style="color:#57606a;font-size:12px">{_h(detail)}</span>' if detail else ""
        items.append(f'<li style="margin:4px 0"><b>{_h(label)}</b>: {_h(text)}{d}</li>')
    return (
        '<div style="background:#fff8e1;border:1px solid #f0d68a;border-radius:10px;padding:10px 14px;margin:12px 0 10px">'
        f'<div style="font-weight:700;margin:0 0 4px">{WARN_TITLE}</div>'
        f'<ul style="margin:0;padding-left:18px;font-size:14px">{"".join(items)}</ul></div>'
    )


def _text_item(it: _Item, *, compact: bool = False) -> str:
    lines = [("⭐ " if it.starred else "") + f"■ {it.title}"]
    if not compact and it.tags:
        lines.append(f"  {it.tags}")
    lines.append(f"  📍 {' · '.join(x for x in (it.institution if compact else '', it.regions) if x)}")
    if not compact and it.institution:
        lines.append(f"  🏥 {it.institution}")
    if it.deadline:
        lines.append(f"  ⏰ {it.deadline}")
    if not compact:
        lines += [f"  • {s}" for s in it.summary]
    if it.url:
        lines.append(f"  원문: {it.url}")
    if it.dash_url:
        lines.append(f"  대시보드: {it.dash_url}")
    lines.append(f"  출처: {it.source}")
    return "\n".join(lines)


def _text_warnings(warnings) -> str:
    lines = [WARN_TITLE]
    for label, text, detail in _warnings(warnings):
        lines.append(f"• {label}: {text}" + (f"\n  ({detail})" if detail else ""))
    return "\n".join(lines)


def _tag(who: str) -> str:
    """메일 제목 머리: '[덴트모아]' 또는 '[덴트모아·SH]'"""
    return f"[덴트모아·{who}]" if who else "[덴트모아]"


def email_digest(postings: list[Posting], warnings, settings: dict, *, now: datetime | None = None,
                 who: str = "") -> tuple[str, str, str]:
    """(제목, HTML, 글)"""
    now = _now(now)
    labels = source_labels()
    shown, more = _limit(postings, settings)
    items = [_item(p, now.date(), labels) for p in shown]
    when = when_text(now)
    heading = f"🦷 덴트모아 · {_who(who)}{when} · 새 공고 {len(postings)}건"
    if postings:
        subject = f"{_tag(who)} 새 공고 {len(postings)}건 · {when}"
    elif warnings:
        subject = f"{_tag(who)} 확인이 필요해요 · {when}"
    else:
        subject = f"{_tag(who)} 새 공고 없음 · {when}"

    html_parts, text_parts = [], [heading]
    if not postings:
        html_parts.append(_mail_note(EMPTY_TEXT))
        text_parts.append(EMPTY_TEXT)
    html_parts += [_mail_item(it) for it in items]
    text_parts += [_text_item(it) for it in items]
    if more:
        html_parts.append(_mail_note(MORE_TEXT.format(n=more)))
        text_parts.append(MORE_TEXT.format(n=more))
    if warnings:
        html_parts.append(_mail_warnings(warnings))
        text_parts.append(_text_warnings(warnings))
    if config.BASE_URL:
        text_parts.append(f"대시보드: {config.BASE_URL}/")
    return subject, _mail_page(heading, "".join(html_parts)), "\n\n".join(text_parts) + "\n"


def email_reminders(postings: list[Posting], settings: dict, *, now: datetime | None = None,
                    who: str = "") -> tuple[str, str, str]:
    now = _now(now)
    labels = source_labels()
    shown, more = _limit(postings, settings)
    items = [_item(p, now.date(), labels) for p in shown]
    when = when_text(now)
    heading = f"⏰ 마감 임박 공고 · {_who(who)}{when} · {len(postings)}건"
    subject = f"{_tag(who)} 마감 임박 공고 {len(postings)}건 · {when}"
    html_parts = [_mail_item(it, compact=True) for it in items]
    text_parts = [heading] + [_text_item(it, compact=True) for it in items]
    if more:
        html_parts.append(_mail_note(MORE_TEXT.format(n=more)))
        text_parts.append(MORE_TEXT.format(n=more))
    return subject, _mail_page(heading, "".join(html_parts)), "\n\n".join(text_parts) + "\n"
