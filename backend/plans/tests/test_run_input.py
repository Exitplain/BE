from unittest.mock import patch

from django.test import TestCase, override_settings

from agent.schemas import RunInput
from common.projects import project_model
from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import RunInputError, build_run_input, build_run_inputs


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class RunInputTests(WizardMixin, TestCase):
    def snapshot(self):
        return PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])

    def test_one_input_per_task_and_seed(self, _):
        inputs = build_run_inputs(self.snapshot(), run_id="run_42")
        self.assertEqual([i["run_id"] for i in inputs],
                         ["run_42_order_s1", "run_42_order_s2", "run_42_order_s3",
                          "run_42_coupon_s1", "run_42_coupon_s2", "run_42_coupon_s3"])
        first = RunInput.model_validate(inputs[0])
        self.assertEqual(first.target_url, "http://shop.local:8080/?variant=problem&case=signup")
        self.assertEqual(first.success_path, "/order/success")
        self.assertEqual(first.allowed_origins, ["http://shop.local:8080"])
        self.assertEqual((len(first.personas), first.personas[0].id), (20, "P-001"))
        self.assertEqual((first.max_steps, first.timeout_seconds, first.concurrency), (40, 300, 5))
        # Figma 실행 조건과 페르소나 수치가 에이전트에 그대로 들어간다
        self.assertEqual((first.seed, first.viewport_width, first.viewport_height, first.action_interval_seconds),
                         (1, 390, 844, 1.0))
        self.assertEqual([i["seed"] for i in inputs[:3]], [1, 2, 3])
        p1 = first.personas[0]
        self.assertEqual((p1.patience, p1.digital_literacy, p1.price_sensitivity, p1.trust_need), (22, 80, 40, 30))
        self.assertEqual(first.personas[5].trust_need, 75)          # 두 번째 세그먼트(꼼꼼한 비교형)
        self.assertEqual(p1.frustration_threshold, 60)

    def test_frustration_off_and_desktop(self, _):
        plan = self.patch(self.ready_plan(), {"frustration_enabled": False, "limits": {"viewport_width": 1280}}).json()
        snapshot = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        inputs = build_run_inputs(snapshot, run_id="r")
        first = RunInput.model_validate(inputs[0])
        self.assertTrue(all(p.frustration_threshold >= 1_000_000 for p in first.personas))   # 가상 이탈 없음
        self.assertEqual((first.viewport_width, first.viewport_height), (1280, 800))

    def test_frozen_after_project_change(self, _):
        snapshot = self.snapshot()
        project_model().objects.update(entry_url="http://changed.local/")
        self.assertEqual(build_run_input(snapshot, run_id="r", task_id="order")["allowed_origins"],
                         ["http://shop.local:8080"])

    def test_errors(self, _):
        snapshot = self.snapshot()
        for kwargs in [{"run_id": "../x", "task_id": "order"}, {"run_id": "r", "task_id": "delivery"},
                       {"run_id": "r", "task_id": "order", "seed": 4}]:
            with self.assertRaises(RunInputError):
                build_run_input(snapshot, **kwargs)

    def test_preview_endpoint(self, _):
        snap = self.confirm()
        res = self.client.get(f"/api/v1/plan-revisions/{snap['plan_revision_id']}/run-inputs").json()
        self.assertEqual(len(res["items"]), 6)
        self.assertEqual(len(res["not_passed_to_agent"]), 2)
        self.assertTrue(res["not_passed_to_agent"][1].startswith("limits.cost_cap"))

    @override_settings(EXITPLAIN_APPEND_DEMO_QUERY=False)
    def test_without_demo_query(self, _):
        self.assertEqual(build_run_input(self.snapshot(), run_id="r", task_id="order")["target_url"],
                         "http://shop.local:8080/")