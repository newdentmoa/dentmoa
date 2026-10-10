"""계정·연결 화면: 사이트 계정, 텔레그램, 메일, 연결 테스트.

텔레그램 봇 토큰과 보내는 메일 계정은 함께 쓰고, 받는 쪽(텔레그램 채팅 ID·받는 메일 주소)은 사람(SH·JY)마다 따로다.
저장된 비밀번호·토큰은 화면에 절대 다시 보여주지 않는다 (저장됨 여부만 표시).
"""

from __future__ import annotations

import re

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import settings_store
from ..settings_store import SECRET_DEFAULTS, SOURCE_KEYS
from .helpers import source_label

bp = Blueprint("accounts", __name__)

CLEAR = "__clear__"

# 화면의 칸: (키, 이름, 입력 종류, 도움말)
SECTIONS: list[dict] = [
    {
        "key": "moreden",
        "title": "모어덴",
        "fields": [
            ("moreden_id", "아이디", "text", "카카오로만 가입했다면 모어덴에서 아이디·비밀번호 로그인을 먼저 만들어 주세요."),
            ("moreden_pw", "비밀번호", "password", ""),
        ],
    },
    {
        "key": "dentphoto",
        "title": "덴트포토",
        "fields": [
            ("dentphoto_id", "아이디", "text", ""),
            ("dentphoto_pw", "비밀번호", "password", ""),
        ],
    },
    {
        "key": "telegram",
        "title": "텔레그램",
        "fields": [
            ("telegram_token", "봇 토큰", "password", "BotFather가 알려준 '123456789:ABC…' 모양의 긴 글자"),
        ],
    },
    {
        "key": "email",
        "title": "메일",
        "fields": [
            ("smtp_host", "SMTP 서버", "text", "Gmail이면 그대로 두세요 (smtp.gmail.com)."),
            ("smtp_port", "포트", "text", "Gmail이면 그대로 두세요 (587)."),
            ("smtp_user", "보내는 Gmail 주소", "email", ""),
            ("smtp_password", "앱 비밀번호", "password", "Gmail 로그인 비밀번호가 아니라 16자리 '앱 비밀번호'예요."),
        ],
    },
]
SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}
# 사람마다 따로인 칸: (키, 화면 묶음, 이름, 도움말)
PERSON_FIELDS = [
    ("telegram_chat_id", "telegram", "채팅 ID", "모르면 비워 두고 '채팅 ID 자동 찾기'를 누르세요."),
    ("email_to", "email", "받는 주소", "여러 곳으로 받으려면 쉼표(,)로 구분하세요."),
]
CHECKABLE_SOURCES = list(SOURCE_KEYS)

APP_PASSWORD_RE = re.compile(r"[a-z]{16}")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _status(key: str, sources: dict[str, str]) -> dict:
    src = sources.get(key, "")
    if src == "env":
        return {"text": "환경변수로 설정됨", "cls": "st-env"}
    if src == "db":
        return {"text": "저장됨 ✓", "cls": "st-ok"}
    if key in SECRET_DEFAULTS:
        return {"text": f"기본값 사용 ({SECRET_DEFAULTS[key]})", "cls": "st-default"}
    return {"text": "비어 있음", "cls": "st-empty"}


def _validate(key: str, value: str) -> str | None:
    if key == "smtp_port" and not (value.isdigit() and 0 < int(value) < 65536):
        return "포트는 숫자로 적어 주세요 (보통 587 또는 465)."
    if key == "smtp_user" and not EMAIL_RE.match(value):
        return "보내는 Gmail 주소를 확인해 주세요."
    if key == "email_to" and not all(EMAIL_RE.match(a) for a in re.split(r"[,;\s]+", value) if a):
        return "받는 주소를 확인해 주세요."
    if key == "telegram_chat_id" and not re.fullmatch(r"-?\d+|@\w{4,}", value):
        return "채팅 ID는 숫자예요. 모르면 비워 두고 '채팅 ID 자동 찾기'를 누르세요."
    return None


