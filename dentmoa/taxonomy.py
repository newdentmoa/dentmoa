"""분류 체계 정의.

화면, 설정, 분류기, 알림이 모두 이 파일의 키와 한글 이름을 공유한다.
키(영문)는 DB와 설정에 저장되므로 바꾸지 말고, 한글 이름만 수정한다.
"""

from __future__ import annotations

# 기관 종류 (한 공고에 하나)
INST_TYPES: dict[str, str] = {
    "dental_univ_hospital": "치과대학병원·치과대학",
    "univ_hospital_dept": "대학병원 치과",
    "general_hospital": "종합병원·병원 치과",
    "public_hospital": "공공병원·보건소",
    "disabled_dental": "장애인치과병원·구강진료센터",
    "dental_hospital": "치과병원 (네트워크 포함)",
    "dental_clinic": "치과의원 (로컬)",
    "other": "기타 기관",
}

# 직위 (여러 개 가능)
POSITIONS: dict[str, str] = {
    "faculty": "전임교원 (조교수 이상)",
    "clinical_professor": "임상교수·진료교수 (비전임)",
    "fellow": "전임의·임상강사 (펠로우)",
    "contract": "촉탁의·계약직·임기제",
    "staff_dentist": "봉직의 (페이닥터)",
    "resident": "전공의·인턴 (수련)",
    "other": "기타 (동업·외래교수 등)",
}

# 분과 (여러 개 가능). gp = 전문의를 따로 요구하지 않는 공고
SPECIALTIES: dict[str, str] = {
    "gp": "일반의 혹은 분과무관",
    "oms": "구강악안면외과",
    "prostho": "치과보철과",
    "ortho": "치과교정과",
    "pedo": "소아치과",
    "perio": "치주과",
    "endo": "치과보존과",
    "oral_medicine": "구강내과",
    "radiology": "영상치의학과",
    "pathology": "구강병리과",
    "preventive": "예방치과",
    "integrated": "통합치의학과",
    "basic_science": "기초치의학 (교원)",
}

# 근무 형태 (여러 개 가능)
WORK_TYPES: dict[str, str] = {
    "fulltime": "풀타임",
    "parttime": "파트타임",
    "locum": "대진",
}

# 글 종류
POST_KINDS: dict[str, str] = {
    "hiring": "구인",
    "seeking": "구직",
    "other": "기타 (양도·공지·결과발표 등)",
}

# 화면/알림에 쓰는 짧은 이름
SHORT_LABELS: dict[str, str] = {
    "dental_univ_hospital": "치과대학병원",
    "univ_hospital_dept": "대학병원",
    "general_hospital": "종합병원",
    "public_hospital": "공공·보건소",
    "disabled_dental": "장애인치과",
    "dental_hospital": "치과병원",
    "dental_clinic": "치과의원",
    "faculty": "전임교원",
    "clinical_professor": "임상·진료교수",
    "fellow": "전임의",
    "contract": "촉탁·계약",
    "staff_dentist": "봉직의",
    "resident": "전공의",
    "gp": "일반의",
    "oms": "구강외과",
    "prostho": "보철",
    "ortho": "교정",
    "pedo": "소아",
    "perio": "치주",
    "endo": "보존",
    "oral_medicine": "구강내과",
    "radiology": "영상치의학",
    "pathology": "구강병리",
    "preventive": "예방",
    "integrated": "통합치의학",
    "basic_science": "기초치의학",
    "fulltime": "풀타임",
    "parttime": "파트",
    "locum": "대진",
    "hiring": "구인",
    "seeking": "구직",
    "other": "기타",
}

DIMENSIONS: dict[str, dict[str, str]] = {
    "inst_types": INST_TYPES,
    "positions": POSITIONS,
    "specialties": SPECIALTIES,
    "work_types": WORK_TYPES,
}


def short(key: str) -> str:
    return SHORT_LABELS.get(key, key)
