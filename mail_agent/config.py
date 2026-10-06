"""환경 변수(.env)에서 설정을 읽어온다."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"환경 변수 {name} 가 설정되지 않았습니다 (.env.example 참고)")
    return value


@dataclass(frozen=True)
class Config:
    # Claude
    model: str
    effort: str

    # A 메일 (IMAP)
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_mailbox: str
    organize_folder: str | None

    # 발송 (SMTP)
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_from: str

    # B 메일
    digest_to: str

    # 필터 / 스케줄
    sender_keywords: tuple[str, ...]
    interval_minutes: int
    state_file: str

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        keywords = tuple(
            k.strip().lower() for k in _required("SENDER_KEYWORDS").split(",") if k.strip()
        )
        if not keywords:
            raise RuntimeError("SENDER_KEYWORDS 에 키워드를 하나 이상 입력하세요")

        smtp_user = _required("SMTP_USER")
        return cls(
            model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
            effort=os.getenv("CLAUDE_EFFORT", "low"),
            imap_host=_required("IMAP_HOST"),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            imap_user=_required("IMAP_USER"),
            imap_password=_required("IMAP_PASSWORD"),
            imap_mailbox=os.getenv("IMAP_MAILBOX", "INBOX"),
            organize_folder=os.getenv("ORGANIZE_FOLDER", "").strip() or None,
            smtp_host=_required("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_user=smtp_user,
            smtp_password=_required("SMTP_PASSWORD"),
            mail_from=os.getenv("MAIL_FROM", smtp_user),
            digest_to=_required("DIGEST_TO"),
            sender_keywords=keywords,
            interval_minutes=int(os.getenv("CHECK_INTERVAL_MINUTES", "10")),
            state_file=os.getenv("STATE_FILE", "state.json"),
        )
