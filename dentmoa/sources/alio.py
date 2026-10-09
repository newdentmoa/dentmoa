"""잡알리오 (job.alio.go.kr) — 공공기관 채용정보.

국립대학교 치과병원(서울대·경북대·부산대·강원대 치과병원)과
국립중앙의료원, 보훈병원 같은 공공 병원은 이곳에 채용 공고를 올려야 한다.

2026-10 실제 화면 확인:
- 검색은 recruit.do 에 폼을 POST 로 보낸다 (keyword=검색어, org_name=기관 코드, pageNo, pageSet=한 쪽 글 수).
  기본 검색 기간은 최근 2개월이다.
- 목록 표: 선택 | 번호 | 채용제목 | 기관명 | 근무지 | 고용형태 | 등록일 | 마감일 | 상태
  제목 칸의 링크(<a href="/recruitview.do?idx=...">)는 글자가 비어 있고 제목은 링크 바로 뒤에 있다.
- 본문(recruitview.do?idx=)은 #txt 안에 요약 표(.detailTxt)와 상세요강(#tab-1)이 있다.
"""

from __future__ import annotations

import re

from .aggregators import AggregatorSource, Detail, Query
from .htmlutil import ListItem, extract_main_text, parse_board_date, soup

BASE = "https://job.alio.go.kr"
LIST_URL = f"{BASE}/recruit.do"
VIEW_RE = re.compile(r"recruitview\.do\?(?:.*&)?idx=(\d+)")
# 잡알리오 기관 선택 목록의 치과병원 코드 (기관 이름에만 '치과'가 있는 공고도 놓치지 않게)
DENTAL_ORGS = {
    "C0084": "서울대학교치과병원",
    "C1031": "경북대학교치과병원",
    "C0853": "부산대학교치과병원",
    "C0003": "강원대학교치과병원",
}
_COMMON = {"pageNo": "{page}", "pageSet": "50", "order": "REG_DATE", "sort": "DESC"}


class AlioSource(AggregatorSource):
    key = "alio"
    label = "잡알리오"
    homepage = LIST_URL
    default_inst_type = "public_hospital"
    item_href = r"recruitview\.do\?.*idx=\d+"
    queries = [Query(LIST_URL, {"keyword": "치과", **_COMMON}, method="post")] + [
        Query(LIST_URL, {"org_name": code, **_COMMON}, method="post", max_pages=1) for code in DENTAL_ORGS
    ]
    content_selectors = ["#txt", "#contentRV"]

    def parse_list(self, html: str, url: str) -> list[ListItem]:
        s = soup(html)
        items: list[ListItem] = []
        for tr in s.find_all("tr"):
            a = tr.find("a", href=VIEW_RE)
            if not a:
                continue
            cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all("td", recursive=False)]
            title_td = a.find_parent("td")
            title = re.sub(r"\s+", " ", title_td.get_text(" ", strip=True) if title_td else a.get_text(" ", strip=True))
            if not title:
                continue
            # 번호 칸 다음부터: 제목, 기관명, 근무지, 고용형태, 등록일, 마감일, 상태
            idx = cells.index(title) if title in cells else 2
            rest = cells[idx + 1:] + [""] * 6
            inst, region, _kind, reg, deadline = rest[0], rest[1], rest[2], rest[3], rest[4]
            items.append(
                ListItem(
                    title=title,
                    url=f"{BASE}/recruitview.do?idx={VIEW_RE.search(a['href']).group(1)}",
                    source_id=VIEW_RE.search(a["href"]).group(1),
                    date_text=reg,
                    posted_at=parse_board_date(reg),
                    row_text=" ".join(cells),
                    cells=cells,
                    institution=inst,
                    region=region,
                    deadline_text=re.sub(r"\s*D[-+]\d+.*$", "", deadline),
                )
            )
        return items

    def parse_detail(self, html: str, it: ListItem) -> Detail:
        s = soup(html)
        info = {}
        for th in s.select(".detailTxt th"):
            td = th.find_next_sibling("td")
            if td:
                info[th.get_text(" ", strip=True)] = re.sub(r"\s+", " ", td.get_text(" ", strip=True))
        inst = s.select_one(".topInfo h2")
        title = s.select_one(".topInfo .titleH2")
        link = s.select_one(".infoLink a[href]")
        for noise in s.select(".benner, .tabsContent, #txt > h3"):
            noise.decompose()
        return Detail(
            body=extract_main_text(str(s), selectors=self.content_selectors),
            title=title.get_text(" ", strip=True) if title else "",
            institution=inst.get_text(" ", strip=True) if inst else "",
            region=info.get("근무지", ""),
            deadline=info.get("채용기간", ""),
            posted_text=info.get("등록일", ""),
            extra={"original_url": link["href"]} if link and link["href"].startswith("http") else {},
        )
