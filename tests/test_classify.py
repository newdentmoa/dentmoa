"""분류 규칙 테스트 — 실제 공고에서 흔한 표현들."""

from datetime import date

import pytest

from dentmoa.classify import classify

P = date(2026, 10, 1)


def C(title, body="", kind="local_board", **kw):
    return classify(title, body, posted=P, source_kind=kind, dentist_only=(kind == "local_board"), **kw)


# ─────────── 글 종류 ───────────


@pytest.mark.parametrize(
    "title, body, expected",
    [
        ("[서울 강남] GP 원장님 모십니다", "", "hiring"),
        ("[구직] 소아치과 전문의 대진 가능합니다", "", "seeking"),
        ("보존과 전문의 자리 구합니다", "경력 5년", "seeking"),
        ("페이 자리 알아봅니다 (서울/경기)", "", "seeking"),
        ("주 3일 근무 희망합니다", "", "seeking"),
        ("[구인] 자리 구합니다 아님 — 원장님 모집", "", "hiring"),
        ("치과 양도합니다 (부산 서면)", "권리금 협의", "other"),
        ("유니트체어 중고 판매", "", "other"),
        ("교정 세미나 안내", "", "other"),
        ("부산 해운대 봉직의 구합니다", "", "hiring"),
        ("함께하실 원장님을 찾습니다", "", "hiring"),
    ],
)
def test_post_kind_local(title, body, expected):
    assert C(title, body).post_kind == expected


@pytest.mark.parametrize(
    "title, expected",
    [
        ("2026년 하반기 임상강사(전임의) 채용 서류전형 합격자 발표", "other"),
        ("2026년 진료교수 채용 면접 일정 안내", "other"),
        ("2027학년도 전임교원 초빙 공고", "hiring"),
        ("치과의사(임기제공무원) 채용 공고", "hiring"),
        ("병원 개원 기념 행사 안내", "other"),
    ],
)
def test_post_kind_board(title, expected):
    assert C(title, kind="hospital_board").post_kind == expected


# ─────────── 직위 ───────────


@pytest.mark.parametrize(
    "title, expected",
    [
        ("치의학전문대학원 전임교원 초빙", ["faculty"]),
        ("소아치과학교실 조교수 신규 임용", ["faculty"]),
        ("진료교수(소아치과) 채용", ["clinical_professor"]),
        ("임상조교수 모집", ["clinical_professor"]),
        ("기금교수 초빙", ["clinical_professor"]),
        ("구강악안면외과 임상강사(전임의) 모집", ["fellow"]),
        ("펠로우 선생님 모집", ["fellow"]),
        ("치과 촉탁의 채용", ["contract"]),
        ("보건소 치과의사(임기제공무원) 채용", ["contract"]),
        ("치과 전공의(레지던트) 모집", ["resident"]),
        ("외래교수 위촉 안내", ["other"]),
    ],
)
def test_positions_board(title, expected):
    assert C(title, kind="hospital_board").positions == expected


def test_positions_local_default():
    assert C("[수원] 페이닥터 구합니다").positions == ["staff_dentist"]
    assert C("[수원] 치과에서 함께할 분").positions == ["staff_dentist"]
    assert "positions" in C("[수원] 치과에서 함께할 분").uncertain


def test_professor_mentions_not_faculty():
    c = C("대학병원 교수 출신 대표원장님과 함께할 GP 원장님 모십니다")
    assert "faculty" not in c.positions
    assert c.positions == ["staff_dentist"]


# ─────────── 분과 ───────────


