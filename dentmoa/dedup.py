"""같은 공고 찾기 (여러 사이트에 동시에 올라온 글, 같은 게시판의 재등록 글)."""

from __future__ import annotations

import re
from datetime import timedelta
from difflib import SequenceMatcher

from .models import Posting

WINDOW_DAYS = 30


def _norm_title(s: str) -> str:
    s = re.sub(r"\[[^\]]{0,20}\]|\([^)]{0,20}\)|【[^】]{0,20}】", " ", s or "")  # [서울 강남] 같은 머리말 제거
    s = re.sub(r"(재공고|재등록|재업|끌올|급구|마감임박|up|UP)", " ", s)
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s).lower()


def _norm_body(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")[:600]


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def is_duplicate(a: Posting, b: Posting) -> bool:
    if a.id == b.id:
        return False
    if abs((a.effective_date - b.effective_date).days) > WINDOW_DAYS:
        return False
    ta, tb = _norm_title(a.title), _norm_title(b.title)
    if len(ta) >= 6 and ta == tb:
        return True
    tr = _ratio(ta, tb)
    if tr >= 0.92:
        return True
    same_inst = a.institution and a.institution == b.institution
    if same_inst and tr >= 0.75:
        return True
    ba, bb = _norm_body(a.body), _norm_body(b.body)
    if len(ba) >= 120 and len(bb) >= 120 and _ratio(ba, bb) >= 0.9:
        return True
    return False


def find_original(p: Posting, candidates: list[Posting]) -> Posting | None:
    """p 보다 먼저 저장된 같은 공고(원본)를 찾는다."""
    best = None
    for other in candidates:
        if other.id >= p.id or other.dup_of is not None:
            continue
        if abs((other.effective_date - p.effective_date)) > timedelta(days=WINDOW_DAYS):
            continue
        if is_duplicate(p, other):
            if best is None or other.id < best.id:
                best = other
    return best
