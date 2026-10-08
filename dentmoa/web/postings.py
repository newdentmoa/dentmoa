"""공고 목록·상세, 관심(★)·숨기기."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from .. import config, db, settings_store
from ..matching import match
from ..models import Posting
from ..regions_data import SIDO_ORDER
from ..taxonomy import INST_TYPES, POSITIONS, POST_KINDS, SPECIALTIES, WORK_TYPES
from .helpers import Card, labels, source_keys, source_labels
from .security import safe_next, wants_json

bp = Blueprint("postings", __name__)

PAGE_SIZE = 50
PERIODS = (7, 30, 90, 365)
DEFAULT_DAYS = 30
VIEWS = {"match": "내 조건에 맞는 공고", "all": "전체 공고", "star": "관심(★)"}


@dataclass
class ListQuery:
    """목록 화면의 검색 조건 (주소의 ?뒤 값)."""

    view: str = "match"
    q: str = ""
    inst: tuple[str, ...] = ()
    pos: tuple[str, ...] = ()
    spec: tuple[str, ...] = ()
    work: tuple[str, ...] = ()
    sido: tuple[str, ...] = ()
    src: tuple[str, ...] = ()
    days: int = DEFAULT_DAYS
    show_hidden: bool = False
    include_other: bool = False
    page: int = 1

    @classmethod
    def from_args(cls, args) -> "ListQuery":
        def pick(name: str, allowed) -> tuple[str, ...]:
            return tuple(dict.fromkeys(v for v in args.getlist(name) if v in allowed))

        try:
            days = int(args.get("days", DEFAULT_DAYS))
        except ValueError:
            days = DEFAULT_DAYS
        try:
            page = max(1, int(args.get("page", 1)))
        except ValueError:
            page = 1
        view = args.get("view", "match")
        return cls(
            view=view if view in VIEWS else "match",
            q=" ".join(args.get("q", "").split())[:100],
            inst=pick("inst", INST_TYPES),
            pos=pick("pos", POSITIONS),
            spec=pick("spec", SPECIALTIES),
            work=pick("work", WORK_TYPES),
            sido=pick("sido", SIDO_ORDER),
            src=pick("src", source_labels()),
            days=days if days in PERIODS else DEFAULT_DAYS,
            show_hidden=args.get("hidden") == "1",
            include_other=args.get("other") == "1",
            page=page,
        )

    @property
    def narrowed(self) -> bool:
        """세부 검색 조건을 하나라도 썼는지 (검색 창을 펼쳐 둘지)."""
        return bool(
            self.q or self.inst or self.pos or self.spec or self.work or self.sido or self.src
            or self.show_hidden or self.include_other or self.days != DEFAULT_DAYS
        )

    def passes(self, p: Posting) -> bool:
        if self.q:
            hay = f"{p.title}\n{p.institution}\n{p.institution_hint}\n{p.body}".lower()
            if not all(word in hay for word in self.q.lower().split()):
                return False
        if self.inst and p.inst_type not in self.inst:
            return False
        if self.pos and not set(self.pos) & set(p.positions):
            return False
        if self.spec and not set(self.spec) & set(p.specialties + p.specialty_hints):
            return False
        if self.work and not set(self.work) & set(p.work_types):
            return False
        if self.sido and not set(self.sido) & {r.sido for r in p.regions}:
            return False
        if self.src and p.source not in self.src:
            return False
        return True


def dup_map(postings: list[Posting]) -> dict[int, list[Posting]]:
    """원본 공고 id → 다른 출처에 올라온 같은 공고들"""
    out: dict[int, list[Posting]] = defaultdict(list)
    for p in postings:
        if p.dup_of is not None:
            out[p.dup_of].append(p)
    return out


def matching_count(filters: dict, days: int = 30) -> int:
    """최근 days일 공고 중 알림 조건에 맞는 공고 수 (숨긴 글·중복 제외)."""
    since = config.now() - timedelta(days=days)
    return sum(
        1
        for p in db.all_postings(since=since, include_hidden=False)
        if p.dup_of is None and match(p, filters).ok
    )


def build_cards(lq: ListQuery, settings: dict) -> list[Card]:
    since = None if lq.view == "star" else config.now() - timedelta(days=lq.days)
    posts = db.all_postings(since=since)
    dups = dup_map(posts)
    filters = settings["filters"]
    loose = {**filters, "post_kinds": list(POST_KINDS), "dentist_only": False} if lq.include_other else filters

    cards = []
    for p in posts:
        if p.hidden and not lq.show_hidden:
            continue
        if lq.view == "star":
            if not p.starred:
                continue
        elif p.dup_of is not None:
            continue  # 같은 공고는 원본 카드에 '다른 출처에도 있음'으로 표시
        if lq.view != "match" and not lq.include_other and (p.post_kind != "hiring" or not p.is_dentist):
            continue
        if not lq.passes(p):
            continue
        m = match(p, filters)
        if lq.view == "match" and not (m.ok or (loose is not filters and match(p, loose).ok)):
            continue
        cards.append(Card(p, m, dups.get(p.id, [])))
    return cards


@bp.get("/")
def index():
    settings = settings_store.load()
    lq = ListQuery.from_args(request.args)
    cards = build_cards(lq, settings)
    pages = max(1, math.ceil(len(cards) / PAGE_SIZE))
    page = min(lq.page, pages)
    shown = cards[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return render_template(
        "postings.html",
        lq=lq,
        views=VIEWS,
        periods=PERIODS,
        cards=shown,
        total=len(cards),
        page=page,
        pages=pages,
        any_postings=bool(cards) or db.count_postings() > 0,
        sources=[(k, source_labels().get(k, k)) for k in source_keys()],
        sidos=[(s, s) for s in SIDO_ORDER],
    )


# ──────────────────────────── 상세 ────────────────────────────

EVIDENCE_ROWS = [
    ("post_kind", "글 종류"),
    ("is_dentist", "치과의사 공고인가"),
    ("inst_types", "기관 종류"),
    ("positions", "직위"),
    ("specialties", "분과"),
    ("specialty_hints", "참고 분야 (우대·가능)"),
    ("specialty_ignored", "무시한 표현"),
    ("work_types", "근무 형태"),
    ("regions", "지역"),
]
DEFAULT_EVIDENCE = "기본값"  # 분류기가 단서를 못 찾았을 때 남기는 말


def _evidence_value(p: Posting, key: str) -> str:
    if key == "post_kind":
        return POST_KINDS.get(p.post_kind, p.post_kind)
    if key == "is_dentist":
        return "예" if p.is_dentist else "아니오"
    if key == "inst_types":
        name = INST_TYPES.get(p.inst_type, p.inst_type)
        return f"{name} · {p.institution}" if p.institution else name
    if key == "positions":
        return labels(p.positions, POSITIONS) or "—"
    if key == "specialties":
        return labels(p.specialties, SPECIALTIES) or "—"
    if key == "specialty_hints":
        return labels(p.specialty_hints, SPECIALTIES) or "없음"
    if key == "specialty_ignored":
        return "이미 근무 중이거나 제외한 분과 (분류에 쓰지 않음)"
    if key == "work_types":
        return labels(p.work_types, WORK_TYPES) or "—"
    if key == "regions":
        return ", ".join(p.region_labels) or "알 수 없음"
    return ""


def evidence_rows(p: Posting) -> list[dict]:
    rows = []
    for key, title in EVIDENCE_ROWS:
        ev = [e for e in p.evidence.get(key) or [] if e != DEFAULT_EVIDENCE]
        if key == "specialty_ignored" and not ev:
            continue
        rows.append({
            "key": key,
            "title": title,
            "value": _evidence_value(p, key),
            "evidence": ev,
            "guessed": key in p.uncertain,
        })
    return rows


def _related(p: Posting) -> tuple[Posting | None, list[Posting]]:
    """(원본 공고, 다른 출처의 같은 공고들)"""
    original = db.get_posting(p.dup_of) if p.dup_of else None
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM postings WHERE dup_of=? ORDER BY id", (p.id,)).fetchall()
    return original, [Posting.from_row(r) for r in rows]


@bp.get("/posting/<int:pid>")
def detail(pid: int):
    p = db.get_posting(pid)
    if p is None:
        abort(404)
    settings = settings_store.load()
    original, dups = _related(p)
    return render_template(
        "posting_detail.html",
        p=p,
        m=match(p, settings["filters"]),
        rows=evidence_rows(p),
        original=original,
        dups=dups,
        back=safe_next(request.args.get("back"), url_for("postings.index")),
    )


# ──────────────────────────── 관심·숨기기 ────────────────────────────


def _toggle(pid: int, flag: str):
    p = db.get_posting(pid)
    if p is None:
        abort(404)
    value = not getattr(p, flag)
    db.set_flag(pid, flag, value)
    if wants_json():
        return jsonify(ok=True, value=value)
    return redirect(safe_next(request.form.get("next"), url_for("postings.detail", pid=pid)))


@bp.post("/posting/<int:pid>/star")
def star(pid: int):
    return _toggle(pid, "starred")


@bp.post("/posting/<int:pid>/hide")
def hide(pid: int):
    return _toggle(pid, "hidden")