@pytest.mark.parametrize(
    "title, body, targets, hints",
    [
        ("[서울 강남] 교정과 전문의 원장님 모십니다", "", ["ortho"], []),
        ("소아치과 전문의 모십니다", "", ["pedo"], []),
        ("소치 원장님 구합니다", "", ["pedo"], []),
        ("어린이치과 원장님 모십니다", "", ["pedo"], []),
        ("OO키즈치과 GP 원장님 구합니다", "", ["gp", "pedo"], []),
        ("교정과 전문의 상주 치과 / GP 원장님 구합니다", "", ["gp"], []),
        ("교정과 전문의 계시고 일반 진료 원장님 모십니다", "", ["gp"], []),
        ("교정, 소아 전문의 상주 / GP 모십니다", "", ["gp"], []),
        ("소아치과 전문의 2인 상주, 일반 진료 원장님 구합니다", "", ["gp"], []),
        ("교정 전문의 대표원장님과 함께할 원장님 모십니다", "", ["gp"], []),
        ("보존, 보철 전문의 모집합니다", "", ["prostho", "endo"], []),
        ("보존과, 보철과 전문의 모집", "", ["prostho", "endo"], []),
        ("교정과 보철 위주 진료하실 원장님 구합니다", "", ["gp"], ["prostho", "ortho"]),
        ("자연치 보존 위주 진료 원장님 모십니다", "", ["gp"], ["endo"]),
        ("소아 진료 가능하신 원장님 구합니다", "소아 환자 많아요", ["gp"], ["pedo"]),
        ("GP 원장님 구합니다", "교정 X, 임플란트 가능자 우대", ["gp"], []),
        ("원장님 모십니다", "교정은 하지 않습니다", ["gp"], []),
        ("구강악안면외과 전문의 모집", "", ["oms"], []),
        ("구강외과 원장님 구합니다", "사랑니 발치 많음", ["oms"], []),
        ("치주과 전문의 모십니다", "", ["perio"], []),
        ("통합치의학과 전문의 우대", "", ["integrated"], []),
        ("AGD 원장님 구합니다", "", ["integrated"], []),
        ("구강내과 전문의 모집", "턱관절 환자 위주", ["oral_medicine"], []),
        ("전문의 무관, 신규 가능", "", ["gp"], []),
        ("[부산] 보존과 원장님 모셔요", "", ["endo"], []),
        ("임플란트 잘 하시는 원장님", "", ["gp"], []),
    ],
)
def test_specialties_local(title, body, targets, hints):
    c = C(title, body)
    assert sorted(c.specialties) == sorted(targets), c.evidence
    assert sorted(c.specialty_hints) == sorted(hints), c.evidence


@pytest.mark.parametrize(
    "title, body, targets",
    [
        ("2027학년도 치의학전문대학원 전임교원 초빙 공고 (소아치과학, 구강내과학 분야)", "", ["pedo", "oral_medicine"]),
        ("치과대학 교원 초빙", "초빙분야: 치과보존학, 구강병리학", ["endo", "pathology"]),
        ("치과 전임의 모집", "모집과목: 구강악안면외과, 치과보철과, 치과교정과", ["oms", "prostho", "ortho"]),
        ("영상치의학과 임상강사 모집", "", ["radiology"]),
        ("예방치과 진료교수 채용", "", ["preventive"]),
        ("치과대학 기초치의학(구강해부학) 교원 초빙", "", ["basic_science"]),
        ("치과의사(임기제공무원) 채용", "", ["gp"]),
    ],
)
def test_specialties_board(title, body, targets):
    assert sorted(C(title, body, kind="hospital_board").specialties) == sorted(targets)


# ─────────── 근무 형태 ───────────


@pytest.mark.parametrize(
    "title, body, expected",
    [
        ("주 4.5일 원장님", "", ["fulltime"]),
        ("[인천 송도] 주 2일 파트 원장님 구해요", "", ["parttime"]),
        ("토요일 파트 원장님", "", ["parttime"]),
        ("휴가 대진 원장님 구합니다", "10/20~10/25", ["locum"]),
        ("출산 대진 3개월", "", ["locum"]),
        ("풀타임/파트 모두 가능", "", ["fulltime", "parttime"]),
        ("원장님 모십니다", "", ["fulltime"]),
        ("대진대학교 출신 원장님 모십니다", "", ["fulltime"]),
    ],
)
def test_work_types(title, body, expected):
    assert C(title, body).work_types == expected


