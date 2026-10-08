"""모어덴 (moreden.co.kr) — 치과의사 구인 게시판.

자바스크립트로 화면을 그리는 사이트라 브라우저(Chromium)로 읽는다.
1) 구인 게시판을 연다 → 로그인 화면이 나오면 아이디·비밀번호로 로그인
2) 화면이 받아오는 JSON 응답과 화면의 링크 둘 다에서 글 목록을 찾는다
3) 처음 보는 글만 열어서 본문을 읽는다
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urljoin

from ..models import RawPosting
from .base import FetchContext, LoginError, MissingCredentials, Source, StructureError
from .browser import BrowserSession, dump_captured, find_record, generic_login, open_browser, records_from_json
from .htmlutil import extract_main_text, find_label_value, parse_board_date, soup

BASE = "https://moreden.co.kr"
LIST_URL = f"{BASE}/recruit/doctor"
LOGIN_URLS = [f"{BASE}/login", f"{BASE}/signin", f"{BASE}/auth/login", f"{BASE}/member/login"]
DETAIL_HREF = re.compile(r"/recruit/(?:doctor/)?(?:detail/)?(?P<id>[A-Za-z0-9_-]{2,})/?$")
REGION_LABELS = ["근무지역", "근무 지역", "근무지", "지역", "위치", "주소", "소재지"]
INST_LABELS = ["병원명", "치과명", "기관명", "업체명", "병원 이름", "치과 이름"]


def _is_login_page(page) -> bool:
    url = page.url.lower()
    if any(k in url for k in ("login", "signin", "auth")):
        return True
    try:
        return page.locator("input[type=password]").first.is_visible(timeout=1500)
    except Exception:
        return False


class MoredenSource(Source):
    key = "moreden"
    label = "모어덴"
    homepage = LIST_URL
    source_kind = "local_board"
    dentist_only = True
    default_inst_type = "dental_clinic"
    requires_login = True

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        user, pw = ctx.secrets.get("moreden_id", ""), ctx.secrets.get("moreden_pw", "")
        if not user or not pw:
            raise MissingCredentials("모어덴 아이디·비밀번호가 설정되지 않았습니다.")

        with open_browser(self.key, capture_json=r"moreden|api") as sess:
            self._open_list(sess, ctx, user, pw)
            items = self._collect_list(sess, ctx)
            if not items:
                ctx.dump("moreden-list.html", sess.page.content())
                dump_captured(sess, ctx, "moreden-list")
                raise StructureError("모어덴 구인 게시판에서 글 목록을 찾지 못했습니다.")
            ctx.log(f"목록에서 {len(items)}건 확인")
            for it in items:
                if it["posted_at"] and it["posted_at"] < ctx.since:
                    continue
                if it["id"] in ctx.known_ids:
                    # 이미 저장된 글은 본문을 다시 열지 않는다(제목만 갱신)
                    yield RawPosting(self.key, it["id"], it["url"], it["title"], posted_at=it["posted_at"],
                                     region_hint=it["region"], institution_hint=it["institution"])
                    continue
                yield self._detail(sess, ctx, it)

    # ─────────────── 단계별 ───────────────

    def _open_list(self, sess: BrowserSession, ctx: FetchContext, user: str, pw: str) -> None:
        page = sess.page
        page.goto(LIST_URL, wait_until="domcontentloaded")
        self._settle(sess)
        if not _is_login_page(page) and not self._needs_login_text(page):
            return
        ctx.log("로그인 필요 — 로그인 시도")
        login_url = page.url if _is_login_page(page) else LOGIN_URLS[0]
        tried = []
        for url in [login_url] + [u for u in LOGIN_URLS if u != login_url]:
            try:
                generic_login(sess, url, user, pw, success_check=lambda p: not _is_login_page(p))
                break
            except LoginError as e:
                tried.append(f"{url}: {e}")
                ctx.dump("moreden-login.html", page.content())
        else:
            raise LoginError("모어덴 로그인에 실패했습니다. 아이디·비밀번호(카카오 로그인이 아닌 모어덴 아이디)를 확인해 주세요.")
        page.goto(LIST_URL, wait_until="domcontentloaded")
        self._settle(sess)
        if _is_login_page(page):
            raise LoginError("로그인 후에도 구인 게시판이 열리지 않습니다.")

    def _needs_login_text(self, page) -> bool:
        try:
            body = page.inner_text("body", timeout=3000)[:2000]
        except Exception:
            return False
        return bool(re.search(r"로그인\s*(?:이|후)\s*(?:필요|이용)|로그인\s*하세요|회원\s*전용", body))

    def _settle(self, sess: BrowserSession) -> None:
        try:
            sess.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        sess.human_pause(1.0, 2.5)

    def _collect_list(self, sess: BrowserSession, ctx: FetchContext) -> list[dict]:
        items: dict[str, dict] = {}
        for page_no in range(1, ctx.max_pages + 1):
            if page_no > 1:
                before = len(items)
                # 더 보기: 스크롤 → 그래도 안 늘면 ?page=N
                sess.page.mouse.wheel(0, 20000)
                self._settle(sess)
                self._merge(items, sess, ctx)
                if len(items) == before:
                    sess.page.goto(f"{LIST_URL}?page={page_no}", wait_until="domcontentloaded")
                    self._settle(sess)
                    self._merge(items, sess, ctx)
                if len(items) == before:
                    break
            else:
                self._merge(items, sess, ctx)
            dated = [i["posted_at"] for i in items.values() if i["posted_at"]]
            if dated and min(dated) < ctx.since:
                break
            if page_no < ctx.max_pages:
                ctx.sleep()
        if ctx.save_debug:
            ctx.dump("moreden-list.html", sess.page.content())
            dump_captured(sess, ctx, "moreden-list")
        return list(items.values())

    def _merge(self, items: dict[str, dict], sess: BrowserSession, ctx: FetchContext) -> None:
        # 1) JSON 응답
        for cap in sess.captured:
            for rec in records_from_json(cap.data):
                it = items.setdefault(rec["id"], self._blank(rec["id"]))
                it["title"] = it["title"] or rec["title"]
                it["body"] = it["body"] or _strip_html(rec["body"])
                it["region"] = it["region"] or rec["region"]
                it["institution"] = it["institution"] or rec["institution"]
                it["posted_at"] = it["posted_at"] or parse_board_date(rec["date"])
        # 2) 화면의 링크
        s = soup(sess.page.content())
        for a in s.find_all("a", href=True):
            href = urljoin(BASE, a["href"])
            if not href.startswith(BASE):
                continue
            m = DETAIL_HREF.search(href.split("?")[0])
            if not m or m.group("id") in ("doctor", "hygienist", "write", "new", "list", "search"):
                continue
            pid = m.group("id")
            card_text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            it = items.setdefault(pid, self._blank(pid))
            it["url"] = href
            if not it["title"]:
                title_node = a.find(["h2", "h3", "h4", "strong", "p"])
                it["title"] = (title_node.get_text(" ", strip=True) if title_node else card_text)[:150]
            it["card"] = card_text
            if not it["posted_at"]:
                it["posted_at"] = parse_board_date(_date_like(card_text))
        for it in items.values():
            if not it["url"]:
                it["url"] = f"{LIST_URL}/{it['id']}"
        # 제목 없는 항목은 버린다
        for k in [k for k, v in items.items() if not v["title"]]:
            items.pop(k)

    @staticmethod
    def _blank(pid: str) -> dict:
        return {"id": pid, "url": "", "title": "", "body": "", "region": "", "institution": "", "posted_at": None, "card": ""}

    def _detail(self, sess: BrowserSession, ctx: FetchContext, it: dict) -> RawPosting:
        ctx.sleep()
        page = sess.page
        sess.captured.clear()
        page.goto(it["url"], wait_until="domcontentloaded")
        self._settle(sess)
        html = page.content()
        if ctx.save_debug:
            ctx.dump(f"moreden-detail-{it['id']}.html", html)
            dump_captured(sess, ctx, f"moreden-detail-{it['id']}")
        body = extract_main_text(html)
        # 상세 JSON 에 본문이 있으면 그것이 더 정확하다
        for cap in sess.captured:
            rec = find_record(cap.data, it["id"])
            if rec:
                it["body"] = max(it["body"], _strip_html(rec["body"]), key=len)
                it["region"] = it["region"] or rec["region"]
                it["institution"] = it["institution"] or rec["institution"]
                it["posted_at"] = it["posted_at"] or parse_board_date(rec["date"])
        if len(it["body"]) > len(body):
            body = it["body"]
        region = it["region"] or find_label_value(html, REGION_LABELS)
        inst = it["institution"] or find_label_value(html, INST_LABELS)
        posted = it["posted_at"] or parse_board_date(_date_like(body[:300]))
        return RawPosting(
            source=self.key,
            source_id=it["id"],
            url=it["url"],
            title=it["title"],
            body=body,
            posted_at=posted,
            region_hint=region,
            institution_hint=inst,
        )


def _strip_html(s: str) -> str:
    if not s or "<" not in s:
        return s or ""
    from .htmlutil import text_of

    return text_of(soup(s))


def _date_like(text: str) -> str:
    m = re.search(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}|\d+\s*(?:분|시간|일)\s*전|어제|오늘|\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", text or "")
    return m.group(0) if m else ""
