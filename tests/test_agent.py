from datetime import date, timedelta
from email import message_from_bytes as EmailMessage_from
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime

import pytest

from mail_agent import agent
from mail_agent.config import Config
from mail_agent.filters import is_obvious_ad, matches_sender
from mail_agent.mail_client import encode_mailbox, parse_mail
from mail_agent.summarizer import MailAnalysis


def raw_mail(sender: str, subject: str, body: str = "본문", html: str | None = None,
             date: str = "Tue, 06 Oct 2026 09:30:00 +0900") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg["Date"] = date
    if html is None:
        msg.set_content(body)
    else:
        msg.set_content(html, subtype="html")
    return msg.as_bytes()


def make_cfg(tmp_path, **kw) -> Config:
    base = dict(
        xai_api_key="xai-test", model="grok-4.3",
        imap_host="imap.test", imap_port=993, imap_user="a@test", imap_password="x",
        imap_mailbox="INBOX", organize_folder=None,
        smtp_host="smtp.test", smtp_port=465, smtp_user="a@test", smtp_password="x",
        mail_from="a@test", digest_to="b@test",
        sender_keywords=("github", "홍길동"), interval_minutes=10,
        state_file=str(tmp_path / "state.json"),
    )
    base.update(kw)
    return Config(**base)


# ── 파싱 / 필터 ───────────────────────────────────────────────────────


def test_parse_mail_decodes_korean_headers_and_html():
    m = parse_mail(7, raw_mail('"홍길동" <Gildong@Corp.kr>', "회의 안내",
                               html="<p>내일&nbsp;10시</p><script>x()</script>"))
    assert (m.uid, m.sender_name, m.sender_addr, m.subject) == (7, "홍길동", "gildong@corp.kr", "회의 안내")
    assert m.date == "2026-10-06 09:30"
    assert "내일" in m.body and "10시" in m.body and "x()" not in m.body


def test_matches_sender_by_name_or_address():
    kw = ("github", "홍길동")
    assert matches_sender(parse_mail(1, raw_mail("noreply@github.com", "s")), kw) == "github"
    assert matches_sender(parse_mail(1, raw_mail('"홍길동" <h@x.com>', "s")), kw) == "홍길동"
    assert matches_sender(parse_mail(1, raw_mail("shop@mall.com", "s")), kw) is None


@pytest.mark.parametrize("subject,expected", [
    ("(광고) 가을 세일", True), ("[광고]이벤트", True), ("(AD) Sale", True),
    ("광고 집행 결과 보고", False), ("PR 리뷰 요청", False),
])
def test_is_obvious_ad(subject, expected):
    assert is_obvious_ad(parse_mail(1, raw_mail("a@b.c", subject))) is expected


def test_encode_mailbox_modified_utf7():
    assert encode_mailbox("INBOX") == '"INBOX"'
    assert encode_mailbox("AI-정리") == '"AI-&yBW5rA-"'
    assert encode_mailbox("A&B") == '"A&-B"'


# ── run_once (IMAP/SMTP/Grok 은 가짜로 대체) ────────────────────────


class FakeMailbox:
    mails: dict[int, bytes] = {}
    uidvalidity = 1
    copied: list = []

    def __init__(self, cfg):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def uid_validity(self):
        return self.uidvalidity

    def max_uid(self):
        return max(self.mails, default=0)

    def uids_since(self, day):
        return sorted(u for u, raw in self.mails.items()
                      if parsedate_to_datetime(EmailMessage_from(raw)["Date"]).date() >= day)

    def uids_after(self, last):
        return sorted(u for u in self.mails if u > last)

    def fetch(self, uid):
        return parse_mail(uid, self.mails[uid])

    def copy_to_folder(self, uids, folder):
        FakeMailbox.copied.append((uids, folder))


class FakeSummarizer:
    def analyze(self, mail):
        return MailAnalysis(
            is_advertisement="뉴스레터" in mail.subject,
            summary=f"{mail.subject} 요약",
            key_points=["10/7 마감"],
            action_items=["리뷰하기"],
        )


@pytest.fixture
def env(monkeypatch):
    sent = []
    FakeMailbox.mails = {}
    FakeMailbox.uidvalidity = 1
    FakeMailbox.copied = []
    monkeypatch.setattr(agent, "Mailbox", FakeMailbox)
    monkeypatch.setattr(agent, "send_mail", lambda cfg, s, t, h=None: sent.append((s, t, h)))
    return sent


