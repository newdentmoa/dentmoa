"""글에서 지역(시·도 / 시군구)을 찾아내는 규칙."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .regions_data import (
    ALIASES,
    REGION_GROUPS,
    RISKY_SHORT,
    SHORT_BLOCK_SUFFIX,
    SIDO_NAMES,
    SIDO_ORDER,
    SIGUNGU,
)

HANGUL = "가-힣"
NATIONWIDE = "전국"


@dataclass(frozen=True, order=True)
class Region:
    sido: str
    sigungu: str | None = None

    @property
    def label(self) -> str:
        return f"{self.sido} {self.sigungu}" if self.sigungu else self.sido

    def to_json(self) -> list:
        return [self.sido, self.sigungu]

    @classmethod
    def from_json(cls, value) -> "Region":
        if isinstance(value, str):
            return parse_selection(value)
        sido, sgg = (list(value) + [None])[:2]
        return cls(sido, sgg or None)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[  -​　]", " ", text)


# ──────────────────────────── 인덱스 구성 ────────────────────────────


@dataclass(frozen=True)
class _Entry:
    targets: tuple[tuple[str, str | None], ...]
    risky: bool = False  # 시·도 이름이 같은 글에 있어야 인정
    ctx: str | None = None  # 이 시·도 이름이 같은 글에 있어야 인정
    block: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, list[_Entry]], re.Pattern, re.Pattern, re.Pattern]:
    names: dict[str, list[_Entry]] = {}

    def add(name: str, entry: _Entry) -> None:
        names.setdefault(name, []).append(entry)

    sido_short_names = {n for ns in SIDO_NAMES.values() for n in ns}
    for sido, sggs in SIGUNGU.items():
        for sgg in sggs:
            if sido == "경기" and sgg == "광주시":
                continue  # 광주는 따로 처리
            add(sgg, _Entry(((sido, sgg),)))
            short = sgg[:-1]
            if len(short) >= 2 and short not in sido_short_names:
                add(
                    short,
                    _Entry(
                        ((sido, sgg),),
                        risky=short in RISKY_SHORT,
                        block=tuple(SHORT_BLOCK_SUFFIX.get(short, ())),
                    ),
                )
    for alias in ALIASES:
        add(
            alias["name"],
            _Entry(tuple(alias["to"]), ctx=alias.get("ctx"), block=tuple(SHORT_BLOCK_SUFFIX.get(alias["name"], ()))),
        )
    # 공항 등 다른 지역 이름이 들어간 고유명사
    add("김포공항", _Entry((("서울", "강서구"),)))
    add("인천공항", _Entry((("인천", "영종구"),)))
    add("인천국제공항", _Entry((("인천", "영종구"),)))

    alts = sorted(names, key=len, reverse=True)
    name_re = re.compile(
        r"(?<![" + HANGUL + r"A-Za-z])(" + "|".join(re.escape(n) for n in alts) + ")"
    )

    sido_alts = sorted({n for ns in SIDO_NAMES.values() for n in ns}, key=len, reverse=True)
    # 시·도 이름 뒤에 '대'가 오면 대학 이름(서울대, 경북대 …)이라 지역으로 보지 않는다.
    sido_re = re.compile(
        r"(?<![" + HANGUL + r"])(" + "|".join(re.escape(n) for n in sido_alts) + r")(?!대(?!로))"
    )
    # '서울강남구'처럼 붙어 있는 경우 사이에 공백을 넣기 위한 패턴
    glue_re = re.compile(
        "(" + "|".join(re.escape(n) for n in sido_alts) + ")(?=(" + "|".join(re.escape(n) for n in alts) + "))"
    )
    return names, name_re, sido_re, glue_re


@lru_cache(maxsize=1)
def _sido_lookup() -> dict[str, str]:
    out = {}
    for sido, ns in SIDO_NAMES.items():
        for n in ns:
            out[n] = sido
    return out


# ──────────────────────────── 파싱 ────────────────────────────


def _find_sidos(text: str) -> list[tuple[int, int, str]]:
    _, _, sido_re, _ = _index()
    lookup = _sido_lookup()
    found = []
    for m in sido_re.finditer(text):
        name = m.group(1)
        sido = lookup[name]
        end = m.end()
        if name == "세종" and re.match(r"(대왕|문화|로|대\s*학|대학교|병원)", text[end:]):
            continue
        if name == "경기" and re.match(r"\s*(가|는|를|의\s*(?:흐름|침체|불황)|침체|불황|회복|악화|둔화)", text[end:]):
            continue
        found.append((m.start(), end, sido))
    return found


def _gyeonggi_gwangju(text: str, start: int) -> bool:
    """'광주'가 경기 광주시를 뜻하는지 (바로 앞에 '경기'가 있는지)."""
    before = text[max(0, start - 8):start]
    return bool(re.search(r"경기(도)?\s*[/·,]?\s*$", before))


def parse_regions(text: str, max_results: int = 6) -> list[Region]:
    """글에서 지역 목록을 위치 순서대로 찾는다. 확실하지 않은 이름은 버린다."""
    if not text:
        return []
    names, name_re, _, glue_re = _index()
    text = normalize(text)
    text = glue_re.sub(r"\1 ", text)

    sidos = _find_sidos(text)
    mentioned = {s for _, _, s in sidos}

    hits: list[tuple[int, Region]] = []
    covered: list[tuple[int, int]] = []

    for m in name_re.finditer(text):
        name = m.group(1)
        after = text[m.end():m.end() + 6]
        chosen: list[tuple[str, str | None]] = []
        for entry in names[name]:
            if any(after.startswith(b) for b in entry.block):
                continue
            if entry.ctx and entry.ctx not in mentioned:
                continue
            if entry.risky and not any(t[0] in mentioned for t in entry.targets):
                continue
            chosen.extend(entry.targets)
        if not chosen:
            continue
        chosen = list(dict.fromkeys(chosen))
        sido_set = {s for s, _ in chosen}
        if len(sido_set) > 1 and not _is_alias_multi(name):
            # 같은 이름이 여러 시·도에 있음(중구, 강서구, 고성군 …) → 가까운 시·도 이름으로 결정
            near = _nearest_sido(sidos, m.start())
            if near in sido_set:
                chosen = [c for c in chosen if c[0] == near]
            else:
                in_text = [c for c in chosen if c[0] in mentioned]
                if len({s for s, _ in in_text}) == 1:
                    chosen = in_text
                else:
                    continue  # 결정 불가 → 버림
        for sido, sgg in chosen:
            hits.append((m.start(), Region(sido, sgg)))
        covered.append((m.start(), m.end()))

    # 광주 처리
    for m in re.finditer(r"(?<![" + HANGUL + r"])광주(광역시|시)?(?!대(?:학|\s*병원))", text):
        if any(s <= m.start() < e for s, e in covered):
            continue
        if _gyeonggi_gwangju(text, m.start()):
            hits.append((m.start(), Region("경기", "광주시")))
            covered.append((m.start(), m.end()))

    # 시·도만 언급된 경우
    for start, end, sido in sidos:
        if any(s <= start < e for s, e in covered):
            continue
        if sido == "광주" and _gyeonggi_gwangju(text, start):
            continue
        if sido == "경기" and re.match(r"\s*(도\s*)?광주", text[end:]):
            continue
        hits.append((start, Region(sido)))

    # 수도권, 영남권 같은 묶음 표현
    for group, group_sidos in REGION_GROUPS.items():
        for m in re.finditer(re.escape(group), text):
            for sido in group_sidos:
                hits.append((m.start(), Region(sido)))

    hits.sort(key=lambda h: h[0])
    ordered = list(dict.fromkeys(r for _, r in hits))
    # 같은 시·도의 구체적인 시군구가 있으면 시·도만 있는 항목은 뺀다
    specific_sidos = {r.sido for r in ordered if r.sigungu}
    result = [r for r in ordered if r.sigungu or r.sido not in specific_sidos]
    return result[:max_results]


def _is_alias_multi(name: str) -> bool:
    return any(a["name"] == name and len({t[0] for t in a["to"]}) > 1 for a in ALIASES)


def _nearest_sido(sidos: list[tuple[int, int, str]], pos: int, window: int = 25) -> str | None:
    best = None
    for start, end, sido in sidos:
        if end <= pos and pos - end <= window:
            best = sido  # 앞쪽에서 가장 가까운 것 (리스트가 위치순)
    if best:
        return best
    for start, end, sido in sidos:
        if start >= pos and start - pos <= 6:
            return sido
    return None


# ──────────────────────────── 선택(설정) 처리 ────────────────────────────


def parse_selection(value: str) -> Region:
    value = (value or "").strip()
    if value == NATIONWIDE:
        return Region(NATIONWIDE)
    parts = value.split(maxsplit=1)
    return Region(parts[0], parts[1] if len(parts) > 1 else None)


def selection_matches(selection: list[str], regions: list[Region]) -> str:
    """설정의 지역 선택과 공고 지역 비교.

    돌려주는 값: 'yes' (맞음) / 'no' (안 맞음) / 'unknown' (공고 지역이 불명확)
    """
    sel = [parse_selection(s) for s in selection or []]
    if not sel or any(s.sido == NATIONWIDE for s in sel):
        return "yes"
    if not regions:
        return "unknown"
    whole = {s.sido for s in sel if not s.sigungu}
    specific = {(s.sido, s.sigungu) for s in sel if s.sigungu}
    specific_sidos = {s for s, _ in specific}
    verdict = "no"
    for r in regions:
        if r.sido == NATIONWIDE or r.sido in whole:
            return "yes"
        if r.sigungu and (r.sido, r.sigungu) in specific:
            return "yes"
        if not r.sigungu and r.sido in specific_sidos:
            verdict = "unknown"  # 시·도만 알고 구는 모름
    return verdict


def all_options() -> list[dict]:
    """화면의 지역 선택 트리."""
    return [{"sido": s, "sigungu": SIGUNGU[s]} for s in SIDO_ORDER]


def is_valid_selection(value: str) -> bool:
    r = parse_selection(value)
    if r.sido == NATIONWIDE:
        return r.sigungu is None
    if r.sido not in SIGUNGU:
        return False
    return r.sigungu is None or r.sigungu in SIGUNGU[r.sido]
