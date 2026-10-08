"""브라우저(Chromium)로 사이트 읽기 — 자바스크립트로 그려지는 사이트(모어덴 등)용.

- 로그인 상태(쿠키)를 data/browser/<출처> 에 보관해서 매번 로그인하지 않는다.
- 사이트가 화면을 그리면서 받아오는 JSON 응답도 모아 두었다가(responses) 글 목록을 찾는 데 쓴다.
"""

from __future__ import annotations

import json
import random
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from .. import config
from .base import USER_AGENT, LoginError, SourceError


@dataclass
class CapturedJSON:
    url: str
    data: object


@dataclass
class BrowserSession:
    page: object
    context: object
    captured: list[CapturedJSON] = field(default_factory=list)

    def human_pause(self, lo: float = 0.8, hi: float = 2.0) -> None:
        self.page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


@contextmanager
def open_browser(key: str, *, capture_json: str | None = None) -> Iterator[BrowserSession]:
    """capture_json: 이 정규식에 맞는 주소의 JSON 응답을 모은다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise SourceError("브라우저 기능(playwright)이 설치되지 않았습니다.") from e

    profile = config.BROWSER_DIR / key
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(profile),
                headless=True,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
        except Exception as e:
            raise SourceError(f"브라우저를 시작하지 못했습니다: {e}") from e
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        sess = BrowserSession(page=page, context=ctx)
        if capture_json:
            rx = re.compile(capture_json)

            def on_response(resp):
                try:
                    if not rx.search(resp.url):
                        return
                    ctype = resp.headers.get("content-type", "")
                    if "json" not in ctype:
                        return
                    sess.captured.append(CapturedJSON(resp.url, resp.json()))
                except Exception:
                    pass

            page.on("response", on_response)
        try:
            yield sess
        finally:
            ctx.close()


USER_SELECTORS = [
    "input[type=email]",
    "input[name*=email i]",
    "input[name*=id i]:not([type=hidden]):not([type=password])",
    "input[name*=user i]:not([type=hidden])",
    "input[name*=login i]:not([type=hidden]):not([type=password])",
    "input[placeholder*=아이디]",
    "input[placeholder*=이메일]",
    "input[type=text]",
    "input:not([type])",
]


def generic_login(sess: BrowserSession, login_url: str, user: str, password: str, *, success_check) -> None:
    """로그인 화면에서 아이디·비밀번호 칸을 찾아 입력하고 제출한다.

    success_check(page) -> bool : 로그인이 됐는지 확인하는 함수
    """
    page = sess.page
    page.goto(login_url, wait_until="domcontentloaded")
    sess.human_pause()
    # '아이디로 로그인' 같은 버튼이 있으면 누른다 (간편로그인 버튼만 보이는 화면 대비)
    for label in ("아이디로 로그인", "이메일로 로그인", "ID 로그인", "아이디 로그인", "이메일 로그인", "일반 로그인"):
        try:
            btn = page.get_by_text(label, exact=False).first
            if btn and btn.is_visible(timeout=800):
                btn.click()
                sess.human_pause()
                break
        except Exception:
            continue
    pw_box = page.locator("input[type=password]").first
    try:
        pw_box.wait_for(state="visible", timeout=10000)
    except Exception as e:
        raise LoginError("로그인 화면에서 비밀번호 칸을 찾지 못했습니다.") from e
    user_box = None
    for sel in USER_SELECTORS:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 5)):
            cand = loc.nth(i)
            try:
                if cand.is_visible():
                    user_box = cand
                    break
            except Exception:
                continue
        if user_box:
            break
    if user_box is None:
        raise LoginError("로그인 화면에서 아이디 칸을 찾지 못했습니다.")
    user_box.click()
    user_box.type(user, delay=random.randint(40, 110))
    sess.human_pause(0.3, 0.8)
    pw_box.click()
    pw_box.type(password, delay=random.randint(40, 110))
    sess.human_pause(0.3, 0.8)
    pw_box.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    sess.human_pause(1.5, 3.0)
    if not success_check(page):
        raise LoginError("로그인에 실패했습니다. 아이디와 비밀번호를 확인해 주세요.")


# ──────────────────────────── JSON 에서 글 목록 찾기 ────────────────────────────

ID_KEYS = ("id", "recruitId", "recruit_id", "postId", "post_id", "articleId", "seq", "no", "idx", "uid", "boardId")
TITLE_KEYS = ("title", "subject", "recruitTitle", "postTitle", "name")
BODY_KEYS = ("content", "contents", "body", "description", "detail", "text", "html", "recruitContent")
REGION_KEYS = ("region", "regionName", "area", "areaName", "location", "address", "addr", "sido", "city", "district", "workPlace")
DATE_KEYS = ("createdAt", "created_at", "regDate", "reg_date", "postedAt", "publishedAt", "writeDate", "date", "createDate", "updatedAt")
INST_KEYS = ("hospitalName", "clinicName", "companyName", "hospital", "clinic", "company", "orgName", "institution")


def _pick(d: dict, keys) -> object:
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
        if k.lower() in lower and lower[k.lower()] not in (None, ""):
            return lower[k.lower()]
    return None


def _flatten_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return " ".join(_flatten_text(x) for x in v.values() if isinstance(x, (str, dict, list)))
    if isinstance(v, list):
        return " ".join(_flatten_text(x) for x in v)
    return str(v)


def records_from_json(data, *, min_items: int = 2) -> list[dict]:
    """JSON 안에서 '제목과 번호가 있는 객체들의 목록'을 찾는다."""
    found: list[dict] = []

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, list) and len(node) >= min_items and all(isinstance(x, dict) for x in node[:5]):
            hits = [x for x in node if _pick(x, TITLE_KEYS) and _pick(x, ID_KEYS) is not None]
            if len(hits) >= max(1, len(node) // 2):
                for x in hits:
                    found.append(
                        {
                            "id": str(_pick(x, ID_KEYS)),
                            "title": _flatten_text(_pick(x, TITLE_KEYS)).strip(),
                            "body": _flatten_text(_pick(x, BODY_KEYS)),
                            "region": _flatten_text(_pick(x, REGION_KEYS)),
                            "date": _flatten_text(_pick(x, DATE_KEYS)),
                            "institution": _flatten_text(_pick(x, INST_KEYS)),
                            "raw": x,
                        }
                    )
                return
        if isinstance(node, dict):
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:50]:
                walk(v, depth + 1)

    walk(data)
    return found


def find_record(data, record_id: str) -> dict | None:
    """JSON 안에서 번호가 record_id 인 객체를 찾아 글 정보로 돌려준다 (상세 화면용)."""

    def walk(node, depth=0):
        if depth > 7:
            return None
        if isinstance(node, dict):
            rid = _pick(node, ID_KEYS)
            if rid is not None and str(rid) == str(record_id) and (_pick(node, TITLE_KEYS) or _pick(node, BODY_KEYS)):
                return {
                    "id": str(rid),
                    "title": _flatten_text(_pick(node, TITLE_KEYS)).strip(),
                    "body": _flatten_text(_pick(node, BODY_KEYS)),
                    "region": _flatten_text(_pick(node, REGION_KEYS)),
                    "date": _flatten_text(_pick(node, DATE_KEYS)),
                    "institution": _flatten_text(_pick(node, INST_KEYS)),
                    "raw": node,
                }
            for v in node.values():
                r = walk(v, depth + 1)
                if r:
                    return r
        elif isinstance(node, list):
            for v in node[:200]:
                r = walk(v, depth + 1)
                if r:
                    return r
        return None

    return walk(data)


def dump_captured(sess: BrowserSession, ctx, prefix: str) -> None:
    """문제 해결용: 모은 JSON 을 debug 폴더에 저장."""
    for i, c in enumerate(sess.captured[:30]):
        ctx.dump(f"{prefix}-json-{i:02d}.json", json.dumps({"url": c.url, "data": c.data}, ensure_ascii=False, indent=1)[:500000])
