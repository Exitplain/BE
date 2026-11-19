from agent.explain import describe


def test_dropout_reads_like_the_persona_talking():
    issues = [
        {"category": "navigation", "stage": "browse"},
        {"category": "input_burden", "primary": True, "observed_fields": 6, "configured_limit": 4,
         "dropout": {"stage": "payment", "path": "/checkout", "score": 72.4, "threshold": 60.0,
                     "last_thought": "주문하기 버튼을 눌러 결제로 이동"}},
    ]
    text = describe("simulated_dropout", "Configured frustration threshold exceeded", issues)
    assert text == ("결제 정보 화면에서 그만뒀어요. 입력할 칸이 6개나 돼서 부담스러웠어요. "
                    "4개 정도까지는 괜찮았을 거예요. 앞에서도 길을 헤맨 것 때문에 조금씩 지쳐 있었어요. "
                    "그때는 '주문하기 버튼을 눌러 결제로 이동' 하려던 참이었어요.")
    assert "72" not in text and "주 원인" not in text            # 내부 점수·용어는 문장에 넣지 않는다


def test_old_sessions_without_dropout_record_still_get_sentences():
    text = describe("simulated_dropout", "", [{"category": "cost", "primary": True}], "cart")
    assert text.startswith("장바구니 화면에서 그만뒀어요.") and "배송비" in text
    assert describe("simulated_dropout", "", []) == "여기서 그만뒀어요. 왜 그만뒀는지는 기록이 남지 않았어요."


def test_custom_path_stage_uses_the_path():
    text = describe("simulated_dropout", "", [{"category": "trust", "primary": True,
                                               "dropout": {"stage": "/delivery-check", "path": "/delivery-check"}}])
    assert text.startswith("/delivery-check 화면에서 그만뒀어요.")


def test_other_endings_say_it_was_not_a_dropout():
    assert describe("success") == "끝까지 진행해서 목표 화면에 도착했어요."
    assert describe("agent_failure", "Policy stopped without verified completion").startswith("이탈은 아니에요.")
    assert describe("site_failure", "HTTP 500").endswith("(HTTP 500).")
