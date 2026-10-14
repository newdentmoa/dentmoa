"""브라우저(Chromium)로 사이트 읽기 — 자바스크립트로 그려지는 사이트(모어덴 등)용.

- 로그인 상태(쿠키)를 data/browser/<출처> 에 보관해서 매번 로그인하지 않는다.
- 사이트가 화면을 그리면서 받아오는 JSON 응답도 모아 두었다가(responses) 글 목록을 찾는 데 쓴다.
- 메모리가 작은 서버(쓸 수 있는 메모리 약 412MB)에서 돌아가므로 가볍게 띄운다 (2026-10-14):
  프로세스를 줄이는 실행 옵션, 이미지·글꼴·동영상과 채팅 위젯·광고 추적은 받지 않는다.
  모어덴 로그인 화면 기준 메모리(PSS 합) 약 309MB·프로세스 7개 → 약 220MB·2개.
  그래도 메모리가 모자라면 서버 전체(대시보드·예약 실행)가 아니라 브라우저가 먼저 멈추도록 oom_score_adj 를 올린다.
"""

from __future__ import annotations

import json
import os
import random
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from .. import config
from .base import USER_AGENT, LoginError, SourceError, StructureError


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


# 프로세스 수와 메모리를 줄이는 실행 옵션. --disable-features 는 Playwright 가 이미 넣는 목록을 덮어쓰므로 쓰지 않는다.
LEAN_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--no-zygote",
    "--in-process-gpu",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--enable-features=NetworkServiceInProcess2",
    "--renderer-process-limit=1",
    "--disable-site-isolation-trials",
    "--disable-extensions",
    "--disable-background-networking",
    "--no-first-run",
    "--js-flags=--max-old-space-size=192",
]
# 글 목록·로그인에 필요 없는 것 (모어덴 로그인 화면은 이것들 없이도 그대로 그려진다 — 2026-10-14 확인)
BLOCKED_TYPES = frozenset({"image", "media", "font"})
BLOCKED_HOSTS = re.compile(
    r"(?:^|\.)(?:channel\.io|google-analytics\.com|googletagmanager\.com|doubleclick\.net|facebook\.(?:net|com)"
    r"|hotjar\.com|clarity\.ms|criteo\.(?:com|net)|wcs\.naver\.(?:net|com))$"
)
# 메모리가 모자랄 때 커널이 먼저 멈출 프로세스 점수 (높을수록 먼저). 덴트모아 본체(파이썬)는 그대로 둔다.
BROWSER_OOM_SCORE = "800"


def is_blocked(resource_type: str, url: str) -> bool:
    """받지 않을 요청인지 (이미지·글꼴·동영상, 채팅 위젯·광고 추적)."""
    if resource_type in BLOCKED_TYPES:
        return True
    host = re.sub(r"^[a-z]+://", "", url or "", flags=re.I).split("/", 1)[0].split(":", 1)[0].lower()
    return bool(BLOCKED_HOSTS.search(host))


def _browser_pids(root: int) -> list[int]:
    """root 의 자손 중 브라우저(크로미움) 프로세스."""
    children: dict[int, list[int]] = {}
    names: dict[int, bytes] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", "rb") as f:
                ppid = int(f.read().rsplit(b")", 1)[1].split()[1])
            with open(f"/proc/{d}/cmdline", "rb") as f:
                names[int(d)] = f.read()
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(d))
    out, todo = [], [root]
    while todo:
        for c in children.get(todo.pop(), []):
            todo.append(c)
            if b"chrom" in names.get(c, b"") or b"headless_shell" in names.get(c, b""):
                out.append(c)
    return out


def prefer_browser_for_oom() -> int:
    """메모리가 모자라면 브라우저가 먼저 멈추게 한다 (리눅스에서만, 실패해도 그냥 넘어간다). 바꾼 프로세스 수."""
    if not os.path.isdir("/proc"):
        return 0
    n = 0
    for pid in _browser_pids(os.getpid()):
        try:
            with open(f"/proc/{pid}/oom_score_adj", "w") as f:
                f.write(BROWSER_OOM_SCORE)
            n += 1
        except OSError:
            pass
    return n


@contextmanager
def open_browser(key: str, *, capture_json: str | None = None, timeout_ms: int = 30000) -> Iterator[BrowserSession]:
    """capture_json: 이 정규식에 맞는 주소의 JSON 응답을 모은다. timeout_ms: 화면 요소를 기다리는 기본 시간."""
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
                args=LEAN_ARGS,
            )
        except Exception as e:
            raise SourceError(f"브라우저를 시작하지 못했습니다: {e}") from e
        prefer_browser_for_oom()

        def route(r):
            try:
                if is_blocked(r.request.resource_type, r.request.url):
                    r.abort()
                else:
                    r.continue_()
            except Exception:
                pass

        ctx.route("**/*", route)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(timeout_ms)
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
    try:
        page.wait_for_selector("input[type=password]", state="attached", timeout=30000)
    except Exception:
        pass  # 아래에서 다시 확인한다
    for label in ("아이디로 로그인", "이메일로 로그인", "ID 로그인", "아이디 로그인", "이메일 로그인", "일반 로그인"):
        try:
            btn = page.get_by_text(label, exact=False).first
            if btn and btn.is_visible():
                btn.click()
                sess.human_pause()
                break
        except Exception:
            continue
    pw_box = _visible_password(page)
    if pw_box is None:
        # 칸을 못 찾은 것은 비밀번호가 틀린 것이 아니다 → 화면 문제로 알린다(다음 수집 때 다시 시도).
        # LoginError 로 알리면 계정 잠김을 막느라 하루 동안 다시 시도하지 않는다.
        raise StructureError("로그인 화면에서 비밀번호 칸을 찾지 못했습니다 (화면이 늦게 뜨거나 바뀌었을 수 있어요).")
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
        raise StructureError("로그인 화면에서 아이디 칸을 찾지 못했습니다.")
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


def _visible_password(page, wait_ms: int = 10000):
    """보이는 비밀번호 칸 (숨은 칸이 먼저 나오는 화면이 있다 — 모어덴 로그인 화면은 첫 칸이 숨어 있다)."""
    for _ in range(max(1, wait_ms // 500)):
        loc = page.locator("input[type=password]")
        for i in range(min(loc.count(), 5)):
            cand = loc.nth(i)
            try:
                if cand.is_visible():
                    return cand
            except Exception:
                continue
        page.wait_for_timeout(500)
    return None


# ──────────────────────────── JSON 에서 글 목록 찾기 ────────────────────────────

ID_KEYS = ("id", "bid", "recruitId", "recruit_id", "postId", "post_id", "articleId", "seq", "no", "idx", "uid", "boardId")
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
