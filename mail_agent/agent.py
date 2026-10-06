"""한 번의 실행: 새 메일 확인 → 필터링 → Claude 요약 → B 메일로 발송."""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from html import escape

from pydantic import ValidationError

from .config import Config
from .filters import is_obvious_ad, matches_sender
from .mail_client import Mail, Mailbox, send_mail
from .summarizer import MailAnalysis, Summarizer

log = logging.getLogger(__name__)

# 오랫동안 꺼져 있다 켜졌을 때 한 번에 너무 많은 메일을 요약하지 않도록 제한.
# 남은 메일은 다음 실행(10분 뒤)에서 이어서 처리된다.
MAX_MAILS_PER_RUN = 50


@dataclass
class DigestItem:
    mail: Mail
    keyword: str
    analysis: MailAnalysis | None


# ── 상태 저장: 어디까지 읽었는지(UID) 기억 ────────────────────────────


def load_state(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(path: str, uidvalidity: int, last_uid: int) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"uidvalidity": uidvalidity, "last_uid": last_uid}, f)
    os.replace(tmp, path)


# ── 요약 메일 본문 ───────────────────────────────────────────────────


def build_digest(items: list[DigestItem]) -> tuple[str, str, str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject = f"[메일 요약] 새 메일 {len(items)}건 ({now})"

    text_parts, html_parts = [], []
    for i, it in enumerate(items, 1):
        m, a = it.mail, it.analysis
        sender = f"{m.sender_name} <{m.sender_addr}>" if m.sender_name else m.sender_addr
        summary = a.summary if a else "(자동 요약 실패 – 원문을 확인하세요)"
        key_points = a.key_points if a else []
        actions = a.action_items if a else []

        text = [f"{i}. {m.subject}", f"   보낸 사람: {sender}  |  {m.date}  |  키워드: {it.keyword}",
                f"   요약: {summary}"]
        text += [f"   - {p}" for p in key_points]
        text += [f"   ☐ {t}" for t in actions]
        text_parts.append("\n".join(text))

        lis = "".join(f"<li>{escape(p)}</li>" for p in key_points)
        todos = "".join(f"<li>☐ {escape(t)}</li>" for t in actions)
        html_parts.append(
            f"<div style='margin:0 0 20px;padding:12px 16px;border-left:4px solid #4f6bed;background:#f6f7fb'>"
            f"<div style='font-size:16px;font-weight:600'>{i}. {escape(m.subject)}</div>"
            f"<div style='color:#666;font-size:12px;margin:4px 0 8px'>{escape(sender)} · {escape(m.date)}"
            f" · 키워드 <b>{escape(it.keyword)}</b></div>"
            f"<div>{escape(summary)}</div>"
            + (f"<ul style='margin:8px 0'>{lis}</ul>" if lis else "")
            + (f"<div style='margin-top:8px;font-weight:600'>할 일</div><ul style='margin:4px 0'>{todos}</ul>"
               if todos else "")
            + "</div>"
        )

    text = f"{subject}\n\n" + "\n\n".join(text_parts)
    html = (f"<div style='font-family:sans-serif;max-width:720px'>"
            f"<h2 style='font-size:18px'>{escape(subject)}</h2>{''.join(html_parts)}</div>")
    return subject, text, html


# ── 실행 ─────────────────────────────────────────────────────────────


def run_once(cfg: Config, summarizer: Summarizer) -> int:
    """새 메일을 처리하고, B 메일로 보낸 요약 건수를 돌려준다."""
    with Mailbox(cfg) as mb:
        uidvalidity = mb.uid_validity()
        state = load_state(cfg.state_file)

        if state is None or state.get("uidvalidity") != uidvalidity:
            # 첫 실행(또는 메일함이 재생성됨): 기존 메일 전체를 요약하지 않도록
            # 현재 위치를 기준점으로 저장하고, 이후 들어오는 메일부터 처리한다.
            last = mb.max_uid()
            save_state(cfg.state_file, uidvalidity, last)
            log.info("기준점 저장 (UID %s). 다음 실행부터 새 메일을 처리합니다.", last)
            return 0

        new_uids = mb.uids_after(state["last_uid"])[:MAX_MAILS_PER_RUN]
        if not new_uids:
            log.info("새 메일 없음")
            return 0
        log.info("새 메일 %d건 확인", len(new_uids))

        items: list[DigestItem] = []
        for uid in new_uids:
            mail = mb.fetch(uid)
            if mail is None:
                continue
            keyword = matches_sender(mail, cfg.sender_keywords)
            if keyword is None:
                log.debug("UID %s 건너뜀 (키워드 불일치): %s", uid, mail.sender_addr)
                continue
            if is_obvious_ad(mail):
                log.info("UID %s 광고 제외 (제목 표시): %s", uid, mail.subject)
                continue

            try:
                analysis = summarizer.analyze(mail)
            except ValidationError as e:
                log.warning("UID %s 요약 결과 형식 오류: %s", uid, e)
                analysis = None
            if analysis is not None and analysis.is_advertisement:
                log.info("UID %s 광고 제외 (Claude 판단): %s", uid, mail.subject)
                continue
            items.append(DigestItem(mail, keyword, analysis))

        if items:
            subject, text, html = build_digest(items)
            send_mail(cfg, subject, text, html)
            log.info("요약 %d건을 %s 로 보냈습니다", len(items), cfg.digest_to)
            if cfg.organize_folder:
                mb.copy_to_folder([it.mail.uid for it in items], cfg.organize_folder)

        # 발송까지 성공한 뒤에만 진행 위치를 저장 → 중간에 실패하면 다음 실행에서 다시 시도
        save_state(cfg.state_file, uidvalidity, new_uids[-1])
        return len(items)
