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
        if best is None or len(m.group(0)) > len(best):
            best = m.group(0)
    return lookup[best] if best else None
