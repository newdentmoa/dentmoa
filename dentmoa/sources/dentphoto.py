"""덴트포토 (board.dentphoto.com) — 게시판 > 치과의사 구인구직.

일반적인 PHP 게시판이라 requests 로 읽는다.
1) 목록을 연다 → 로그인 화면이면 로그인 폼을 찾아 아이디·비밀번호를 보낸다
   (폼을 못 찾으면 브라우저로 로그인한 뒤 쿠키를 넘겨받는다)
2) 목록에서 글 링크를 찾고, 처음 보는 글만 열어서 본문을 읽는다
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import RawPosting
from .base import FetchContext, LoginError, MissingCredentials, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import (
    ListItem,
    extract_main_text,
    find_label_value,
    find_list_items,
    find_login_form,
    looks_like_login_page,
    parse_board_date,
    soup,
)

LIST_URL = "https://board.dentphoto.com/recruit_dental/list.php"
LOGIN_URLS = [
    "https://board.dentphoto.com/login.php",
    "https://board.dentphoto.com/member/login.php",
    "https://www.dentphoto.com/login.php",
    "https://www.dentphoto.com/member/login.php",
    "https://www.dentphoto.com/bbs/login.php",
    "https://dentphoto.com/login.php",
]
ITEM_HREF = r"recruit_dental/(?:view|read|content)\.php|recruit_dental/.*[?&](?:no|idx|wr_id|uid|num)=\d+"
REGION_LABELS = ["근무지역", "근무 지역", "근무지", "지역", "위치", "주소", "소재지"]
INST_LABELS = ["병원명", "치과명", "기관명", "업체명"]
CONTENT_SELECTORS = ["#view_content", ".view_content", "#bo_v_con", ".board_view_content", "td.content", ".content"]


class DentphotoSource(Source):
    key = "dentphoto"
    label = "덴트포토"
    homepage = LIST_URL
    source_kind = "local_board"
    dentist_only = True
    default_inst_type = "dental_clinic"
    requires_login = True

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        user, pw = ctx.secrets.get("dentphoto_id", ""), ctx.secrets.get("dentphoto_pw", "")
        if not user or not pw:
            raise MissingCredentials("덴트포토 아이디·비밀번호가 설정되지 않았습니다.")
        http = PoliteSession(self.key, ctx)
        html, url = self._get_list(http, ctx, 1, user, pw)

        seen: set[str] = set()
        for page_no in range(1, ctx.max_pages + 1):
            if page_no > 1:
                html, url = self._get_list(http, ctx, page_no, user, pw)
            items = [i for i in self._items(html, url) if i.source_id not in seen]
            if not items:
                if page_no == 1:
                    ctx.dump("dentphoto-list.html", html)
                    raise StructureError("덴트포토 게시판에서 글 목록을 찾지 못했습니다.")
                break
            if ctx.save_debug:
                ctx.dump(f"dentphoto-list-{page_no}.html", html)
            oldest = None
            for it in items:
                seen.add(it.source_id)
                if it.posted_at:
                    oldest = it.posted_at if oldest is None else min(oldest, it.posted_at)
                if it.is_notice:
                    continue
                if it.posted_at and it.posted_at < ctx.since:
                    continue
                if it.source_id in ctx.known_ids:
                    yield RawPosting(self.key, it.source_id, it.url, it.title, posted_at=it.posted_at)
                    continue
                yield self._detail(http, ctx, it)
            if oldest and oldest < ctx.since:
                break
        http.save_cookies()

    # ─────────────── 목록·로그인 ───────────────

    def _list_url(self, page_no: int) -> str:
        return LIST_URL if page_no == 1 else f"{LIST_URL}?page={page_no}"

    def _items(self, html: str, url: str) -> list[ListItem]:
        items = find_list_items(html, url, href_pattern=ITEM_HREF)
        if not items:
            items = find_list_items(html, url)
        return items

    def _get_list(self, http: PoliteSession, ctx: FetchContext, page_no: int, user: str, pw: str) -> tuple[str, str]:
        r = http.get(self._list_url(page_no))
        html = decode(r)
        if r.status_code in (401, 403) or looks_like_login_page(html) or not self._items(html, r.url):
            if page_no > 1 and not looks_like_login_page(html):
                return html, r.url
            ctx.log("로그인 필요 — 로그인 시도")
            self._login(http, ctx, html, r.url, user, pw)
            r = http.get(self._list_url(page_no))
            html = decode(r)
            if looks_like_login_page(html) and not self._items(html, r.url):
                ctx.dump("dentphoto-after-login.html", html)
                raise LoginError("덴트포토 로그인에 실패했습니다. 아이디·비밀번호를 확인해 주세요.")
            http.save_cookies()
        return html, r.url

    def _login(self, http: PoliteSession, ctx: FetchContext, html: str, url: str, user: str, pw: str) -> None:
        form = find_login_form(html, url)
        pages = [(html, url)]
        if not form:
            for cand in LOGIN_URLS:
                try:
                    r = http.get(cand)
                except SourceError:
                    continue
                if r.status_code >= 400:
                    continue
                h = decode(r)
                pages.append((h, r.url))
                form = find_login_form(h, r.url)
                if form:
                    break
        if form:
            data = dict(form.fields)
            data[form.user_field] = user
            data[form.pass_field] = pw
            for k in ("url", "return_url", "returnUrl", "ret_url", "redirect", "referer", "go_url"):
                if k in data and not data[k]:
                    data[k] = LIST_URL
            if form.method == "get":
                resp = http.get(form.action, params=data)
            else:
                resp = http.post(form.action, data=data, headers={"Referer": url})
            body = decode(resp)
            if re.search(r"(비밀번호|아이디).{0,20}(틀|일치하지|잘못|확인)", body) and len(body) < 5000:
                raise LoginError("덴트포토 로그인에 실패했습니다. 아이디·비밀번호를 확인해 주세요.")
            return
        # 폼을 못 찾음 → 브라우저로 로그인 후 쿠키 넘겨받기
        for i, (h, _) in enumerate(pages):
            ctx.dump(f"dentphoto-login-{i}.html", h)
        self._browser_login(http, ctx, url, user, pw)

    def _browser_login(self, http: PoliteSession, ctx: FetchContext, url: str, user: str, pw: str) -> None:
        from .browser import generic_login, open_browser

        with open_browser(self.key) as sess:
            last_err: Exception | None = None
            for cand in [url, *LOGIN_URLS]:
                try:
                    generic_login(
                        sess, cand, user, pw,
                        success_check=lambda p: not p.locator("input[type=password]").first.is_visible(timeout=1500),
                    )
                    break
                except LoginError as e:
                    last_err = e
            else:
                raise LoginError(f"덴트포토 로그인에 실패했습니다. ({last_err})")
            for c in sess.context.cookies():
                http.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))

    # ─────────────── 본문 ───────────────

    def _detail(self, http: PoliteSession, ctx: FetchContext, it: ListItem) -> RawPosting:
        r = http.get(it.url)
        html = decode(r)
        if ctx.save_debug:
            ctx.dump(f"dentphoto-detail-{it.source_id}.html", html)
        body = extract_main_text(html, selectors=CONTENT_SELECTORS)
        title = it.title
        h = soup(html).find(["h1", "h2", "h3"], string=True)
        if h and len(h.get_text(strip=True)) > len(title):
            title = h.get_text(" ", strip=True)[:200]
        posted = it.posted_at
        if posted is None:
            m = re.search(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?", html)
            posted = parse_board_date(m.group(0)) if m else None
        author = ""
        if it.cells and len(it.cells) >= 4:
            # 흔한 목록 구성: 번호 | (지역) | 제목 | 글쓴이 | 날짜 | 조회
            author = next((c for c in it.cells if c and c not in (it.title, it.date_text) and not c.isdigit()), "")
        return RawPosting(
            source=self.key,
            source_id=it.source_id,
            url=it.url,
            title=title,
            body=body,
            posted_at=posted,
            author=author[:40],
            region_hint=find_label_value(html, REGION_LABELS) or _region_cell(it),
            institution_hint=find_label_value(html, INST_LABELS),
        )


def _region_cell(it: ListItem) -> str:
    """목록에 '지역' 칸이 따로 있으면 그 값."""
    from ..regions import parse_regions

    for c in it.cells or []:
        if c and c != it.title and len(c) <= 15 and parse_regions(c):
            return c
    return ""
