"""나라일터 (gojobs.go.kr) — 정부·지자체·국립병원 채용.

보건소 치과의사(임기제공무원), 국립병원·교정시설 치과의사 공고가 올라온다.

2026-10 실제 화면 확인:
- 검색: apmList.do 에 폼을 POST (searchKeyword=공고명 검색어, pageIndex=쪽, menuNo=401). 최신순, 한 쪽에 10건.
  검색 없이 넘기면 하루 150건 정도라 공고명 검색('치과', '구강')으로 좁힌다.
- 목록 표: 번호 | 공고명 | 기관명 | 공고게시일 | 접수마감일 | 조회
  공고명 링크는 javascript:fn_apmView('직종코드', '공고번호') 이고, 긴 공고명은 목록에서 잘린다.
- 본문: apmView.do?empmnsn=공고번호 (GET 으로도 열린다). #apmViewTbl 표 안에 공고명·기관명·근무지역·본문.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import RawPosting
from .base import FetchContext, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import extract_main_text, parse_board_date, soup, text_of

BASE = "https://www.gojobs.go.kr"
LIST_URL = f"{BASE}/apmList.do"
VIEW_URL = BASE + "/apmView.do?empmnsn={id}"
KEYWORDS = ("치과", "구강")
VIEW_RE = re.compile(r"fn_apmView\(\s*'(\w*)'\s*,\s*'(\d+)'")
DATE_RE = re.compile(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}")


class GojobsSource(Source):
    key = "gojobs"
    label = "나라일터"
    homepage = LIST_URL
    source_kind = "aggregator"
    dentist_only = False
    default_inst_type = "public_hospital"

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        http = PoliteSession(self.key, ctx, persist_cookies=False)
        found_any = False
        seen: set[str] = set()
        errors: list[str] = []
        for kw in KEYWORDS:
            for page in range(1, ctx.max_pages + 1):
                try:
                    r = http.post(LIST_URL, data={"menuNo": "401", "pageIndex": page, "searchKeyword": kw},
                                  headers={"Referer": LIST_URL})
                except SourceError as e:
                    errors.append(str(e))
                    break
                if r.status_code >= 400:
                    errors.append(f"HTTP {r.status_code}")
                    break
                html = decode(r)
                if ctx.save_debug:
                    ctx.dump(f"gojobs-list-{len(seen)}-{page}.html", html)
                rows = [row for row in self._rows(html) if row[0] not in seen]
                if not rows:
                    break
                found_any = True
                oldest = None
                for sid, title, org, posted, deadline in rows:
                    seen.add(sid)
                    if posted:
                        oldest = posted if oldest is None else min(oldest, posted)
                    if posted and posted < ctx.since:
                        continue
                    url = VIEW_URL.format(id=sid)
                    if sid in ctx.known_ids:
                        yield RawPosting(self.key, sid, url, title, posted_at=posted, institution_hint=org)
                        continue
                    yield self._detail(http, ctx, sid, url, title, org, posted, deadline)
                if oldest and oldest < ctx.since:
                    break
        if not found_any:
            if errors:
                raise SourceError("나라일터 접속 실패: " + " / ".join(errors)[:300])
            raise StructureError("나라일터 검색 결과에서 공고를 찾지 못했습니다.")

    def _detail(self, http, ctx, sid, url, title, org, posted, deadline) -> RawPosting:
        d = http.get(url)
        dhtml = decode(d)
        if ctx.save_debug:
            ctx.dump(f"gojobs-detail-{sid}.html", dhtml)
        info = _view_info(dhtml)
        body = _view_body(dhtml)
        if deadline and deadline not in body:
            body = f"접수마감일: {deadline}\n{body}"
        return RawPosting(
            source=self.key,
            source_id=sid,
            url=url,
            title=info.get("공고명") or title,
            body=body,
            posted_at=posted or parse_board_date(info.get("등록일", "")),
            institution_hint=info.get("기관명") or org,
            region_hint=info.get("근무지역", ""),
        )

    def _rows(self, html: str):
        """(공고번호, 공고명, 기관명, 게시일, 마감일)"""
        s = soup(html)
        for tr in s.find_all("tr"):
            link = None
            for a in tr.find_all("a"):
                m = VIEW_RE.search((a.get("onclick") or "") + (a.get("href") or ""))
                if m:
                    link = (a, m.group(2))
                    break
            if not link:
                continue
            a, sid = link
            title = re.sub(r"\s+", " ", a.get("title") or a.get_text(" ", strip=True))
            cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all("td")]
            org = ""
            dates = []
            for c in cells:
                if c == title or not c:
                    continue
                if DATE_RE.fullmatch(c):
                    dates.append(c)
                elif not org and not c.isdigit() and len(c) <= 40 and title not in c:
                    org = c
            posted = parse_board_date(dates[0]) if dates else None
            yield sid, title, org, posted, dates[1] if len(dates) > 1 else ""


def _view_body(html: str) -> str:
    """요약 표 + 공고 내용. 내용은 숨겨진 <textarea id="content"> 에 들어 있고 화면에서 스크립트로 그린다."""
    s = soup(html)
    ta = s.select_one("textarea#content")
    content = ta.get_text() if ta else ""
    if re.search(r"</?(?:p|br|div|span|table|td)\b", content, re.I):
        content = text_of(soup(content))
    if ta:
        ta.decompose()
    table = extract_main_text(str(s), selectors=["#apmViewTbl", ".sub_table_detail"])
    table = table.replace("내용 읽기 - 공고명, 기관명, 조회, 등록일, 마감일, 관련링크, 채용직급, 근무지역, 장애인 채용 우대, 첨부파일 구성", "")
    return f"{table.strip()}\n\n{content.strip()}".strip()


def _view_info(html: str) -> dict[str, str]:
    """본문 표의 '항목 → 값'."""
    s = soup(html)
    table = s.select_one("#apmViewTbl") or s
    info: dict[str, str] = {}
    for th in table.find_all("th"):
        td = th.find_next_sibling("td")
        key = re.sub(r"\s+", "", th.get_text("", strip=True))
        if td and key and key not in info:
            info[key] = re.sub(r"\s+", " ", td.get_text(" ", strip=True))
    return info
