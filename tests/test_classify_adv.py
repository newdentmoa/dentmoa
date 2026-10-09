"""처음 보는 까다로운 공고로 만든 2차 회귀 테스트 (tests/fixtures/classify_cases_adv.json).

expected 에 적힌 항목만 비교한다. 게시일은 2026-10-01 로 가정한다.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from dentmoa.classify import classify

CASES = json.loads((Path(__file__).parent / "fixtures" / "classify_cases_adv.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["title"][:30] for c in CASES])
def test_case(case):
    kind = case.get("source_kind", "local_board")
    c = classify(case["title"], case.get("body", ""), posted=date(2026, 10, 1), source_kind=kind,
                 dentist_only=kind == "local_board", institution_hint=case.get("institution_hint", ""))
    got = {
        "post_kind": c.post_kind, "inst_type": c.inst_type, "positions": sorted(c.positions),
        "specialties": sorted(c.specialties), "specialty_hints": sorted(c.specialty_hints),
        "work_types": sorted(c.work_types), "regions": [r.label for r in c.regions],
        "deadline": c.deadline.isoformat() if c.deadline else "", "is_dentist": c.is_dentist,
    }
    for key, val in case["expected"].items():
        want = sorted(val) if isinstance(val, list) and key != "regions" else val
        assert got[key] == want, f"{key}: {got[key]} != {want}\n근거: {c.evidence}"
