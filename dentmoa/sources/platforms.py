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

가톨릭중앙의료원 채용 사이트 (recruit.cmcnu.or.kr) — 2026-10 확인:
- 서울성모·여의도성모·의정부성모·부천성모·은평성모·성빈센트·대전성모·성의교정·중앙의료원이 함께 쓴다 (인천성모만 recruiter.co.kr).
  예전 주소 /<병원>/application/appList.do 는 없어졌고(404) 각 병원 첫 화면 /<병원>/index.do 에 공고 목록이 있다.
- 첫 화면의 '전체 기관 검색': /cmc/index.do?INST_CD_FILTER=ALL&ALL_ST=ALL(제목+내용)&ALL_SV=검색어(EUC-KR/CP949 로 인코딩)
  → 두 번째 div.emp_tab_ui 안의 <a href=".../application/appView.do?seq_no=번호"> (strong.reduce = '[서울성모]제목', em.data = 날짜).
  열려 있는(접수 중인) 공고만 나온다.
- 글: appView.do?seq_no=번호 → table.table_type01 (제목·기관·교직구분·부서·직종·접수기간) + 그 아래 본문.
- 대부분은 직원(치위생직·치기공직·간호직) 공고이고, 치과의사 공고는 드물다 (합격자 안내 기록 2010년~: 2018년 의정부성모 1건).
- 사이트의 암호화 설정이 오래돼 기본 설정으로는 접속이 거부된다 → base.LEGACY_TLS_HOSTS.

병원 홈페이지 채용 프로그램 (/prog/recruitNotice/list.do — 건양대병원) — 2026-10 확인:
- 서버에서 그린 목록이지만 글 링크가 <a> 가 아니라 단추(<button onclick="fn_search_view('RT2026H…')">)라서
  일반 게시판 읽기로는 안 읽힌다. 단추 안: strong.job > span(직종: 간호직·의사직…) + em(제목), em.period(접수 기간).
- 기본 목록은 공고중·접수중인 글만 보여 준다(마감된 글은 searchOtptSttus=2N). 글이 없으면 '총 게시물 0 건'.
- 글: 같은 폴더의 view.do?noticeId=… → div.program--view (div.info_box 의 접수구분·접수시작일·첨부파일 + div.cnts_txt).
  본문은 PDF 첨부(그림)뿐인 공고가 많다 → 제목·접수 기간·첨부파일 이름만 남는다.
- 충남대병원(cnuh.co.kr/prog/recruitNotice/01/ALL/list.do)도 주소는 같지만 표 모양 목록이고 글이 2020년 1건뿐이라 읽지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urljoin, urlparse

from .. import config
from .base import PoliteSession, SourceError, StructureError, decode
from .htmlutil import extract_main_text, parse_board_date, soup, text_of

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


# ──────────────────────────── 가톨릭중앙의료원 (recruit.cmcnu.or.kr) ────────────────────────────

CMC_HOST = "recruit.cmcnu.or.kr"
CMC_SEARCH = f"https://{CMC_HOST}/cmc/index.do?INST_CD_FILTER=ALL&ALL_ST=ALL&ALL_SV="
CMC_KEYWORDS = ("치과", "구강")
# 공고 제목 앞의 [병원] → data/institutions.json 의 기관 이름
CMC_PREFIX = {
    "서울성모": "가톨릭대학교 서울성모병원",
    "여의도성모": "가톨릭대학교 여의도성모병원",
    "의정부성모": "가톨릭대학교 의정부성모병원",
    "부천성모": "가톨릭대학교 부천성모병원",
    "은평성모": "가톨릭대학교 은평성모병원",
    "성빈센트": "가톨릭대학교 성빈센트병원",
    "대전성모": "가톨릭대학교 대전성모병원",
}


