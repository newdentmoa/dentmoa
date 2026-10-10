"""병원 홈페이지 채용 게시판 (치과대학병원, 장애인치과병원 등).

data/institutions.json 에서 recruit_url 이 있고 watch 가 true 인 기관의 게시판을 돌면서
치과의사와 관련 있어 보이는 글만 본문까지 읽는다.
- 일반 게시판: htmlutil.find_list_items (날짜가 있는 줄을 글 목록으로 본다)
- recruiter.co.kr 채용 사이트: platforms.recruiter_items (공고명 검색 '치과', '구강')
- 가톨릭중앙의료원 채용 사이트(recruit.cmcnu.or.kr): platforms.cmc_items — 전체 기관 검색 한 번으로 7개 병원을 함께 읽는다
- fetch="browser" 인 곳: 자바스크립트 확인 화면이 있어 브라우저(Chromium)로 연다
한 곳이 실패해도 나머지는 계속 읽는다.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import ExitStack
from typing import Iterable

from .. import config, db, institutions
from ..institutions import Institution
from ..models import RawPosting
from .base import FetchContext, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import extract_main_text, find_list_items, looks_empty_board, parse_board_date
from .platforms import (
    CMC_KEYWORDS,
    RECRUITER_KEYWORDS,
    cmc_body,
    cmc_hospital,
    cmc_items,
    cmc_site,
    recruiter_body,
    recruiter_host,
    recruiter_items,
)

DENTIST_TITLE_RE = re.compile(
    r"치과\s*의사|치의|치과|구강|교수|교원|전임의|임상\s*강사|펠로우|촉탁|전문의|진료의|의사|레지던트|전공의|인턴|초빙"
)
DENTAL_WORD_RE = re.compile(r"치과|치의|구강")
GENERIC_RECRUIT_RE = re.compile(r"채용|모집|초빙|공고|선발|충원")
NON_DENTIST_TITLE_RE = re.compile(
    r"위생사|치위생|간호|기공사|치기공|조무|행정|사무|원무|시설|미화|보안|운전|약사|영양|방사선사|방사선직|임상병리|물리치료|전산|청원경찰|"
    r"지원직|코디네이터|"
    r"주차|조리|사회복지|연구원|연구보조|보조원|합격자|결과\s*발표|면접\s*(?:안내|일정)"
)
DENTAL_KINDS = {"dental_univ_hospital", "disabled_dental", "dental_school"}
MAX_DETAILS_PER_BOARD = 8


def _slug(inst: Institution) -> str:
    return hashlib.sha1(inst.recruit_url.encode()).hexdigest()[:8]


def watched_boards() -> list[Institution]:
    out = []
    for inst in institutions.load():
        if inst.recruit_url and getattr(inst, "watch", True):
            out.append(inst)
    return out


def is_relevant_title(title: str, inst: Institution) -> bool:
    if NON_DENTIST_TITLE_RE.search(title) and not re.search(r"치과\s*의사|교수|전임의|임상\s*강사|촉탁의", title):
        return False
    if inst.title_filter == "dental":
        # 의료원 전체가 함께 쓰는 게시판 → 치과 관련 글만
        return bool(DENTAL_WORD_RE.search(title))
    if inst.kind == "dental_school":
        # 대학 전체 교원 채용 게시판인 경우가 많다 → 치과 관련 단어나 교원·교수 초빙 공고만
        return bool(re.search(r"치과|치의|구강|교원|교수|초빙", title))
    if inst.kind in DENTAL_KINDS:
        # 치과 기관은 일반 채용 공고에도 치과의사가 섞여 있을 수 있다
        return bool(DENTIST_TITLE_RE.search(title) or GENERIC_RECRUIT_RE.search(title))
    # 일반 병원은 치과 관련 단어가 제목에 있어야 한다
    return bool(DENTAL_WORD_RE.search(title))


def _hints(inst: Institution, text: str = "") -> tuple[str, str]:
    """(기관 이름, 지역) — 여러 기관이 함께 쓰는 게시판이면 비워 두고 분류기가 제목에서 찾게 한다.

    치과대학 항목은 대학 전체 교원 채용 게시판인 경우가 많아서, 제목·본문에 치과 관련 단어가 없는 글
    (예: '객원교수 채용(철학과)')에는 치과대학 이름을 붙이지 않는다 — 붙이면 치과의사 공고로 잘못 분류된다.
    """
    if inst.shared:
        return "", ""
    if inst.kind == "dental_school" and not DENTAL_WORD_RE.search(text):
        return "", _region(inst)
    return inst.name, _region(inst)


class HospitalBoardsSource(Source):
    key = "hospitals"
    label = "병원 채용게시판"
    source_kind = "hospital_board"
    dentist_only = False
    default_inst_type = None

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        boards = watched_boards()
        if not boards:
            raise StructureError("감시할 병원 게시판 목록이 비어 있습니다 (data/institutions.json).")
        status: dict[str, dict] = {}
        ok = 0
        with ExitStack() as stack:
            self._stack, self._browser = stack, None
            for inst in boards:
                try:
                    n = 0
                    for raw in self._board(inst, ctx):
                        n += 1
                        yield raw
                    status[inst.name] = {"ok": True, "message": f"{n}건", "at": config.now().isoformat()}
                    ok += 1
                except SourceError as e:
                    status[inst.name] = {"ok": False, "message": str(e), "at": config.now().isoformat()}
                    ctx.log(f"{inst.name}: {e}")
                except Exception as e:  # 한 곳의 예상 못 한 오류로 전체가 멈추지 않게
                    status[inst.name] = {"ok": False, "message": f"{e.__class__.__name__}: {e}", "at": config.now().isoformat()}
                    ctx.log(f"{inst.name}: {e!r}")
                ctx.sleep()
        try:
            db.kv_set("board_status", status)
        except Exception:
            pass
        if ok == 0:
            raise SourceError(f"병원 게시판 {len(boards)}곳을 모두 읽지 못했습니다.")

    # ─────────────── 게시판 한 곳 ───────────────

    def _board(self, inst: Institution, ctx: FetchContext) -> Iterable[RawPosting]:
        host = recruiter_host(inst.recruit_url)
        if host:
            yield from self._recruiter(inst, host, ctx)
            return
        if cmc_site(inst.recruit_url):
            yield from self._cmc(ctx)
            return
        http = PoliteSession(f"board-{_slug(inst)}", ctx, persist_cookies=False)
        html, url = self._get_list(inst, http)
        if ctx.save_debug:
            ctx.dump(f"board-{_slug(inst)}-list.html", html)
        items = find_list_items(html, url)
        if not items:
            if looks_empty_board(html):
                return  # 올라온 글이 하나도 없는 게시판
            raise StructureError("게시판에서 글 목록을 찾지 못했습니다 (자바스크립트 게시판일 수 있음).")
        details = 0
        for it in items:
            if it.posted_at and it.posted_at < ctx.since:
                continue
            if not is_relevant_title(it.title, inst):
                continue
            sid = f"{_slug(inst)}:{it.source_id}"
            if sid in ctx.known_ids or details >= MAX_DETAILS_PER_BOARD or "#js-" in it.url:
                # 본문을 못 열거나(자바스크립트 링크) 이미 본 글은 제목만
                inst_hint, region_hint = _hints(inst, it.title)
                yield RawPosting(self.key, sid, it.url.split("#js-")[0], it.title, posted_at=it.posted_at,
                                 institution_hint=inst_hint, region_hint=region_hint)
                continue
            details += 1
            dhtml = self._get_page(inst, http, it.url)
            if ctx.save_debug:
                ctx.dump(f"board-{_slug(inst)}-{re.sub(r'[^A-Za-z0-9]', '_', it.source_id)[:40]}.html", dhtml)
            body = extract_main_text(dhtml)
            inst_hint, region_hint = _hints(inst, f"{it.title}\n{body}")
            posted = it.posted_at
            if posted is None:
                m = re.search(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", body[:500])
                posted = parse_board_date(m.group(0)) if m else None
            yield RawPosting(
                source=self.key,
                source_id=sid,
                url=it.url,
                title=it.title,
                body=body,
                posted_at=posted,
                institution_hint=inst_hint,
                region_hint=region_hint,
            )

    def _get_list(self, inst: Institution, http: PoliteSession) -> tuple[str, str]:
        if inst.fetch == "browser":
            return self._browser_get(inst.recruit_url)
        r = http.get(inst.recruit_url, polite=False)
        if r.status_code >= 400:
            raise SourceError(f"게시판 접속 실패 (HTTP {r.status_code})")
        return decode(r), r.url

    def _get_page(self, inst: Institution, http: PoliteSession, url: str) -> str:
        if inst.fetch == "browser":
            http.ctx.sleep()
            return self._browser_get(url)[0]
        return decode(http.get(url))

    def _browser_get(self, url: str) -> tuple[str, str]:
        from .browser import open_browser

        if self._browser is None:
            self._browser = self._stack.enter_context(open_browser(self.key))
        page = self._browser.page
        try:
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            self._browser.human_pause(1.0, 2.0)
        except Exception as e:
            raise SourceError(f"게시판 접속 실패 ({e.__class__.__name__})") from e
        return page.content(), page.url

    # ─────────────── recruiter.co.kr ───────────────

    def _recruiter(self, inst: Institution, host: str, ctx: FetchContext) -> Iterable[RawPosting]:
        http = PoliteSession(f"rc-{host}", ctx, persist_cookies=False)
        inst_hint, region_hint = _hints(inst, "치과")  # 공고명 검색어가 치과·구강
        seen: set[str] = set()
        details = 0
        for kw in RECRUITER_KEYWORDS:
            for it in recruiter_items(http, host, kw):
                if it.sid in seen:
                    continue
                seen.add(it.sid)
                if it.posted_at and it.posted_at < ctx.since:
                    continue
                if not is_relevant_title(it.title, inst):
                    continue
                if it.sid in ctx.known_ids or details >= MAX_DETAILS_PER_BOARD:
                    yield RawPosting(self.key, it.sid, it.url, it.title, posted_at=it.posted_at,
                                     institution_hint=inst_hint, region_hint=region_hint)
                    continue
                details += 1
                html = decode(http.get(it.url))
                if ctx.save_debug:
                    ctx.dump(f"rc-{host}-{it.sid.split(':')[-1]}.html", html)
                yield RawPosting(
                    source=self.key,
                    source_id=it.sid,
                    url=it.url,
                    title=it.title,
                    body=recruiter_body(html, it),
                    posted_at=it.posted_at,
                    institution_hint=inst_hint,
                    region_hint=region_hint,
                )


    # ─────────────── 가톨릭중앙의료원 (recruit.cmcnu.or.kr) ───────────────

    def _cmc(self, ctx: FetchContext) -> Iterable[RawPosting]:
        """전체 기관 검색('치과', '구강')으로 서울성모·여의도·의정부·부천·은평·성빈센트·대전성모를 한 번에 읽는다.

        검색이 본문까지 보므로 제목에 치과가 없어도(예: '임상강사 모집' 에 치과 포함) 가져온다.
        직원(치위생직·간호직 등) 공고는 제목으로 걸러서 열지 않는다.
        """
        http = PoliteSession("cmc", ctx, persist_cookies=False)
        by_name = {i.name: i for i in institutions.load()}
        seen: set[str] = set()
        details = 0
        for kw in CMC_KEYWORDS:
            for it in cmc_items(http, kw):
                if it.sid in seen:
                    continue
                seen.add(it.sid)
                if it.posted_at and it.posted_at < ctx.since:
                    continue
                if NON_DENTIST_TITLE_RE.search(it.title) and not re.search(r"치과\s*의사|교수|전임의|임상\s*강사|촉탁의", it.title):
                    continue
                inst = by_name.get(cmc_hospital(it.title))
                inst_hint, region_hint = (inst.name, _region(inst)) if inst else ("", "")
                if it.sid in ctx.known_ids or details >= MAX_DETAILS_PER_BOARD:
                    yield RawPosting(self.key, it.sid, it.url, it.title, posted_at=it.posted_at,
                                     institution_hint=inst_hint, region_hint=region_hint)
                    continue
                details += 1
                yield RawPosting(
                    source=self.key,
                    source_id=it.sid,
                    url=it.url,
                    title=it.title,
                    body=cmc_body(http, it.url),
                    posted_at=it.posted_at,
                    institution_hint=inst_hint,
                    region_hint=region_hint,
                )


def _region(inst: Institution) -> str:
    return f"{inst.sido} {inst.sigungu or ''}".strip()