# ─────────── 기관·지역 (기관 목록 활용) ───────────


@pytest.mark.parametrize(
    "title, hint, kind, inst_type, regions",
    [
        ("경북대학교치과병원 진료교수(소아치과) 채용 공고", "", "hospital_board", "dental_univ_hospital", ["대구 중구"]),
        ("2026년 제5차 직원 채용 공고", "서울대학교치과병원", "aggregator", "dental_univ_hospital", ["서울 종로구"]),
        ("삼성서울병원 치과 구강악안면외과 전임의 모집", "", "local_board", "univ_hospital_dept", ["서울 강남구"]),
        ("[강릉원주대치과병원] 전임의 모집", "", "local_board", "dental_univ_hospital", ["강원 강릉시"]),
        ("부산대학교치과병원 소아치과 전임의", "", "local_board", "dental_univ_hospital", ["경남 양산시"]),
        ("서울특별시장애인치과병원 치과의사 채용", "", "hospital_board", "disabled_dental", ["서울 성동구"]),
        ("OO구 보건소 치과의사 채용", "", "hospital_board", "public_hospital", []),
        ("[분당] OO치과병원 페이닥터", "", "local_board", "dental_hospital", ["경기 성남시"]),
        ("[대구] OO병원 치과 봉직의", "", "local_board", "general_hospital", ["대구"]),
        ("[서울 강남] 미소치과의원 원장님 구인", "", "local_board", "dental_clinic", ["서울 강남구"]),
    ],
)
def test_institution_and_region(title, hint, kind, inst_type, regions):
    c = C(title, kind=kind, institution_hint=hint)
    assert c.inst_type == inst_type
    assert [r.label for r in c.regions] == regions


def test_region_hint_and_body():
    c = C("원장님 모십니다", "근무지 : 경기도 성남시 분당구 정자동\n주 5일", region_hint="")
    assert [r.label for r in c.regions] == ["경기 성남시"]
    c = C("[경기] 원장님 모십니다", "위치: 수원 영통구 광교")
    assert [r.label for r in c.regions] == ["경기 수원시"]
    c = C("원장님 모십니다", "", region_hint="서울 송파구")
    assert [r.label for r in c.regions] == ["서울 송파구"]


# ─────────── 치과의사 공고 여부 (병원 게시판) ───────────


@pytest.mark.parametrize(
    "title, body, hint, expected",
    [
        ("2026년 치과위생사 채용 공고", "치과위생사 2명", "서울대학교치과병원", False),
        ("2026년 하반기 직원 채용 공고", "임상강사 1명, 치과위생사 3명", "서울대학교치과병원", True),
        ("내과 전문의 채용", "", "서울아산병원", False),
        ("임상강사(전임의) 채용", "모집과: 내과, 외과, 치과(구강악안면외과)", "서울아산병원", True),
        ("간호사 채용", "", "다라병원", False),
    ],
)
def test_is_dentist(title, body, hint, expected):
    assert C(title, body, kind="hospital_board", institution_hint=hint).is_dentist is expected


# ─────────── 마감·요약 ───────────


def test_deadline_and_summary():
    body = """안녕하세요. 강남역 인근 치과입니다.
근무: 주 4.5일 (월~금 09:30~18:30, 토 격주)
급여: 세후 1,500 보장 + 인센티브
자격: 소아치과 전문의 우대
문의: 010-1234-5678
마감: 2026.10.20"""
    c = C("[강남] 소아치과 전문의 모십니다", body)
    assert c.deadline_kind == "date" and c.deadline == date(2026, 10, 20)
    assert len(c.summary) == 3
    assert any("세후 1,500" in s for s in c.summary)
    assert not any("010-1234" in s for s in c.summary)
