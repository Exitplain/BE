"""직접 추가한 과업의 목표·성공 기준 만들기 (agent/criteria.py). 실제 API 호출 없음."""
from types import SimpleNamespace

from agent.criteria import CriteriaDraft, CriteriaSet, draft_criteria, template_criteria


class Client:
    def __init__(self, answer=None, error=None, stop="end_turn"):
        self.answer, self.error, self.stop = answer, error, stop
        self.messages = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop, parsed_output=self.answer)


TASKS = [{"id": "custom_1", "text": "배송 일정 바꾸기"}, {"id": "custom_2", "text": "쿠폰 찾기"}]


def test_model_answer_is_used_and_missing_ones_fall_back():
    answer = CriteriaSet(tasks=[CriteriaDraft(id="custom_1", goal="다음 배송 날짜를 바꾸고 싶다",
                                              success_criteria="바뀐 배송 날짜가 보이는 화면")])
    out = draft_criteria(TASKS, entry_url="https://shop.example/", client=Client(answer))
    assert out["custom_1"] == {"goal": "다음 배송 날짜를 바꾸고 싶다", "success_criteria": "바뀐 배송 날짜가 보이는 화면",
                               "criteria_source": "model"}
    assert out["custom_2"] == template_criteria("쿠폰 찾기")


def test_errors_refusals_and_blank_answers_never_raise():
    blank = CriteriaSet(tasks=[CriteriaDraft(id="custom_1", goal=" ", success_criteria="화면")])
    for client in (Client(error=RuntimeError("timeout")), Client(answer=None, stop="refusal"), Client(blank)):
        out = draft_criteria(TASKS, entry_url="https://shop.example/", client=client)
        assert {v["criteria_source"] for v in out.values()} == {"template"}


def test_no_api_key_means_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    out = draft_criteria(TASKS[:1], entry_url="https://shop.example/")
    assert out["custom_1"]["criteria_source"] == "template"
    assert "'배송 일정 바꾸기'를 마쳤다는 결과가 화면에 보인다" in out["custom_1"]["success_criteria"]