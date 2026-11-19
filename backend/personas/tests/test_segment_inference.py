"""3단계 세그먼트 유형 추론 → 확정 때 그대로 쓰고, 같은 유형 안 사람마다 능력치를 조금씩 다르게."""
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from common.testing import WizardMixin, fake_discovery
from personas import generators
from personas.services import individual_variables


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class SegmentInferenceTests(WizardMixin, TestCase):
    def infer(self, plan):
        return self.client.post(f"/api/v1/plans/{plan['id']}/segment-inferences", {"revision": plan["revision"]},
                                format="json")

    def test_segments_are_named_on_step3_and_confirmation_reuses_them(self, _):
        plan = self.ready_plan()
        self.assertTrue(all(s["name"] is None for s in plan["segment_profiles"]))     # 추론 전
        res = self.infer(plan)
        self.assertEqual(res.status_code, 200, res.content)
        plan = res.json()
        names = [s["name"] for s in plan["segment_profiles"]]
        self.assertTrue(all(names) and len(set(names)) == 4)
        self.assertEqual([s["count"] for s in plan["segment_profiles"]], [s["count"] for s in plan["segments"]])
        self.assertTrue(plan["segment_generator"])

        with patch.object(generators, "generate", side_effect=AssertionError("확정 때 다시 만들면 안 됨")):
            snapshot = self.confirm(plan)
        self.assertEqual([s["name"] for s in snapshot["data"]["segments"]], names)

    def test_people_in_a_segment_get_slightly_different_abilities(self, _):
        plan = self.infer(self.ready_plan()).json()
        plan = self.patch(plan, {"segments": [{"id": f"segment_{i}", "count": 4 if i < 3 else 2}
                                              for i in range(1, 5)]}).json()
        personas = self.confirm(plan)["data"]["personas"]
        first = [p for p in personas if p["segment_id"] == "segment_1"]
        base = next(s["variables"] for s in plan["segment_profiles"] if s["id"] == "segment_1")
        self.assertEqual(len(first), 4)
        self.assertEqual(first[0]["variables"], base)                                  # 대표는 기본값
        self.assertGreater(len({tuple(p["variables"].values()) for p in first}), 1)    # 나머지는 조금씩 다름
        for p in first:
            self.assertTrue(all(abs(p["variables"][k] - base[k]) <= 30 and 5 <= p["variables"][k] <= 95
                                for k in base))

    def test_changing_the_audience_clears_inferred_segments(self, _):
        plan = self.infer(self.ready_plan()).json()
        plan = self.patch(plan, {"audience": {"context": "주말에 한꺼번에 장보는 가족", "ages": ["40s"]}}).json()
        self.assertTrue(all(s["name"] is None for s in plan["segment_profiles"]))
        # 추론 없이 확정해도 예전처럼 그때 만든다
        self.assertEqual(len(self.confirm(plan)["data"]["segments"]), 4)

    def test_generator_failure_is_503_with_reason_not_500(self, _):
        plan = self.ready_plan()
        with patch.object(generators, "generate",
                          side_effect=generators.GenerationError("생성 모델 API 키가 맞지 않습니다")):
            res = self.infer(plan)
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"]["code"], "PERSONA_GENERATION_FAILED")
        self.assertIn("API 키", res.json()["error"]["message"])

    def test_stale_revision_is_rejected(self, _):
        plan = self.ready_plan()
        res = self.client.post(f"/api/v1/plans/{plan['id']}/segment-inferences", {"revision": plan["revision"] - 1},
                               format="json")
        self.assertEqual(res.status_code, 409)

    def test_individual_variables_are_deterministic(self, _):
        base = {"patience": 50, "digital_literacy": 50, "price_sensitivity": 50, "trust_need": 50}
        self.assertEqual(individual_variables(base, "segment_2", 3), individual_variables(base, "segment_2", 3))
        self.assertEqual(individual_variables(base, "segment_2", 0), base)


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class SegmentInferenceOutsideTransactionTests(WizardMixin, TransactionTestCase):
    """운영처럼 요청이 트랜잭션 밖에서 돌 때도 된다 (TestCase 는 테스트를 트랜잭션으로 감싸 이 오류를 숨긴다)."""

    def test_inference_works_without_an_outer_transaction(self, _):
        plan = self.ready_plan()
        res = self.client.post(f"/api/v1/plans/{plan['id']}/segment-inferences", {"revision": plan["revision"]},
                               format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(all(s["name"] for s in res.json()["segment_profiles"]))
