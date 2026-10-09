"""공개 채용정보 사이트 공통 처리 (로그인 불필요).

검색어 여러 개로 목록을 받아서, 처음 보는 글만 본문을 연다.
사이트마다 목록·본문 모양이 다르면 parse_list / parse_detail 을 고쳐 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlencode

from ..models import RawPosting
from .base import FetchContext, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import ListItem, extract_main_text, find_label_value, find_list_items, parse_board_date

INST_WORDS = re.compile(r"병원|의료원|공단|센터|대학교|대학|재단|공사|보건소|연구원|협회|학교")


@dataclass
class Query:
    url: str  # 페이지 번호 자리는 {page}
    params: dict
    method: str = "get"  # post 면 params 를 폼으로 보낸다
    max_pages: int | None = None  # 이 검색은 몇 쪽까지만 볼지 (없으면 출처 기본값)


@dataclass
class Detail:
    """본문 화면에서 읽은 것. 빈 값은 목록에서 읽은 값을 쓴다."""

    body: str = ""
    title: str = ""
    institution: str = ""
    region: str = ""
    deadline: str = ""
    posted_text: str = ""
    extra: dict = field(default_factory=dict)


class AggregatorSource(Source):
    source_kind = "aggregator"
    dentist_only = False
    item_href: str = ""  # 글 링크 정규식
    queries: list[Query] = []
    content_selectors: list[str] = []
    inst_labels = ["기관명", "기관", "채용기관", "대학명", "학교명", "소속"]
    region_labels = ["근무지", "근무지역", "근무 지역", "지역", "소재지"]
    deadline_labels = ["마감일", "접수마감", "접수기간", "모집기간", "지원기간", "원서접수", "채용기간"]
    pages_factor = 1  # 목록 페이지를 max_pages 의 몇 배까지 볼지

    def wanted(self, it: ListItem) -> bool:
        """본문까지 열어볼 글인지 (기본: 전부)."""
        return True

    def parse_list(self, html: str, url: str) -> list[ListItem]:
        return find_list_items(html, url, href_pattern=self.item_href)

    def parse_detail(self, html: str, it: ListItem) -> Detail:
        return Detail(
            body=extract_main_text(html, selectors=self.content_selectors),
            institution=find_label_value(html, self.inst_labels),
            region=find_label_value(html, self.region_labels),
            deadline=find_label_value(html, self.deadline_labels),
        )

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        http = PoliteSession(self.key, ctx, persist_cookies=False)
        seen: set[str] = set()
        any_list = False
        errors = []
        for q in self.queries:
            try:
                pages = q.max_pages or ctx.max_pages * self.pages_factor
                for page in range(1, pages + 1):
                    items = self._list(http, ctx, q, page)
                    if items is None:
                        break
                    any_list = True
                    fresh = [i for i in items if i.source_id not in seen]
                    if not fresh:
                        break
                    oldest = None
                    for it in fresh:
                        seen.add(it.source_id)
                        if it.posted_at:
                            oldest = it.posted_at if oldest is None else min(oldest, it.posted_at)
                        if it.posted_at and it.posted_at < ctx.since:
                            continue
                        if not self.wanted(it):
                            continue
                        if it.source_id in ctx.known_ids:
                            yield self._title_only(it)
                            continue
                        yield self._detail(http, ctx, it)
                    if oldest and oldest < ctx.since:
                        break
            except SourceError as e:
                errors.append(str(e))
                ctx.log(f"검색 실패: {e}")
        if not any_list:
            raise StructureError(f"{self.label} 검색 결과 목록을 찾지 못했습니다. " + " / ".join(errors)[:300])

    def _list(self, http: PoliteSession, ctx: FetchContext, q: Query, page: int) -> list[ListItem] | None:
        params = {k: (v.format(page=page) if isinstance(v, str) else v) for k, v in q.params.items()}
        url = q.url.format(page=page)
        if q.method == "post":
            r = http.post(url, data=params, headers={"Referer": url})
        else:
            r = http.get(url, params=params)
        if r.status_code >= 400:
            raise SourceError(f"HTTP {r.status_code}: {url}?{urlencode(params)}")
        html = decode(r)
        if ctx.save_debug:
            ctx.dump(f"{self.key}-list-{abs(hash(str(params))) % 10000}-{page}.html", html)
        items = self.parse_list(html, r.url)
        return items or ([] if page > 1 else None)

    def _inst_from_cells(self, it: ListItem) -> str:
        if it.institution:
            return it.institution
        for c in it.cells or []:
            if c and c != it.title and len(c) <= 40 and INST_WORDS.search(c):
                return c
        return ""

    def _title_only(self, it: ListItem) -> RawPosting:
        """이미 저장된 글 — 본문을 다시 열지 않는다."""
        return RawPosting(self.key, it.source_id, it.url, it.title, posted_at=it.posted_at,
                          institution_hint=self._inst_from_cells(it)[:60], region_hint=it.region[:60])

    def _detail(self, http: PoliteSession, ctx: FetchContext, it: ListItem) -> RawPosting:
        r = http.get(it.url)
        html = decode(r)
        if ctx.save_debug:
            ctx.dump(f"{self.key}-detail-{re.sub(r'[^A-Za-z0-9]', '_', it.source_id)[:40]}.html", html)
        d = self.parse_detail(html, it)
        body = d.body
        deadline = d.deadline or it.deadline_text
        if deadline and deadline not in body:
            body = f"접수기간: {deadline}\n{body}"
        posted = it.posted_at
        if posted is None:
            if d.posted_text:
                posted = parse_board_date(d.posted_text)
            else:
                m = re.search(r"(?:등록일|게시일|작성일)\s*[:：]?\s*(20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})", html)
                posted = parse_board_date(m.group(1)) if m else None
        return RawPosting(
            source=self.key,
            source_id=it.source_id,
            url=it.url,
            title=d.title or it.title,
            body=body,
            posted_at=posted,
            institution_hint=(d.institution or self._inst_from_cells(it))[:60],
            region_hint=(d.region or it.region)[:60],
            extra=d.extra,
        )
