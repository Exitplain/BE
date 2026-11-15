import pytest
from agent.funnel import FUNNEL_KEYS, FUNNEL_NAMES, funnel_stage, stage_name


def test_seven_stages_in_report_order():
    assert [FUNNEL_NAMES[k] for k in FUNNEL_KEYS] == ["랜딩", "탐색", "상세", "장바구니", "가입", "결제 정보", "완료"]


@pytest.mark.parametrize("url,stage", [
    ("http://127.0.0.1:8000/", "landing"),
    ("http://127.0.0.1:8000/?variant=problem", "landing"),
    ("/search?q=반찬", "browse"),
    ("/category/side-dish", "browse"),
    ("/product", "detail"),
    ("/product/42", "detail"),
    ("/cart", "cart"),
    ("/signup", "signup"),
    ("/login", "signup"),
    ("/checkout", "payment"),
    ("/order/success", "complete"),
    ("/Thank-You/", "complete"),
    ("/about-us", "browse"),
])
def test_keyword_guess(url, stage):
    assert funnel_stage(url) == stage


def test_success_path_is_complete():
    assert funnel_stage("/finish", success_path="/finish") == "complete"
    assert funnel_stage("/finish") == "browse"


def test_stage_paths_from_site_analysis_win():
    table = {"/shop": "browse", "/shop/item": "detail", "/buy": "payment"}
    assert funnel_stage("/shop/item/3", stage_paths=table) == "detail"   # 더 긴 경로 우선
    assert funnel_stage("/shop/new", stage_paths=table) == "browse"
    assert funnel_stage("/buy", stage_paths=table) == "payment"
    assert funnel_stage("/buyer", stage_paths=table) == "browse"        # 단어 일부만 같으면 아님
    assert funnel_stage("/cart", stage_paths=table) == "cart"           # 표에 없으면 추측


def test_stage_name():
    assert stage_name("payment") == "결제 정보" and stage_name("other") == "other"

def test_stage_paths_param_segments_split_list_and_detail():
    # 같은 접두사를 쓰는 목록과 상세를 구분한다 (접두사 매칭만으로는 /products/5 가 목록이 된다)
    table = {"/products": "browse", "/products/:id": "detail"}
    assert funnel_stage("/products", stage_paths=table) == "browse"
    assert funnel_stage("/products/5", stage_paths=table) == "detail"
    assert funnel_stage("/products/5/reviews", stage_paths=table) == "detail"   # 접두사 규칙은 그대로
    assert funnel_stage("/PRODUCTS/5/", stage_paths=table) == "detail"          # 대소문자·끝 슬래시 무시


def test_stage_paths_most_specific_pattern_wins():
    table = {"/shop": "browse", "/shop/:cat": "browse", "/shop/:cat/:id": "detail", "/shop/sale/:id": "cart"}
    assert funnel_stage("/shop/shoes/9", stage_paths=table) == "detail"
    assert funnel_stage("/shop/sale/9", stage_paths=table) == "cart"            # 고정 구간이 많은 쪽이 이긴다
    assert funnel_stage("/shop/shoes", stage_paths=table) == "browse"


def test_stage_paths_root_matches_only_exactly_root():
    assert funnel_stage("/anything", stage_paths={"/": "landing"}) == "browse"
    assert funnel_stage("/", stage_paths={"/": "landing"}) == "landing"


def test_stage_paths_are_checked_before_success_path_and_legacy_words():
    assert funnel_stage("/done", success_path="/done", stage_paths={"/done": "payment"}) == "payment"
    assert funnel_stage("/cart", stage_paths={"/cart": "browse"}) == "browse"    # 표가 단어 추측보다 먼저


def test_without_legacy_words_unknown_paths_are_browse(monkeypatch):
    import agent.funnel as funnel
    monkeypatch.setattr(funnel, "USE_LEGACY_WORDS", False)
    assert funnel.funnel_stage("/cart") == "browse"
    assert funnel.funnel_stage("/") == "landing"
    assert funnel.funnel_stage("/finish", success_path="/finish") == "complete"
