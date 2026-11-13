"""cursor 페이지용 불투명 토큰. 정수 몇 개를 base64 로 감싼다 (프런트는 내용을 해석하지 않는다)."""
import base64
import binascii
import json

from common.exceptions import ApiError


def encode(*numbers):
    return base64.urlsafe_b64encode(json.dumps(list(numbers)).encode()).decode().rstrip("=")


def decode(cursor, count=1):
    try:
        values = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
        if (not isinstance(values, list) or len(values) != count
                or any(not isinstance(v, int) or isinstance(v, bool) for v in values)):
            raise ValueError
        return values
    except (ValueError, TypeError, binascii.Error):
        raise ApiError(422, "VALIDATION_ERROR", "cursor가 올바르지 않습니다.",
                       {"cursor": ["cursor가 올바르지 않습니다."]}) from None
