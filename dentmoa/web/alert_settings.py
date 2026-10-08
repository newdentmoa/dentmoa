"""알림 조건 화면: 받을 공고의 조건, 알림 시각·채널, 수집 설정."""

from __future__ import annotations

import copy
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import regions, settings_store
from ..settings_store import SOURCE_KEYS, TIME_RE
from ..taxonomy import INST_TYPES, POSITIONS, POST_KINDS, SPECIALTIES, WORK_TYPES
from .helpers import source_description, source_label
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


def parse_form(form, current: dict) -> tuple[dict, list[str]]:
    """화면 입력 → 설정. 돌려주는 값: (설정, 오류 목록)"""
    s = copy.deepcopy(current)
    errors: list[str] = []

    f = s["filters"]
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
    for key in ("telegram", "email", "send_empty", "reminder_enabled", "reminder_starred_only"):
        n[key] = form.get(key) == "1"
    n["reminder_days"] = form.get("reminder_days", n["reminder_days"])

    c = s["collect"]
    c["sources"] = {k: form.get(f"source_{k}") == "1" for k in SOURCE_KEYS}
    for key in ("backfill_days", "min_delay_sec", "max_delay_sec"):
        c[key] = form.get(key, c[key])
    return s, errors


def _render(s: dict, errors: list[str] | None = None, status: int = 200):
    return render_template(
        "settings.html",
        s=s,
        errors=errors or [],
        tx_fields=[
            ("inst_types", "기관 종류", INST_TYPES),
            ("positions", "직위", POSITIONS),
            ("specialties", "분과", SPECIALTIES),
            ("work_types", "근무 형태", WORK_TYPES),
        ],
        post_kinds=POST_KINDS,
        region_options=regions.all_options(),
        selected_regions=set(s["filters"]["regions"]),
        times=(s["notify"]["times"] + [""] * MAX_TIMES)[:MAX_TIMES],
        source_options=[(k, source_label(k), source_description(k)) for k in SOURCE_KEYS],
        preview=matching_count(s["filters"]) if not errors else None,
    ), status


@bp.route("/settings", methods=["GET", "POST"])
def index():
    current = settings_store.load()
    if request.method == "GET":
        return _render(current)

    new, errors = parse_form(request.form, current)
    if errors:
        return _render(new, errors, 400)
    settings_store.save(new)
    try:
        from dentmoa import scheduler

        scheduler.reschedule()
    except Exception:
        pass  # 예약 기능이 아직 없거나 서버가 따로 돌 때
    flash("저장했습니다.", "ok")
    return redirect(url_for("settings.index"))
