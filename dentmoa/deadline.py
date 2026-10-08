"""마감일 찾기.

결과 종류
- ('date', 날짜)          : 마감일을 찾음
- ('until_filled', None)  : 채용 시 마감 / 상시 채용
- ('none', None)          : 정보 없음
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .regions import normalize

UNTIL_FILLED_RE = re.compile(
    r"채용\s*시\s*(?:까지|마감)|충원\s*시\s*(?:까지|마감)|구인\s*시\s*(?:까지|마감)|"
    r"상시\s*(?:채용|모집|접수)|수시\s*(?:채용|모집)|채용\s*완료\s*시|조기\s*마감\s*가능|"
    r"마감\s*[:：]?\s*(?:채용시|상시|없음|미정)|인원\s*충족\s*시|적임자\s*(?:채용|선발)\s*시"
)

# 날짜 표현 (연도는 있을 수도 없을 수도)
_Y = r"(?:(?P<y>20\d{2}|\d{2})\s*(?:년|[.\-/]))?\s*"
_MD_NUM = r"(?P<m>1[0-2]|0?[1-9])\s*[.\-/]\s*(?P<d>3[01]|[12]\d|0?[1-9])(?!\d)"
_MD_KOR = r"(?P<m2>1[0-2]|0?[1-9])\s*월\s*(?P<d2>3[01]|[12]\d|0?[1-9])\s*일"
DATE_RE = re.compile(r"(?<!\d)" + _Y + r"(?:" + _MD_NUM + r"|" + _MD_KOR + r")")

KEYWORD_RE = re.compile(r"마감|접수\s*(?:기간|기한|마감)|모집\s*기간|지원\s*(?:기간|기한|마감)|서류\s*(?:접수|제출)|까지|~|∼|〜|－|-\s*\d")
STRONG_KEYWORD_RE = re.compile(r"마감|접수\s*(?:기간|기한)|모집\s*기간|지원\s*(?:기간|기한)|원서\s*접수|서류\s*접수|제출\s*기한")
RANGE_SEP_RE = re.compile(r"\s*(?:~|∼|〜|부터|－|―|-(?=\s*\d))\s*")


def _mk(y: int | None, m: int, d: int, ref: date) -> date | None:
    if y is not None and y < 100:
        y += 2000
    if y is None:
        y = ref.year
        try:
            cand = date(y, m, d)
        except ValueError:
            return None
        # 연도가 없으면 기준일에서 가장 가까운 미래(또는 직전 2달 이내)로
        if cand < ref - timedelta(days=60):
            try:
                cand = date(y + 1, m, d)
            except ValueError:
                return None
        return cand
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _dates_in(line: str, ref: date) -> list[tuple[int, int, date]]:
    out = []
    last_year = None
    for m in DATE_RE.finditer(line):
        y = m.group("y")
        mo = m.group("m") or m.group("m2")
        d = m.group("d") or m.group("d2")
        # 숫자 형식(10.20)은 소수/금액/전화번호와 헷갈리지 않도록 뒤를 확인
        if m.group("m") and not m.group("y"):
            tail = line[m.end():m.end() + 2]
            head = line[max(0, m.start() - 1):m.start()]
            if re.match(r"\s*(?:%|만|억|원|천|명|시간|년|개월|kg|cm)", tail) or head in ("$", "₩"):
                continue
            if re.match(r"\d", tail):
                continue
        yy = int(y) if y else None
        if yy is None and last_year is not None:
            yy = last_year
        dt = _mk(yy, int(mo), int(d), ref)
        if dt:
            out.append((m.start(), m.end(), dt))
            if yy is not None:
                last_year = yy if yy >= 100 else yy + 2000
    return out


def find_deadline(text: str, posted: date) -> tuple[str, date | None]:
    """본문에서 마감일을 찾는다. posted = 게시일(연도 추정 기준)."""
    text = normalize(text)
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]

    best: date | None = None
    best_score = 0
    for ln in lines:
        dates = _dates_in(ln, posted)
        if not dates:
            continue
        score = 0
        if STRONG_KEYWORD_RE.search(ln):
            score = 3
        elif re.search(r"까지", ln):
            score = 2
        elif KEYWORD_RE.search(ln) and len(dates) >= 2:
            score = 1
        if re.search(r"(?:~|∼|〜)\s*$", ln[:dates[-1][0]]):
            score = max(score, 2)  # '~10.31' 처럼 끝 날짜만 있는 기간
        if score == 0:
            continue
        # 기간이면 마지막 날짜가 마감일
        cand = dates[-1][2]
        if len(dates) >= 2 and RANGE_SEP_RE.search(ln[dates[-2][1]:dates[-1][0]] or " "):
            cand = dates[-1][2]
        elif re.search(r"까지", ln):
            # '10월 20일까지' 처럼 '까지' 바로 앞 날짜
            k = ln.index("까지")
            before = [dt for s, e, dt in dates if e <= k + 1]
            if before:
                cand = before[-1]
        if cand < posted - timedelta(days=1):
            continue  # 게시일보다 이전 날짜는 마감일이 아님
        if score > best_score or (score == best_score and best and cand > best):
            best, best_score = cand, score

    if best:
        return "date", best
    if UNTIL_FILLED_RE.search(text):
        return "until_filled", None
    return "none", None
