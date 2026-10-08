"""로그인 확인, CSRF 토큰, 로그인 시도 제한."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, request, session, url_for

from .. import db, settings_store

CSRF_FIELD = "_csrf"
MAX_FAILURES = 5
LOCK_SECONDS = 600

# 로그인하지 않아도 열 수 있는 화면
PUBLIC_ENDPOINTS = {"static", "healthz"}
AUTH_ENDPOINTS = {"auth.login", "auth.setup"}


class LoginLimiter:
    """IP별 로그인 실패 횟수를 메모리에 세고, 너무 많으면 잠시 막는다."""

    def __init__(self, max_failures: int = MAX_FAILURES, lock_seconds: int = LOCK_SECONDS, clock=time.monotonic):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}  # ip → (실패 횟수, 마지막 실패 시각)
        self._locked: dict[str, float] = {}  # ip → 풀리는 시각

    def remaining(self, ip: str) -> int:
        """잠겨 있으면 남은 초, 아니면 0."""
        with self._lock:
            until = self._locked.get(ip)
            if until is None:
                return 0
            left = until - self._clock()
            if left <= 0:
                self._locked.pop(ip, None)
                return 0
            return math.ceil(left)

    def fail(self, ip: str) -> None:
        now = self._clock()
        with self._lock:
            count, last = self._fails.get(ip, (0, now))
            if now - last > self.lock_seconds:
                count = 0  # 오래전 실패는 잊는다
            count += 1
            if count >= self.max_failures:
                self._fails.pop(ip, None)
                self._locked[ip] = now + self.lock_seconds
            else:
                self._fails[ip] = (count, now)
            self._prune(now)

    def success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
            self._locked.pop(ip, None)

    def _prune(self, now: float) -> None:
        if len(self._fails) + len(self._locked) < 1000:
            return
        self._fails = {k: v for k, v in self._fails.items() if now - v[1] <= self.lock_seconds}
        self._locked = {k: v for k, v in self._locked.items() if v > now}


def limiter(app: Flask | None = None) -> LoginLimiter:
    from flask import current_app

    return (app or current_app).extensions["dentmoa_login_limiter"]


def client_ip() -> str:
    return request.remote_addr or "?"


# ──────────────────────────── 로그인 상태 ────────────────────────────


def _password_fingerprint() -> str:
    """비밀번호가 바뀌면 달라지는 값. 비밀번호를 바꾸면 다른 기기의 로그인이 풀린다."""
    h = (db.kv_get("auth", {}) or {}).get("hash", "")
    return hashlib.sha256(h.encode()).hexdigest()[:24] if h else ""


def login_user() -> None:
    session.clear()
    session.permanent = True
    session["auth"] = _password_fingerprint()
    session["csrf"] = secrets.token_urlsafe(32)


def logout_user() -> None:
    session.clear()


def is_logged_in() -> bool:
    token = session.get("auth")
    return bool(token) and hmac.compare_digest(str(token), _password_fingerprint())


# ──────────────────────────── CSRF ────────────────────────────


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = session["csrf"] = secrets.token_urlsafe(32)
    return token


def _csrf_ok() -> bool:
    sent = request.form.get(CSRF_FIELD) or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("csrf") or ""
    return bool(sent and expected) and hmac.compare_digest(str(sent), str(expected))


def wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def safe_next(target: str | None, default: str) -> str:
    """로그인 후·버튼 처리 후 돌아갈 주소. 이 사이트 안의 주소만 허용한다."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return default  # 브라우저는 탭·줄바꿈을 지우므로 '/\t/다른사이트' 같은 주소를 막는다
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return default
    return target


# ──────────────────────────── 모든 요청 앞에서 확인 ────────────────────────────


def _guard():
    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS:
        return None

    if request.method == "POST" and not _csrf_ok():
        abort(400)

    if not settings_store.has_password():
        g.logged_in = False
        if endpoint != "auth.setup":
            return redirect(url_for("auth.setup"))
        return None

    g.logged_in = is_logged_in()
    if g.logged_in or endpoint in AUTH_ENDPOINTS:
        return None
    if wants_json():
        return jsonify(ok=False, error="로그인이 필요해요. 페이지를 새로고침해 주세요."), 401
    nxt = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=nxt) if nxt and nxt != "/" else url_for("auth.login"))


def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
    )
    if request.endpoint != "static":
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def init_app(app: Flask) -> None:
    app.extensions["dentmoa_login_limiter"] = LoginLimiter()
    app.before_request(_guard)
    app.after_request(_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["CSRF_FIELD"] = CSRF_FIELD
