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
STRONG_KEYWORD_RE = re.compile(r"마감|접수\s*(?:기간|기한|[:：])|지원\s*[:：]|모집\s*기간|지원\s*(?:기간|기한)|원서\s*접수|서류\s*접수|제출\s*기한")
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


# 지원 마감이 아닌 날짜가 있는 줄 (근무 기간, 대진 일정, 면접, 합격자 서류 등)
NOT_DEADLINE_LINE_RE = re.compile(
    r"대진|근무\s*(?:기간|일정|일자|예정|시작|개시)|계약\s*(?:기간|일)|임용\s*(?:예정|기간|일)|휴가\s*기간|"
    r"면접|합격자|합격\s*발표|개원\s*예정|부터\s*\d{1,2}\s*일\s*까지"
)
PERIOD_LABEL_RE = re.compile(r"(?:^|[\s\-•·])기간\s*[:：]")


def find_deadline(text: str, posted: date) -> tuple[str, date | None]:
    """본문에서 마감일을 찾는다. posted = 게시일(연도 추정 기준)."""
    text = normalize(text)
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]

    best: date | None = None
    best_score = 0
    carry = False  # '접수기간' 다음 줄에 날짜가 오는 경우
    for ln in lines:
        dates = _dates_in(ln, posted)
        strong = STRONG_KEYWORD_RE.search(ln)
        if not dates:
            carry = bool(strong) and len(ln) <= 20
            continue
        prev_carry, carry = carry, False
        if NOT_DEADLINE_LINE_RE.search(ln) and not (strong and strong.start() < NOT_DEADLINE_LINE_RE.search(ln).start()):
            continue
        score = 0
        if strong or prev_carry:
            score = 3
            if strong:
                # 키워드 바로 뒤의 날짜 (기간이면 끝 날짜)
                after = [d for d in dates if d[0] >= strong.start()]
                if after:
                    i = dates.index(after[0])
                    nxt = dates[i + 1] if i + 1 < len(dates) else None
                    if nxt and RANGE_SEP_RE.fullmatch(re.sub(r"\([^)]*\)|\d{1,2}:\d{2}|[.,]", "", ln[after[0][1]:nxt[0]]) or " "):
                        dates = dates[: i + 2]
                    else:
                        dates = dates[: i + 1]
        elif PERIOD_LABEL_RE.search(ln) and len(dates) >= 2:
            score = 2
        elif re.search(r"까지", ln):
            k = ln.index("까지")
            near = [d for d in dates if 0 <= k - d[1] <= 6]  # '10/20(화)까지'
            if not near:
                continue
            dates = [near[-1]]
            score = 2
        prev_end = dates[-2][1] if len(dates) >= 2 else 0
        gap = ln[prev_end:dates[-1][0]]
        is_range = len(dates) >= 2 and re.fullmatch(r"\s*(?:\([^)]*\))?\s*[~∼〜\-]\s*", gap)
        if not is_range and re.search(r"(?:~|∼|〜)\s*$", gap):
            score = max(score, 2)  # '~10.31' 처럼 끝 날짜만 있는 기간 (키워드 없는 '10/19~10/24'는 근무 기간일 수 있음)
        if score == 0:
            continue
        # 기간이면 마지막 날짜가 마감일
        cand = dates[-1][2]
        if len(dates) >= 2 and RANGE_SEP_RE.search(ln[dates[-2][1]:dates[-1][0]] or " "):
            cand = dates[-1][2]

        if cand < posted - timedelta(days=1):
            continue  # 게시일보다 이전 날짜는 마감일이 아님
        if score > best_score or (score == best_score and best and cand > best):
            best, best_score = cand, score

    if best:
        return "date", best
    if UNTIL_FILLED_RE.search(text):
        return "until_filled", None
    return "none", None
