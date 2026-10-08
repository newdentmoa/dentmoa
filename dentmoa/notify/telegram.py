"""텔레그램 봇으로 메시지 보내기 (HTML 형식).

메시지 한 개는 4096자까지라서 4000자를 넘으면 줄 단위로 나눠 보낸다.
태그(<b>, <a> …)는 항상 한 줄 안에서 열고 닫으므로 줄 경계에서 자르면 HTML이 깨지지 않는다.
"""

from __future__ import annotations

import re
import time
from typing import Iterable

import requests

from .errors import NotifyError

API = "https://api.telegram.org"
LIMIT = 4000
TIMEOUT = 30
MAX_RETRIES = 2  # 429(너무 많은 요청)일 때 다시 시도하는 횟수
PAUSE_SEC = 1.0  # 여러 메시지를 이어 보낼 때 사이 간격

TOKEN_MSG = "봇 토큰이 올바르지 않습니다. BotFather 가 알려준 토큰을 다시 확인해 주세요"
CHAT_MSG = "채팅 ID가 올바르지 않거나 봇에게 먼저 /start 를 보내지 않았습니다"
TEST_TEXT = "덴트모아 테스트 메시지입니다 ✅"

_TAG_RE = re.compile(r"<[^>]*>")


# ──────────────────────────── 메시지 나누기 ────────────────────────────


def pack(blocks: Iterable[str], limit: int = LIMIT, sep: str = "\n\n") -> list[str]:
    """덩어리(공고 하나 등)를 자르지 않고 limit 자 이하의 메시지들로 묶는다.

    덩어리 하나가 limit 보다 길 때만 줄 단위로, 한 줄도 길면 글자 단위로 자른다.
    """
    chunks: list[str] = []
    cur = ""
    for block in blocks:
        block = block.strip("\n")
        if not block:
            continue
        pieces = [block] if len(block) <= limit else _split_long(block, limit)
        for piece in pieces:
            if cur and len(cur) + len(sep) + len(piece) <= limit:
                cur += sep + piece
            else:
                if cur:
                    chunks.append(cur)
                cur = piece
    if cur:
        chunks.append(cur)
    return chunks


def split_html(html: str, limit: int = LIMIT) -> list[str]:
    """긴 메시지를 빈 줄(항목 경계) → 줄 순서로 나눈다."""
    if len(html) <= limit:
        return [html] if html.strip() else []
    return pack(html.split("\n\n"), limit)


def _split_long(block: str, limit: int) -> list[str]:
    lines = block.split("\n")
    if len(lines) > 1:
        return pack(lines, limit, "\n")
    return _hard_split(block, limit)


def _hard_split(line: str, limit: int) -> list[str]:
    """한 줄이 너무 길면 태그를 빼고 글자 수로 자른다 (&amp; 같은 엔티티 중간은 피한다)."""
    text = _TAG_RE.sub("", line)
    out = []
    while len(text) > limit:
        cut = limit
        amp = text.rfind("&", max(0, cut - 10), cut)
        if amp > 0 and ";" not in text[amp:cut]:
            cut = amp
        out.append(text[:cut])
        text = text[cut:]
    if text:
        out.append(text)
    return out


# ──────────────────────────── API 호출 ────────────────────────────


def _json(resp) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(data: dict) -> float:
    try:
        wait = float((data.get("parameters") or {}).get("retry_after", 5))
    except (TypeError, ValueError):
        wait = 5.0
    return max(1.0, min(wait, 120.0))


def _explain(status: int, desc: str) -> str:
    d = (desc or "").lower()
    if status in (401, 404) or "unauthorized" in d:
        return TOKEN_MSG
    if "chat not found" in d or "can't initiate" in d or "chat_id is empty" in d:
        return CHAT_MSG
    if "blocked by the user" in d:
        return "봇이 차단되어 있습니다. 텔레그램에서 봇 차단을 풀고 /start 를 보내 주세요"
    if "deactivated" in d:
        return "받는 텔레그램 계정이 비활성화되어 있습니다"
    if status == 409:
        return "이 봇에 웹훅(webhook)이 설정되어 있어 메시지를 읽을 수 없습니다. 다른 곳에서 쓰는 봇이 아닌지 확인해 주세요"
    if "can't parse entities" in d or "too long" in d:
        return f"메시지 형식 오류예요. 프로그램 수리가 필요해요 ({desc})"
    return f"텔레그램 전송 실패 ({status}): {desc or '알 수 없는 오류'}"


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    token = (token or "").strip()
    if not token:
        raise NotifyError("텔레그램 봇 토큰이 비어 있습니다")
    url = f"{API}/bot{token}/{method}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload or {}, timeout=TIMEOUT)
        except requests.RequestException as e:
            # 오류 문구에 토큰이 든 주소가 섞일 수 있어 종류만 알린다
            raise NotifyError(f"텔레그램 서버에 접속하지 못했습니다 ({e.__class__.__name__}). 인터넷 연결을 확인해 주세요") from None
        data = _json(resp)
        if resp.status_code == 429:
            if attempt < MAX_RETRIES:
                time.sleep(_retry_after(data))
                continue
            raise NotifyError("텔레그램이 잠시 전송을 막았습니다(메시지가 너무 많음). 잠시 후 다시 시도해 주세요")
        if resp.status_code == 200 and data.get("ok"):
            return data
        raise NotifyError(_explain(resp.status_code, str(data.get("description", ""))))
    raise NotifyError("텔레그램 전송 실패")  # 여기까지 오지 않음


# ──────────────────────────── 공개 함수 ────────────────────────────


def is_configured(secrets: dict) -> bool:
    return bool((secrets.get("telegram_token") or "").strip() and (secrets.get("telegram_chat_id") or "").strip())


def send_chunks(token: str, chat_id: str, chunks: Iterable[str], *, disable_preview: bool = True) -> int:
    """이미 나눠 둔 메시지들을 차례로 보낸다. 보낸 메시지 수를 돌려준다."""
    parts = [p for c in chunks for p in split_html(c)]
    for i, part in enumerate(parts):
        if i:
            time.sleep(PAUSE_SEC)
        _call(
            token,
            "sendMessage",
            {
                "chat_id": str(chat_id).strip(),
                "text": part,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": disable_preview},
            },
        )
    return len(parts)


def send_message(token: str, chat_id: str, html: str, *, disable_preview: bool = True) -> int:
    """HTML 메시지 보내기. 4000자를 넘으면 줄 경계에서 나눠 여러 개로 보낸다."""
    return send_chunks(token, chat_id, [html], disable_preview=disable_preview)


def find_chat_id(token: str) -> str | None:
    """봇에게 온 최근 메시지에서 개인 대화(채팅) ID를 찾는다. 없으면 None.

    사용자가 텔레그램에서 봇에게 /start 를 보낸 뒤 부르면 된다.
    """
    data = _call(token, "getUpdates", {"timeout": 0})
    for upd in reversed(data.get("result") or []):
        for key in ("message", "edited_message", "my_chat_member"):
            chat = (upd.get(key) or {}).get("chat") or {}
            if chat.get("type") == "private" and chat.get("id") is not None:
                return str(chat["id"])
    return None


def send_test(secrets: dict) -> None:
    if not is_configured(secrets):
        raise NotifyError("텔레그램 봇 토큰과 채팅 ID를 먼저 입력해 주세요")
    send_message(secrets["telegram_token"], secrets["telegram_chat_id"], TEST_TEXT)
