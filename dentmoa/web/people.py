"""보는 사람 바꾸기 (SH·JY). 고른 사람은 이 기기(브라우저)의 로그인 정보에만 기억한다.

공고 목록의 '조건에 맞는 공고', 관심(★), 알림 조건 화면이 고른 사람 기준으로 바뀐다.
"""

from __future__ import annotations

from flask import Blueprint, redirect, request, session, url_for

from .. import settings_store
from .security import safe_next

bp = Blueprint("people", __name__)


@bp.post("/person/<key>")
def switch(key: str):
    if any(p["key"] == key for p in settings_store.load()["people"]):
        session["person"] = key
    return redirect(safe_next(request.form.get("next"), url_for("postings.index")))
