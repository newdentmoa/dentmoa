from datetime import date, datetime, timedelta

import pytest

from dentmoa import config, db, settings_store
from dentmoa.deadline import find_deadline
from dentmoa.matching import match
from dentmoa.models import RawPosting
from dentmoa.regions import Region, parse_regions, selection_matches

POSTED = date(2026, 10, 1)


# ───────────── 지역 ─────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[서울 강남] 교정과 전문의 모십니다", ["서울 강남구"]),
        ("서울강남구 역삼동", ["서울 강남구"]),
        ("경기 성남시 분당구 정자동", ["경기 성남시"]),
        ("분당 서현역 도보 3분", ["경기 성남시"]),
        ("[경기광주] 오포읍 치과", ["경기 광주시"]),
        ("경기도 광주시 태전동", ["경기 광주시"]),
        ("광주 광산구 수완지구", ["광주 광산구"]),
        ("광주 상무지구", ["광주 서구"]),
        ("부산 서면 치과", ["부산 부산진구"]),
        ("서면으로 제출 바랍니다", []),
        ("인천 검단신도시", ["인천 검단구"]),
        ("인천 중구", ["인천 제물포구", "인천 영종구"]),
        ("대구 중구 동성로", ["대구 중구"]),
        ("중구 소재 치과", []),
        ("전문의 상주, 상주 가능", []),
        ("경북 상주시", ["경북 상주시"]),
        ("고양이 좋아하시는 분", []),
        ("연봉 이천만원 추가", []),
        ("경기 이천시 치과", ["경기 이천시"]),
        ("수도권 지역", ["서울", "경기", "인천"]),
        ("제주 노형동", ["제주 제주시"]),
        ("세종시 나성동", ["세종"]),
        ("세종대왕", []),
        ("서울대입구역", ["서울 관악구"]),
        ("김포공항 근처", ["서울 강서구"]),
        ("경남 고성", ["경남 고성군"]),
        ("고성군 치과", []),
        ("창원 마산합포구", ["경남 창원시"]),
        ("순천향대학교 부천병원", ["경기 부천시"]),
        ("남양주 다산신도시", ["경기 남양주시"]),
        ("[구미] 치과 봉직의", ["경북 구미시"]),
        ("[진주] 치과 봉직의", ["경남 진주시"]),
        # 도로 이름 속의 구·시 이름 (2026-10 모어덴 실제 글: 안양 '관악대로')
        ("경기도 안양시 동안구 관악대로 236 (비산동)", ["경기 안양시"]),
        ("관악대로 236 5층", []),
        ("서울 금천구 시흥대로 123", ["서울 금천구"]),
        ("경기 시흥시 시흥대로 5", ["경기 시흥시"]),
        ("서울 관악구 관악로 1", ["서울 관악구"]),
    ],
)
def test_parse_regions(text, expected):
    assert [r.label for r in parse_regions(text)] == expected


def test_selection_matches():
    gangnam = [Region("서울", "강남구")]
    assert selection_matches(["전국"], gangnam) == "yes"
    assert selection_matches(["서울"], gangnam) == "yes"
    assert selection_matches(["서울 강남구"], gangnam) == "yes"
    assert selection_matches(["서울 송파구"], gangnam) == "no"
    assert selection_matches(["부산"], gangnam) == "no"
    assert selection_matches(["서울 송파구"], [Region("서울")]) == "unknown"
    assert selection_matches(["서울"], []) == "unknown"
    assert selection_matches(["부산", "서울 강남구"], gangnam) == "yes"


# ───────────── 마감일 ─────────────


@pytest.mark.parametrize(
    "text, kind, day",
    [
        ("마감일: 2026-10-20", "date", date(2026, 10, 20)),
        ("접수기간: 2026. 10. 5.(월) ~ 10. 16.(금) 18:00", "date", date(2026, 10, 16)),
        ("~10.31", "date", date(2026, 10, 31)),
        ("10/20(화)까지 이메일 접수", "date", date(2026, 10, 20)),
        ("채용시 마감", "until_filled", None),
        ("연락처 010-1234-5678", "none", None),
        ("급여 1,200만원 / 주 4.5일", "none", None),
        ("서류접수 : 10월 5일 ~ 10월 16일", "date", date(2026, 10, 16)),
        ("모집기간 : 2026.09.25 ~ 2026.10.08", "date", date(2026, 10, 8)),
        ("11월 1일부터 근무 가능하신 분", "none", None),
        ("접수 마감 1/10", "date", date(2027, 1, 10)),
    ],
)
def test_deadline(text, kind, day):
    assert find_deadline(text, POSTED) == (kind, day)


# ───────────── 저장·분류·매칭 ─────────────


def _raw(sid, title, body="", days_ago=0):
    return RawPosting(
        source="moreden",
        source_id=sid,
        url=f"https://example.com/{sid}",
        title=title,
        body=body,
        posted_at=config.now() - timedelta(days=days_ago),
    )


