"""공고가 사용자 조건에 맞는지 판단."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Posting
from .regions import selection_matches
from .taxonomy import INST_TYPES, POSITIONS, POST_KINDS, SPECIALTIES, WORK_TYPES


@dataclass
class MatchResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)  # 안 맞는 이유 또는 특이사항
    forced: bool = False  # '무조건 포함' 키워드 때문에 포함됨
    region_uncertain: bool = False


def _contains(text: str, keywords: list[str]) -> str | None:
    low = text.lower()
    for k in keywords:
        if k and k.lower() in low:
            return k
    return None


def match(p: Posting, filters: dict) -> MatchResult:
    text = f"{p.title}\n{p.institution}\n{p.body}"
    reasons: list[str] = []

    if p.post_kind not in filters.get("post_kinds", ["hiring"]):
        return MatchResult(False, [f"글 종류: {POST_KINDS.get(p.post_kind, p.post_kind)}"])

    forced = _contains(text, filters.get("include_keywords", []))
    if forced:
        return MatchResult(True, [f"무조건 포함 키워드: {forced}"], forced=True)

    excluded = _contains(text, filters.get("exclude_keywords", []))
    if excluded:
        return MatchResult(False, [f"제외 키워드: {excluded}"])

    if filters.get("dentist_only", True) and not p.is_dentist:
        reasons.append("치과의사 공고 아님")

    if p.inst_type not in filters.get("inst_types", []):
        reasons.append(f"기관: {INST_TYPES.get(p.inst_type, p.inst_type)}")

    pos = set(filters.get("positions", []))
    if p.positions and not pos.intersection(p.positions):
        reasons.append("직위: " + ", ".join(POSITIONS.get(k, k) for k in p.positions))

    spec = set(filters.get("specialties", []))
    cands = set(p.specialties)
    if filters.get("include_hints"):
        cands |= set(p.specialty_hints)
    if cands and not spec.intersection(cands):
        reasons.append("분과: " + ", ".join(SPECIALTIES.get(k, k) for k in p.specialties))

    work = set(filters.get("work_types", []))
    if p.work_types and not work.intersection(p.work_types):
        reasons.append("근무형태: " + ", ".join(WORK_TYPES.get(k, k) for k in p.work_types))

    region = selection_matches(filters.get("regions", ["전국"]), p.regions)
    region_uncertain = False
    if region == "no":
        reasons.append("지역: " + (", ".join(p.region_labels) or "미상"))
    elif region == "unknown":
        if filters.get("include_uncertain_region", True):
            region_uncertain = True
        else:
            reasons.append("지역 불명확")

    return MatchResult(not reasons, reasons, region_uncertain=region_uncertain)
