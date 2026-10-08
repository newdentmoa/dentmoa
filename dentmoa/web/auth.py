"""처음 비밀번호 정하기, 로그인, 로그아웃, 비밀번호 바꾸기."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import settings_store
from .security import client_ip, is_logged_in, limiter, login_user, logout_user, safe_next

bp = Blueprint("auth", __name__)

MIN_LENGTH = 8


def _check_new_password(pw: str, pw2: str) -> str | None:
    """새 비밀번호 확인. 문제가 있으면 오류 메시지."""
    if len(pw) < MIN_LENGTH:
        return f"비밀번호는 {MIN_LENGTH}자 이상으로 정해 주세요."
    if pw != pw2:
        return "두 번 입력한 비밀번호가 서로 달라요."
    return None


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if settings_store.has_password():
        return redirect(url_for("postings.index") if is_logged_in() else url_for("auth.login"))
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        error = _check_new_password(pw, request.form.get("password2", ""))
        if not error:
            settings_store.set_password(pw)
            login_user()
            flash("비밀번호를 정했어요. 이제 '계정·연결'에서 사이트 계정과 알림을 설정해 주세요.", "ok")
            return redirect(url_for("accounts.index"))
    return render_template("setup.html", error=error), (400 if error else 200)


@bp.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"), url_for("postings.index"))
    if is_logged_in():
        return redirect(nxt)
    error = None
    status = 200
    if request.method == "POST":
        ip = client_ip()
        lim = limiter()
        wait = lim.remaining(ip)
        if wait:
            error = f"로그인을 너무 많이 실패했어요. {(wait + 59) // 60}분 뒤에 다시 시도해 주세요."
            status = 429
        elif settings_store.check_password(request.form.get("password", "")):
            lim.success(ip)
            login_user()
            return redirect(nxt)
        else:
            lim.fail(ip)
            wait = lim.remaining(ip)
            if wait:
                error = f"비밀번호가 틀렸어요. 너무 많이 실패해서 {(wait + 59) // 60}분 동안 로그인할 수 없어요."
                status = 429
            else:
                error = "비밀번호가 틀렸어요."
                status = 401
    return render_template("login.html", error=error, next=nxt), status


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user()
    flash("로그아웃했어요.", "info")
    return redirect(url_for("auth.login"))


@bp.post("/password")
def change_password():
    current = request.form.get("current", "")
    pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
    ip = client_ip()
    if limiter().remaining(ip):
        flash("비밀번호를 너무 많이 틀렸어요. 10분 뒤에 다시 시도해 주세요.", "error")
    elif not settings_store.check_password(current):
        limiter().fail(ip)
        flash("지금 비밀번호가 맞지 않아요.", "error")
    elif error := _check_new_password(pw, pw2):
        flash(error, "error")
    else:
        settings_store.set_password(pw)
        login_user()  # 다른 기기의 로그인은 풀리고, 이 기기는 그대로
        flash("비밀번호를 바꿨어요. 다른 기기에서는 다시 로그인해야 해요.", "ok")
    return redirect(url_for("status.index") + "#password")