def days_ago(n: int) -> str:
    from datetime import datetime, timezone
    return format_datetime(datetime.now(timezone.utc) - timedelta(days=n))


def test_first_run_with_lookback_0_sets_baseline_without_sending(tmp_path, env):
    FakeMailbox.mails = {1: raw_mail("noreply@github.com", "옛날 메일")}
    cfg = make_cfg(tmp_path, initial_lookback_days=0)
    assert agent.run_once(cfg, FakeSummarizer()) == 0
    assert env == []
    assert agent.load_state(cfg.state_file) == {"uidvalidity": 1, "last_uid": 1}


def test_filters_summarizes_and_sends_digest(tmp_path, env):
    cfg = make_cfg(tmp_path, organize_folder="AI-정리")
    agent.save_state(cfg.state_file, 1, 10)
    FakeMailbox.mails = {
        10: raw_mail("noreply@github.com", "이미 처리한 메일"),
        11: raw_mail("noreply@github.com", "PR #42 리뷰 요청"),
        12: raw_mail("shop@mall.com", "키워드 없는 송신자"),
        13: raw_mail('"홍길동" <h@corp.kr>', "(광고) 제목 표시 광고"),
        14: raw_mail('"홍길동" <h@corp.kr>', "주간 뉴스레터"),  # Grok 이 광고로 판단
        15: raw_mail('"홍길동" <h@corp.kr>', "회의록 공유"),
    }

    assert agent.run_once(cfg, FakeSummarizer()) == 2
    (subject, text, html), = env
    assert "새 메일 2건" in subject
    assert "PR #42 리뷰 요청 요약" in text and "회의록 공유 요약" in text
    for excluded in ("이미 처리한", "키워드 없는", "제목 표시 광고", "주간 뉴스레터"):
        assert excluded not in text
    assert "☐ 리뷰하기" in text and "<li>10/7 마감</li>" in html
    assert FakeMailbox.copied == [([11, 15], "AI-정리")]
    assert agent.load_state(cfg.state_file)["last_uid"] == 15

    # 같은 메일을 두 번 보내지 않는다
    assert agent.run_once(cfg, FakeSummarizer()) == 0
    assert len(env) == 1


def test_state_not_advanced_when_send_fails(tmp_path, env, monkeypatch):
    cfg = make_cfg(tmp_path)
    agent.save_state(cfg.state_file, 1, 0)
    FakeMailbox.mails = {1: raw_mail("noreply@github.com", "중요")}

    def boom(*a, **k):
        raise OSError("smtp down")

    monkeypatch.setattr(agent, "send_mail", boom)
    with pytest.raises(OSError):
        agent.run_once(cfg, FakeSummarizer())
    assert agent.load_state(cfg.state_file)["last_uid"] == 0  # 다음 실행에서 재시도


def test_failed_summary_still_included(tmp_path, env):
    class NoneSummarizer:
        def analyze(self, mail):
            return None

    cfg = make_cfg(tmp_path)
    agent.save_state(cfg.state_file, 1, 0)
    FakeMailbox.mails = {1: raw_mail("noreply@github.com", "중요")}
    assert agent.run_once(cfg, NoneSummarizer()) == 1
    assert "자동 요약 실패" in env[0][1]


def test_config_treats_empty_env_as_default(monkeypatch):
    # GitHub Actions 에서 등록하지 않은 vars/secrets 는 빈 문자열로 들어온다
    required = dict(XAI_API_KEY="xai-test", IMAP_HOST="imap.test", IMAP_USER="a@test", IMAP_PASSWORD="x",
                    SMTP_HOST="smtp.test", SMTP_USER="a@test", SMTP_PASSWORD="x",
                    DIGEST_TO="b@test", SENDER_KEYWORDS=" GitHub , 홍길동 ,")
    for k, v in required.items():
        monkeypatch.setenv(k, v)
    for k in ("IMAP_PORT", "SMTP_PORT", "IMAP_MAILBOX", "GROK_MODEL",
              "MAIL_FROM", "ORGANIZE_FOLDER"):
        monkeypatch.setenv(k, "")
    monkeypatch.setattr("mail_agent.config.load_dotenv", lambda: None)

    cfg = Config.from_env()
    assert (cfg.imap_port, cfg.smtp_port, cfg.imap_mailbox) == (993, 465, "INBOX")
    assert (cfg.model, cfg.mail_from) == ("grok-4.3", "a@test")
    assert cfg.organize_folder is None
    assert cfg.sender_keywords == ("github", "홍길동")


