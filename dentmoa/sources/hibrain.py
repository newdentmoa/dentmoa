"""하이브레인넷 (hibrain.net) — 대학 교원 채용정보.

치과대학·치의학전문대학원 전임교원, 기금·연구교수, 일부 대학병원 임상교수 공고가 올라온다.

2026-10 실제 화면 확인:
- 검색: /recruitment/recruits?keyword=치과&listType=ING (진행 중 공고). 검색어는 제목뿐 아니라 본문에도 걸린다
  (그래서 '투자유치과' 같은 엉뚱한 글도 섞인다 — 분류기가 걸러낸다).
- 목록: ul#articleList > li.row 에 번호(.td_number), 제목 링크, 접수기간(.td_receipt), 등록일(.td_rdtm).
  같은 화면의 광고 칸에도 /recruitment/recruits/번호 링크가 있으므로 #articleList 안만 본다.
- 본문: .viewContent 안에 요약 표(td.tdLabel → 값)와 본문. 원본 사이트로 연결만 하는 공고는 본문이 짧아서
  <meta name="description"> 의 요약을 함께 쓴다. 일부 공고는 회원 전용이라 로그인 화면이 나온다 → 제목만 저장.
"""

from __future__ import annotations

import re

from .aggregators import AggregatorSource, Detail, Query
from .htmlutil import ListItem, extract_main_text, parse_board_date, soup, text_of

BASE = "https://www.hibrain.net"
LIST_URL = f"{BASE}/recruitment/recruits"
ITEM_RE = re.compile(r"/recruitment/recruits/(\d+)")
NOT_DENTAL_RE = re.compile(r"(?:유|투자유|관광유|기업유)치과")


class HibrainSource(AggregatorSource):
    key = "hibrain"
    label = "하이브레인넷"
    homepage = "https://www.hibrain.net"
    default_inst_type = None
    item_href = r"/recruitment/recruits/\d+"
    queries = [
        Query(LIST_URL, {"keyword": kw, "listType": "ING", "displayType": "TIT", "sortType": "SORTDTM", "pagesize": "50", "page": "{page}"})
        for kw in ("치과", "치의")
    ]
    content_selectors = [".viewContent", ".contentBody"]

    def wanted(self, it: ListItem) -> bool:
        # 검색어가 본문에 걸린 글은 다 보지만, 제목의 '투자유치과' 같은 부서 이름만 걸린 글은 뺀다
        return not (NOT_DENTAL_RE.search(it.title) and not re.search(r"(?<!유)치과|치의|구강", NOT_DENTAL_RE.sub("", it.title)))

    def parse_list(self, html: str, url: str) -> list[ListItem]:
        s = soup(html)
        box = s.select_one("#articleList") or s
        items: list[ListItem] = []
        for li in box.select("li.row"):
            a = li.find("a", href=ITEM_RE)
            if not a:
                continue
            title = (a.get("title") or a.get_text(" ", strip=True)).strip()
            if not title:
                continue
            sid = ITEM_RE.search(a["href"]).group(1)
            reg = li.select_one(".td_rdtm")
            receipt = li.select_one(".td_receipt")
            reg_text = reg.get_text(" ", strip=True) if reg else ""
            items.append(
                ListItem(
                    title=re.sub(r"\s+", " ", title),
                    url=f"{LIST_URL}/{sid}",
                    source_id=sid,
                    date_text=reg_text,
                    posted_at=parse_board_date(reg_text),
                    row_text=re.sub(r"\s+", " ", li.get_text(" ", strip=True)),
                    deadline_text=re.sub(r"\s+", " ", receipt.get_text(" ", strip=True)) if receipt else "",
                )
            )
        return items

    def parse_detail(self, html: str, it: ListItem) -> Detail:
        s = soup(html)
        view = s.select_one(".viewContent")
        if view is None:
            # 회원 전용 공고 (로그인 화면) → 목록의 제목·접수기간만
            return Detail(body=f"접수기간: {it.deadline_text}" if it.deadline_text else "", extra={"members_only": True})
        info: dict[str, str] = {}
        for lbl in s.select("td.tdLabel"):
            val = lbl.find_next_sibling("td")
            key = lbl.get_text("", strip=True)
            if val and key not in info:
                info[key] = re.sub(r"\s+", " ", val.get_text(" ", strip=True))
        for noise in view.select(".viewCountInfo, .viewMenu, .buttonSet, .warning, .tabMenuWrap"):
            noise.decompose()
        body = text_of(view) or extract_main_text(html, selectors=self.content_selectors)
        meta = s.find("meta", attrs={"name": "description"})
        summary = (meta.get("content") or "").strip() if meta else ""
        if summary and summary[:40] not in body:
            body = f"{summary}\n\n{body}"
        inst = info.get("기관", "")
        if info.get("세부기관"):
            inst = f"{inst} {info['세부기관']}".strip()
        opened = s.select_one(".recruitOpendate li span")
        return Detail(
            body=body,
            institution=inst,
            region=info.get("근무예정지", ""),
            deadline=info.get("접수기간", ""),
            posted_text=opened.get_text(strip=True) if opened else "",
        )
