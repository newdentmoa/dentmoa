"""알림 조건 화면.

위쪽: 보는 사람(SH·JY)의 이름, 받을 공고의 조건, 알림 방법 — 사람마다 따로.
아래쪽: 알림 시각과 수집 설정 — 모두 함께 쓴다 (공고는 한 번만 모은다).
사람 추가·지우기도 여기서 한다.
"""

from __future__ import annotations

import copy
import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .. import db, regions, settings_store
from ..settings_store import SOURCE_KEYS, TIME_RE
from ..taxonomy import INST_TYPES, POSITIONS, POST_KINDS, SPECIALTIES, WORK_TYPES
from .helpers import current_person, source_description, source_label
from .postings import matching_count

bp = Blueprint("settings", __name__)

MAX_TIMES = 4

# (설정 키, 고를 수 있는 값, 오류 메시지에 쓸 이름)
CHOICE_FIELDS = [
    ("post_kinds", POST_KINDS, "받을 글 종류를"),
    ("inst_types", INST_TYPES, "기관 종류를"),
    ("positions", POSITIONS, "직위를"),
    ("specialties", SPECIALTIES, "분과를"),
    ("work_types", WORK_TYPES, "근무 형태를"),
]


def split_keywords(text: str) -> list[str]:
    return list(dict.fromkeys(k.strip() for k in re.split(r"[,\n]", text or "") if k.strip()))[:100]


def normalize_regions(values: list[str]) -> list[str]:
    """'전국'이 있으면 전국만, 시·도 전체를 골랐으면 그 아래 시군구는 뺀다."""
    picked = list(dict.fromkeys(v.strip() for v in values if regions.is_valid_selection(v.strip())))
    if regions.NATIONWIDE in picked:
        return [regions.NATIONWIDE]
    whole = {v for v in picked if " " not in v}
    return [v for v in picked if " " not in v or v.split(" ", 1)[0] not in whole]


ALERT_CHECKS = ("telegram", "email", "send_empty", "reminder_enabled", "reminder_starred_only", "warnings")


def parse_form(form, current: dict, key: str) -> tuple[dict, list[str]]:
    """화면 입력 → 설정. key: 고친 사람. 돌려주는 값: (설정, 오류 목록)"""
    s = copy.deepcopy(current)
    errors: list[str] = []
    me = settings_store.person(s, key)

    name = settings_store.clean_name(form.get("name", me["name"]))
    if not name:
        errors.append("이름을 적어 주세요. (예: SH)")
    elif any(p["name"] == name for p in s["people"] if p["key"] != me["key"]):
        errors.append(f"'{name}'은(는) 이미 다른 사람의 이름이에요. 다른 이름을 적어 주세요.")
    else:
        me["name"] = name

    f = me["filters"]
    for key, choices, name in CHOICE_FIELDS:
        f[key] = [v for v in dict.fromkeys(form.getlist(key)) if v in choices]
        if not f[key]:
            errors.append(f"{name} 하나 이상 골라 주세요.")
    f["regions"] = normalize_regions(form.getlist("regions"))
    if not f["regions"]:
        errors.append("지역을 하나 이상 골라 주세요. 어디든 괜찮으면 '전국'을 고르세요.")
    for key in ("dentist_only", "include_uncertain_region", "include_hints"):
        f[key] = form.get(key) == "1"
    f["include_keywords"] = split_keywords(form.get("include_keywords", ""))
    f["exclude_keywords"] = split_keywords(form.get("exclude_keywords", ""))

    a = me["alerts"]
    for k in ALERT_CHECKS:
        a[k] = form.get(k) == "1"
    a["reminder_days"] = form.get("reminder_days", a["reminder_days"])

    n = s["notify"]
    times = [t.strip()[:5] for t in form.getlist("times") if t.strip()]
    if any(not TIME_RE.match(t) for t in times):
        errors.append("알림 시각은 '08:10'처럼 시:분으로 적어 주세요.")
    times = sorted({t for t in times if TIME_RE.match(t)})
    if not times:
        errors.append("알림 시각을 하나 이상 정해 주세요.")
    elif len(times) > MAX_TIMES:
        errors.append(f"알림 시각은 {MAX_TIMES}개까지 정할 수 있어요.")
    n["times"] = times

    c = s["collect"]
    c["sources"] = {k: form.get(f"source_{k}") == "1" for k in SOURCE_KEYS}
    for key in ("backfill_days", "min_delay_sec", "max_delay_sec"):
        c[key] = form.get(key, c[key])
    return s, errors


