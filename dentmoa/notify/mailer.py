"""메일 보내기 (SMTP). Gmail 기준: smtp.gmail.com, 포트 587(STARTTLS) 또는 465(SSL), 앱 비밀번호."""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .errors import NotifyError

TIMEOUT = 30
SENDER_NAME = "덴트모아"
AUTH_MSG = "메일 로그인 실패: Gmail은 일반 비밀번호가 아니라 '앱 비밀번호' 16자리를 넣어야 합니다"
TEST_SUBJECT = "덴트모아 테스트 메일"
TEST_TEXT = "덴트모아 테스트 메일입니다 ✅"


def is_configured(secrets: dict) -> bool:
    return all((secrets.get(k) or "").strip() for k in ("smtp_user", "smtp_password", "email_to"))


def recipients(value: str) -> list[str]:
    """'a@x.com, b@y.com' → ['a@x.com', 'b@y.com']"""
    return list(dict.fromkeys(a for a in re.split(r"[,;\s]+", value or "") if a))


def build_message(sender: str, to: list[str], subject: str, html: str, text: str) -> EmailMessage:
    msg = EmailMessage()  # 기본 정책: UTF-8, 한글 제목 자동 인코딩
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, sender))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content(text, charset="utf-8")
    msg.add_alternative(html, subtype="html", charset="utf-8")  # multipart/alternative
    return msg


def send_email(secrets: dict, subject: str, html: str, text: str) -> None:
    if not is_configured(secrets):
        raise NotifyError("보내는 메일 계정, 앱 비밀번호, 받는 메일 주소를 먼저 입력해 주세요")
    host = (secrets.get("smtp_host") or "smtp.gmail.com").strip()
    try:
        port = int(str(secrets.get("smtp_port") or 587).strip())
    except ValueError:
        raise NotifyError("메일 포트 번호가 올바르지 않습니다 (보통 587 또는 465)") from None
    user = secrets["smtp_user"].strip()
    password = secrets["smtp_password"].strip()
    if "gmail" in host:
        password = password.replace(" ", "")  # 앱 비밀번호를 'abcd efgh …' 처럼 띄어 써도 되게
    to = recipients(secrets["email_to"])
    msg = build_message(user, to, subject, html, text)

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if port != 465:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
            server.login(user, password)
            server.send_message(msg, from_addr=user, to_addrs=to)
    except smtplib.SMTPAuthenticationError:
        raise NotifyError(AUTH_MSG) from None
    except smtplib.SMTPRecipientsRefused as e:
        raise NotifyError("받는 메일 주소가 올바르지 않습니다: " + ", ".join(e.recipients)) from None
    except smtplib.SMTPSenderRefused:
        raise NotifyError(f"보내는 메일 주소({user})를 메일 서버가 거부했습니다") from None
    except smtplib.SMTPNotSupportedError:
        raise NotifyError("이 메일 서버는 보안 연결(STARTTLS)을 지원하지 않습니다. 포트를 465로 바꿔 보세요") from None
    except smtplib.SMTPException as e:
        raise NotifyError(f"메일 전송 실패: {e}") from None
    except ssl.SSLError:
        raise NotifyError("메일 서버와 보안 연결에 실패했습니다. 포트 번호(587 또는 465)를 확인해 주세요") from None
    except OSError as e:  # 접속 불가, 시간 초과, 주소 오류
        raise NotifyError(
            f"메일 서버({host}:{port})에 접속하지 못했습니다. 서버 주소와 포트를 확인해 주세요 ({e.__class__.__name__})"
        ) from None


def send_test(secrets: dict) -> None:
    html = f"<p style=\"font-size:16px\">{TEST_TEXT}</p><p>이 메일이 보이면 메일 알림 설정이 끝난 거예요.</p>"
    send_email(secrets, TEST_SUBJECT, html, f"{TEST_TEXT}\n이 메일이 보이면 메일 알림 설정이 끝난 거예요.")
