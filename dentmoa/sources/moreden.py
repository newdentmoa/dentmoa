"""모어덴 (moreden.co.kr) — 치과의사 구인 게시판.

자바스크립트로 화면을 그리는 사이트라 브라우저(Chromium)로 읽는다.

2026-10 실제 사이트 확인 (로그인 후 화면까지):
- 구인 게시판: moreden.co.kr/recruit/doctor (2쪽부터 ?page=N — 주소로 바로 열어도 그 쪽 JSON 을 받는다), 글: /recruit/doctor/<bid>
- 로그인 안 된 상태로 열면 moreden.co.kr/login?auth_check=true… → 통합 로그인 화면 auth.deneer.co.kr/login 으로 넘어간다.
  화면에 로그인 칸이 두 벌 있다: 치과의사용(#communityIdInput·#communityPwInput·#communityLoginButton,
  '기존 아이디로 로그인' 을 눌러야 보임)과 직원용 마켓(#marketIdInput…, 처음부터 보임). 치과의사용을 써야 한다.
  틀리면 경고창(alert)에 '아이디가 존재하지 않습니다.' 같은 말이 뜬다.
  맞으면 moreden.co.kr/auth?code=… → 원래 화면. 로그인 표(token)는 브라우저 저장소(localStorage)에 남아 다음 실행에도 쓰인다.
- 화면이 받아오는 JSON (public-api.moreden.co.kr/community, 'authorization' 머리글 필요):
  목록 /management/recruit/list(?page=N) → {"board": [20건], "hot_items": [인기 글], "query", "page", "pages"}
    글 하나에 본문까지 다 있다: bid, title, content(일반 글자), location1, location2, address, detail_address, hospital_name,
    reg_dttm(20261009150008, 한국 시각), major, working_time, wage_type, gross_wage, net_wage, terminated_at, deleted, status
  글 /management/recruit/article/<bid> → {"article": {목록과 같은 칸 + unick(글쓴이 별명)}}
- major 는 전공 글자 코드를 이어 붙인 문자열(예: "AE"), working_time 은 full_time/part_time/whatever.
- 급여 칸은 비워 둘 수 없어서 '협의'인 글은 0 이나 10000(1만원)을 적는다.
- 같은 화면에서 목록 JSON 을 두 번 받는다(같은 내용).

1) 구인 게시판을 연다 → 로그인 화면이 나오면 치과의사 아이디·비밀번호로 로그인
2) 목록 JSON 에서 글을 읽는다 — 본문까지 들어 있어서 글을 하나씩 열지 않는다
3) 목록 JSON 을 못 받았거나 본문이 비어 있으면: 화면의 링크로 글을 찾고, 처음 보는 글만 열어서 읽는다
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urljoin

from ..models import RawPosting
from .base import FetchContext, LoginError, MissingCredentials, Source, StructureError
from .browser import BrowserSession, dump_captured, find_record, generic_login, open_browser, records_from_json
from .htmlutil import extract_main_text, find_label_value, parse_board_date, soup, strip_invisible

BASE = "https://moreden.co.kr"
LIST_URL = f"{BASE}/recruit/doctor"
API_RE = r"public-api\.moreden\.co\.kr/.*management/recruit/"
LIST_API = re.compile(r"/management/recruit/list(?:\?|$)")
ARTICLE_API = re.compile(r"/management/recruit/article/(\d+)")
DETAIL_HREF = re.compile(r"/recruit/doctor/(?P<id>\d+)/?$")
REGION_LABELS = ["근무지 위치", "근무지역", "근무 지역", "근무지", "지역", "위치", "주소", "소재지"]
INST_LABELS = ["병원명", "치과명", "기관명", "업체명", "병원 이름", "치과 이름"]

# 사이트의 코드표 (자바스크립트에서 확인)
MAJORS = {
    "A": "전공무관", "B": "구강악안면외과", "C": "치과보철과", "D": "치과교정과", "E": "소아치과", "F": "치주과",
    "G": "치과보존과", "H": "구강내과", "I": "영상치의학과", "J": "구강병리과", "K": "예방치과", "L": "통합치의학과",
}
WORKING_TIME = {"full_time": "풀타임 근무", "part_time": "파트타임 근무", "whatever": "근무형태 협의 가능"}
WAGE_TYPE = {"annualy": "연봉", "weekly": "주급", "daily": "일급", "hourly": "시급", "monthly": "월급", "": "협의"}


def _on_login_page(page) -> bool:
    url = page.url.lower()
    if "auth.deneer.co.kr" in url or "/login" in url or "signin" in url:
        return True
    try:
        return page.locator("#communityPwInput, #communityIdInput").first.is_visible(timeout=1000)
    except Exception:
        return False


def _won(v) -> str:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return ""
    if n <= 10000:
        return ""  # 0·1만원은 '협의'라는 뜻으로 적은 값
    return f"{n / 10000:g}만원"


def wage_text(a: dict) -> str:
    kind = WAGE_TYPE.get(a.get("wage_type") or "", "")
    parts = []
    if _won(a.get("gross_wage")):
        parts.append(f"세전 {_won(a['gross_wage'])}")
    if _won(a.get("net_wage")):
        parts.append(f"세후 {_won(a['net_wage'])}")
    if not parts:
        return "협의"
    return f"{kind} {' / '.join(parts)}".strip()


def majors_text(code: str | None) -> str:
    return " / ".join(MAJORS[c] for c in (code or "") if c in MAJORS)


def item_from_api(rec: dict) -> dict | None:
    """목록 JSON 의 글 하나 → 내부 형식 (본문이 있으면 본문까지)."""
    bid = rec.get("bid")
    title = (rec.get("title") or "").strip()
    if bid is None or not title:
        return None
    region = " ".join(x for x in (rec.get("location1"), rec.get("location2")) if x)
    return {
        "id": str(bid),
        "url": f"{LIST_URL}/{bid}",
        "title": title,
        "body": body_from_article(rec) if (rec.get("content") or "").strip() else "",
        "region": rec.get("address") or region,
        "institution": (rec.get("hospital_name") or "").strip(),
        "posted_at": parse_board_date(str(rec.get("reg_dttm") or "")),
        "major": rec.get("major") or "",
        "working_time": rec.get("working_time") or "",
        "closed": bool(rec.get("terminated_at")) or rec.get("deleted") is True,
        "card": "",
    }


def body_from_article(a: dict) -> str:
    """글 JSON → 분류기가 읽기 좋은 본문 (화면의 요약 칸 + 본문)."""
    lines = []
    addr = " ".join(x for x in (a.get("address"), a.get("detail_address")) if x)
    if addr:
        lines.append(f"위치: {addr}")
    if a.get("hospital_name"):
        lines.append(f"병원: {a['hospital_name']}")
    if majors_text(a.get("major")):
        lines.append(f"전공: {majors_text(a.get('major'))}")
    if WORKING_TIME.get(a.get("working_time") or ""):
        lines.append(f"근무형태: {WORKING_TIME[a['working_time']]}")
    lines.append(f"급여: {wage_text(a)}")
    content = strip_invisible(_strip_html(a.get("content") or ""))
    content = re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", content).strip()  # 빈 줄 여러 개 → 하나
    return "\n".join(lines) + ("\n\n" + content if content else "")


class MoredenSource(Source):
    key = "moreden"
    label = "모어덴"
    homepage = LIST_URL
    source_kind = "local_board"
    dentist_only = True
    default_inst_type = "dental_clinic"
    requires_login = True

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        user, pw = ctx.secrets.get("moreden_id", ""), ctx.secrets.get("moreden_pw", "")
        if not user or not pw:
            raise MissingCredentials("모어덴 아이디·비밀번호가 설정되지 않았습니다.")

        with open_browser(self.key, capture_json=API_RE) as sess:
            self._open_list(sess, ctx, user, pw)
            items = self._collect_list(sess, ctx)
            if not items:
                ctx.dump("moreden-list.html", sess.page.content())
                dump_captured(sess, ctx, "moreden-list")
                raise StructureError("모어덴 구인 게시판에서 글 목록을 찾지 못했습니다.")
            ctx.log(f"목록에서 {len(items)}건 확인")
            for it in items:
                if it["posted_at"] and it["posted_at"] < ctx.since:
                    continue
                if it.get("closed"):
                    continue
                if it["body"]:
                    # 목록 JSON 에 본문까지 있다 → 글을 따로 열지 않는다 (이미 저장된 글은 고친 내용이 반영된다)
                    yield self._raw(it)
                    continue
                if it["id"] in ctx.known_ids:
                    # 이미 저장된 글은 본문을 다시 열지 않는다(제목만 갱신)
                    yield RawPosting(self.key, it["id"], it["url"], it["title"], posted_at=it["posted_at"],
                                     region_hint=it["region"], institution_hint=it["institution"])
                    continue
                yield self._detail(sess, ctx, it)

    def _raw(self, it: dict) -> RawPosting:
        return RawPosting(
            source=self.key,
            source_id=it["id"],
            url=it["url"],
            title=it["title"],
            body=it["body"],
            posted_at=it["posted_at"],
            region_hint=it["region"],
            institution_hint=it["institution"],
            extra=_extra(it),
        )

    # ─────────────── 로그인 ───────────────

    def _open_list(self, sess: BrowserSession, ctx: FetchContext, user: str, pw: str) -> None:
        page = sess.page
        sess.captured.clear()
        page.goto(LIST_URL, wait_until="domcontentloaded")
        self._settle(sess)
        if not _on_login_page(page):
            return
        ctx.log("로그인 필요 — 로그인 시도")
        self._login(sess, ctx, user, pw)
        if not page.url.startswith(LIST_URL):
            sess.captured.clear()
            page.goto(LIST_URL, wait_until="domcontentloaded")
            self._settle(sess)
        if _on_login_page(page):
            ctx.dump("moreden-after-login.html", page.content())
            raise LoginError("모어덴 로그인 후에도 구인 게시판이 열리지 않습니다.")

    def _login(self, sess: BrowserSession, ctx: FetchContext, user: str, pw: str) -> None:
        page = sess.page
        alerts: list[str] = []

        def on_dialog(d):
            alerts.append(d.message)
            try:
                d.dismiss()
            except Exception:
                pass

        page.on("dialog", on_dialog)
        try:
            id_box = page.locator("#communityIdInput")
            if id_box.count() == 0:
                # 화면 구조가 바뀜 → 일반적인 방법 (아이디·비밀번호 칸 찾기)
                ctx.dump("moreden-login.html", page.content())
                generic_login(sess, page.url, user, pw, success_check=lambda p: not _on_login_page(p))
                return
            if not id_box.is_visible():
                btn = page.get_by_text("기존 아이디로 로그인", exact=False).first
                btn.click()
                sess.human_pause()
            id_box.wait_for(state="visible", timeout=10000)
            id_box.click()
            id_box.type(user, delay=_typing_delay())
            sess.human_pause(0.3, 0.8)
            pw_box = page.locator("#communityPwInput")
            pw_box.click()
            pw_box.type(pw, delay=_typing_delay())
            sess.human_pause(0.3, 0.8)
            page.locator("#communityLoginButton").click()
            # 로그인되면 moreden.co.kr/auth → 원래 화면으로 돌아온다
            try:
                page.wait_for_url(re.compile(r"^https://(?:www\.)?moreden\.co\.kr/(?!auth)"), timeout=20000)
            except Exception:
                pass
            self._settle(sess)
            if _on_login_page(page) or "auth.deneer.co.kr" in page.url:
                ctx.dump("moreden-login.html", page.content())
                why = f" (사이트 메시지: {alerts[-1]})" if alerts else ""
                raise LoginError(
                    "모어덴 로그인에 실패했습니다" + why + ". 카카오·애플 로그인이 아닌 모어덴 아이디·비밀번호인지 확인해 주세요."
                )
        finally:
            page.remove_listener("dialog", on_dialog)

    def _settle(self, sess: BrowserSession) -> None:
        try:
            sess.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        sess.human_pause(1.0, 2.5)

    # ─────────────── 목록 ───────────────

    def _collect_list(self, sess: BrowserSession, ctx: FetchContext) -> list[dict]:
        items: dict[str, dict] = {}
        for page_no in range(1, ctx.max_pages + 1):
            before = len(items)
            if page_no > 1:
                ctx.sleep()
                sess.captured.clear()
                sess.page.goto(f"{LIST_URL}?page={page_no}", wait_until="domcontentloaded")
                self._settle(sess)
            self._merge(items, sess)
            if ctx.save_debug:
                ctx.dump(f"moreden-list-{page_no}.html", sess.page.content())
                dump_captured(sess, ctx, f"moreden-list-{page_no}")
            if len(items) == before:
                break
            dated = [i["posted_at"] for i in items.values() if i["posted_at"]]
            if dated and min(dated) < ctx.since:
                break
            if self._last_page(sess, page_no):
                break
        return list(items.values())

    @staticmethod
    def _list_payloads(sess: BrowserSession) -> list[dict]:
        return [c.data for c in sess.captured if LIST_API.search(c.url) and isinstance(c.data, dict)]

    def _last_page(self, sess: BrowserSession, page_no: int) -> bool:
        for data in self._list_payloads(sess):
            try:
                return int(data.get("pages") or 0) <= page_no
            except (TypeError, ValueError):
                pass
        return False

    def _merge(self, items: dict[str, dict], sess: BrowserSession) -> None:
        # 1) 목록 JSON ('board'. 'hot_items' 는 인기 글 모음이라 뺀다)
        payloads = self._list_payloads(sess)
        for data in payloads:
            for rec in data.get("board") or []:
                it = item_from_api(rec) if isinstance(rec, dict) else None
                if it and it["id"] not in items:
                    items[it["id"]] = it
        if payloads:
            return
        # 2) JSON 모양이 바뀌었으면: 아무 JSON 에서나 '번호+제목' 목록 찾기
        for cap in sess.captured:
            for rec in records_from_json(cap.data):
                it = items.setdefault(rec["id"], self._blank(rec["id"]))
                it["title"] = it["title"] or rec["title"]
                it["body"] = it["body"] or _strip_html(rec["body"])
                it["region"] = it["region"] or rec["region"]
                it["institution"] = it["institution"] or rec["institution"]
                it["posted_at"] = it["posted_at"] or parse_board_date(rec["date"])
        # 3) 화면의 링크
        s = soup(sess.page.content())
        for a in s.find_all("a", href=True):
            href = urljoin(BASE, a["href"])
            if not href.startswith(BASE):
                continue
            m = DETAIL_HREF.search(href.split("?")[0])
            if not m:
                continue
            pid = m.group("id")
            card_text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            it = items.setdefault(pid, self._blank(pid))
            it["url"] = href.split("?")[0]
            if not it["title"]:
                title_node = a.find(["h2", "h3", "h4", "strong", "p"])
                it["title"] = (title_node.get_text(" ", strip=True) if title_node else card_text)[:150]
            it["card"] = card_text
            if not it["posted_at"]:
                it["posted_at"] = parse_board_date(_date_like(card_text))
        for it in items.values():
            if not it["url"]:
                it["url"] = f"{LIST_URL}/{it['id']}"
        # 제목 없는 항목은 버린다
        for k in [k for k, v in items.items() if not v["title"]]:
            items.pop(k)

    @staticmethod
    def _blank(pid: str) -> dict:
        return {"id": pid, "url": "", "title": "", "body": "", "region": "", "institution": "", "posted_at": None,
                "card": "", "major": "", "working_time": "", "closed": False}

    # ─────────────── 본문 ───────────────

    def _detail(self, sess: BrowserSession, ctx: FetchContext, it: dict) -> RawPosting:
        ctx.sleep()
        page = sess.page
        sess.captured.clear()
        page.goto(it["url"], wait_until="domcontentloaded")
        self._settle(sess)
        html = page.content()
        if ctx.save_debug:
            ctx.dump(f"moreden-detail-{it['id']}.html", html)
            dump_captured(sess, ctx, f"moreden-detail-{it['id']}")
        article = None
        for cap in sess.captured:
            m = ARTICLE_API.search(cap.url)
            if m and m.group(1) == it["id"] and isinstance(cap.data, dict) and isinstance(cap.data.get("article"), dict):
                article = cap.data["article"]
                break
        if article:
            title = (article.get("title") or it["title"]).strip()
            region = article.get("address") or it["region"]
            return RawPosting(
                source=self.key,
                source_id=it["id"],
                url=it["url"],
                title=title,
                body=body_from_article(article),
                posted_at=it["posted_at"] or parse_board_date(str(article.get("reg_dttm") or "")),
                author=(article.get("unick") or "")[:40],
                region_hint=region,
                institution_hint=(article.get("hospital_name") or it["institution"]).strip(),
                extra=_extra(it),
            )
        # JSON 을 못 받았으면 화면 글자로
        body = extract_main_text(html)
        for cap in sess.captured:
            rec = find_record(cap.data, it["id"])
            if rec:
                it["body"] = max(it["body"], _strip_html(rec["body"]), key=len)
                it["region"] = it["region"] or rec["region"]
                it["institution"] = it["institution"] or rec["institution"]
                it["posted_at"] = it["posted_at"] or parse_board_date(rec["date"])
        if len(it["body"]) > len(body):
            body = it["body"]
        region = it["region"] or find_label_value(html, REGION_LABELS)
        inst = it["institution"] or find_label_value(html, INST_LABELS)
        posted = it["posted_at"] or parse_board_date(_date_like(body[:300]))
        return RawPosting(
            source=self.key,
            source_id=it["id"],
            url=it["url"],
            title=it["title"],
            body=body,
            posted_at=posted,
            region_hint=region,
            institution_hint=inst,
            extra=_extra(it),
        )


def _extra(it: dict) -> dict:
    extra = {"major": majors_text(it.get("major")), "working_time": WORKING_TIME.get(it.get("working_time") or "", "")}
    return {k: v for k, v in extra.items() if v}


def _typing_delay() -> int:
    import random

    return random.randint(40, 110)


def _strip_html(s: str) -> str:
    if not s or "<" not in s:
        return s or ""
    from .htmlutil import text_of

    return text_of(soup(s))


def _date_like(text: str) -> str:
    m = re.search(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}|\d+\s*(?:분|시간|일)\s*전|어제|오늘|\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", text or "")
    return m.group(0) if m else ""
