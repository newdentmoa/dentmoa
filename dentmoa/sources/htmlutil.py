"""게시판 HTML 을 읽는 공통 도구.

사이트마다 구조가 달라서, 특정 선택자를 모를 때도 동작하도록
'링크가 있는 줄 + 날짜' 를 찾는 일반적인 방법을 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .. import config

ID_PARAMS = ("wr_id", "no", "idx", "id", "num", "seq", "bbsIdx", "board_seq", "nttId", "articleNo", "bidx", "uid", "b_idx", "list_no")
NOISE_TAGS = ("script", "style", "noscript", "iframe", "svg", "header", "footer", "nav", "button", "select", "input")


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text_of(node: Tag | None) -> str:
    """줄바꿈을 살린 텍스트."""
    if node is None:
        return ""
    for br in node.find_all("br"):
        br.replace_with("\n")
    for blk in node.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        blk.insert_after("\n")
    text = node.get_text("")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


# ──────────────────────────── 날짜 ────────────────────────────

_FULL = re.compile(r"(20\d{2}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?![\d.])")
_HM = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_AGO = re.compile(r"(\d+)\s*(분|시간|일|주)\s*전")
# 20261007102130 / 20261007 (모어덴 API)
_COMPACT = re.compile(r"(20\d{2})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?)?")


def parse_board_date(text: str, now: datetime | None = None) -> datetime | None:
    """게시판에 흔한 날짜 표기를 읽는다. 예: 2026-10-08, 26.10.08, 10-08, 14:32, 3시간 전, 어제"""
    now = now or config.now()
    t = (text or "").strip()
    if not t:
        return None
    m = _COMPACT.fullmatch(t)
    if m:
        try:
            return datetime(*(int(g) for g in m.groups() if g is not None), tzinfo=config.TZ)
        except ValueError:
            return None
    m = _FULL.search(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        hm = _HM.search(t[m.end():])
        try:
            dt = datetime(y, mo, d, tzinfo=config.TZ)
        except ValueError:
            return None
        if hm:
            dt = dt.replace(hour=int(hm.group(1)), minute=int(hm.group(2)))
        return dt
    m = _AGO.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"분": timedelta(minutes=n), "시간": timedelta(hours=n), "일": timedelta(days=n), "주": timedelta(weeks=n)}[unit]
        return now - delta
    if "방금" in t:
        return now
    if "어제" in t:
        return (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    if "오늘" in t:
        return now
    m = _MD.search(t)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                dt = datetime(now.year, mo, d, tzinfo=config.TZ)
            except ValueError:
                return None
            if dt > now + timedelta(days=2):
                dt = dt.replace(year=now.year - 1)
            return dt
    m = _HM.fullmatch(t) or (_HM.search(t) if len(t) <= 8 else None)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return None


# ──────────────────────────── 목록 ────────────────────────────


@dataclass
class ListItem:
    title: str
    url: str
    source_id: str
    date_text: str = ""
    posted_at: datetime | None = None
    row_text: str = ""
    cells: list[str] | None = None
    is_notice: bool = False
    # 사이트별 목록 읽기에서 칸이 따로 있으면 채운다
    institution: str = ""
    region: str = ""
    deadline_text: str = ""


_JS_REDIRECT = re.compile(r"location\.(?:replace\(|href\s*=\s*\(?)\s*['\"]([^'\"]+)['\"]")


def js_redirect_url(html: str) -> str:
    """본문 없이 '<script>location.replace(...)</script>' 만 있는 응답이면 그 주소."""
    if len(html) > 3000:
        return ""
    m = _JS_REDIRECT.search(html)
    return m.group(1) if m else ""


# 주소에서 글 번호로 보지 않을 매개변수 (쪽 번호·검색어·메뉴 등 — 같은 글이라도 바뀔 수 있다)
_VOLATILE_PARAMS = re.compile(
    r"^(?:page|pageindex|pageno|pagenum|pageunit|pagesize|currentpage|curpage|cpage|nowpage|gotopage|offset|limit|start|"
    r"rows|listcnt|recordcountperpage|search\w*|keyword|sch\w*|sfl|stx|sst|sod|sop|spt|sca|order\w*|sort\w*|rtn_url|"
    r"returnurl|ret_url|mode|act|proc_type|p|pg|_)$",
    re.I,
)


def id_from_url(url: str) -> str:
    """주소에서 글 번호를 뽑는다. 못 찾으면 숫자로 된 매개변수들, 그것도 없으면 경로 전체."""
    u = urlparse(url)
    qs = parse_qs(u.query)
    for p in ID_PARAMS:
        if p in qs and qs[p][0]:
            return qs[p][0]
    m = re.search(r"/(\d{2,})(?:/|\.html?|\.do)?$", u.path)
    if m:
        return m.group(1)
    numeric = sorted((k, v[0]) for k, v in qs.items() if v and v[0].isdigit() and not _VOLATILE_PARAMS.match(k))
    if numeric:
        return "&".join(f"{k}={v}" for k, v in numeric)
    return (u.path + ("?" + u.query if u.query else "")).strip("/") or url


def _row_of(a: Tag) -> Tag:
    for parent in a.parents:
        if parent.name in ("tr", "li"):
            return parent
        if parent.name in ("div", "article", "dl") and parent.get("class") and any(
            re.search(r"(item|row|list|post|article|card|board)", c, re.I) for c in parent.get("class", [])
        ):
            return parent
        if parent.name in ("table", "ul", "ol", "body"):
            break
    return a.parent if isinstance(a.parent, Tag) else a


_ROW_DATE = re.compile(r"(?<!\d)(?:20\d{2}|\d{2})\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}(?!\d)")
_ROW_MAX_TEXT = 700


_TITLE_PARTS = ".subject, .title, .tit, .subj, .txt_title, .board_title, .bbs_title, strong, b, h3, h4, h5"
_BUTTON_TEXT = re.compile(r"(?:자세히|상세|내용)?\s*(?:보기|더\s*보기|바로\s*가기)\s*[>›»]?|view|more|read\s*more|detail", re.I)


def _anchor_title(a: Tag) -> str:
    """링크 글자. 카드 모양 목록처럼 링크 안에 제목과 날짜가 같이 있으면 제목 부분만."""
    text = re.sub(r"\s+", " ", a.get_text(" ", strip=True)) or (a.get("title") or "").strip()
    if _ROW_DATE.search(text) and a.find(True):
        for part in a.select(_TITLE_PARTS):
            t = re.sub(r"\s+", " ", part.get_text(" ", strip=True))
            if len(t) >= 4 and not _ROW_DATE.search(t):
                return t
        # 제목 칸 표시가 없으면 날짜·D-day 가 든 작은 조각을 빼고 남은 글자
        keep = []
        for piece in a.find_all(string=True):
            t = piece.strip()
            if not t or _ROW_DATE.search(t) or re.fullmatch(r"D[-+]?\s*\d*|마감일?|등록일|접수기간", t):
                continue
            keep.append(t)
        rest = re.sub(r"\s+", " ", " ".join(keep)).strip()
        if len(rest) >= 4:
            return rest
    return text


def _short_date_cell(node: Tag) -> bool:
    """표의 한 칸이 '10-07' 이나 '14:32' 처럼 날짜·시각만 있는 줄인지."""
    tr = node if node.name == "tr" else (node.find_parent("tr") if node.name == "td" else None)
    if tr is None:
        return False
    for td in tr.find_all("td", recursive=False):
        t = td.get_text(" ", strip=True)
        if _MD.fullmatch(t) or _HM.fullmatch(t):
            return True
    return False


def _dated_row(a: Tag, title: str, title_anchor) -> Tag | None:
    """링크를 감싼 가장 작은 '날짜가 있는 줄'. 그 줄에 다른 글 링크가 섞여 있으면(메뉴·목록 전체) None."""
    node: Tag = a
    for _ in range(8):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]", "table", "thead", "tbody", "ul", "ol"):
            return None
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text) > _ROW_MAX_TEXT:
            return None
        if not _ROW_DATE.search(text.replace(title, " ", 1)) and not _short_date_cell(node):
            continue
        others = {id(x) for x in node.find_all("a") if title_anchor(x) and x is not a}
        hrefs = {x.get("href") for x in node.find_all("a") if id(x) in others} - {a.get("href")}
        if len(hrefs) > 1:
            return None
        if node.name in ("td", "th"):
            node = node.find_parent("tr") or node
        return node
    return None


def find_list_items(
    html: str,
    base_url: str,
    *,
    href_pattern: str | None = None,
    min_title_len: int = 4,
    now: datetime | None = None,
) -> list[ListItem]:
    """게시판 목록에서 글 링크를 찾는다.

    href_pattern 이 있으면 그 정규식에 맞는 링크만 본다(예: r"view\\.php|wr_id=").
    없으면 ① 날짜가 함께 있는 줄(tr·li·div)이 가장 많이 모인 곳을 글 목록으로 보고,
    ② 날짜가 없는 게시판이면 '같은 모양의 링크가 여러 줄 반복되는 것'을 글 목록으로 본다.
    """
    s = soup(html)
    for t in s(["script", "style", "noscript"]):
        t.decompose()

    def title_anchor(a: Tag) -> bool:
        return len(re.sub(r"\s+", "", a.get_text("", strip=True))) >= min_title_len

    anchors = []
    for a in s.find_all("a"):
        href = (a.get("href") or "").strip()
        onclick = a.get("onclick") or ""
        title = _anchor_title(a)
        if len(title) < min_title_len:
            continue
        if href.startswith(("#", "javascript", "mailto:", "tel:")) or not href:
            m = re.search(r"['\"]?(\d{2,})['\"]?", onclick + href)
            if not (onclick or href.startswith("javascript")) or not m:
                continue
            href = f"{base_url}#js-{m.group(1)}"
        full = urljoin(base_url, href)
        if href_pattern and not re.search(href_pattern, full):
            continue
        anchors.append((a, full, title))

    rows: dict[int, Tag] = {}
    if not href_pattern and anchors:
        picked = _dated_rows(anchors, title_anchor)
        if picked:
            anchors = [(a, full, title) for a, full, title, _ in picked]
            rows = {id(a): row for a, _, _, row in picked}
        else:
            anchors = _same_shape(anchors)

    items: list[ListItem] = []
    seen: set[str] = set()
    for a, full, title in anchors:
        row = rows.get(id(a)) or _row_of(a)
        if _BUTTON_TEXT.fullmatch(title):
            # '자세히 보기' 단추만 링크이고 제목은 옆 칸에 있는 목록
            part = next((p for p in row.select(_TITLE_PARTS) if len(p.get_text(strip=True)) >= min_title_len
                         and not _BUTTON_TEXT.fullmatch(p.get_text(" ", strip=True))), None)
            if part is None:
                continue
            title = re.sub(r"\s+", " ", part.get_text(" ", strip=True))
        row_text = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in row.find_all(["td", "th"], recursive=False)] or None
        date_text = ""
        for cand in [c for c in (cells or []) if c != title] + [row_text.replace(title, " ", 1)]:
            if _FULL.search(cand) or (cells and (_MD.fullmatch(cand.strip()) or _HM.fullmatch(cand.strip()))) or _AGO.search(cand):
                date_text = cand
                break
        sid = id_from_url(full) if "#js-" not in full else full.split("#js-")[1]
        if sid in seen:
            continue
        seen.add(sid)
        notice = bool(re.search(r"공지|notice", " ".join(row.get("class", [])) + " " + (cells[0] if cells else ""), re.I))
        posted = parse_board_date(date_text, now) if date_text else None
        deadline = ""
        if posted and posted > (now or config.now()) + timedelta(days=2):
            # 앞으로의 날짜는 올린 날이 아니라 마감일이다
            posted, deadline = None, date_text
        items.append(
            ListItem(
                title=title,
                url=full,
                source_id=sid,
                date_text=date_text,
                posted_at=posted,
                row_text=row_text,
                cells=cells,
                is_notice=notice,
                deadline_text=deadline,
            )
        )
    return items


def _url_shape(u: str) -> str:
    p = urlparse(u)
    path = re.sub(r"\d+", "#", p.path)
    qs = parse_qs(p.query)
    keys = ",".join([k for k in ID_PARAMS if k in qs][:1]) or ",".join(sorted(k for k in qs if not _VOLATILE_PARAMS.match(k)))
    return f"{p.netloc}{path}?{keys}{'#js' if '#js-' in u else ''}"


def _dated_rows(anchors, title_anchor) -> list:
    """날짜가 있는 줄들 중 같은 묶음(부모)에 가장 많이 모인 것. (a, 주소, 제목, 줄)"""
    groups: dict[int, list] = {}
    for a, full, title in anchors:
        row = _dated_row(a, title, title_anchor)
        if row is None:
            continue
        groups.setdefault(id(row.parent), []).append((a, full, title, row))
    if not groups:
        return []
    # 한 줄에 링크가 두 개(제목+첨부 등)면 글자가 긴 쪽 하나만
    for key, rows in groups.items():
        best: dict[int, tuple] = {}
        for x in rows:
            k = id(x[3])
            if k not in best or len(x[2]) > len(best[k][2]):
                best[k] = x
        groups[key] = [x for x in rows if best[id(x[3])] is x]
    main = max(groups.values(), key=len)
    if len(main) < 2:
        return []
    # 공지 표·일반 표처럼 나뉜 경우: 주소 모양이 같은 다른 묶음도 함께
    shape = max({_url_shape(x[1]) for x in main}, key=lambda sh: sum(_url_shape(x[1]) == sh for x in main))
    out = list(main)
    for rows in groups.values():
        if rows is not main and len(rows) >= 2 and all(_url_shape(x[1]) == shape for x in rows):
            out.extend(rows)
    order = {id(a): i for i, (a, _, _) in enumerate(anchors)}
    out.sort(key=lambda x: order[id(x[0])])
    return out


_MENU_BOX = re.compile(
    r"(?:^|[-_ ])(?:gnb|lnb|snb|nav|menu|header|footer|quick|sitemap|site-map|util|breadcrumb|location|"
    r"family|banner|side|aside|allmenu|topmenu|leftmenu|submenu|depth\d?)(?:$|[-_ \d])",
    re.I,
)


def _in_menu(a: Tag) -> bool:
    for p in a.parents:
        if p.name in ("nav", "header", "footer", "aside"):
            return True
        marks = " ".join(p.get("class", []) or []) + " " + (p.get("id") or "")
        if marks.strip() and _MENU_BOX.search(marks):
            return True
    return False


def _same_shape(anchors) -> list:
    """가장 많이 반복되는 주소 모양(경로 + 쿼리 이름들)의 링크. 메뉴·머리말·꼬리말 안의 링크는 뺀다."""
    anchors = [x for x in anchors if not _in_menu(x[0])]
    if not anchors:
        return []
    counts: dict[str, int] = {}
    for _, full, _ in anchors:
        counts[_url_shape(full)] = counts.get(_url_shape(full), 0) + 1
    best = max(counts, key=lambda k: counts[k])
    if counts[best] < 3:
        return []
    return [x for x in anchors if _url_shape(x[1]) == best]


# ──────────────────────────── 본문 ────────────────────────────

CONTENT_SELECTORS = [
    "#bo_v_con", "#bo_v_atc", ".view_content", ".view-content", ".board_view .content", ".board-view .content",
    ".bbs_view .content", ".view_cont", ".view-cont", ".bbs-view", ".board_view", ".board-view", ".view_wrap",
    ".viewCont", ".cont_view", ".article-body", ".article_body", ".post-content", ".post_content", ".entry-content",
    "#article", "article", ".content_view", "td.content", "#content .view", ".tbl_view", ".table_view", ".detail",
]


def extract_main_text(html: str, *, selectors: list[str] | None = None, min_len: int = 30) -> str:
    """본문으로 보이는 부분의 텍스트."""
    s = soup(html)
    for t in s(NOISE_TAGS):
        t.decompose()
    # 공공기관 게시판은 본문 전체가 <form> 안에 있는 경우가 많아서 form 은 지우지 않고 껍데기만 벗긴다
    for f in s.find_all("form"):
        f.unwrap()
    for sel in (selectors or []) + CONTENT_SELECTORS:
        try:
            node = s.select_one(sel)
        except Exception:
            continue
        if node:
            txt = text_of(node)
            if len(txt) >= min_len:
                return txt
    # 선택자로 못 찾으면 텍스트가 가장 많은 블록
    best, best_len = None, 0
    for node in s.find_all(["div", "td", "section", "article"]):
        own = node.get_text(" ", strip=True)
        links = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        score = len(own) - 2 * links
        depth_penalty = len(node.find_all(["div", "td", "section"])) * 5
        score -= depth_penalty
        if score > best_len:
            best, best_len = node, score
    return text_of(best) if best is not None else text_of(s.body)


def find_label_value(html_or_text: str, labels: list[str]) -> str:
    """'근무지역 : 서울 강남구' 같은 표에서 값 찾기."""
    if "<" in html_or_text:
        s = soup(html_or_text)
        for th in s.find_all(["th", "dt", "label", "strong", "span", "td"]):
            key = th.get_text(" ", strip=True)
            if any(lbl == key or (len(key) <= 12 and lbl in key) for lbl in labels):
                sib = th.find_next_sibling(["td", "dd", "span", "div"])
                if sib:
                    val = sib.get_text(" ", strip=True)
                    if val:
                        return val[:120]
        text = text_of(s.body or s)
    else:
        text = html_or_text
    for lbl in labels:
        m = re.search(re.escape(lbl) + r"\s*[:：]\s*([^\n]{1,80})", text)
        if m:
            return m.group(1).strip()
    return ""


# ──────────────────────────── 로그인 폼 ────────────────────────────


@dataclass
class LoginForm:
    action: str
    method: str
    fields: dict
    user_field: str
    pass_field: str


def find_login_form(html: str, base_url: str) -> LoginForm | None:
    s = soup(html)
    for form in s.find_all("form"):
        pw = form.find("input", {"type": "password"})
        if not pw or not pw.get("name"):
            continue
        fields = {}
        user_field = ""
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
                continue
            if typ in ("submit", "button", "image"):
                continue
            fields[name] = inp.get("value", "")
            if typ in ("text", "email", "tel") and not user_field and inp is not pw:
                user_field = name
        if not user_field:
            for cand in ("mb_id", "user_id", "userid", "id", "login_id", "username", "email", "uid", "memberId"):
                if cand in fields:
                    user_field = cand
                    break
        if not user_field:
            continue
        return LoginForm(
            action=urljoin(base_url, form.get("action") or base_url),
            method=(form.get("method") or "post").lower(),
            fields=fields,
            user_field=user_field,
            pass_field=pw["name"],
        )
    return None


def looks_like_login_page(html: str) -> bool:
    s = soup(html)
    if s.find("input", {"type": "password"}):
        return True
    text = s.get_text(" ", strip=True)[:3000]
    return bool(re.search(r"로그인\s*(?:이|후)\s*(?:필요|이용|가능)|회원만\s*(?:이용|열람)|권한이\s*없", text))


_EMPTY_BOARD = re.compile(
    r"(?:등록된|검색된|작성된)?\s*(?:게시물|게시글|글|자료|데이터|공고|채용\s*공고|내용)(?:이|가)?\s*(?:없습니다|존재하지\s*않습니다)"
)


def looks_empty_board(html: str) -> bool:
    """'게시물이 없습니다' 처럼 글이 하나도 없는 게시판인지."""
    s = soup(html)
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    return bool(_EMPTY_BOARD.search(re.sub(r"\s+", " ", s.get_text(" ", strip=True))))
