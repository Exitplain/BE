from agent.explain import describe


def test_dropout_names_screen_cause_score_and_last_action():
    issues = [
        {"category": "navigation", "stage": "browse"},
        {"category": "input_burden", "primary": True, "observed_fields": 6, "configured_limit": 4,
         "dropout": {"stage": "payment", "path": "/checkout", "score": 72.4, "threshold": 60.0,
                     "last_thought": "주문하기 버튼을 눌러 결제로 이동"}},
    ]
    text = describe("simulated_dropout", "Configured frustration threshold exceeded", issues)
    assert text.startswith("결제 정보 화면(/checkout)에서 이탈")
    assert "주 원인 입력 부담: 필수 입력 6칸 (허용 4칸)" in text
    assert "불만 72/60" in text and "함께 쌓인 원인 탐색 실패" in text
    assert text.endswith("직전 행동: 주문하기 버튼을 눌러 결제로 이동")


def test_old_sessions_without_dropout_record_still_get_a_line():
    text = describe("simulated_dropout", "", [{"category": "cost", "primary": True}], "cart")
    assert text.startswith("장바구니 화면에서 이탈") and "비용 충격" in text
    assert describe("simulated_dropout", "", []) == "이탈 · 주 원인 미상"


def test_other_endings_are_plain_korean():
    assert describe("success") == "목표 화면에 도달했어요"
    assert "기술 실패" in describe("agent_failure", "Policy stopped without verified completion")
    assert describe("site_failure", "HTTP 500").endswith("(HTTP 500)")
