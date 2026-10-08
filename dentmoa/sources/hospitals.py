"""병원 홈페이지 채용 게시판 (치과대학병원, 장애인치과병원 등).

data/institutions.json 에서 recruit_url 이 있고 watch 가 true 인 기관의 게시판을 돌면서
치과의사와 관련 있어 보이는 글만 본문까지 읽는다.
게시판마다 모양이 달라서 htmlutil.find_list_items 의 일반적인 방법을 쓴다.
한 곳이 실패해도 나머지는 계속 읽는다.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from .. import config, db, institutions
from ..institutions import Institution
from ..models import RawPosting
from .base import FetchContext, PoliteSession, Source, SourceError, StructureError, decode
from .htmlutil import extract_main_text, find_list_items, parse_board_date

DENTIST_TITLE_RE = re.compile(
    r"치과\s*의사|치의|치과|구강|교수|교원|전임의|임상\s*강사|펠로우|촉탁|전문의|진료의|의사|레지던트|전공의|인턴|초빙"
)
GENERIC_RECRUIT_RE = re.compile(r"채용|모집|초빙|공고|선발|충원")
NON_DENTIST_TITLE_RE = re.compile(
    r"위생사|간호|기공사|조무|행정|사무|원무|시설|미화|보안|운전|약사|영양|방사선사|임상병리|물리치료|전산|청원경찰|"
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
    if inst.kind == "dental_school":
        # 대학 전체 교원 채용 게시판인 경우가 많다 → 치과 관련 단어나 교원·교수 초빙 공고만
        return bool(re.search(r"치과|치의|구강|교원|교수|초빙", title))
    if inst.kind in DENTAL_KINDS:
        # 치과 기관은 일반 채용 공고에도 치과의사가 섞여 있을 수 있다
        return bool(DENTIST_TITLE_RE.search(title) or GENERIC_RECRUIT_RE.search(title))
    # 일반 병원은 치과 관련 단어가 제목에 있어야 한다
    return bool(re.search(r"치과|치의|구강", title))


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

    def _board(self, inst: Institution, ctx: FetchContext) -> Iterable[RawPosting]:
        http = PoliteSession(f"board-{_slug(inst)}", ctx, persist_cookies=False)
        r = http.get(inst.recruit_url, polite=False)
        if r.status_code >= 400:
            raise SourceError(f"게시판 접속 실패 (HTTP {r.status_code})")
        html = decode(r)
        if ctx.save_debug:
            ctx.dump(f"board-{_slug(inst)}-list.html", html)
        items = find_list_items(html, r.url)
        if not items:
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
                yield RawPosting(self.key, sid, it.url.split("#js-")[0], it.title, posted_at=it.posted_at,
                                 institution_hint=inst.name, region_hint=_region(inst))
                continue
            details += 1
            dr = http.get(it.url)
            dhtml = decode(dr)
            if ctx.save_debug:
                ctx.dump(f"board-{_slug(inst)}-{re.sub(r'[^A-Za-z0-9]', '_', it.source_id)[:40]}.html", dhtml)
            body = extract_main_text(dhtml)
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
                institution_hint=inst.name,
                region_hint=_region(inst),
            )


def _region(inst: Institution) -> str:
    return f"{inst.sido} {inst.sigungu or ''}".strip()
