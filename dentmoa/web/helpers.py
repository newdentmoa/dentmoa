"""화면에서 쓰는 작은 도우미 (날짜 표시, 출처 이름, 마감 배지 등)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import Flask, current_app, g, request, url_for

from .. import config, taxonomy
from ..matching import MatchResult
from ..models import Posting
from ..settings_store import SOURCE_KEYS

# 출처 모듈을 불러오지 못할 때 쓰는 이름
SOURCE_LABELS: dict[str, str] = {
    "moreden": "모어덴",
    "dentphoto": "덴트포토",
    "alio": "잡알리오",
    "hibrain": "하이브레인넷",
    "gojobs": "나라일터",
    "hospitals": "병원 채용게시판",
}

# 알림 조건 화면에 붙일 짧은 설명
SOURCE_DESCRIPTIONS: dict[str, str] = {
    "moreden": "치과의사 커뮤니티 구인 게시판 (로그인 필요)",
    "dentphoto": "치과의사 커뮤니티 구인 게시판 (로그인 필요)",
    "alio": "공공기관 채용정보 — 국립대병원·공공병원 공고",
    "hibrain": "대학 교원·연구직 채용 — 치과대학 교수 공고",
    "gojobs": "공무원·공공기관 채용 — 보건소·국립병원 공고",
    "hospitals": "치과대학병원·대학병원·공공병원 홈페이지의 채용 게시판을 직접 확인",
}

WEEKDAYS = "월화수목금토일"

NAV = [
    ("postings.index", "공고"),
    ("settings.index", "알림 조건"),
    ("accounts.index", "계정·연결"),
    ("status.index", "상태"),
]


@dataclass
class Card:
    """목록에 보여줄 공고 한 장."""

    p: Posting
    m: MatchResult | None = None
    dups: list[Posting] = field(default_factory=list)


def source_labels() -> dict[str, str]:
    """출처 키 → 이름. 출처 모듈의 이름을 먼저 쓰고, 없으면 기본 이름."""
    if "source_labels" in g:
        return g.source_labels
    labels = dict(SOURCE_LABELS)
    try:
        from ..sources import all_sources

        for s in all_sources():
            if s.key and s.label:
                labels[s.key] = s.label
    except Exception:
        pass
    g.source_labels = labels
    return labels


def source_label(key: str) -> str:
    return source_labels().get(key, key)


def source_description(key: str) -> str:
    return SOURCE_DESCRIPTIONS.get(key, "")


def static_url(filename: str) -> str:
    """정적 파일 주소. 수정 시각을 붙여서 업데이트 뒤 예전 파일이 캐시에 남지 않게 한다."""
    try:
        version = int((Path(current_app.static_folder or "") / filename).stat().st_mtime)
    except OSError:
        version = 0
    return url_for("static", filename=filename, v=version)


def source_keys() -> list[str]:
    keys = list(SOURCE_KEYS)
    for k in source_labels():
        if k not in keys:
            keys.append(k)
    return keys


def today() -> date:
    return config.now().date()


def _local(dt: datetime) -> datetime:
    return dt.astimezone(config.TZ) if dt.tzinfo else dt


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    dt = _local(dt)
    d = dt.date()
    if d == today():
        return f"오늘 {dt:%H:%M}"
    if d.year == today().year:
        return f"{d.month}월 {d.day}일 {dt:%H:%M}"
    return f"{d.year}년 {d.month}월 {d.day}일 {dt:%H:%M}"


def fmt_date(d: date | datetime | None) -> str:
    if not d:
        return ""
    if isinstance(d, datetime):
        d = _local(d).date()
    diff = (today() - d).days
    if diff == 0:
        return "오늘"
    if diff == 1:
        return "어제"
    if 1 < diff < 7:
        return f"{diff}일 전"
    if d.year == today().year:
        return f"{d.month}월 {d.day}일"
    return f"{d.year}년 {d.month}월 {d.day}일"


def fmt_full_date(d: date | None) -> str:
    if not d:
        return ""
    return f"{d.year}년 {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"


def deadline_badge(p: Posting) -> dict | None:
    """마감 배지: {'text', 'cls', 'title'}"""
    if p.deadline_kind == "until_filled":
        return {"text": "채용시 마감", "cls": "badge-open", "title": "채용되면 마감"}
    if p.deadline_kind != "date" or not p.deadline:
        return None
    left = (p.deadline - today()).days
    title = f"마감 {fmt_full_date(p.deadline)}"
    if left < 0:
        return {"text": "마감됨", "cls": "badge-passed", "title": title}
    text = "오늘 마감" if left == 0 else f"D-{left}"
    return {"text": text, "cls": "badge-urgent" if left <= 3 else "badge-dday", "title": title}


def safe_url(url: str | None) -> str:
    """원문 링크는 http(s) 주소만 연다 (javascript: 같은 주소 차단)."""
    url = (url or "").strip()
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return "#"
    return url if scheme in ("http", "https") else "#"


def posting_tags(p: Posting) -> list[dict]:
    """카드에 붙일 짧은 꼬리표."""
    tags = [{"text": taxonomy.short(p.inst_type), "cls": "tag-inst"}]
    tags += [{"text": taxonomy.short(k), "cls": ""} for k in p.positions]
    tags += [{"text": taxonomy.short(k), "cls": "tag-spec"} for k in p.specialties]
    tags += [
        {"text": f"{taxonomy.short(k)} 우대", "cls": "tag-hint"}
        for k in p.specialty_hints
        if k not in p.specialties
    ]
    tags += [{"text": taxonomy.short(k), "cls": ""} for k in p.work_types]
    return tags


def labels(keys: list[str], table: dict[str, str]) -> str:
    return ", ".join(table.get(k, k) for k in keys)


def url_with(**changes) -> str:
    """지금 주소의 검색 조건을 유지한 채 일부만 바꾼 주소."""
    args = [(k, v) for k, v in request.args.items(multi=True) if k not in changes]
    for k, v in changes.items():
        if v is None or v == "":
            continue
        if isinstance(v, (list, tuple)):
            args += [(k, x) for x in v]
        else:
            args.append((k, v))
    qs = urlencode(args)
    return request.path + ("?" + qs if qs else "")


def nav_items() -> list[dict]:
    current = (request.endpoint or "").split(".")[0]
    return [{"endpoint": ep, "label": label, "active": ep.split(".")[0] == current} for ep, label in NAV]


def init_app(app: Flask) -> None:
    app.jinja_env.filters.update(
        fmt_dt=fmt_dt,
        fmt_date=fmt_date,
        fmt_full_date=fmt_full_date,
        short=taxonomy.short,
        safe_url=safe_url,
        source_label=source_label,
    )
    app.jinja_env.globals.update(
        deadline_badge=deadline_badge,
        posting_tags=posting_tags,
        labels=labels,
        url_with=url_with,
        nav_items=nav_items,
        source_label=source_label,
        static_url=static_url,
        tx=taxonomy,
    )
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
