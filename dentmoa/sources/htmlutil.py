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
NOISE_TAGS = ("script", "style", "noscript", "iframe", "svg", "header", "footer", "nav", "form", "button", "select")


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


def parse_board_date(text: str, now: datetime | None = None) -> datetime | None:
    """게시판에 흔한 날짜 표기를 읽는다. 예: 2026-10-08, 26.10.08, 10-08, 14:32, 3시간 전, 어제"""
    now = now or config.now()
    t = (text or "").strip()
    if not t:
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


def id_from_url(url: str) -> str:
    """주소에서 글 번호를 뽑는다. 못 찾으면 경로 전체."""
    u = urlparse(url)
    qs = parse_qs(u.query)
    for p in ID_PARAMS:
        if p in qs and qs[p][0]:
            return qs[p][0]
    m = re.search(r"/(\d{2,})(?:/|\.html?)?$", u.path)
    if m:
        return m.group(1)
    return (u.path + ("?" + u.query if u.query else "")).strip("/") or url


def _row_of(a: Tag) -> Tag:
    for parent in a.parents:
        if parent.name in ("tr", "li"):
            return parent
        if parent.name in ("div", "article") and parent.get("class") and any(
            re.search(r"(item|row|list|post|article|card|board)", c, re.I) for c in parent.get("class", [])
        ):
            return parent
        if parent.name in ("table", "ul", "ol", "body"):
            break
    return a.parent if isinstance(a.parent, Tag) else a


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
    없으면 '같은 모양의 링크가 여러 줄 반복되는 것'을 글 목록으로 본다.
    """
    s = soup(html)
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    anchors = []
    for a in s.find_all("a"):
        href = (a.get("href") or "").strip()
        onclick = a.get("onclick") or ""
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
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

    if not href_pattern and anchors:
        # 가장 많이 반복되는 주소 모양(경로 + 쿼리 이름들)을 글 링크로 본다
        def shape(u: str) -> str:
            p = urlparse(u)
            path = re.sub(r"\d+", "#", p.path)
            qs = parse_qs(p.query)
            keys = ",".join([k for k in ID_PARAMS if k in qs][:1]) or ",".join(sorted(qs.keys()))
            return f"{p.netloc}{path}?{keys}{'#js' if '#js-' in u else ''}"

        counts: dict[str, int] = {}
        for _, full, _ in anchors:
            counts[shape(full)] = counts.get(shape(full), 0) + 1
        best = max(counts, key=lambda k: counts[k])
        if counts[best] < 3:
            return []
        anchors = [x for x in anchors if shape(x[1]) == best]

    items: list[ListItem] = []
    seen: set[str] = set()
    for a, full, title in anchors:
        row = _row_of(a)
        row_text = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in row.find_all(["td", "th"], recursive=False)] or None
        date_text = ""
        for cand in (cells or []) + [row_text]:
            if _FULL.search(cand) or (cells and (_MD.fullmatch(cand.strip()) or _HM.fullmatch(cand.strip()))) or _AGO.search(cand):
                date_text = cand
                break
        sid = id_from_url(full) if "#js-" not in full else full.split("#js-")[1]
        if sid in seen:
            continue
        seen.add(sid)
        notice = bool(re.search(r"공지|notice", " ".join(row.get("class", [])) + " " + (cells[0] if cells else ""), re.I))
        items.append(
            ListItem(
                title=title,
                url=full,
                source_id=sid,
                date_text=date_text,
                posted_at=parse_board_date(date_text, now) if date_text else None,
                row_text=row_text,
                cells=cells,
                is_notice=notice,
            )
        )
    return items


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
