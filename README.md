# simple-agent: 메일 요약 에이전트

10분마다 **A 메일함**에서 새 메일을 확인하고, 원하는 송신자의 메일만 골라 광고를 걸러낸 뒤,
**Claude**로 핵심을 요약해서 **B 메일 주소**로 보내 줍니다.

```
┌──────── 10분마다 (APScheduler) ────────┐
│ 1. A 메일함 IMAP 접속, 지난 실행 이후 새 메일(UID)만 조회
│ 2. 송신자 키워드 필터     ── 불일치 → 건너뜀
│ 3. 제목 "(광고)" 표시 필터 ── 광고 → 건너뜀          (Claude 호출 없음, 비용 0)
│ 4. Claude: 광고 판단 + 요약 (structured output)
│        └ is_advertisement=true → 건너뜀
│ 5. 남은 메일들을 하나의 요약 메일로 B 주소에 발송 (SMTP)
│ 6. (선택) 해당 메일을 정리 폴더/라벨로 복사
│ 7. 처리 위치(last UID) 저장 → 다음 실행은 그 이후부터
└────────────────────────────────────────┘
```

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
```

### `.env` 주요 항목

| 변수 | 설명 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `IMAP_*` | A 메일 계정 (새 메일 읽기) |
| `SMTP_*`, `MAIL_FROM` | 요약 메일을 보낼 계정 (보통 A 계정 그대로) |
| `DIGEST_TO` | B 메일 주소 (요약 받는 곳) |
| `SENDER_KEYWORDS` | 송신자 이름/주소에 포함되면 대상. 쉼표 구분, 대소문자 무시 (예: `github.com,홍길동,@mycompany.co.kr`) |
| `ORGANIZE_FOLDER` | (선택) 대상 메일을 복사해 둘 폴더. Gmail에서는 라벨로 보임 |
| `CHECK_INTERVAL_MINUTES` | 확인 주기, 기본 10 |
| `ANTHROPIC_MODEL`, `CLAUDE_EFFORT` | 기본 `claude-opus-5-5`, `low` |

**Gmail을 쓰는 경우:** 일반 비밀번호로는 로그인되지 않습니다.
Google 계정 → 보안 → 2단계 인증 켜기 → **앱 비밀번호** 생성 후 `IMAP_PASSWORD`/`SMTP_PASSWORD`에 넣으세요.
(Gmail 설정 → 전달 및 POP/IMAP 에서 IMAP 사용도 켜져 있어야 합니다.)
네이버 메일도 환경설정에서 IMAP/SMTP를 켜면 `imap.naver.com` / `smtp.naver.com`(465)으로 똑같이 동작합니다.

## GitHub Actions 로 실행 (서버 없이)

`.github/workflows/mail-agent.yml` 이 10분마다 `python -m mail_agent --once` 를 실행합니다.
저장소 **Settings → Secrets and variables → Actions** 에서 아래 값을 등록하면 바로 동작합니다.

**Secrets** (값이 로그에 가려짐 – 개인정보는 전부 여기에)

| 이름 | 예시 |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `IMAP_USER` / `IMAP_PASSWORD` | A 계정 / 앱 비밀번호 |
| `SMTP_USER` / `SMTP_PASSWORD` | 보통 A 계정과 동일 |
| `DIGEST_TO` | B 메일 주소 |
| `SENDER_KEYWORDS` | `github.com,홍길동` |
| `MAIL_FROM`, `ORGANIZE_FOLDER` | (선택) |

**Variables** (선택, 공개 로그에 보임 – 민감하지 않은 값만)

| 이름 | 기본값 |
|---|---|
| `IMAP_HOST` / `SMTP_HOST` | `imap.gmail.com` / `smtp.gmail.com` |
| `IMAP_PORT` / `SMTP_PORT` | `993` / `465` |
| `IMAP_MAILBOX` | `INBOX` |
| `ANTHROPIC_MODEL` / `CLAUDE_EFFORT` | `claude-opus-5-5` / `low` |

등록 후 **Actions → mail-agent → Run workflow** 로 한 번 수동 실행해서 확인하세요
(첫 실행은 기준점만 저장하고 메일은 보내지 않습니다. 그 뒤 새 메일이 오면 다음 실행에서 요약이 옵니다).

알아둘 점:
- **처리 위치(`state.json`)는 Actions 캐시에 저장됩니다.** UID 숫자만 들어 있어 메일 내용은 남지 않습니다.
  7일 넘게 실행이 멈춰 캐시가 지워지면 다시 기준점부터 시작합니다 (그 사이 메일은 요약되지 않음).
- **공개 저장소라 실행 로그를 누구나 볼 수 있습니다.** 그래서 로그에는 UID·건수만 남기고 제목·주소·본문은 남기지 않습니다.
  디버그용 `-v` 옵션은 Actions 에서 켜지 마세요.
- GitHub 의 예약 실행은 부하에 따라 **몇 분씩 늦어지거나 가끔 건너뛰어질 수 있습니다.** 정확히 10분이 필요하면 직접 서버에서 돌리세요.
- 공개 저장소에서 **60일간 커밋 등 활동이 없으면 예약 실행이 자동으로 꺼집니다.** GitHub 에서 알림 메일이 오면 Actions 탭에서 다시 켜면 됩니다.
- 공개 저장소의 표준 러너는 Actions 사용 시간이 무료입니다. (비공개로 바꾸면 월 무료 시간 2,000분을 넘기게 되므로 주기를 늘리세요.)

## 직접 실행

```bash
python -m mail_agent          # 10분마다 반복 실행 (시작 직후 1회 실행)
python -m mail_agent --once   # 1회만 실행 (cron 등 외부 스케줄러용)
python -m mail_agent -v       # 디버그 로그 (어떤 메일이 왜 걸러졌는지 확인)
```

> **첫 실행은 기준점만 저장합니다.** 메일함의 기존 메일 전체를 요약하지 않도록,
> 첫 실행 때는 현재 마지막 메일 위치만 `state.json`에 기록하고 그 이후 도착한 메일부터 처리합니다.
> 처음부터 다시 하고 싶으면 `state.json`을 지우면 됩니다.

cron을 쓰고 싶다면 내장 스케줄러 대신:

```cron
*/10 * * * * cd /path/to/simple-agent && .venv/bin/python -m mail_agent --once >> agent.log 2>&1
```

## 동작 메모

- 메일은 `BODY.PEEK`로 읽어서 **읽음 표시가 붙지 않습니다.** 원본 메일은 이동·삭제하지 않습니다.
- 요약 메일 **발송까지 성공해야** 처리 위치를 저장합니다. SMTP/Claude 오류가 나면 다음 주기에 같은 메일을 다시 시도합니다.
- 한 번에 최대 50통까지 처리하고, 나머지는 다음 주기로 넘깁니다 (오래 꺼져 있다 켜졌을 때 비용 폭주 방지).
- 30,000자를 넘는 본문은 앞부분만 Claude에 보내고, 잘렸다는 사실을 프롬프트에 함께 적습니다.
- 메일 본문은 신뢰할 수 없는 입력이므로, 프롬프트에서 데이터로만 다루도록 지시하고 출력은 정해진 JSON 스키마로만 받습니다.
- Claude 안전 분류기가 요청을 거절하는 드문 경우에 대비해 서버 측 `fallbacks: "default"`를 켜 두었습니다.
  그래도 요약이 실패하면 메일은 "자동 요약 실패" 표시와 함께 요약 메일에 포함됩니다 (빠뜨리지 않음).

## 구조

```
mail_agent/
├── __main__.py     # CLI + 10분 스케줄러
├── agent.py        # 1회 실행 흐름, 상태 저장, 요약 메일 본문 생성
├── config.py       # .env 로딩
├── filters.py      # 송신자 키워드 / (광고) 제목 필터
├── mail_client.py  # IMAP 읽기, SMTP 발송
└── summarizer.py   # Claude 호출 (광고 판단 + 요약)
tests/test_agent.py # IMAP/SMTP/Claude 를 가짜로 바꾼 단위 테스트
```

테스트: `pip install pytest && python -m pytest -q`
