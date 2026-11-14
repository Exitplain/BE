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