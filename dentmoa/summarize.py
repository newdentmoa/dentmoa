"""본문 3줄 요약 (AI 없이 규칙으로).

본문에서 근무조건·급여·자격·접수 같은 핵심 정보가 담긴 줄을 골라
원래 순서대로 최대 3줄을 돌려준다.
"""

from __future__ import annotations

import re

from .regions import normalize

LINE_MAX = 70

# (점수, 패턴)
SCORES: list[tuple[int, re.Pattern]] = [
    (5, re.compile(r"급여|연봉|월급|페이|세후|세전|net|NET|네트|보장|인센|러닝|%|만\s*원|억")),
    (5, re.compile(r"근무\s*(?:시간|요일|형태|일|조건)|주\s*\d(?:\.\d)?\s*일?|진료\s*시간|야간|토요일|일요일|휴무|월\s*~\s*금|\d{1,2}\s*:\s*\d{2}")),
    (4, re.compile(r"자격|전문의|우대|경력|신규|가능자|필수|요건|면허|학위|박사")),
    (4, re.compile(r"모집\s*(?:분야|인원|과목|대상)|채용\s*(?:분야|인원|과목|직위|직종)|초빙\s*(?:분야|인원)|분야|전공|직위|직급")),
    (3, re.compile(r"마감|접수|제출|기간|서류|면접|전형")),
    (3, re.compile(r"진료\s*(?:과목|내용|분야)|임플란트|교정|보철|보존|소아|치주|수술|환자\s*수|체어|유니트")),
    (2, re.compile(r"숙소|사택|4대\s*보험|퇴직금|복지|휴가|연차|식사|주차")),
    (2, re.compile(r"근무지|위치|주소|소재|지역|역\s*(?:도보|인근|근처)")),
]

SKIP_RE = re.compile(
    r"^\s*(?:안녕하세요|안녕하십니까|감사합니다|문의|연락|전화|카톡|카카오|이메일|e-?mail|tel|☎|📞)|"
    r"\d{2,3}-\d{3,4}-\d{4}|@[\w.-]+\.\w+|open\.kakao|많은\s*(?:관심|지원)|연락\s*(?:주세요|바랍니다|부탁)|"
    r"^\s*\W*\s*$"
)


DEADLINE_ONLY_RE = re.compile(r"^(?:접수\s*)?마감(?:일)?\s*[:：]?\s*[\d./~\-\s()월일화수목금토월]*$")


def _clean(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip(" -•·*▶▷►■□◆◇○●※>#\t")
    if len(line) > LINE_MAX:
        line = line[: LINE_MAX - 1].rstrip() + "…"
    return line


def summarize(body: str, n: int = 3, title: str = "") -> list[str]:
    text = normalize(body or "")
    raw_lines = re.split(r"[\n\r]+|(?<=[.!?])\s+(?=[가-힣A-Za-z])|\s*[|]\s*", text)
    lines = []
    seen = set()
    title_norm = re.sub(r"\s+", "", title or "")
    for ln in raw_lines:
        c = _clean(ln)
        if len(c) < 4 or SKIP_RE.search(c):
            continue
        key = re.sub(r"\s+", "", c)
        if key in seen or (title_norm and key == title_norm):
            continue
        seen.add(key)
        lines.append(c)
    if not lines:
        return []

    scored = []
    for i, ln in enumerate(lines):
        s = sum(w for w, rx in SCORES if rx.search(ln))
        if re.search(r"[:：]", ln):
            s += 1  # '항목: 내용' 형식은 정보가 많음
        if len(ln) < 8:
            s -= 1
        if DEADLINE_ONLY_RE.match(ln):
            s -= 6  # 마감일은 알림에 따로 표시되므로 요약에서 뺀다
        scored.append((s, i, ln))

    picked = sorted(scored, key=lambda x: (-x[0], x[1]))[:n]
    picked = [p for p in picked if p[0] > 0] or scored[:n]
    picked.sort(key=lambda x: x[1])
    return [p[2] for p in picked]
