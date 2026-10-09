"""덴트포토 (board.dentphoto.com) — 게시판 > 치과의사 구인구직양도.

일반적인 PHP 게시판이라 requests 로 읽는다.

2026-10 실제 사이트 확인 (로그인 후 화면까지):
- 로그인 안 된 상태로 목록을 열면 본문 없이
  <script>location.replace('https://member.dentphoto.com/login/login.php?login_url=...')</script>
- 로그인 폼: member.dentphoto.com/login/login.php → login_check.php 로 POST (login_url, dp_id, dp_password)
  맞으면 <script>location.replace('<login_url>')</script>, 틀리면 <script>alert('아이디 또는 비밀번호가 맞지 않습니다.');history.back();</script>
  쿠키(PHPSESSID 등)는 .dentphoto.com 에 걸려서 board.dentphoto.com 에서도 쓰인다.
- 목록: board.dentphoto.com/recruit_dental/list.php (2쪽부터 ?gotopage=N&key=&code=&keyword=&keyfield=), 한 쪽에 50건.
  div.board_list 안의 <ul> 하나가 글 하나, <li> 칸: 번호 | 분류(구인·양도) | '지역 | 제목' 링크 | 작성자 | 날짜(2026-10-09) | 조회 | 추천 | 신고
  공지는 번호 칸에 notice.gif 그림. '양도'(병원 양도) 글도 섞여 있다 → 뺀다.
- 글 링크: list2content.php?num=<번호>&gotopage=1&bnum=<다른 번호>&code= → 스크립트로 content.php?(같은 값) 로 보낸다.
  content.php 는 num 만으로는 '올바른 접근이 아닙니다' → 브라우저처럼 list2content.php 를 열고 따라간다.
- 글 화면: div.board_content_title 의 <li> 가 '이름표 | | | 값' 차례 (작성자·제목·지역·연락처).
  작성자 칸 끝에 '(2026-10-09 22:22:25, 14 읽음 )'. 본문은 #qcontents — 맨 앞에 '주소 : …', '직장명 : … 전화번호 : …', '급여 : …'.
  그 아래 댓글 입력 칸에는 로그인한 사람 이름이 나오므로 본문으로 읽지 않는다.

1) 목록을 연다 → 로그인 화면(또는 로그인 화면으로 보내는 스크립트)이면 로그인 폼을 찾아 아이디·비밀번호를 보낸다
   (폼을 못 찾으면 브라우저로 로그인한 뒤 쿠키를 넘겨받는다)
2) 목록에서 구인 글만 골라, 처음 보는 글만 열어서 본문을 읽는다
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qs, quote, urljoin, urlparse

from ..models import RawPosting
from .base import FetchContext, LoginError, MissingCredentials, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import (
    ListItem,
    extract_main_text,
    find_label_value,
    find_list_items,
    find_login_form,
    js_redirect_url,
    looks_like_login_page,
    parse_board_date,
    soup,
    text_of,
)

BOARD = "https://board.dentphoto.com/recruit_dental"
LIST_URL = f"{BOARD}/list.php"
LOGIN_URL = "https://member.dentphoto.com/login/login.php?login_url=" + quote(LIST_URL, safe="")
LOGIN_URLS = [LOGIN_URL, "https://member.dentphoto.com/login/login.php"]
ITEM_HREF = r"recruit_dental/(?:list2content|content|view|read)\.php|recruit_dental/.*[?&](?:no|idx|wr_id|uid|num)=\d+"
REGION_LABELS = ["근무지역", "근무 지역", "근무지", "지역", "위치", "주소", "소재지"]
INST_LABELS = ["직장명", "병원명", "치과명", "기관명", "업체명"]
CONTENT_SELECTORS = ["#qcontents", ".board_content_view", "#view_content", ".view_content", "#bo_v_con", ".board_view_content",
                     "td.content", ".content"]
ALERT_RE = re.compile(r"alert\(\s*['\"]([^'\"]{2,120})['\"]")
LOGIN_FAIL_RE = re.compile(r"(비밀번호|아이디).{0,20}(맞지\s*않|틀|일치하지|잘못|확인)|존재하지\s*않|탈퇴|정지|승인")
KINDS = ("구인", "구직", "양도")  # 목록의 분류 칸
SKIP_KINDS = {"양도", "구직"}  # 치과의사를 구하는 글이 아니다
POSTED_RE = re.compile(r"\(\s*(20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)\s*,[^)]*읽음\s*\)")
WORKPLACE_RE = re.compile(r"직장명\s*[:：]\s*(.+?)(?=\s+(?:전화번호|연락처)\s*[:：]|$)", re.M)
# 급여 칸을 비워 두지 못해 1만원으로 적은 글 → 협의
PLACEHOLDER_WAGE_RE = re.compile(r"^(급여[ \t]*[:：][ \t]*)(?:연봉|월급|주급|일급|시급)?[ \t]*[01][ \t]*만[ \t]*원(?:[ \t]*\((?:세전|세후)\))?[ \t]*$", re.M)


def _is_login_redirect(html: str) -> str:
    """로그인 화면으로 보내는 스크립트만 있는 응답이면 그 주소."""
    url = js_redirect_url(html)
    return url if "login" in url.lower() else ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def split_region_prefix(title: str) -> tuple[str, str]:
    """목록 제목 '경기도 여주시 | 여주그랜드치과에서 …' → ('경기도 여주시', '여주그랜드치과에서 …')."""
    m = re.match(r"^([가-힣 ]{2,30}?)\s*\|\s*(.+)$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", title


def _detail_url(full: str) -> str:
    """목록의 글 주소 (쪽 번호는 1로 고정 — 글이 다음 쪽으로 밀려도 주소가 같게)."""
    qs = parse_qs(urlparse(full).query)
    num, bnum = (qs.get("num") or [""])[0], (qs.get("bnum") or [""])[0]
    if num and bnum and "list2content.php" in full:
        return f"{BOARD}/list2content.php?num={num}&gotopage=1&bnum={bnum}&code="
    return full


def parse_list(html: str, base_url: str = LIST_URL) -> list[ListItem]:
    """실제 목록 화면(div.board_list > ul) 읽기. 모양이 다르면 빈 목록."""
    items: list[ListItem] = []
    for ul in soup(html).select("div.board_list > ul"):
        lis = ul.find_all("li", recursive=False)
        a = ul.find("a", href=re.compile(r"(?:list2content|content)\.php\?[^#]*\bnum=\d+"))
        if a is None or len(lis) < 5:
            continue
        cells = [_clean(li.get_text(" ", strip=True)) for li in lis]
        num = re.search(r"\bnum=(\d+)", a["href"]).group(1)
        region, title = split_region_prefix(_clean(a.get_text(" ", strip=True)))
        is_notice = bool(lis[0].find("img", src=re.compile(r"notice", re.I))) or not cells[0].isdigit()
        date_text = next((c for c in cells[3:] if re.fullmatch(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", c)), "")
        items.append(
            ListItem(
                title=title,
                url=_detail_url(urljoin(base_url, a["href"])),
                source_id=num,
                date_text=date_text,
                posted_at=parse_board_date(date_text) if date_text else None,
                row_text=" ".join(cells),
                cells=cells,
                is_notice=is_notice,
                region=region,
            )
        )
    return items


def _kind(it: ListItem) -> str:
    """목록의 분류 칸 (구인·양도). 일반 게시판 모양으로 읽었으면 빈 값."""
    return it.cells[1] if it.cells and len(it.cells) > 1 and it.cells[1] in KINDS else ""


def parse_detail(html: str) -> dict:
    """글 화면에서 제목·지역·작성자·올린 시각·본문·직장명."""
    s = soup(html)
    info: dict[str, str] = {}
    box = s.select_one("div.board_content_title")
    if box:
        cells = [li for li in box.find_all("li")]
        for i in range(len(cells) - 2):
            if cells[i + 1].get_text(strip=True) == "|":
                key = re.sub(r"\s+", "", cells[i].get_text("", strip=True))
                info.setdefault(key, _clean(cells[i + 2].get_text(" ", strip=True)))
    author_cell = info.get("작성자", "")
    m = POSTED_RE.search(author_cell)
    posted = parse_board_date(m.group(1)) if m else None
    author = POSTED_RE.sub("", author_cell).strip()
    author = re.sub(r"\s*\([A-Za-z0-9_.\-]+\)\s*$", "", author)  # 닉네임 뒤 (아이디) 는 뺀다
    kind = re.match(r"^\[(구인|구직|양도)\]\s*", info.get("제목", ""))
    title = info.get("제목", "")[kind.end():] if kind else info.get("제목", "")
    # 본문은 #qcontents 만 (그 아래 댓글 칸에는 로그인한 사람 이름이 있다)
    node = s.select_one("#qcontents")
    if node is not None:
        # 줄마다 '<br>' 다음에 줄바꿈 글자가 또 있다 → 화면처럼 <br> 만 줄바꿈으로 본다
        for t in node.find_all(string=True):
            t.replace_with(t.replace("\r", "").replace("\n", " "))
        body = text_of(node)
    else:
        body = extract_main_text(html, selectors=CONTENT_SELECTORS)
    body = PLACEHOLDER_WAGE_RE.sub(r"\1협의", body)
    wp = WORKPLACE_RE.search(body)
    return {
        "title": title,
        "kind": kind.group(1) if kind else "",
        "region": info.get("지역", ""),
        "author": author,
        "posted_at": posted,
        "body": body,
        "institution": _clean(wp.group(1)) if wp else "",
    }


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
                if it.posted_at and not it.is_notice:
                    oldest = it.posted_at if oldest is None else min(oldest, it.posted_at)
                if it.is_notice or _kind(it) in SKIP_KINDS:
                    continue
                if it.posted_at and it.posted_at < ctx.since:
                    continue
                if it.source_id in ctx.known_ids:
                    yield RawPosting(self.key, it.source_id, it.url, it.title, posted_at=it.posted_at, region_hint=it.region)
                    continue
                raw = self._detail(http, ctx, it, referer=url)
                if raw is not None:
                    yield raw
            if oldest and oldest < ctx.since:
                break
        http.save_cookies()

    # ─────────────── 목록·로그인 ───────────────

    def _list_url(self, page_no: int) -> str:
        # 사이트의 쪽 번호 링크와 같은 모양
        return LIST_URL if page_no == 1 else f"{LIST_URL}?gotopage={page_no}&key=&code=&keyword=&keyfield="

    def _items(self, html: str, url: str) -> list[ListItem]:
        items = parse_list(html, url)
        if not items:
            # 화면 모양이 바뀌었으면 일반적인 게시판 읽기
            items = find_list_items(html, url, href_pattern=ITEM_HREF) or find_list_items(html, url)
        return items

    def _needs_login(self, r, html: str) -> bool:
        return (
            r.status_code in (401, 403)
            or "member.dentphoto.com/login" in r.url
            or bool(_is_login_redirect(html))
            or looks_like_login_page(html)
        )

    def _get_list(self, http: PoliteSession, ctx: FetchContext, page_no: int, user: str, pw: str) -> tuple[str, str]:
        r = http.get(self._list_url(page_no), headers={"Referer": LIST_URL} if page_no > 1 else None)
        html = decode(r)
        if self._needs_login(r, html) or (page_no == 1 and not self._items(html, r.url)):
            if page_no > 1 and not self._needs_login(r, html):
                return html, r.url
            ctx.log("로그인 필요 — 로그인 시도")
            self._login(http, ctx, html, r.url, user, pw)
            r = http.get(self._list_url(page_no))
            html = decode(r)
            if self._needs_login(r, html) and not self._items(html, r.url):
                ctx.dump("dentphoto-after-login.html", html)
                raise LoginError("덴트포토 로그인 후에도 게시판이 열리지 않습니다. 아이디·비밀번호를 확인해 주세요.")
            http.save_cookies()
        return html, r.url

    def _login(self, http: PoliteSession, ctx: FetchContext, html: str, url: str, user: str, pw: str) -> None:
        form = find_login_form(html, url)
        pages = [(html, url)]
        redirect = _is_login_redirect(html)
        candidates = ([redirect] if redirect else []) + [u for u in LOGIN_URLS if u != redirect]
        login_page = url
        if not form:
            for cand in candidates:
                try:
                    r = http.get(cand, headers={"Referer": LIST_URL})
                except SourceError:
                    continue
                if r.status_code >= 400:
                    continue
                h = decode(r)
                pages.append((h, r.url))
                form = find_login_form(h, r.url)
                if form:
                    login_page = r.url
                    break
        if form:
            data = dict(form.fields)
            data[form.user_field] = user
            data[form.pass_field] = pw
            for k in ("login_url", "url", "return_url", "returnUrl", "ret_url", "redirect", "referer", "go_url"):
                if k in data and not data[k]:
                    data[k] = quote(LIST_URL, safe="") if k == "login_url" else LIST_URL
            if form.method == "get":
                resp = http.get(form.action, params=data)
            else:
                resp = http.post(form.action, data=data, headers={"Referer": login_page})
            body = decode(resp)
            alert = ALERT_RE.search(body)
            if alert and len(body) < 5000 and ("history.back" in body or LOGIN_FAIL_RE.search(alert.group(1))):
                raise LoginError(f"덴트포토 로그인에 실패했습니다: {alert.group(1)} 아이디·비밀번호를 확인해 주세요.")
            if LOGIN_FAIL_RE.search(body) and len(body) < 5000:
                raise LoginError("덴트포토 로그인에 실패했습니다. 아이디·비밀번호를 확인해 주세요.")
            # 성공하면 스크립트로 원래 화면(목록)에 보낸다. 곧바로 목록을 다시 여니 따로 따라가지는 않는다.
            # (다른 화면으로 보내면 게시판 쪽 쿠키를 그 화면에서 받을 수 있으니 브라우저처럼 한 번 따라간다)
            nxt = js_redirect_url(body)
            if nxt and "login" not in nxt.lower() and not urljoin(resp.url, nxt).startswith(LIST_URL):
                try:
                    http.get(urljoin(resp.url, nxt), headers={"Referer": resp.url})
                except SourceError:
                    pass
            return
        # 폼을 못 찾음 → 브라우저로 로그인 후 쿠키 넘겨받기
        for i, (h, _) in enumerate(pages):
            ctx.dump(f"dentphoto-login-{i}.html", h)
        self._browser_login(http, ctx, candidates[0], user, pw)

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

    def _detail(self, http: PoliteSession, ctx: FetchContext, it: ListItem, *, referer: str = LIST_URL) -> RawPosting | None:
        r = http.get(it.url, headers={"Referer": referer})
        html = decode(r)
        nxt = js_redirect_url(html)
        if nxt and "login" not in nxt.lower():
            # list2content.php → content.php (브라우저처럼 곧바로 따라간다)
            r = http.get(urljoin(r.url, nxt), headers={"Referer": r.url}, polite=False)
            html = decode(r)
        if ctx.save_debug:
            ctx.dump(f"dentphoto-detail-{it.source_id}.html", html)
        d = parse_detail(html)
        if not d["title"] and not d["body"]:
            # 그 사이 지워진 글(경고창만 옴)·로그인이 풀린 경우 → 저장하지 않고 다음 수집 때 다시 본다
            alert = ALERT_RE.search(html)
            ctx.log(f"글 {it.source_id} 을 읽지 못함" + (f": {alert.group(1)}" if alert else ""))
            ctx.dump(f"dentphoto-detail-{it.source_id}.html", html)
            return None
        if d["kind"] in SKIP_KINDS:
            return None
        title = d["title"] or it.title
        if not d["title"]:
            h = soup(html).find(["h1", "h2", "h3"], string=True)
            if h and len(h.get_text(strip=True)) > len(title):
                title = h.get_text(" ", strip=True)[:200]
        posted = d["posted_at"] or it.posted_at
        if posted is None:
            m = re.search(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?", html)
            posted = parse_board_date(m.group(0)) if m else None
        author = d["author"]
        if not author and it.cells and len(it.cells) >= 4:
            if _kind(it):
                author = it.cells[3]
            else:
                # 흔한 목록 구성: 번호 | (지역) | 제목 | 글쓴이 | 날짜 | 조회
                author = next((c for c in it.cells if c and c not in (it.title, it.date_text) and not c.isdigit()), "")
        return RawPosting(
            source=self.key,
            source_id=it.source_id,
            url=it.url,
            title=title,
            body=d["body"],
            posted_at=posted,
            author=author[:40],
            region_hint=d["region"] or it.region or find_label_value(html, REGION_LABELS) or _region_cell(it),
            institution_hint=d["institution"] or find_label_value(html, INST_LABELS[1:]),
        )


def _region_cell(it: ListItem) -> str:
    """목록에 '지역' 칸이 따로 있으면 그 값."""
    from ..regions import parse_regions

    for c in it.cells or []:
        if c and c != it.title and len(c) <= 15 and parse_regions(c):
            return c
    return ""
