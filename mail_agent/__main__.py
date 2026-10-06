"""실행 진입점.

    python -m mail_agent          # 10분마다 반복 실행 (스케줄러)
    python -m mail_agent --once   # 한 번만 실행 (cron 등 외부 스케줄러용)
"""

import argparse
import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from .agent import run_once
from .config import Config
from .summarizer import Summarizer

log = logging.getLogger("mail_agent")


def main() -> None:
    parser = argparse.ArgumentParser(description="새 메일을 정리해 요약본을 보내는 Claude 에이전트")
    parser.add_argument("--once", action="store_true", help="한 번만 실행하고 종료")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그 출력")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 디버그 모드에서도 HTTP 라이브러리 로그는 조용히
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config.from_env()
    summarizer = Summarizer(cfg)

    def job() -> None:
        try:
            run_once(cfg, summarizer)
        except Exception:  # 한 번 실패해도 스케줄러는 계속 돈다
            log.exception("실행 중 오류 – 다음 주기에 다시 시도합니다")

    if args.once:
        run_once(cfg, summarizer)
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(
        job,
        "interval",
        minutes=cfg.interval_minutes,
        next_run_time=datetime.now(),  # 시작하자마자 한 번 실행
        max_instances=1,  # 이전 실행이 안 끝났으면 겹쳐 실행하지 않음
        coalesce=True,
    )
    log.info("%d분마다 %s 메일함을 확인합니다 (Ctrl+C 로 종료)", cfg.interval_minutes, cfg.imap_user)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
