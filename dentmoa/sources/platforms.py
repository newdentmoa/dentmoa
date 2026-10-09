"""병원들이 함께 쓰는 채용 플랫폼 읽기 (hospitals 수집기가 게시판 주소를 보고 고른다).

recruiter.co.kr (마이다스아이티) — 2026-10 확인:
- 병원마다 <병원>.recruiter.co.kr 주소를 쓴다 (예: snudh=서울대치과병원·장애인치과병원, yuhs=세브란스 계열, paik=백병원들).
  화면 주소가 /career/home 처럼 바뀐 곳도 예전 목록 기능(/app/jobnotice/list.json)은 그대로 있다.
- 목록: POST /app/jobnotice/list.json  (X-Requested-With: XMLHttpRequest)
    keyword=검색어 & searchByNameOnly=true(공고명에서만) & jobnoticeStateCode=10 & pageSize & currentPage
  → {"pageUtil": {...}, "list": [{"jobnoticeSn", "jobnoticeName", "recruitClassName", "receiptState",
       "applyStartDate": {"time": 밀리초}, "applyEndDate": {...}, "systemKindCode"}]}
- 본문: /app/jobnotice/view?systemKindCode=MRS2&jobnoticeSn=번호 → #viewSmartEditorContent (숨겨진 본문 HTML).
  본문이 그림 한 장뿐인 공고도 많다 → 그때는 제목·접수기간만 남는다.

incruit (recruit.incruit.com/<회사>/job/) 는 서버에서 그린 목록이라 일반 게시판 읽기(find_list_items)로 읽힌다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from .. import config
from .base import PoliteSession, SourceError, StructureError, decode
from .htmlutil import extract_main_text, soup, text_of

RECRUITER_HOST = re.compile(r"^([a-z0-9-]+)\.recruiter\.co\.kr$", re.I)
RECRUITER_KEYWORDS = ("치과", "구강")


def recruiter_host(url: str) -> str:
    """'https://snudh.recruiter.co.kr/career/home' → 'snudh' (아니면 '')"""
    m = RECRUITER_HOST.match(urlparse(url).hostname or "")
    return m.group(1).lower() if m else ""


@dataclass
class PlatformItem:
    sid: str
    title: str
    url: str
    posted_at: datetime | None
    period: str  # 접수기간 글자
    category: str  # 공고 구분 (전문의, 일반직 …)
    state: str  # 접수중 / 접수마감


def _ms(d) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(d["time"]) / 1000, config.TZ)
    except (TypeError, KeyError, ValueError, OSError):
        return None


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y.%m.%d %H:%M") if dt else ""


def recruiter_items(http: PoliteSession, host: str, keyword: str, *, page_size: int = 50) -> list[PlatformItem]:
    base = f"https://{host}.recruiter.co.kr"
    r = http.post(
        f"{base}/app/jobnotice/list.json",
        data={"recruitClassSn": "", "recruitClassName": "", "jobnoticeStateCode": "10", "pageSize": str(page_size),
              "searchByNameOnly": "true", "currentPage": "1", "keyword": keyword},
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{base}/app/jobnotice/list",
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
    )
    if r.status_code >= 400:
        raise SourceError(f"채용 사이트 접속 실패 (HTTP {r.status_code})")
    try:
        data = r.json()
    except ValueError as e:
        raise StructureError("채용 사이트(recruiter.co.kr) 목록 형식이 바뀌었습니다.") from e
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise StructureError("채용 사이트(recruiter.co.kr) 목록 형식이 바뀌었습니다.")
    out = []
    for x in data["list"]:
        sn, name = x.get("jobnoticeSn"), (x.get("jobnoticeName") or "").strip()
        if not sn or not name:
            continue
        start, end = _ms(x.get("applyStartDate")), _ms(x.get("applyEndDate"))
        kind = x.get("systemKindCode") or "MRS2"
        out.append(PlatformItem(
            sid=f"rc-{host}:{sn}",
            title=re.sub(r"\s+", " ", name),
            url=f"{base}/app/jobnotice/view?systemKindCode={kind}&jobnoticeSn={sn}",
            posted_at=start,
            period=f"{_fmt(start)} ~ {_fmt(end)}".strip(" ~"),
            category=(x.get("recruitClassName") or "").strip(),
            state=(x.get("receiptState") or "").strip(),
        ))
    return out


def recruiter_body(html: str, it: PlatformItem) -> str:
    s = soup(html)
    box = s.select_one("#viewSmartEditorContent")
    content = text_of(box) if box else ""
    if not content:
        ta = s.select_one("textarea#jobnoticeContents")
        if ta:
            content = text_of(soup(ta.get_text()))
    if not content:
        content = extract_main_text(html, selectors=["td.view-bbs-contents", "table.horizon"])
    head = [f"접수기간: {it.period}"] if it.period else []
    if it.category:
        head.append(f"구분: {it.category}")
    if it.state:
        head.append(f"상태: {it.state}")
    files = [a.get_text(" ", strip=True) for a in s.select("a.fileWrapperView")]
    if files:
        head.append("첨부: " + ", ".join(files[:6]))
    return "\n".join(head) + ("\n\n" + content.strip() if content.strip() else "")


def fetch_page(http: PoliteSession, url: str) -> tuple[str, str]:
    r = http.get(url)
    if r.status_code >= 400:
        raise SourceError(f"게시판 접속 실패 (HTTP {r.status_code})")
    return decode(r), r.url