def _person_rows(settings: dict) -> list[dict]:
    """사람마다 채팅 ID·받는 주소의 상태 (값은 보여 주지 않는다)."""
    rows = []
    for p in settings["people"]:
        fields = {}
        for key, group, label, help_ in PERSON_FIELDS:
            src = settings_store.target_source(settings, p, key)
            fields[group] = {"key": key, "label": label, "help": help_, "status": _status(key, {key: src}), "env": src == "env"}
        rows.append({"key": p["key"], "name": p["name"], **fields})
    return rows


@bp.get("/accounts")
def index():
    settings = settings_store.load()
    sources = settings_store.secret_sources()
    sections = [
        {
            **sec,
            "rows": [
                {"key": k, "label": label, "type": typ, "help": help_, "status": _status(k, sources),
                 "env": sources.get(k) == "env"}
                for k, label, typ, help_ in sec["fields"]
            ],
        }
        for sec in SECTIONS
    ]
    return render_template(
        "accounts.html",
        sections=sections,
        people=_person_rows(settings),
        many=len(settings["people"]) > 1,
        check_sources=[(k, source_label(k)) for k in CHECKABLE_SOURCES],
    )


@bp.post("/accounts")
def save():
    sec = SECTION_BY_KEY.get(request.form.get("section", ""))
    if sec is None:
        flash("알 수 없는 항목이에요.", "error")
        return redirect(url_for("accounts.index"))
    clear = set(request.form.getlist("clear"))
    values: dict[str, str] = {}
    for key, label, _typ, _help in sec["fields"]:
        if key in clear:
            values[key] = CLEAR
            continue
        val = (request.form.get(key) or "").strip()
        if not val:
            continue  # 빈칸 = 그대로 두기
        if key == "smtp_password" and APP_PASSWORD_RE.fullmatch(compact := re.sub(r"\s+", "", val)):
            val = compact  # 'abcd efgh ijkl mnop' → 붙여서 저장 (휴대폰에서 복사한 특수 공백 포함)
        if error := _validate(key, val):
            flash(error, "error")
            return redirect(url_for("accounts.index") + f"#{sec['key']}")
        values[key] = val

    if not values:
        flash("바뀐 내용이 없어요. 새로 입력한 칸만 저장돼요.", "info")
    else:
        settings_store.update_secrets(values)
        flash(f"{sec['title']} 정보를 저장했습니다.", "ok")
    return redirect(url_for("accounts.index") + f"#{sec['key']}")


@bp.post("/accounts/person/<key>")
def save_person(key: str):
    """한 사람의 채팅 ID 또는 받는 주소 저장 (빈칸이면 그대로, '지우기'면 지움)."""
    settings = settings_store.load()
    group = request.form.get("section", "telegram")
    p = next((x for x in settings["people"] if x["key"] == key), None)
    field = next((f for f in PERSON_FIELDS if f[1] == group), None)
    if p is None or field is None:
        flash("알 수 없는 항목이에요.", "error")
        return redirect(url_for("accounts.index"))
    name = field[0]
    if name in request.form.getlist("clear"):
        p[name] = ""
    else:
        val = (request.form.get(name) or "").strip()
        if not val:
            flash("바뀐 내용이 없어요. 새로 입력한 칸만 저장돼요.", "info")
            return redirect(url_for("accounts.index") + f"#{group}")
        if error := _validate(name, val):
            flash(error, "error")
            return redirect(url_for("accounts.index") + f"#{group}")
        p[name] = val
    settings_store.save(settings)
    flash(f"{p['name']}의 {field[2]}를 저장했습니다.", "ok")
    return redirect(url_for("accounts.index") + f"#{group}")


def _person_from_form(settings: dict) -> dict | None:
    key = request.form.get("person")
    return next((x for x in settings["people"] if x["key"] == key), None) if key else settings["people"][0]


# ──────────────────────────── 테스트 버튼 ────────────────────────────


