"""리포트 퍼널 7단계 (Figma P12): 화면 경로 → 단계.

정하는 순서
1) stage_paths: 백엔드(사이트 분석)가 넘겨준 '경로 → 단계' 표. 가장 구체적인 경로가 우선
   - 경로 접두사로 맞춘다 ('/'는 정확히 '/'만).
   - 키에 ':param' 구간을 쓸 수 있다 ('/products/:id' 는 '/products/5' 에 맞고 '/products' 에는 맞지 않는다).
     같은 접두사를 쓰는 목록('/products')과 상세('/products/:id')를 구분하기 위해 필요하다.
     표는 ai_extra/integration 이 대상 코드의 구조 신호로 만든다 (단어 사전 없음).
2) success_path와 같은 경로는 '완료'
3) 첫 화면('/')은 '랜딩'
4) (레거시, USE_LEGACY_WORDS) 경로에 들어 있는 단어로 추측 (아래 LEGACY_WORDS)
5) 나머지 모르는 화면은 '탐색'

LEGACY_WORDS 는 데모 쇼핑몰·기존 테스트가 stage_paths 없이 단계를 얻던 방식이라 기본값으로 남겨 둔다.
stage_paths 를 데모에도 공급하게 되면 USE_LEGACY_WORDS 를 False 로 두고 이 표를 지운다.

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

# stage_paths 가 ':param' 구간을 이해한다는 표시 (ai_extra/integration 검증이 이 값을 본다)
SUPPORTS_STAGE_PATTERNS = True
# False 면 단어 추측을 쓰지 않고 모르는 화면은 '탐색'으로 둔다
USE_LEGACY_WORDS = True

# 레거시: 위에서부터 먼저 맞는 것을 쓴다 ('/order/success'는 결제가 아니라 완료)
LEGACY_WORDS = [
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


def _match_stage_paths(path, stage_paths):
    """접두사 일치, ':param' 구간은 어떤 한 구간과도 맞는다. 가장 구체적인(구간 수, 고정 구간 수) 표가 이긴다."""
    segs = [s for s in path.split("/") if s]
    best = None
    for key, stage in stage_paths.items():
        pat = [s for s in _path(key).split("/") if s]
        if len(pat) > len(segs) or (not pat and segs):  # '/' 는 정확히 '/' 만
            continue
        if all(p.startswith(":") or p == s for p, s in zip(pat, segs)):
            score = (len(pat), sum(1 for p in pat if not p.startswith(":")), stage)
            if best is None or score > best[0]:
                best = (score, stage)
    return best[1] if best else None


def funnel_stage(url_or_path, success_path=None, stage_paths=None):
    """URL이나 경로를 받아 7단계 키(landing … complete) 중 하나를 돌려준다."""
    path = _path(url_or_path)
    if stage_paths:
        matched = _match_stage_paths(path, stage_paths)
        if matched:
            return matched
    if success_path and path == _path(success_path):
        return "complete"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "landing"
    if USE_LEGACY_WORDS:
        for stage, words in LEGACY_WORDS:
            if any(segment in words for segment in segments):
                return stage
    return "browse"


def stage_name(key):
    return FUNNEL_NAMES.get(key, key)