def test_config_defaults_to_naver_and_completes_sender(monkeypatch):
    for k in ("IMAP_HOST", "SMTP_HOST", "MAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    for k, v in dict(IMAP_USER="myid", IMAP_PASSWORD="x", SMTP_USER="myid", SMTP_PASSWORD="x",
                     DIGEST_TO="b@test", SENDER_KEYWORDS="github",
                     XAI_API_KEY="xai-test").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("mail_agent.config.load_dotenv", lambda: None)

    cfg = Config.from_env()
    assert (cfg.imap_host, cfg.smtp_host) == ("imap.naver.com", "smtp.naver.com")
    assert cfg.mail_from == "myid@naver.com"

    # 전체 주소를 넣었거나 MAIL_FROM 을 지정하면 그대로 사용
    monkeypatch.setenv("SMTP_USER", "me@naver.com")
    assert Config.from_env().mail_from == "me@naver.com"
    monkeypatch.setenv("MAIL_FROM", "alias@naver.com")
    assert Config.from_env().mail_from == "alias@naver.com"


def test_first_run_processes_last_7_days(tmp_path, env):
    FakeMailbox.mails = {
        1: raw_mail("noreply@github.com", "10일 전 메일", date=days_ago(10)),
        2: raw_mail("noreply@github.com", "8일 전 메일", date=days_ago(8)),
        3: raw_mail("noreply@github.com", "6일 전 메일", date=days_ago(6)),
        4: raw_mail("shop@mall.com", "키워드 없음", date=days_ago(3)),
        5: raw_mail("noreply@github.com", "오늘 메일", date=days_ago(0)),
    }
    cfg = make_cfg(tmp_path)  # 기본값 7일

    assert agent.run_once(cfg, FakeSummarizer()) == 2
    text = env[0][1]
    assert "6일 전 메일" in text and "오늘 메일" in text
    assert "8일 전" not in text and "10일 전" not in text
    assert agent.load_state(cfg.state_file)["last_uid"] == 5


def test_first_run_without_recent_mail_uses_latest_uid(tmp_path, env):
    FakeMailbox.mails = {1: raw_mail("noreply@github.com", "옛날", date=days_ago(30))}
    cfg = make_cfg(tmp_path)
    assert agent.run_once(cfg, FakeSummarizer()) == 0
    assert env == []
    assert agent.load_state(cfg.state_file)["last_uid"] == 1


# ── Grok 호출 (HTTP 를 가짜 응답으로 대체) ───────────────────────────


def _grok_client(handler):
    import httpx2 as httpx
    from openai import OpenAI
    return OpenAI(api_key="xai-test", base_url="https://api.x.ai/v1",
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _completion(content=None, refusal=None, finish_reason="stop"):
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "grok-4.3",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content, "refusal": refusal}}]}


def test_summarizer_sends_schema_and_parses_result(tmp_path):
    import json
    import httpx2 as httpx
    from mail_agent.summarizer import Summarizer

    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        out = {"is_advertisement": False, "summary": "회의 일정 변경",
               "key_points": ["10/8 14시"], "action_items": ["참석 회신"]}
        return httpx.Response(200, json=_completion(json.dumps(out, ensure_ascii=False)))

    s = Summarizer(make_cfg(tmp_path), _grok_client(handler))
    result = s.analyze(parse_mail(1, raw_mail("noreply@github.com", "회의", "내일 회의 <b>")))

    assert result == MailAnalysis(is_advertisement=False, summary="회의 일정 변경",
                                  key_points=["10/8 14시"], action_items=["참석 회신"])
    assert seen["url"] == "https://api.x.ai/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == "grok-4.3"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "<email>" in body["messages"][1]["content"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"].keys() >= {
        "is_advertisement", "summary", "key_points", "action_items"}


@pytest.mark.parametrize("payload", [
    _completion(refusal="cannot help"),
    _completion('{"summary": "잘림', finish_reason="length"),
])
def test_summarizer_returns_none_on_refusal_or_truncation(tmp_path, payload):
    import httpx2 as httpx
    from openai import LengthFinishReasonError
    from mail_agent.summarizer import Summarizer

    s = Summarizer(make_cfg(tmp_path), _grok_client(lambda req: httpx.Response(200, json=payload)))
    try:
        assert s.analyze(parse_mail(1, raw_mail("noreply@github.com", "t"))) is None
    except LengthFinishReasonError:
        pytest.fail("finish_reason=length 가 예외로 새어 나옴")