def _error_text(e: Exception) -> str:
    if isinstance(e, (ImportError, NotImplementedError)):
        return "아직 이 기능이 준비되지 않았어요. 프로그램을 최신으로 업데이트해 주세요."
    if isinstance(e, KeyError):
        return "이 출처의 수집기를 찾지 못했어요."
    return str(e) or e.__class__.__name__


@bp.post("/accounts/telegram/test")
def telegram_test():
    settings = settings_store.load()
    p = _person_from_form(settings)
    if p is None:
        return redirect(url_for("accounts.index") + "#telegram")
    try:
        from dentmoa.notify import telegram

        telegram.send_test(settings_store.person_secrets(settings, p))
    except Exception as e:
        flash(f"{p['name']} 텔레그램 테스트 실패: {_error_text(e)}", "error")
    else:
        flash(f"{p['name']}에게 테스트 메시지를 보냈어요. {p['name']}의 텔레그램을 확인해 보세요.", "ok")
    return redirect(url_for("accounts.index") + "#telegram")


@bp.post("/accounts/telegram/find-chat")
def telegram_find_chat():
    settings = settings_store.load()
    p = _person_from_form(settings)
    token = settings_store.secrets().get("telegram_token", "")
    if p is None:
        return redirect(url_for("accounts.index") + "#telegram")
    if not token:
        flash("먼저 봇 토큰을 저장해 주세요.", "error")
        return redirect(url_for("accounts.index") + "#telegram")
    # 다른 사람 것으로 이미 저장한 채팅은 건너뛴다 (아내 휴대폰에서 '시작'을 누른 뒤 찾을 때)
    others = [settings_store.person_secrets(settings, x)["telegram_chat_id"] for x in settings["people"] if x["key"] != p["key"]]
    try:
        from dentmoa.notify import telegram

        found = telegram.find_chat(token, exclude=others)
    except Exception as e:
        flash(f"채팅 ID 찾기 실패: {_error_text(e)}", "error")
    else:
        if found:
            chat_id, tg_name = found
            p["telegram_chat_id"] = chat_id
            settings_store.save(settings)
            who = f"텔레그램 이름 '{tg_name}'" if tg_name else "채팅"
            flash(f"{who}의 채팅 ID를 찾아서 {p['name']}에게 저장했어요. 이제 '{p['name']} 테스트 메시지'를 눌러 보세요.", "ok")
        else:
            flash(
                f"채팅 ID를 찾지 못했어요. {p['name']}의 휴대폰 텔레그램에서 봇을 열어 '시작'(/start)을 누르거나 "
                "아무 메시지나 보낸 뒤 다시 눌러 주세요.",
                "error",
            )
    return redirect(url_for("accounts.index") + "#telegram")


@bp.post("/accounts/email/test")
def email_test():
    settings = settings_store.load()
    p = _person_from_form(settings)
    if p is None:
        return redirect(url_for("accounts.index") + "#email")
    try:
        from dentmoa.notify import mailer

        mailer.send_test(settings_store.person_secrets(settings, p))
    except Exception as e:
        flash(f"{p['name']} 메일 테스트 실패: {_error_text(e)}", "error")
    else:
        flash(f"{p['name']}에게 테스트 메일을 보냈어요. 받은편지함(안 보이면 스팸함)을 확인해 보세요.", "ok")
    return redirect(url_for("accounts.index") + "#email")


@bp.post("/accounts/source/<key>/check")
def source_check(key: str):
    if key not in CHECKABLE_SOURCES:
        flash("알 수 없는 출처예요.", "error")
        return redirect(url_for("accounts.index") + "#check")
    label = source_label(key)
    try:
        from dentmoa import pipeline

        msg = pipeline.check_source(key)
    except Exception as e:
        flash(f"{label} 연결 테스트 실패: {_error_text(e)}", "error")
    else:
        flash(f"{label}: {msg or '연결 성공'}", "ok")
    return redirect(url_for("accounts.index") + "#check")