def _render(s: dict, key: str, errors: list[str] | None = None, status: int = 200):
    me = settings_store.person(s, key)
    return render_template(
        "settings.html",
        s=s,
        me=me,
        people=s["people"],
        can_add=len(s["people"]) < settings_store.MAX_PEOPLE,
        errors=errors or [],
        tx_fields=[
            ("inst_types", "기관 종류", INST_TYPES),
            ("positions", "직위", POSITIONS),
            ("specialties", "분과", SPECIALTIES),
            ("work_types", "근무 형태", WORK_TYPES),
        ],
        post_kinds=POST_KINDS,
        region_options=regions.all_options(),
        selected_regions=set(me["filters"]["regions"]),
        times=(s["notify"]["times"] + [""] * MAX_TIMES)[:MAX_TIMES],
        source_options=[(k, source_label(k), source_description(k)) for k in SOURCE_KEYS],
        preview=matching_count(me["filters"]) if not errors else None,
    ), status


@bp.route("/settings", methods=["GET", "POST"])
def index():
    current = settings_store.load()
    me = current_person(current)
    if request.method == "GET":
        return _render(current, me["key"])

    # 다른 기기에서 보는 사람을 바꿨어도 이 화면에서 연 사람의 설정을 고친다
    key = request.form.get("person") or me["key"]
    if not any(p["key"] == key for p in current["people"]):
        flash("그 사람은 지워졌어요. 다시 골라 주세요.", "error")
        return redirect(url_for("settings.index"))
    new, errors = parse_form(request.form, current, key)
    if errors:
        return _render(new, key, errors, 400)
    settings_store.save(new)
    try:
        from dentmoa import scheduler

        scheduler.reschedule()
    except Exception:
        pass  # 예약 기능이 아직 없거나 서버가 따로 돌 때
    flash(f"{settings_store.person(new, key)['name']}의 조건을 저장했습니다." if len(new["people"]) > 1 else "저장했습니다.", "ok")
    return redirect(url_for("settings.index"))


# ──────────────────────────── 사람 추가·지우기 ────────────────────────────


@bp.post("/settings/people/add")
def add_person():
    s = settings_store.load()
    if len(s["people"]) >= settings_store.MAX_PEOPLE:
        flash(f"받는 사람은 {settings_store.MAX_PEOPLE}명까지 정할 수 있어요.", "error")
        return redirect(url_for("settings.index"))
    key = settings_store.next_person_key(s)
    names = {p["name"] for p in s["people"]}
    name = next(f"사람{i}" for i in range(len(s["people"]) + 1, 100) if f"사람{i}" not in names)
    s["people"].append(settings_store.new_person(key, name, warnings=False))
    settings_store.save(s)
    session["person"] = key
    flash(f"'{name}'을(를) 추가했어요. 이름과 조건을 정해 저장하고, '계정·연결'에서 텔레그램이나 메일을 연결해 주세요.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/settings/people/<key>/delete")
def delete_person(key: str):
    s = settings_store.load()
    target = next((p for p in s["people"] if p["key"] == key), None)
    if target is None:
        return redirect(url_for("settings.index"))
    if len(s["people"]) < 2:
        flash("받는 사람이 한 명뿐이라 지울 수 없어요.", "error")
        return redirect(url_for("settings.index"))
    s["people"] = [p for p in s["people"] if p["key"] != key]
    settings_store.save(s)
    db.forget_person(key)
    session.pop("person", None)
    flash(f"'{target['name']}'을(를) 지웠어요. 그 사람의 조건·관심(★) 표시·알림 기록도 함께 지웠어요.", "ok")
    return redirect(url_for("settings.index"))