def cmc_site(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == CMC_HOST


def cmc_hospital(title: str) -> str:
    """'[의정부성모] 치과의사 모집' → '가톨릭대학교 의정부성모병원' (모르면 '')"""
    m = re.match(r"\s*\[([^\]]+)\]", title)
    return CMC_PREFIX.get(m.group(1).strip(), "") if m else ""


def _cp949(r) -> str:
    r.encoding = "cp949"  # 사이트는 EUC-KR 이라고 알려 주지만 실제로는 그보다 넓은 CP949
    return r.text


def cmc_items(http: PoliteSession, keyword: str) -> list[PlatformItem]:
    url = CMC_SEARCH + quote(keyword.encode("cp949"))
    r = http.get(url)
    if r.status_code >= 400:
        raise SourceError(f"채용 사이트 접속 실패 (HTTP {r.status_code})")
    sections = soup(_cp949(r)).select("div.emp_tab_ui")
    if len(sections) < 2:
        raise StructureError("가톨릭중앙의료원 채용 사이트의 공고 목록 모양이 바뀌었습니다.")
    out = []
    for a in sections[1].find_all("a", href=re.compile(r"appView\.do\?[^\"']*seq_no=\d+")):
        sn = re.search(r"seq_no=(\d+)", a["href"]).group(1)
        tit = a.select_one("strong") or a
        title = re.sub(r"\s+", " ", tit.get_text(" ", strip=True))
        date = a.select_one("em.data")
        posted = parse_board_date(date.get_text(strip=True)) if date else None
        out.append(PlatformItem(
            sid=f"cmc:{sn}",
            title=title,
            url=urljoin(url, re.sub(r"&cPage=\d+", "", a["href"])),
            posted_at=posted,
            period="",
            category="",
            state="",
        ))
    return out


def cmc_body(http: PoliteSession, url: str) -> str:
    r = http.get(url)
    if r.status_code >= 400:
        raise SourceError(f"공고를 열지 못했습니다 (HTTP {r.status_code})")
    s = soup(_cp949(r))
    area = s.select_one("div.content_area")
    if area is None:
        return extract_main_text(r.text)
    head = []
    table = area.select_one("table.table_type01")
    if table is not None:
        for th in table.find_all("th"):
            td = th.find_next_sibling("td")
            key = th.get_text(strip=True)
            if td is not None and key != "제목":
                value = re.sub(r"\s+", " ", td.get_text(" ", strip=True))
                head.append(f"{key}: {value}")
        table.decompose()
    for btn in area.select("a, button"):
        if btn.get_text(strip=True) in ("지원서 작성하기", "수정/조회", "목록"):
            btn.decompose()
    content = text_of(area)
    return "\n".join(head) + ("\n\n" + content if content else "")


# ──────────────────────────── 병원 홈페이지 채용 프로그램 (/prog/recruitNotice/) ────────────────────────────

RECRUIT_NOTICE_VIEW_RE = re.compile(r"fn_search_view\(\s*'([^']+)'")
RECRUIT_NOTICE_COUNT_RE = re.compile(r"총\s*게시물\s*(\d+)\s*건")


def recruit_notice_site(url: str) -> bool:
    return "/prog/recruitNotice/" in urlparse(url).path


def recruit_notice_items(html: str, list_url: str) -> list[PlatformItem]:
    """'https://www.kyuh.ac.kr/prog/recruitNotice/list.do' 목록 → 공고들. 글이 없는 목록이면 [].

    화면 구조가 바뀌어 글을 못 찾으면 StructureError.
    """
    s = soup(html)
    out = []
    for b in s.select("button[onclick*=fn_search_view]"):
        m = RECRUIT_NOTICE_VIEW_RE.search(b.get("onclick") or "")
        job = b.select_one("strong.job")
        title_el = job.find("em", recursive=False) if job else None
        if not m or title_el is None:
            continue
        title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True))
        if not title:
            continue
        cate = job.find("span", recursive=False)
        state = b.select_one("span.re-stats")
        period_el = b.select_one("em.period")
        if period_el is not None:
            for label in period_el.select("strong.cate"):
                label.decompose()
        period = re.sub(r"\s+", " ", period_el.get_text(" ", strip=True)) if period_el is not None else ""
        d = re.search(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", period)
        notice_id = m.group(1)
        out.append(PlatformItem(
            sid=notice_id,
            title=title,
            url=urljoin(list_url, f"view.do?noticeId={quote(notice_id)}"),
            posted_at=parse_board_date(d.group(0)) if d else None,
            period=period,
            category=re.sub(r"\s+", " ", cate.get_text(" ", strip=True)) if cate else "",
            state=state.get_text(" ", strip=True) if state else "",
        ))
    if not out:
        c = RECRUIT_NOTICE_COUNT_RE.search(s.get_text(" ", strip=True))
        if not (c and c.group(1) == "0"):
            raise StructureError("채용 화면에서 공고 목록을 찾지 못했습니다 (화면 구조가 바뀌었을 수 있음).")
    return out


def recruit_notice_body(html: str, it: PlatformItem) -> str:
    """글 화면 → '접수구분: …', '첨부파일: …' 같은 안내 칸 + 본문 글. 본문은 PDF(첨부) 뿐인 공고가 많다."""
    s = soup(html)
    view = s.select_one("div.program--view")
    if view is None:
        return extract_main_text(html)
    head = []
    for li in view.select("div.info_box li"):
        label = li.find("em")
        if label is None:
            continue
        key = label.get_text(strip=True)
        label.decompose()
        value = re.sub(r"\s+", " ", li.get_text(" ", strip=True))
        if key and value:
            head.append(f"{key}: {value}")
    if it.category:
        head.append(f"구분: {it.category}")
    content = "\n".join(t for t in (text_of(box) for box in view.select("div.cnts_txt")) if t)
    return "\n".join(head) + ("\n\n" + content if content else "")