def test_save_and_match(tmp_db):
    pid, new = db.save_raw(
        _raw("1", "[서울 강남] 소아치과 전문의 원장님 모십니다", "주 4.5일, 세후 1500\n마감: 채용시"),
        source_kind="local_board",
        dentist_only=True,
        default_inst_type=None,
    )
    assert new
    _, again = db.save_raw(_raw("1", "[서울 강남] 소아치과 전문의 원장님 모십니다"), source_kind="local_board", dentist_only=True, default_inst_type=None)
    assert not again
    p = db.get_posting(pid)
    assert p.specialties == ["pedo"]
    assert p.region_labels == ["서울 강남구"]
    assert p.inst_type == "dental_clinic"

    f = settings_store.load()["filters"]
    assert match(p, f).ok
    f["specialties"] = ["ortho"]
    assert not match(p, f).ok
    f["include_keywords"] = ["소아치과"]
    assert match(p, f).ok
    f["include_keywords"] = []
    f["specialties"] = ["pedo"]
    f["regions"] = ["부산"]
    r = match(p, f)
    assert not r.ok and r.reasons[0].startswith("지역")
    f["regions"] = ["서울 강남구"]
    f["exclude_keywords"] = ["세후"]
    assert not match(p, f).ok


def test_settings_validation(tmp_db):
    s = settings_store.load()
    s["filters"]["regions"] = ["서울 강남구", "없는곳", "부산"]
    s["notify"]["times"] = ["25:00", "08:30", "08:30"]
    s["filters"]["include_keywords"] = "임상교수, 진료교수\n전임의"
    saved = settings_store.save(s)
    assert saved["filters"]["regions"] == ["서울 강남구", "부산"]
    assert saved["notify"]["times"] == ["08:30"]
    assert saved["filters"]["include_keywords"] == ["임상교수", "진료교수", "전임의"]


def test_password(tmp_db):
    assert not settings_store.has_password()
    with pytest.raises(ValueError):
        settings_store.set_password("short")
    settings_store.set_password("correct horse")
    assert settings_store.check_password("correct horse")
    assert not settings_store.check_password("wrong")


def test_secrets_env_override(tmp_db, monkeypatch):
    settings_store.update_secrets({"moreden_id": "dbuser", "moreden_pw": "dbpw"})
    assert settings_store.secrets()["moreden_id"] == "dbuser"
    monkeypatch.setenv("MOREDEN_ID", "envuser")
    assert settings_store.secrets()["moreden_id"] == "envuser"
    assert settings_store.secret_sources()["moreden_id"] == "env"
    settings_store.update_secrets({"moreden_pw": "__clear__"})
    assert settings_store.secrets()["moreden_pw"] == ""


def test_dedup_and_reminders(tmp_db):
    from dentmoa import pipeline

    a, _ = db.save_raw(_raw("10", "[부산] 해운대 OO치과 봉직의 구합니다", "마감 ~" + (config.now() + timedelta(days=2)).strftime("%m.%d")),
                       source_kind="local_board", dentist_only=True, default_inst_type=None)
    raw_b = _raw("20", "[부산] 해운대 OO치과 봉직의 구합니다!!")
    raw_b.source = "dentphoto"
    b, _ = db.save_raw(raw_b, source_kind="local_board", dentist_only=True, default_inst_type=None)
    pipeline._dedup([a, b])
    assert db.get_posting(b).dup_of == a
    assert db.get_posting(a).dup_of is None
    assert db.get_posting(a).deadline is not None


def test_setup_code(tmp_db, monkeypatch):
    monkeypatch.delenv("DENTMOA_SETUP_CODE", raising=False)
    code = settings_store.setup_code()
    assert len(code) == 6 and code.isdigit()
    assert settings_store.setup_code() == code  # 한 번 만들면 그대로
    assert settings_store.check_setup_code(f" {code} ")
    assert not settings_store.check_setup_code("")
    monkeypatch.setenv("DENTMOA_SETUP_CODE", "424242")
    assert settings_store.check_setup_code("424242")


def test_short_admin_password_env_does_not_crash(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    settings_store.init_password_from_env()
    assert not settings_store.has_password()


def test_uncertain_defaults_do_not_filter(tmp_db):
    """직위 단어가 없는 병원 공고는 '봉직의' 기본값 때문에 교수직 조건에서 빠지면 안 된다."""
    raw = RawPosting("alio", "a1", "https://x/a1", "소아치과 전문의 채용", "근무지: 서울", posted_at=config.now(),
                     institution_hint="서울대학교치과병원")
    pid, _ = db.save_raw(raw, source_kind="aggregator", dentist_only=False, default_inst_type=None)
    p = db.get_posting(pid)
    assert "positions" in p.uncertain
    f = settings_store.load()["filters"]
    f["positions"] = ["faculty", "clinical_professor", "fellow"]
    f["specialties"] = ["pedo"]
    assert match(p, f).ok

    # 동네 게시판 글의 '치과의원' 은 확실한 값 → 치과의원을 끄면 걸러진다
    pid2, _ = db.save_raw(_raw("2", "[서울] 원장님 모십니다"), source_kind="local_board", dentist_only=True, default_inst_type=None)
    p2 = db.get_posting(pid2)
    f["positions"] = list(settings_store.DEFAULT_SETTINGS["filters"]["positions"])
    f["specialties"] = ["gp"]
    f["inst_types"] = ["dental_univ_hospital"]
    assert not match(p2, f).ok
