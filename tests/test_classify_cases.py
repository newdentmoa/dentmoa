"""실제형 공고 예시 모음(tests/fixtures/classify_cases.json)으로 분류 규칙 회귀 테스트.

예시 추가 방법: 같은 모양으로 항목을 넣는다. 게시일은 2026-10-01 로 가정한다.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from dentmoa.classify import classify

CASES = json.loads((Path(__file__).parent / "fixtures" / "classify_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["title"][:30] for c in CASES])
def test_case(case):
    kind = case["source_kind"]
    c = classify(case["title"], case["body"], posted=date(2026, 10, 1), source_kind=kind, dentist_only=kind == "local_board")
    got = {
        "post_kind": c.post_kind,
        "inst_type": c.inst_type,
        "sido": c.regions[0].sido if c.regions else "",
        "sigungu": (c.regions[0].sigungu or "") if c.regions else "",
        "deadline": c.deadline.isoformat() if c.deadline else "",
    }
    exp = {
        "post_kind": case["expected_post_kind"],
        "inst_type": case["expected_inst_type"],
        "sido": case["expected_sido"],
        "sigungu": case["expected_sigungu"],
        "deadline": case["expected_deadline"],
    }
    if case["expected_post_kind"] == "hiring":
        got.update(positions=sorted(c.positions), specialties=sorted(c.specialties), work_types=sorted(c.work_types))
        exp.update(
            positions=sorted(case["expected_positions"]),
            specialties=sorted(case["expected_specialties"]),
            work_types=sorted(case["expected_work_types"]),
        )
    assert got == exp, f"{case['why_tricky']}\n근거: {c.evidence}"
