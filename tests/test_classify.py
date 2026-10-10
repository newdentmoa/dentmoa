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
        # 모어덴 실제 글(2026-10): 개인 치과 이름 속의 '세브란스'
        ("토요일 근무 가능하신 선생님 모십니다.", "연세세브란스치과의원 공덕점", "local_board", "dental_clinic", []),
        ("강남세브란스병원 치과 임상강사 모집", "", "local_board", "univ_hospital_dept", ["서울 강남구"]),
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
        # 치과 기관의 직원 공고 — 근무 부서가 진료과(소아치과 등)여도 치과의사 공고가 아니다
        ("[서울대학교치과병원] 2026년 장애인 제한경쟁 단시간근무자 채용",
         "모집분야: 사무보조(단시간근무자)\n근무부서: 소아치과\n원활한 의사소통 가능자", "서울대학교치과병원", False),
        ("2026년 제5차 직원 채용 공고", "채용분야: 진료지원직(치과위생사) 2명, 행정직 1명", "서울대학교치과병원", False),
        ("소아치과 외래 계약직 채용", "모집직종: 간호사\n근무부서: 소아치과 외래", "부산대학교치과병원", False),
        ("[경북대학교치과병원] 기간제 근로자 채용 공고", "채용직종: 치과위생사(교정과)\n임기제", "경북대학교치과병원", False),
        ("2026년 하반기 계약직(육아휴직 대체) 채용", "직종: 의료기사(치과기공사)\n부서: 치과보철과 기공실", "전남대학교치과병원", False),
        ("[강릉원주대학교치과병원] 공무직 채용", "모집분야: 환경미화\n원활한 의사소통", "강릉원주대학교치과병원", False),
        ("2026년 제5차 직원 채용 공고", "채용분야: 진료교수(소아치과) 1명, 치과위생사 2명", "서울대학교치과병원", True),
        ("치과 진료의(계약직) 채용", "치과위생사와 함께 근무, 치과의사 면허", "OO의료원", True),
        ("구강악안면외과 채용 공고", "문의: 행정지원팀", "OO대학교병원", True),
        # 잡알리오 실제 공고(2026-10): 공공기관 '청년인턴'은 수련의 인턴이 아니다
        ("2026년 제15차 청년인턴(진료지원-치과위생사/치과보존과) 채용 공고", "", "서울대학교치과병원", False),
        ("서울대학교치과병원 단시간근무자(장애인 제한경쟁) 채용 공고", "", "서울대학교치과병원", False),
        ("2027년도 치과의사 인턴(수련의) 모집", "", "서울대학교치과병원", True),
        # 하이브레인넷 실제 공고(2026-10): 치과대학 연구실의 박사후연구원은 치과의사 공고가 아니다
        ("서울대학교치과병원 김선영교수연구팀 Post-Doc. 모집", "", "", False),
        ("연세대학교 치과대학 구강생물학교실 Post-Doc. 모집", "", "연세대학교 치과대학 구강생물학교실", False),
        ("치과 임상연구 박사후연구원(치과의사) 모집", "", "", True),
        ("일반직 채용제도 개편 관련 사전 예고(어학성적 기준 변경)", "서울대학교 치의학대학원", "경북대학교치과병원", False),
        # 보건소장: 의사 우선이지만 임용이 어려우면 치과의사도 가능 → 치과의사 공고(분과무관)
        ("OO군 보건소장(개방형직위) 임용 공고", "", "", True),
        ("OO군 보건소장 채용", "응시자격: 의사 면허 소지자", "", True),
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


def test_health_center_head_is_gp():
    c = C("OO시 보건소장(지방보건서기관, 임기제) 채용 공고", "자격요건: 의사·치과의사·한의사 면허", kind="aggregator")
    assert c.is_dentist and c.specialties == ["gp"] and c.post_kind == "hiring"


def test_specific_hospital_in_title_beats_operator_hint():
    """잡알리오는 운영 법인('한국보훈복지의료공단')을 함께 준다 → 제목의 병원 이름이 우선 (2026-10 실제 사례)."""
    c = C("[광주보훈병원] 치과 전문의 채용 공고", kind="aggregator", institution_hint="한국보훈복지의료공단")
    assert c.institution == "광주보훈병원" and [r.label for r in c.regions] == ["광주 광산구"]
    c = C("[중앙보훈병원] 전문의(서울요양병원, 치과병원 치주과) 채용 공고", kind="aggregator", institution_hint="한국보훈복지의료공단")
    assert c.institution == "중앙보훈병원" and [r.label for r in c.regions] == ["서울 강동구"]


def test_college_list_boilerplate_is_not_a_dental_post():
    """대학 전체 교원 공고의 '의과대학, 치과대학 지원자는 …' 안내 문구만으로는 치과의사 공고가 아니다 (2026-10 연세대)."""
    note = ("1. 초빙분야\n법학전문대학원 / 민법 / 1\n"
            "의과대학, 치과대학 및 원주의과대학 임상학분야 지원자의 경우 추천서 내용에 임상적 측면에 대한 의견을 포함할 것\n"
            "가. 의학 및 치의학계열의 경우 전문의 자격을 인정받은 자.")
    title = "2027학년도 연세대학교 전임교원 초빙 공고 (법학전문대학원, 교목실)"
    assert not C(title, note, kind="hospital_board").is_dentist
    c = C("2027학년도 연세대학교 전임교원 초빙 공고", note + "\n치과대학 / 통합치의학 / 1 / 통합치의학과 전문의 자격 취득자", kind="hospital_board")
    assert c.is_dentist and "integrated" in c.specialties
    assert C("의과대학, 치과대학 교원 초빙", note, kind="hospital_board").is_dentist  # 제목은 그대로 본다
