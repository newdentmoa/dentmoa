"""덴트모아 웹 대시보드 (Flask).

create_app() 으로 앱을 만든다. 서버 실행(waitress)은 __main__.py 의 serve 가 맡는다.

환경변수
- DENTMOA_SECRET_KEY      로그인 쿠키 서명 키 (없으면 DB에 만들어 보관)
- DENTMOA_SECURE_COOKIES  '1' 이면 https 에서만 로그인 쿠키 전송
- DENTMOA_BEHIND_PROXY    '1' 이면 Caddy 같은 앞단 서버가 알려주는 접속 IP를 믿는다
- ADMIN_PASSWORD          처음 실행할 때 대시보드 비밀번호로 쓴다 (이미 있으면 무시)
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta

from flask import Flask, jsonify, render_template

from .. import db, settings_store
from . import helpers, security


def _secret_key() -> str:
    key = os.environ.get("DENTMOA_SECRET_KEY")
    if key:
        return key
    key = db.kv_get("flask_secret")
    if not key:
        key = secrets.token_hex(32)
        db.kv_set("flask_secret", key)
    return key


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=testing,
        SECRET_KEY=_secret_key(),
        SESSION_COOKIE_NAME="dentmoa_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("DENTMOA_SECURE_COOKIES") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),  # 주소에 수정 시각이 붙어 있어 길게 둬도 된다
    )
    if os.environ.get("DENTMOA_BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    settings_store.init_password_from_env()
    security.init_app(app)
    helpers.init_app(app)

    from . import accounts, alert_settings, auth, people, postings, status

    for mod in (auth, postings, alert_settings, accounts, status, people):
        app.register_blueprint(mod.bp)

    @app.get("/healthz")
    def healthz():
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    def _error(code: int, title: str, message: str):
        def handler(_e):
            if security.wants_json():  # 관심·숨기기 버튼(fetch)은 화면 대신 한글 메시지를 받는다
                return jsonify(ok=False, error=f"{title}. {message}"), code
            return render_template("error.html", code=code, title=title, message=message), code

        app.register_error_handler(code, handler)

    _error(400, "요청을 처리하지 못했어요", "화면이 오래 열려 있었거나 로그인 정보가 바뀌었어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.")
    _error(404, "찾을 수 없어요", "주소가 바뀌었거나 지워진 화면이에요.")
    _error(405, "직접 열 수 없는 주소예요", "화면의 버튼을 눌러서 이용해 주세요.")
    _error(413, "보낸 내용이 너무 커요", "입력한 내용을 줄여서 다시 시도해 주세요.")
    _error(500, "문제가 생겼어요", "잠시 뒤 다시 시도해 주세요. 계속되면 '상태' 화면의 실행 기록을 확인해 주세요.")
    return app
