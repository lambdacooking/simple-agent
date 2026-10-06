"""Claude 를 부르기 전에 돌리는 규칙 기반 필터 (빠르고 비용이 들지 않음)."""

import re

from .mail_client import Mail

# 정보통신망법상 광고성 메일은 제목 앞에 "(광고)" 를 붙여야 하므로 강한 신호로 쓴다.
_AD_SUBJECT = re.compile(r"^\s*[\(\[]\s*(광고|ad|advertisement)\s*[\)\]]", re.IGNORECASE)


def matches_sender(mail: Mail, keywords: tuple[str, ...]) -> str | None:
    """송신자 이름/주소에 포함된 첫 번째 키워드를 돌려준다. 없으면 None."""
    haystack = f"{mail.sender_name} <{mail.sender_addr}>".lower()
    return next((k for k in keywords if k in haystack), None)


def is_obvious_ad(mail: Mail) -> bool:
    """제목에 (광고) 표시가 붙은 메일은 Claude 에 보내지 않고 바로 제외한다.

    List-Unsubscribe 같은 헤더는 원하는 뉴스레터에도 붙어 있어서 여기서 쓰지 않고,
    애매한 경우는 Claude 의 판단(is_advertisement)에 맡긴다.
    """
    return bool(_AD_SUBJECT.match(mail.subject))
