"""리포트 퍼널 7단계 (Figma P12): 화면 경로 → 단계.

정하는 순서
1) stage_paths: 백엔드(사이트 분석)가 넘겨준 '경로 → 단계' 표. 가장 길게 맞는 경로가 우선
2) success_path와 같은 경로는 '완료'
3) 경로에 들어 있는 단어로 추측 (아래 KEYWORDS)
4) 첫 화면('/')은 '랜딩', 나머지 모르는 화면은 '탐색'

기존 frustration.STAGES는 데모 대시보드가 쓰므로 그대로 둔다.
"""
from urllib.parse import urlsplit

FUNNEL = [
    ("landing", "랜딩"),
    ("browse", "탐색"),
    ("detail", "상세"),
    ("cart", "장바구니"),
    ("signup", "가입"),
    ("payment", "결제 정보"),
    ("complete", "완료"),
]
FUNNEL_KEYS = [key for key, _ in FUNNEL]
FUNNEL_NAMES = dict(FUNNEL)
FUNNEL_INDEX = {key: i for i, key in enumerate(FUNNEL_KEYS)}

# 위에서부터 먼저 맞는 것을 쓴다 ('/order/success'는 결제가 아니라 완료)
KEYWORDS = [
    ("complete", ("success", "complete", "completed", "done", "thanks", "thank-you", "thankyou", "confirmation")),
    ("payment", ("checkout", "payment", "pay", "order", "orders", "billing", "shipping", "delivery")),
    ("signup", ("signup", "sign-up", "register", "join", "login", "signin", "sign-in", "auth", "account", "member")),
    ("cart", ("cart", "basket", "bag")),
    ("detail", ("product", "item", "goods", "detail", "p")),
    ("browse", ("search", "category", "categories", "list", "collection", "collections", "products", "shop", "browse", "menu")),
]


def _path(url_or_path):
    path = urlsplit(url_or_path).path or "/"
    path = "/" + path.strip("/")
    return path.lower()


def funnel_stage(url_or_path, success_path=None, stage_paths=None):
    """URL이나 경로를 받아 7단계 키(landing … complete) 중 하나를 돌려준다."""
    path = _path(url_or_path)
    if stage_paths:
        matches = [(len(p), stage) for p, stage in ((_path(k), v) for k, v in stage_paths.items())
                   if path == p or (p != "/" and path.startswith(p + "/"))]
        if matches:
            return max(matches)[1]
    if success_path and path == _path(success_path):
        return "complete"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "landing"
    for stage, words in KEYWORDS:
        if any(segment in words for segment in segments):
            return stage
    return "browse"


def stage_name(key):
    return FUNNEL_NAMES.get(key, key)