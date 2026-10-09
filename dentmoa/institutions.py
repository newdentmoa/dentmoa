"""알려진 기관 목록 (이름 → 기관 종류·위치).

data/institutions.json 을 읽는다. 글에 기관 이름이 나오면 지역과 기관 종류를
가장 확실하게 정할 수 있으므로 분류기가 제일 먼저 확인한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .regions import normalize

DATA_FILE = Path(__file__).parent / "data" / "institutions.json"


@dataclass(frozen=True)
class Institution:
    name: str
    kind: str
    sido: str
    sigungu: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    homepage: str = ""
    recruit_url: str = ""
    operator: str = ""
    watch: bool = True  # 채용 게시판을 직접 확인할지
    # 게시판 읽기 방법 (hospitals 수집기)
    title_filter: str = ""  # "dental" = 제목에 치과·치의·구강이 있는 글만 (의료원 전체 채용 게시판 등)
    shared: bool = False  # 여러 기관이 함께 쓰는 게시판 → 글마다 기관 이름·위치를 붙이지 않는다
    fetch: str = ""  # "browser" = 자바스크립트 확인 화면이 있어 브라우저로 연다

    @property
    def inst_type(self) -> str:
        """분류 체계(taxonomy.INST_TYPES)의 기관 종류."""
        return {"dental_school": "dental_univ_hospital"}.get(self.kind, self.kind)


@lru_cache(maxsize=1)
def load() -> list[Institution]:
    if not DATA_FILE.exists():
        return []
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    out = []
    for item in raw:
        out.append(
            Institution(
                name=item["name"],
                kind=item["kind"],
                sido=item["sido"],
                sigungu=item.get("sigungu") or None,
                aliases=tuple(item.get("aliases", [])),
                homepage=item.get("homepage", ""),
                recruit_url=item.get("recruit_url", ""),
                operator=item.get("operator", ""),
                watch=bool(item.get("watch", True)),
                title_filter=item.get("title_filter", ""),
                shared=bool(item.get("shared", False)),
                fetch=item.get("fetch", ""),
            )
        )
    return out


def _key(s: str) -> str:
    return re.sub(r"[\s·()\[\]\-]", "", s)


@lru_cache(maxsize=1)
def _matcher() -> tuple[re.Pattern | None, dict[str, Institution]]:
    lookup: dict[str, Institution] = {}
    for inst in load():
        for n in (inst.name, *inst.aliases):
            k = _key(n)
            if len(k) >= 3 and k not in lookup:
                lookup[k] = inst
    if not lookup:
        return None, {}
    alts = sorted(lookup, key=len, reverse=True)
    return re.compile("|".join(re.escape(a) for a in alts)), lookup


def find(text: str) -> Institution | None:
    """글에서 가장 길게 일치하는 기관 이름을 찾는다."""
    rx, lookup = _matcher()
    if rx is None or not text:
        return None
    squeezed = _key(normalize(text))
    best = None
    for m in rx.finditer(squeezed):
        # '보령아산병원' 속의 '아산병원' 처럼 다른 이름의 일부인 짧은 별칭은 무시
        if len(m.group(0)) <= 5 and m.start() > 0 and re.match(r"[가-힣]", squeezed[m.start() - 1]):
            prev = squeezed[max(0, m.start() - 3):m.start()]
            if not re.search(r"(?:치과|병원|에서|에서는|의|은|는|이|가)$", prev):
                continue
        if best is None or len(m.group(0)) > len(best):
            best = m.group(0)
    return lookup[best] if best else None
