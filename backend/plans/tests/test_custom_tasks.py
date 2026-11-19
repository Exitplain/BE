"""직접 추가한 과업(PLAN-02): 확정 때 목표·성공 기준으로 바꿔 실행에 넣는다 (EXITPLAIN_RUN_CUSTOM_TASKS)."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from agent.criteria import CriteriaDraft, CriteriaSet
from agent.schemas import RunInput
from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import build_run_inputs
from runs.worker import agent_policy


class FakeClient:
    """messages.parse 만 흉내 낸다. 모델 답(CriteriaSet)을 그대로 돌려준다."""

    def __init__(self, drafts):
        self.calls = []
        self.messages = SimpleNamespace(parse=self.parse)
        self.drafts = drafts

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=CriteriaSet(tasks=self.drafts))


@override_settings(EXITPLAIN_RUN_CUSTOM_TASKS=True)
@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class CustomTaskRunTests(WizardMixin, TestCase):
    # STEP2: 고른 과업 order·coupon + 직접 추가한 과업 "배송지 바꾸기"

    def snapshot(self):
        return PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])

    def test_review_counts_custom_task_as_run(self, _):
        review = self.ready_plan()["review"]
        self.assertEqual(review["tasks"]["custom_names"], ["배송지 바꾸기"])
        self.assertEqual(review["not_in_run"]["custom_tasks"], [])
        self.assertEqual(review["total_sessions"], 10 * 3 * 1)          # 페르소나 10 × 과업 3 × 시드 1

    def test_model_writes_goal_and_on_screen_criteria(self, _):
        client = FakeClient([CriteriaDraft(id="custom_1", goal="받는 곳 주소를 다른 곳으로 바꾸고 싶다",
                                           success_criteria="배송지가 바뀌었다는 안내나 새 주소가 보이는 화면")])
        with patch("agent.criteria._client", return_value=client):
            snap = self.snapshot()
        custom = snap.data["tasks"][-1]
        self.assertEqual((custom["id"], custom["name"], custom["custom"]), ("custom_1", "배송지 바꾸기", True))
        self.assertEqual(custom["success_criteria"], "배송지가 바뀌었다는 안내나 새 주소가 보이는 화면")
        self.assertEqual((custom["success_path"], custom["criteria_source"]), (None, "model"))
        self.assertEqual(snap.data["not_in_run"]["custom_tasks"], [])
        self.assertEqual(snap.data["total_sessions"], 10 * 3 * 1)
        # 사용자 문장은 데이터로만 넘긴다 (지시문이 아니라 messages 안의 JSON)
        self.assertIn("배송지 바꾸기", client.calls[0]["messages"][0]["content"])

    def test_without_model_uses_template_and_confirm_is_not_blocked(self, _):
        with patch("agent.criteria._client", return_value=None):
            custom = self.snapshot().data["tasks"][-1]
        self.assertEqual(custom["criteria_source"], "template")
        self.assertEqual(custom["goal"], "배송지 바꾸기")
        self.assertIn("'배송지 바꾸기'를 마쳤다는 결과가 화면에 보인다", custom["success_criteria"])

    def test_broken_model_answer_falls_back_per_task(self, _):
        client = FakeClient([CriteriaDraft(id="not-asked", goal="엉뚱한 과업", success_criteria="엉뚱한 화면")])
        with patch("agent.criteria._client", return_value=client):
            custom = self.snapshot().data["tasks"][-1]
        self.assertEqual(custom["criteria_source"], "template")

    def test_run_input_is_valid_and_opens_demo_shop_like_other_tasks(self, _):
        with patch("agent.criteria._client", return_value=None):
            inputs = build_run_inputs(self.snapshot(), run_id="run_7")
        self.assertEqual([i["run_id"] for i in inputs], ["run_7_order_s1", "run_7_coupon_s1", "run_7_custom_1_s1"])
        custom = RunInput.model_validate(inputs[-1])
        self.assertIsNone(custom.success_path)
        self.assertTrue(custom.success_criteria)
        self.assertEqual(custom.target_url, "http://shop.local:8080/?variant=problem&case=all")

    @override_settings(EXITPLAIN_RUN_CUSTOM_TASKS=False)
    def test_switch_off_keeps_the_old_draft_only_behavior(self, _):
        snap = self.snapshot()
        self.assertEqual([t["id"] for t in snap.data["tasks"]], ["order", "coupon"])
        self.assertEqual(snap.data["not_in_run"]["custom_tasks"][0]["text"], "배송지 바꾸기")


class AgentPolicyTests(TestCase):
    @override_settings(AGENT_POLICY="shop_scripted", AGENT_CRITERIA_POLICY="claude")
    def test_only_criteria_tasks_switch_to_the_model_policy(self):
        self.assertEqual(agent_policy({"success_path": "/order/success"}), "shop_scripted")
        self.assertEqual(agent_policy({"success_path": None, "success_criteria": "완료 화면이 보인다"}), "claude")

    @override_settings(AGENT_POLICY="openai", AGENT_CRITERIA_POLICY="claude")
    def test_model_policy_is_left_as_it_is(self):
        self.assertEqual(agent_policy({"success_path": None, "success_criteria": "완료 화면이 보인다"}), "openai")