"""나라일터 (gojobs.go.kr) — 정부·지자체·국립병원 채용.

보건소 치과의사(임기제공무원), 국립병원·교정시설 치과의사 공고가 올라온다.
검색어 기능이 확인되지 않아 최신 목록을 넘기면서 제목에 '치과·치의·구강'이 있는 글만 연다.
목록의 링크는 fn_apmView('직종코드','공고번호') 같은 자바스크립트라 공고번호를 직접 뽑는다.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import RawPosting
from .base import FetchContext, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import extract_main_text, find_label_value, parse_board_date, soup

LIST_URL = "https://www.gojobs.go.kr/apmList.do"
VIEW_URL = "https://www.gojobs.go.kr/apmView.do?empmnsn={id}"
TITLE_RE = re.compile(r"치과|치의|구강")
VIEW_RE = re.compile(r"fn_apmView\(\s*'(\d*)'\s*,\s*'(\d+)'")


class GojobsSource(Source):
    key = "gojobs"
    label = "나라일터"
    homepage = LIST_URL
    source_kind = "aggregator"
    dentist_only = False
    default_inst_type = "public_hospital"

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        http = PoliteSession(self.key, ctx, persist_cookies=False)
        # 하루 150건 정도 올라와서 페이지를 조금 더 본다
        pages = ctx.max_pages * 3
        found_any = False
        for page in range(1, pages + 1):
            r = http.get(LIST_URL, params={"menuNo": "401", "pageIndex": page})
            if r.status_code >= 400:
                raise SourceError(f"나라일터 접속 실패 (HTTP {r.status_code})")
            html = decode(r)
            if ctx.save_debug:
                ctx.dump(f"gojobs-list-{page}.html", html)
            rows = list(self._rows(html))
            if not rows:
                if page == 1:
                    raise StructureError("나라일터 목록에서 공고를 찾지 못했습니다.")
                break
            found_any = True
            oldest = None
            for sid, title, org, posted in rows:
                if posted:
                    oldest = posted if oldest is None else min(oldest, posted)
                if posted and posted < ctx.since:
                    continue
                if not TITLE_RE.search(f"{title} {org}"):
                    continue
                url = VIEW_URL.format(id=sid)
                if sid in ctx.known_ids:
                    yield RawPosting(self.key, sid, url, title, posted_at=posted, institution_hint=org)
                    continue
                d = http.get(url)
                dhtml = decode(d)
                if ctx.save_debug:
                    ctx.dump(f"gojobs-detail-{sid}.html", dhtml)
                yield RawPosting(
                    source=self.key,
                    source_id=sid,
                    url=url,
                    title=title,
                    body=extract_main_text(dhtml),
                    posted_at=posted,
                    institution_hint=org or find_label_value(dhtml, ["기관명", "채용기관"]),
                    region_hint=find_label_value(dhtml, ["근무지", "근무지역", "근무예정지"]),
                )
            if oldest and oldest < ctx.since:
                break
        if not found_any:
            raise StructureError("나라일터 목록을 읽지 못했습니다.")

    def _rows(self, html: str):
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
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all("td")]
            # 열: 번호 | 제목 | 기관 | 등록일 | 마감일 | 조회
            org = ""
            dates = []
            for c in cells:
                if c == title or not c:
                    continue
                if re.fullmatch(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", c):
                    dates.append(c)
                elif not org and not c.isdigit() and len(c) <= 40:
                    org = c
            posted = parse_board_date(dates[0]) if dates else None
            yield sid, title, org, posted
