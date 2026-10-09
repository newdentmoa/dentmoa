"""공고 분류 규칙 (AI 없이 조건문으로).

입력: 제목, 본문, 출처 정보
출력: Classification — 글 종류, 치과의사 공고 여부, 기관 종류, 직위, 분과, 근무형태,
      지역, 마감일, 3줄 요약, 그리고 '왜 그렇게 분류했는지' 근거.

분과 판단 원칙
- '교정과 전문의 모십니다'처럼 뽑는 대상이면 → 분과(specialties)
- '교정 가능자 우대', '소아 환자 많음'처럼 기술·선호만 말하면 → 참고 분야(specialty_hints)
- '교정과 전문의 상주', '소아 전문의와 협진'처럼 이미 있는 사람이면 → 무시
- '교정 제외', '소아 X' → 무시
- 뽑는 분과가 하나도 없으면 → 일반의(gp)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from . import institutions
from .deadline import find_deadline
from .regions import Region, normalize, parse_regions
from .summarize import summarize

CLASSIFIER_VERSION = 1


@dataclass
class Classification:
    post_kind: str = "hiring"
    is_dentist: bool = True
    inst_type: str = "dental_clinic"
    institution: str = ""
    positions: list[str] = field(default_factory=list)
    specialties: list[str] = field(default_factory=list)
    specialty_hints: list[str] = field(default_factory=list)
    work_types: list[str] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    deadline_kind: str = "none"
    deadline: date | None = None
    summary: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)

    def note(self, dim: str, text: str) -> None:
        lst = self.evidence.setdefault(dim, [])
        text = text.strip()
        if text and text not in lst and len(lst) < 6:
            lst.append(text)


# ════════════════════════════ 글 종류 ════════════════════════════

TAG_HIRING_RE = re.compile(r"[\[(【<]\s*(?:구인|모집|채용|초빙)\s*[\])】>]")
TAG_SEEKING_RE = re.compile(r"[\[(【<]\s*구직\s*[\])】>]")
SEEKING_RE = re.compile(
    r"구직\s*(?:합니다|해요|중(?!\s*인)|글|원합니다|희망)|"
    r"(?:자리|일자리|근무처|직장|근무지|병원|치과)\s*(?:를|을)?\s*(?:구합니다|구해요|구함|찾습니다|찾아요|찾음|알아봅니다|알아보고)|"
    r"(?:봉직|페이|대진|파트|근무)\s*(?:자리|할\s*곳|할\s*치과)\s*(?:구|찾|알아)|"
    r"(?:근무|대진|파트|봉직)\s*(?:가능|희망)\s*(?:합니다|해요|함|합니당|입니다)|"
    r"(?:근무|일)\s*하고\s*싶습니다|이력서\s*(?:보내|드립|드려)|저를\s*(?:필요|찾)"
)
# 누구를 뽑는지가 분명한 구인 표현 ('원장님 모십니다', '페이닥터 구합니다')
HIRING_STRONG_RE = re.compile(
    r"(?:원장|선생|페이\s*닥터|페닥|봉직의|치과\s*의사|전문의|GP|지피|전임의|교수|교원)님?\s*(?:을|를|분)?\s*"
    r"(?:모십니다|모셔요|모심|모십|구합니다|구해요|구함|모집|초빙|채용|찾습니다)",
    re.I,
)
HIRING_RE = re.compile(
    r"구인|모집|채용|초빙|모십니다|모셔요|모심|모시고|모십|구합니다|구해요|구함|구하고|찾습니다|찾아요|찾고\s*있|"
    r"공고|선발|충원|오실\s*분|함께\s*하실|함께할|급구|모셔|필요합니다"
)
RESULT_RE = re.compile(
    r"합격자|결과\s*(?:발표|안내|공고)|전형\s*결과|면접\s*(?:안내|일정|대상자|시험\s*안내)|"
    r"(?:서류|최종|필기)\s*(?:전형\s*)?합격|발표\s*$|취소\s*(?:공고|안내)|채용\s*(?:취소|중지|결과)|임용\s*후보자"
)
OTHER_KIND_RE = re.compile(
    r"양도|양수|매매|매각|임대|분양|인수(?!\s*인계)|장비\s*(?:판매|처분)|중고|세미나|강의|강좌|연수회|학술|학회|"
    r"설문|공지|이벤트|광고|홍보|개원\s*(?:자리|입지)\s*(?:추천|문의)"
)


def detect_post_kind(title: str, body: str, *, default: str) -> tuple[str, str]:
    """(종류, 근거)"""
    t = title
    if TAG_SEEKING_RE.search(t):
        return "seeking", "제목에 [구직]"
    if TAG_HIRING_RE.search(t):
        return "hiring", "제목에 [구인]"
    m = RESULT_RE.search(t)
    if m:
        return "other", m.group(0)
    m = SEEKING_RE.search(t)
    if m and not HIRING_STRONG_RE.search(t):
        return "seeking", m.group(0)
    hm = HIRING_RE.search(t)
    om = OTHER_KIND_RE.search(t)
    if om and not hm:
        return "other", om.group(0)
    if hm:
        return "hiring", hm.group(0)
    # 제목에 단서가 없으면 본문 앞부분
    head = body[:400]
    m = SEEKING_RE.search(head)
    if m and not HIRING_RE.search(head[: m.start()]):
        return "seeking", m.group(0)
    if HIRING_RE.search(head):
        return "hiring", HIRING_RE.search(head).group(0)
    return default, ""


# ════════════════════════════ 치과의사 공고 여부 ════════════════════════════

NON_DENTIST_RE = re.compile(
    r"치과\s*위생사|위생사|치위생|간호(?:사|조무사|직)?|조무사|기공사|치기공|행정(?:직)?|사무(?:직|원)?|원무|약사|영양사|"
    r"의료기사|방사선사|임상병리사|물리치료사|작업치료사|시설(?:직)?|미화|보안|운전|전산|코디네이터|코디|상담\s*실장|데스크|"
    r"실장님?|연구원|연구직|보조원|사회복지사"
)
DENTIST_STRONG_RE = re.compile(
    r"치과\s*의사|치의학|치과\s*대학|치과대학|치의학\s*(?:전문)?대학원|치전원|"
    r"구강악안면외과|치과\s*보철과|치과\s*교정과|소아\s*치과|치주과|치과\s*보존과|구강\s*내과|영상\s*치의학|구강\s*병리|예방\s*치과|통합\s*치의학|"
    r"치과\s*(?:전문의|교수|전임의|임상\s*강사|진료\s*교수|임상\s*교수|촉탁의|과장|의|진료의|레지던트|전공의|원장|봉직의)|"
    r"봉직의|페이\s*닥터|페닥"
)
POSITION_ANY_RE = re.compile(
    r"교수|교원|전임의|임상\s*강사|펠로우|촉탁|전문의|의사|진료의|원장|레지던트|전공의|인턴|수련의|임기제|봉직"
)
DENTAL_INST_TYPES = {"dental_univ_hospital", "dental_hospital", "disabled_dental", "dental_clinic"}


def detect_dentist(text: str, title: str, inst_type: str, dentist_only: bool) -> tuple[bool, str]:
    if dentist_only:
        return True, "치과의사 전용 게시판"
    t_wo = NON_DENTIST_RE.sub(" ", text)
    m = DENTIST_STRONG_RE.search(t_wo)
    if m:
        return True, m.group(0)
    has_pos = POSITION_ANY_RE.search(t_wo)
    if "치과" in t_wo and has_pos:
        return True, f"치과 + {has_pos.group(0)}"
    if inst_type in DENTAL_INST_TYPES and has_pos and not NON_DENTIST_RE.search(title):
        return True, f"치과 기관 + {has_pos.group(0)}"
    return False, ""


# ════════════════════════════ 기관 종류 ════════════════════════════

INST_RULES: list[tuple[str, re.Pattern]] = [
    ("disabled_dental", re.compile(r"장애인\s*(?:치과|구강)")),
    (
        "dental_univ_hospital",
        re.compile(
            r"(?:대학교|대학|대)\s*(?:부속\s*)?(?:치과\s*대학\s*병원|치과\s*병원)|치과\s*대학\s*병원|"
            r"치과\s*대학(?!\s*(?:졸업|출신|동문|동기|선후배))|치의학\s*(?:전문)?\s*대학원|치전원|치의학과\s*교원"
        ),
    ),
    (
        "univ_hospital_dept",
        re.compile(
            r"(?:대학교|대학|대)\s*(?:부속\s*)?(?:병원|의료원)|대학\s*병원|의과\s*대학|의대\s*부속|세브란스|아산\s*병원|"
            r"삼성\s*서울\s*병원|강북\s*삼성|성모\s*병원|길\s*병원|백\s*병원|순천향|분당\s*서울대"
        ),
    ),
    (
        "public_hospital",
        re.compile(
            r"보건\s*(?:소|지소|의료원)|보훈\s*병원|국립|시립|도립|군립|구립|국군|군\s*병원|경찰\s*병원|적십자\s*병원|"
            r"근로\s*복지\s*공단|산재\s*병원|교도소|구치소|교정\s*시설|임기제|공무원|지방\s*의료원|"
            r"(?:서울|부산|인천|대구|대전|광주|울산|경기도?|강원도?|청주|충주|천안|공주|서산|홍성|군산|남원|목포|순천|강진|"
            r"포항|김천|안동|울진|마산|진주|통영|거창|서귀포|제주|파주|이천|안성|수원|의정부|포천|원주|강릉|속초|삼척|영월)\s*의료원"
        ),
    ),
    ("dental_hospital", re.compile(r"치과\s*병원")),
    ("general_hospital", re.compile(r"종합\s*병원|요양\s*병원|한방\s*병원|[가-힣A-Za-z]{2,}병원\s*[\])]?\s*(?:내\s*)?치과|병원\s*치과")),
    ("dental_clinic", re.compile(r"치과\s*의원|[가-힣A-Za-z]{1,}치과(?!\s*(?:병원|대학|위생|기공|재료|의사|전문의|진료|치료))")),
]

INSTITUTION_NAME_RES = [
    # 붙여 쓴 이름: OO치과, OO치과의원, OO대학교치과병원, OO병원
    re.compile(
        r"(?<![가-힣A-Za-z0-9])([가-힣A-Za-z0-9]{2,}(?:치과대학병원|치과병원|치과의원|대학교병원|대학병원|의료원|보건소|병원|구강진료센터|치과))"
    ),
    # 띄어 쓴 이름: OO대학교 치과병원, OO구 보건소
    re.compile(r"(?<![가-힣A-Za-z0-9])([가-힣]{2,}(?:대학교|대학|구|시|군)\s(?:치과병원|치과대학병원|보건소|병원))"),
]
NAME_STOPWORDS = re.compile(
    r"^(?:저희|우리|본|당|인근|근처|신규|개원|일반|소아|교정|해당|모든|여러|좋은|이번|지역|동네|로컬|상주|협진|전문|대형|중형|소형|"
    r"네트워크|강남역|역세권|신도시|대학|종합|요양|한방|치과|병원)"
)
GENERIC_NAMES = {
    "치과", "병원", "대학병원", "치과병원", "치과의원", "종합병원", "요양병원", "대학교병원", "보건소", "의료원",
    "어린이치과", "소아치과", "교정치과", "키즈치과",
}


# 기관 이름 바로 뒤에 오면 '그 기관'이 아니라 위치·경력 설명
LANDMARK_AFTER = re.compile(
    r"^\s*(?:정문|후문|본관|바로)?\s*(?:맞은편|건너편|앞|옆|인근|근처|근방|부근|도보|사거리|역|방면|쪽)"
)
CAREER_AFTER = re.compile(r"^\s*(?:교수|전임의|과장|수련|전공의)?\s*(?:출신|경력|수련|이상|급|근무\s*경력)")


def _is_context_mention(text: str, end: int) -> bool:
    after = text[end:end + 12]
    return bool(LANDMARK_AFTER.search(after) or CAREER_AFTER.search(after))


def _inst_in_text(inst, text: str) -> bool:
    """기관 이름이 위치·경력 설명이 아닌 곳에 한 번이라도 나오는지."""
    for name in (inst.name, *inst.aliases):
        pat = r"\s*".join(re.escape(ch) for ch in re.sub(r"\s+", "", name))
        for m in re.finditer(pat, text):
            if not _is_context_mention(text, m.end()):
                return True
    return False


def detect_inst(title: str, hint: str, body: str, *, scope_body: bool, default: str):
    """(기관 종류, 기관명, 근거, 알려진 기관 또는 None)"""
    head = f"{hint}\n{title}"
    probe = head + ("\n" + body[:600] if scope_body else "")
    inst = institutions.find(probe)
    if inst and ((hint and institutions.find(hint) is inst) or _inst_in_text(inst, probe)):
        return inst.inst_type, inst.name, f"기관 목록: {inst.name}", inst

    for kind, rx in INST_RULES:
        if kind == "dental_clinic" and scope_body:
            rx = re.compile(r"치과\s*의원")  # 병원·기관 게시판에서는 '치과의원'이라고 써야만 의원으로 본다
        for m in rx.finditer(head):
            if not _is_context_mention(head, m.end()):
                return kind, _guess_name(head), m.group(0), None
    if scope_body:
        part = body[:600]
        for kind, rx in INST_RULES[:5]:  # 본문에서는 확실한 것만
            for m in rx.finditer(part):
                if not _is_context_mention(part, m.end()):
                    return kind, _guess_name(part), m.group(0), None
    return default, _guess_name(f"{head}\n{body[:300]}"), "", None


def _guess_name(text: str) -> str:
    best = None
    for rx in INSTITUTION_NAME_RES:
        for m in rx.finditer(text):
            name = m.group(1).strip()
            if name in GENERIC_NAMES or NAME_STOPWORDS.search(name):
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), name)
            break
    return best[1] if best else ""


# ════════════════════════════ 직위 ════════════════════════════

CLINICAL_PROF_RE = re.compile(
    r"(?:임상|진료|기금|초빙|겸임|연구|비전임)\s*(?:조|부)?\s*교수(?:\s*요원)?|임상\s*교원|진료\s*교원|비전임\s*교원|기금\s*교원|연구\s*교원"
)
OUTSIDE_PROF_RE = re.compile(r"외래\s*(?:조|부)?\s*교수|명예\s*교수|겸무\s*교수")
FACULTY_RE = re.compile(
    r"전임\s*교원|전임\s*교수|신임\s*교원|신규\s*교원|교원\s*(?:초빙|채용|모집|공채|공개\s*채용|임용)|"
    r"(?:조|부|정)\s*교수(?!\s*(?:출신|경력|님|진|들))|교수\s*(?:초빙|채용|모집|공채|임용|요원)|전임\s*강사|테뉴어|tenure"
)
FELLOW_RE = re.compile(r"전임의|임상\s*강사|펠로우|펠로(?!십)|fellow|임상\s*연구의|임상\s*전임의", re.I)
CONTRACT_RE = re.compile(
    r"촉탁(?:의|\s*의사|\s*치과의사|\s*전문의|직)?|계약직|임기제|시간\s*선택제|시간제\s*(?:의사|치과의사|전문의|공무원)|"
    r"기간제\s*(?:의사|치과의사|전문의)|의무\s*(?:사무관|직)|공무원"
)
STAFF_STRONG_RE = re.compile(r"봉직의?|페이\s*닥터|페닥|진료\s*원장|부원장|월급\s*원장|급여\s*원장")
STAFF_RE = re.compile(
    r"봉직의?|페이\s*닥터|페닥|진료\s*원장|부원장|월급\s*원장|급여\s*원장|"
    r"원장님?\s*(?:을|를)?\s*(?:모십니다|모셔요|모심|모십|모시|구합니다|구해요|구함|구인|모집|초빙|찾습니다|찾아요|찾음)|"
    r"선생님?\s*(?:을|를)?\s*(?:모십니다|모심|모십|모시|구합니다|구해요|구함|모집|찾습니다)|"
    r"치과\s*의사\s*(?:구인|모집|채용|구함|구합니다)|GP\s*(?:원장|선생|구인|모집|구함)|일반의\s*(?:구인|모집|채용)",
    re.I,
)
RESIDENT_RE = re.compile(r"전공의|레지던트|인턴(?!십)|수련의|수련\s*치과\s*의사|수련\s*과정")
OTHER_POS_RE = re.compile(r"동업|공동\s*개원|파트너\s*원장|지분\s*(?:참여|투자)|투자\s*원장|양도|양수|인수")


def detect_positions(text: str, c: Classification) -> list[str]:
    found: list[str] = []
    work = text
    for m in OUTSIDE_PROF_RE.finditer(work):
        found.append("other")
        c.note("positions", m.group(0))
    work = OUTSIDE_PROF_RE.sub(" ", work)
    for m in CLINICAL_PROF_RE.finditer(work):
        found.append("clinical_professor")
        c.note("positions", m.group(0))
    work = CLINICAL_PROF_RE.sub(" ", work)
    for key, rx in (
        ("faculty", FACULTY_RE),
        ("fellow", FELLOW_RE),
        ("contract", CONTRACT_RE),
        ("resident", RESIDENT_RE),
        ("staff_dentist", STAFF_RE),
        ("other", OTHER_POS_RE),
    ):
        m = rx.search(work)
        if m:
            found.append(key)
            c.note("positions", m.group(0))
    # '선생님 모집' 같은 일반 표현만으로 잡힌 봉직의는, 더 구체적인 직위가 있으면 뺀다
    specific = {"faculty", "clinical_professor", "fellow", "contract", "resident", "other"}
    if "staff_dentist" in found and specific.intersection(found) and not STAFF_STRONG_RE.search(work):
        found.remove("staff_dentist")
    return list(dict.fromkeys(found))


# ════════════════════════════ 분과 ════════════════════════════

# (분과 키, 확실한 이름들, 줄임말 어간들)
SPECIALTY_TERMS: list[tuple[str, list[str], list[str]]] = [
    ("oms", [r"구강\s*악안면\s*외과(?:학)?", r"악안면\s*외과", r"구강\s*외과", r"\bOMS\b"], [r"구외", r"구강외과"]),
    ("prostho", [r"치과\s*보철과", r"치과\s*보철학", r"보철학"], [r"보철"]),
    ("ortho", [r"치과\s*교정과", r"치과\s*교정학", r"교정학", r"[가-힣A-Za-z]+교정\s*치과", r"교정\s*치과\s*(?:의원|병원)"], [r"교정"]),
    (
        "pedo",
        [r"소아\s*치과(?:학)?", r"소아\s*청소년\s*치과", r"어린이\s*치과", r"키즈\s*치과", r"아동\s*치과", r"\bpedo\b"],
        [r"소아", r"소치"],
    ),
    ("perio", [r"치주\s*과(?:학)?", r"치주\s*과학"], [r"치주"]),
    ("endo", [r"치과\s*보존과", r"치과\s*보존학", r"보존학", r"근관\s*치료\s*전문의", r"\bendo\b"], [r"보존", r"엔도"]),
    ("oral_medicine", [r"구강\s*내과(?:학)?"], [r"턱관절"]),
    ("radiology", [r"영상\s*치의학(?:과)?", r"구강\s*악안면\s*방사선(?:과|학)?", r"구강\s*방사선(?:과|학)?"], []),
    ("pathology", [r"구강\s*병리(?:과|학)?"], []),
    ("preventive", [r"예방\s*치과(?:학)?", r"예방\s*치의학", r"예방\s*치학"], []),
    ("integrated", [r"통합\s*치의학(?:과)?", r"통합\s*치과", r"\bAGD\b"], [r"통치"]),
    (
        "basic_science",
        [
            r"구강\s*해부(?:학)?", r"구강\s*생리(?:학)?", r"구강\s*생화학", r"구강\s*미생물(?:학)?", r"구강\s*조직(?:학)?",
            r"치과\s*재료(?:학)?", r"치과\s*약리(?:학)?", r"치의학\s*교육(?:학)?", r"구강\s*생물(?:학)?", r"치과\s*생체\s*재료",
            r"기초\s*치의학",
        ],
        [],
    ),
]

# 어간 뒤에 오면 '뽑는 대상'으로 보는 말
ROLE_SUFFIX = r"\s*(?:과\s*)?(?:진료\s*)?(?:전담\s*)?(?:전문의|원장|선생|교수|전임의|임상\s*강사|펠로우|과장|의사|전공|교실|담당의)"
# '보존과' 뒤에 올 수 있는 것: 공백·기호 또는 조사
GWA_FOLLOW = r"(?=[^가-힣]|$|에서|에|의|을|를|은|는|이|가|로|도)"
GP_RE = re.compile(
    r"\bGP\b|지피|일반의|일반\s*치과\s*의사|일반\s*진료\s*(?:원장|의|선생)|"
    r"전문의\s*(?:무관|아니어도|아니셔도|상관\s*없|불문|여부\s*무관)|(?:과|전공|분과|전문\s*과목)\s*(?:무관|상관\s*없|불문)",
    re.I,
)
NEG_AFTER = re.compile(
    r"^\s*(?:전문의\s*)?(?:진료\s*)?(?:은|는|도|가|이)?\s*"
    r"(?:제외|불가|안\s*함|안함|않|하지\s*않|X\b|x\b|없음|없습니다|불필요|무관|노\b|NO\b|"
    r"아니(?:셔도|어도|라도|여도|더라도)|무방|상관\s*없|관계\s*없|불문)",
    re.I,
)
EXISTING_AFTER = re.compile(
    r"^.{0,14}?(?:상주|협진|대표\s*원장|오십니다|오시는|오셔서|방문\s*진료|출장|에서\s*담당|근무\s*중|진료\s*중|계십니다|계시고|계시며|계심|계셔|계신|있습니다|있으며|있고|있음|있어|재직|보유|별도|따로|함께\s*하고|운영\s*중)"
)
EXISTING_BEFORE = re.compile(r"(?:외부|대표\s*원장님?\s*(?:은|이|께서|님)?|상주\s*(?:중인|하는)|근무\s*중인|재직\s*중인|계신|함께\s*하는|협진\s*(?:가능한|하는))\s*$")
RECRUIT_NEAR = re.compile(r"모집|구인|구합|구함|모십|모심|모셔|초빙|채용|찾습|찾아|우대|선호|오실|지원")
# 분과 이름이 여러 개 나열된 경우('보존, 보철 전문의') 목록 끝까지 건너뛰기
LIST_ITEMS_RE = re.compile(
    r"(?:\s*(?:,|및|/|·|&|\+|와|과)?\s*(?:치과\s*)?(?:보철|보존|교정|치주|소아|구강\s*악안면\s*외과|구강\s*외과|구외|구강\s*내과|"
    r"통합\s*치의학|통치|예방|영상\s*치의학|구강\s*병리|엔도|소치)(?:\s*치과)?(?:\s*과)?(?:학)?(?![가-힣]))*"
)
STRONG_TAIL = re.compile(
    r"^\s*(?:\(|,)?\s*(?:전문의|원장|선생|교수|전임의|임상\s*강사|펠로우|과장|의사|전공|교실|담당|모집|구인|구합|구함|모십|모심|초빙|채용)"
)
WEAK_TAIL = re.compile(r"^\s*(?:진료|위주|가능|환자|치료|케이스|술식|경험|우대|관심|많|잘|장비|임플란트)")
LABEL_BEFORE = re.compile(r"(?:모집|채용|초빙|구인)\s*(?:분야|과목|과)|분야|과목|전공")


def _list_end(clause: str, pos: int) -> int:
    m = LIST_ITEMS_RE.match(clause, pos)
    return m.end() if m else pos


HINT_AFTER = re.compile(r"^.{0,10}?(?:가능|우대|경험|관심|선호|위주|많|환자|케이스|술식|진료\s*가능|하실\s*수|잘\s*하)")


def _role(text: str, start: int, end: int, strong: bool) -> str:
    """언급의 역할: 'target' 뽑는 대상 / 'hint' 참고 / 'existing' 기존 인력 / 'negated'"""
    after = text[end:end + 18]
    before = text[max(0, start - 14):start]
    if NEG_AFTER.search(after):
        return "negated"
    ex = EXISTING_AFTER.search(after)
    if ex:
        rec = RECRUIT_NEAR.search(after)
        if not rec or rec.start() > ex.end() - 1:
            return "existing"
    if EXISTING_BEFORE.search(before):
        return "existing"
    if strong:
        return "target"
    return "hint"


def _clauses(text: str) -> list[str]:
    parts = re.split(r"[\n\r]+|(?<=[.!?])\s+|\s+/\s+|\s*\|\s*|(?<=[며고])\s+(?=[^\s])", text)
    return [p for p in parts if p and p.strip()]


def detect_specialties(title: str, body: str, inst_name: str, c: Classification) -> tuple[list[str], list[str]]:
    targets: list[str] = []
    hints: list[str] = []
    negated: set[str] = set()

    sources = [title, inst_name] + _clauses(body)
    for clause in sources:
        if not clause:
            continue
        for key, full_forms, stems in SPECIALTY_TERMS:
            spans: list[tuple[int, int]] = []
            # 1) 확실한 이름 (소아치과, 구강악안면외과 …)
            for pat in full_forms:
                for m in re.finditer(pat, clause, re.I):
                    if any(s <= m.start() < e for s, e in spans):
                        continue
                    spans.append((m.start(), m.end()))
                    role = _role(clause, m.start(), _list_end(clause, m.end()), strong=True)
                    _record(key, role, m.group(0), targets, hints, negated, c)
            # 2) 어간 + 역할어 (교정 전문의, 소아 원장님, 보존과 …)
            for stem in stems:
                for m in re.finditer(
                    r"(?<![가-힣])" + stem + r"(?:(?P<role>" + ROLE_SUFFIX + r")|(?P<gwa>\s*과)" + GWA_FOLLOW + ")",
                    clause,
                ):
                    if any(s <= m.start() < e for s, e in spans):
                        continue
                    end = _list_end(clause, m.end())
                    strong = bool(m.group("role")) or not WEAK_TAIL.match(clause[end:])
                    spans.append((m.start(), m.end()))
                    role = _role(clause, m.start(), end, strong=strong)
                    _record(key, role, m.group(0), targets, hints, negated, c)
                # 3) 어간만 (교정 가능, 소아 환자, '보존, 보철 전문의' …)
                for m in re.finditer(r"(?<![가-힣])" + stem + r"(?![가-힣]{0,1}(?:과|학))", clause):
                    if any(s <= m.start() < e for s, e in spans):
                        continue
                    end = _list_end(clause, m.end())
                    tail = clause[end:]
                    strong = bool(STRONG_TAIL.match(tail)) or (
                        not tail.strip() and bool(LABEL_BEFORE.search(clause[: m.start()]))
                    )
                    role = _role(clause, m.start(), end, strong=strong)
                    if role == "hint" and not HINT_AFTER.search(clause[m.end():m.end() + 14]) and not RECRUIT_NEAR.search(clause):
                        continue  # 그냥 지나가는 언급
                    _record(key, role, clause[m.start():min(len(clause), max(end, m.end() + 4))], targets, hints, negated, c)

    targets = list(dict.fromkeys(targets))
    hints = [h for h in dict.fromkeys(hints) if h not in targets]

    gp = None
    for clause in [title] + _clauses(body):
        for m in GP_RE.finditer(clause):
            if _role(clause, m.start(), m.end(), strong=True) == "target":
                gp = m
                break
        if gp:
            break
    if gp:
        c.note("specialties", gp.group(0))
        if "gp" not in targets:
            targets.insert(0, "gp")
    if not [t for t in targets if t != "gp"] and "gp" not in targets:
        targets = ["gp"]
        c.note("specialties", "분과 언급 없음 → 일반의")
    return targets, hints


def _record(key, role, snippet, targets, hints, negated, c):
    if role == "target":
        targets.append(key)
        c.note("specialties", snippet)
    elif role == "hint":
        hints.append(key)
        c.note("specialty_hints", snippet)
    elif role == "negated":
        negated.add(key)
        c.note("specialty_ignored", f"{snippet} (제외)")
    else:
        c.note("specialty_ignored", f"{snippet} (기존 인력)")


# ════════════════════════════ 근무 형태 ════════════════════════════

LOCUM_RE = re.compile(r"대진(?!대)")
PART_RE = re.compile(
    r"파트\s*타임|(?<![가-힣])파트(?!너|장)|비상근|주\s*(?:[1-2]\d|30)\s*시간|\bpart\s*-?\s*time\b|\bpart\b|"
    r"주\s*[1-3]\s*(?:일|회|번)|주\s*[1-3]\s*[~\-]\s*[1-3]\s*(?:일|회)|요일제|시간제|시간\s*선택제|반일|오전만|오후만|"
    r"(?:토요일?|일요일?|야간)\s*(?:만|파트|전담)|하루\s*(?:만|근무)|단기\s*(?:근무|알바)|알바",
    re.I,
)
FULL_RE = re.compile(
    r"풀\s*타임|full\s*-?\s*time|주\s*(?:4|4\.5|5|5\.5|6)\s*(?:일|회)|주\s*(?:4|4\.5|5|5\.5|6)(?![\d.])|정규직|전일제|"
    r"주\s*40\s*시간|전임(?!\s*의)",
    re.I,
)
FULL_WEAK_RE = re.compile(r"(?<!비)상근|월\s*~\s*금")
PART_STRONG_RE = re.compile(r"비상근|시간\s*선택제|시간제|주\s*(?:[1-2]\d|30)\s*시간|반일|오전만|오후만|파트\s*타임")


NOT_PART_BEFORE = re.compile(r"(?:야간|토요일?|일요일?|당직|격주|휴일|주말)\s*$")


def detect_work_types(text: str, c: Classification) -> list[str]:
    out = []
    m = FULL_RE.search(text)
    if m:
        out.append("fulltime")
        c.note("work_types", m.group(0))
    part = None
    for pm in PART_RE.finditer(text):
        # '야간 주2회', '토요일 월 2회'는 근무 조건이지 파트타임이 아님
        if re.match(r"주\s*[1-3]", pm.group(0)) and NOT_PART_BEFORE.search(text[max(0, pm.start() - 6):pm.start()]):
            continue
        part = pm
        break
    if part:
        out.append("parttime")
        c.note("work_types", part.group(0))
    weak = FULL_WEAK_RE.search(text)
    if weak and "fulltime" not in out and not (part and PART_STRONG_RE.search(text)):
        out.insert(0, "fulltime")
        c.note("work_types", weak.group(0))
    m = LOCUM_RE.search(text)
    if m:
        out.append("locum")
        c.note("work_types", m.group(0))
    return out


# ════════════════════════════ 지역 ════════════════════════════

LABELED_LOCATION_RE = re.compile(r"(?:근무\s*지|근무\s*지역|근무\s*장소|위치|주소|소재지|지역|장소|근무처)\s*[:：\]]?\s*(.{2,60})")


def detect_regions(title: str, body: str, region_hint: str, inst) -> tuple[list[Region], str]:
    tiers: list[tuple[str, list[Region]]] = []
    if region_hint:
        tiers.append(("지역 칸", parse_regions(region_hint)))
    tiers.append(("제목", parse_regions(title)))
    labeled = " ".join(m.group(1) for m in LABELED_LOCATION_RE.finditer(body))
    if labeled:
        tiers.append(("근무지 줄", parse_regions(labeled)))
    if inst and inst.sido:
        tiers.append(("기관 위치", [Region(inst.sido, inst.sigungu)]))
    tiers.append(("본문", parse_regions(body[:1500], max_results=4)))

    chosen: list[Region] = []
    why = ""
    for name, regs in tiers:
        if regs:
            chosen, why = list(regs), name
            break
    # 시·도만 있으면 뒤 단계에서 같은 시·도의 구체적인 시군구로 보완
    if chosen and all(r.sigungu is None for r in chosen):
        sidos = {r.sido for r in chosen}
        for name, regs in tiers:
            finer = [r for r in regs if r.sigungu and r.sido in sidos]
            if finer:
                chosen = finer + [r for r in chosen if r.sido not in {f.sido for f in finer}]
                why += f" + {name}"
                break
    return chosen, why


# ════════════════════════════ 전체 ════════════════════════════


def classify(
    title: str,
    body: str = "",
    *,
    posted: date,
    source_kind: str = "local_board",
    dentist_only: bool = False,
    region_hint: str = "",
    institution_hint: str = "",
    default_inst_type: str | None = None,
) -> Classification:
    """source_kind: 'local_board'(모어덴·덴트포토) / 'hospital_board' / 'aggregator'"""
    title = normalize(title or "").strip()
    body = normalize(body or "")
    institution_hint = normalize(institution_hint or "")
    text = f"{title}\n{body}"
    c = Classification()

    # 글 종류
    default_kind = "hiring" if source_kind == "local_board" else ("hiring" if HIRING_RE.search(title) else "other")
    c.post_kind, why = detect_post_kind(title, body, default=default_kind)
    c.note("post_kind", why)

    # 기관
    default_inst = default_inst_type or ("dental_clinic" if source_kind == "local_board" else "other")
    c.inst_type, c.institution, why, inst = detect_inst(
        title, institution_hint, body, scope_body=source_kind != "local_board", default=default_inst
    )
    if not why and source_kind != "local_board":
        c.uncertain.append("inst_types")
    c.note("inst_types", why or "기본값")
    if institution_hint and not c.institution:
        c.institution = institution_hint[:40]

    # 치과의사 공고 여부
    c.is_dentist, why = detect_dentist(text, title, c.inst_type, dentist_only)
    c.note("is_dentist", why)

    # 직위
    c.positions = detect_positions(text, c)
    if c.post_kind == "hiring" and not c.positions:
        c.positions = ["staff_dentist"] if c.inst_type != "public_hospital" else ["contract"]
        c.uncertain.append("positions")
        c.note("positions", "기본값")
    # 지역 공공기관 공고 중 '공무원'은 임기제·계약직
    # 전공의 공고에 '교수' 단어가 함께 있어도(지도교수 등) 그대로 둔다

    # 분과
    c.specialties, c.specialty_hints = detect_specialties(title, body, c.institution, c)

    # 근무 형태
    c.work_types = detect_work_types(text, c)
    if not c.work_types and c.post_kind == "hiring":
        c.work_types = ["fulltime"]
        c.uncertain.append("work_types")
        c.note("work_types", "기본값")

    # 지역
    c.regions, why = detect_regions(title, body, region_hint, inst)
    c.note("regions", why)

    # 마감일
    if c.post_kind == "hiring":
        c.deadline_kind, c.deadline = find_deadline(text, posted)

    # 요약
    c.summary = summarize(body, n=3, title=title)
    return c
