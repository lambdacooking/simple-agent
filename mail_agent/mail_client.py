"""A 메일함 읽기(IMAP)와 요약 메일 발송(SMTP)."""

import base64
import email
import email.policy
import imaplib
import logging
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Mail:
    uid: int
    sender_name: str
    sender_addr: str
    subject: str
    date: str
    body: str
    headers: dict[str, str]


# ── IMAP ──────────────────────────────────────────────────────────────


def encode_mailbox(name: str) -> str:
    """IMAP 폴더 이름을 modified UTF-7(RFC 3501)로 인코딩하고 따옴표로 감싼다.

    "AI-정리" 처럼 한글이 들어간 폴더 이름은 이 인코딩을 거쳐야 서버가 인식한다.
    """
    out, buf = [], []

    def flush():
        if buf:
            raw = "".join(buf).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    encoded = "".join(out).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{encoded}"'


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_body(msg: email.message.EmailMessage) -> str:
    """본문을 텍스트로 뽑는다. text/plain 우선, 없으면 text/html 을 텍스트로 변환."""
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return part.get_content().strip()
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        return _html_to_text(part.get_content())
    return ""


def parse_mail(uid: int, raw: bytes) -> Mail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = (getaddresses([str(msg.get("From", ""))]) or [("", "")])[0]
    try:
        date = parsedate_to_datetime(str(msg["Date"])).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        date = str(msg.get("Date", ""))
    headers = {k.lower(): str(v) for k, v in msg.items()}
    try:
        body = extract_body(msg)
    except (LookupError, KeyError, ValueError) as e:  # 깨진 인코딩/파트
        log.warning("UID %s 본문 파싱 실패: %s", uid, e)
        body = ""
    return Mail(
        uid=uid,
        sender_name=name,
        sender_addr=addr.lower(),
        subject=str(msg.get("Subject", "")).strip(),
        date=date,
        body=body,
        headers=headers,
    )


class Mailbox:
    """A 메일함에 대한 IMAP 연결. `with Mailbox(cfg) as mb:` 로 사용."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "Mailbox":
        self.imap = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port)
        self.imap.login(self.cfg.imap_user, self.cfg.imap_password)
        # readonly 로 열지 않는 이유: 정리 폴더로 COPY 하려면 쓰기 모드가 필요.
        # 대신 본문은 BODY.PEEK 로 읽어서 '읽음' 표시가 붙지 않게 한다.
        typ, _ = self.imap.select(encode_mailbox(self.cfg.imap_mailbox))
        if typ != "OK":
            raise RuntimeError(f"메일함 {self.cfg.imap_mailbox} 을 열 수 없습니다")
        return self

    def __exit__(self, *exc) -> None:
        if self.imap is not None:
            try:
                self.imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def uid_validity(self) -> int:
        typ, data = self.imap.status(encode_mailbox(self.cfg.imap_mailbox), "(UIDVALIDITY)")
        m = re.search(rb"UIDVALIDITY (\d+)", data[0] or b"") if typ == "OK" else None
        if not m:
            raise RuntimeError("UIDVALIDITY 를 읽을 수 없습니다")
        return int(m.group(1))

    def uids_after(self, last_uid: int) -> list[int]:
        """last_uid 보다 큰 UID 목록 (= 마지막 실행 이후 새로 들어온 메일)."""
        typ, data = self.imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if typ != "OK":
            raise RuntimeError("UID SEARCH 실패")
        # "N:*" 는 N 보다 큰 메일이 없을 때 마지막 메일 하나를 돌려주므로 다시 거른다.
        return sorted(u for u in map(int, (data[0] or b"").split()) if u > last_uid)

    def max_uid(self) -> int:
        typ, data = self.imap.uid("SEARCH", None, "ALL")
        uids = [int(u) for u in (data[0] or b"").split()] if typ == "OK" else []
        return max(uids, default=0)

    def fetch(self, uid: int) -> Mail | None:
        typ, data = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = next((item[1] for item in data or [] if isinstance(item, tuple)), None)
        if typ != "OK" or raw is None:
            log.warning("UID %s 를 가져오지 못했습니다", uid)
            return None
        return parse_mail(uid, raw)

    def copy_to_folder(self, uids: list[int], folder: str) -> None:
        """조건에 맞는 메일을 정리 폴더(Gmail 에서는 라벨)로 복사한다. 원본은 그대로 둔다."""
        if not uids:
            return
        target = encode_mailbox(folder)
        self.imap.create(target)  # 이미 있으면 NO 가 돌아오는데 무시해도 된다
        typ, data = self.imap.uid("COPY", ",".join(map(str, uids)), target)
        if typ != "OK":
            log.warning("정리 폴더 복사 실패: %s", data)


# ── SMTP ──────────────────────────────────────────────────────────────


def send_mail(cfg: Config, subject: str, text: str, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.mail_from
    msg["To"] = cfg.digest_to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port) as s:
            s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as s:
            s.starttls()
            s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
