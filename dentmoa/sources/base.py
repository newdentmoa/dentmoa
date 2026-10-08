"""공고 출처(사이트) 공통 틀.

각 사이트는 Source 를 상속해서 fetch() 만 구현하면 된다.
- 조용히 읽기: 요청 사이에 무작위로 몇 초씩 쉬고(ctx.sleep), 이미 본 글은 다시 열지 않는다(ctx.known_ids).
- 로그인 정보는 ctx.secrets 에서 읽는다.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import requests

from .. import config
from ..models import RawPosting

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.5",
}


class SourceError(Exception):
    """수집 실패 (사용자에게 보여줄 한글 메시지)."""


class LoginError(SourceError):
    """로그인 실패 — 아이디/비밀번호 확인 필요."""


class StructureError(SourceError):
    """사이트 구조가 바뀌어서 글 목록을 못 찾음."""


class MissingCredentials(SourceError):
    """아이디/비밀번호가 설정되지 않음."""


@dataclass
class FetchContext:
    since: datetime  # 이보다 오래된 글은 필요 없음 (목록 넘기기를 멈추는 기준)
    known_ids: set[str] = field(default_factory=set)  # 이미 저장된 글 번호 → 본문을 다시 열지 않음
    max_pages: int = 2
    min_delay: float = 3.0
    max_delay: float = 8.0
    secrets: dict = field(default_factory=dict)
    debug_dir: Path = field(default_factory=lambda: config.DEBUG_DIR)
    log: Callable[[str], None] = print
    backfill: bool = False  # 처음 실행이라 과거 글까지 모으는 중인지
    save_debug: bool = False  # 받은 HTML을 debug 폴더에 저장 (문제 해결용)

    def sleep(self) -> None:
        """사람처럼 보이도록 요청 사이에 쉬기."""
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def dump(self, name: str, content: str | bytes) -> None:
        if not self.save_debug:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        p = self.debug_dir / name
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")


class Source:
    key: str = ""
    label: str = ""
    homepage: str = ""
    # local_board: 모어덴·덴트포토 같은 치과의사 게시판
    # hospital_board: 병원 홈페이지 채용 게시판
    # aggregator: 잡알리오·하이브레인넷 같은 채용 정보 사이트
    source_kind: str = "local_board"
    dentist_only: bool = False  # 이 출처의 글은 모두 치과의사 공고인지
    default_inst_type: str | None = None
    requires_login: bool = False

    def fetch(self, ctx: FetchContext) -> Iterable[RawPosting]:
        raise NotImplementedError

    def check(self, ctx: FetchContext) -> str:
        """설정 화면의 '연결 테스트' 버튼. 성공하면 한글 메시지, 실패하면 SourceError."""
        items = []
        for item in self.fetch(ctx):
            items.append(item)
            if len(items) >= 3:
                break
        if not items:
            raise StructureError("글을 하나도 찾지 못했습니다.")
        return f"연결 성공 — 최근 글 예시: {items[0].title[:40]}"


class PoliteSession(requests.Session):
    """요청 사이에 쉬고, 실패하면 몇 번 다시 시도하고, 쿠키를 파일에 보관하는 세션."""

    def __init__(self, key: str, ctx: FetchContext, *, persist_cookies: bool = True):
        super().__init__()
        self.key = key
        self.ctx = ctx
        self.headers.update(DEFAULT_HEADERS)
        self._first = True
        self._cookie_file = config.COOKIE_DIR / f"{key}.json" if persist_cookies else None
        self._load_cookies()

    def _load_cookies(self) -> None:
        if not self._cookie_file or not self._cookie_file.exists():
            return
        try:
            for c in json.loads(self._cookie_file.read_text(encoding="utf-8")):
                self.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        except (ValueError, KeyError, OSError):
            pass

    def save_cookies(self) -> None:
        if not self._cookie_file:
            return
        self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
        data = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.cookies]
        self._cookie_file.write_text(json.dumps(data), encoding="utf-8")
        try:
            self._cookie_file.chmod(0o600)
        except OSError:
            pass

    def clear_cookies(self) -> None:
        self.cookies.clear()
        if self._cookie_file and self._cookie_file.exists():
            self._cookie_file.unlink()

    def request(self, method, url, *args, polite: bool = True, retries: int = 2, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", 30)
        if polite and not self._first:
            self.ctx.sleep()
        self._first = False
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = super().request(method, url, *args, **kwargs)
                if resp.status_code >= 500 and attempt < retries:
                    time.sleep(5 * (attempt + 1))
                    continue
                return resp
            except requests.RequestException as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(5 * (attempt + 1))
        raise SourceError(f"접속 실패: {url} ({last_exc})")


def decode(resp: requests.Response) -> str:
    """한국 사이트는 EUC-KR 인 곳이 있어서 인코딩을 추정한다."""
    if resp.encoding is None or resp.encoding.lower() in ("iso-8859-1", "ascii"):
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text
