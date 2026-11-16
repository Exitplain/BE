from unittest.mock import patch

from django.test import TestCase, override_settings

from common.testing import WizardMixin, fake_discovery
from personas.models import PersonaSnapshot
from plans.models import PlanSnapshot


def custom_generator(audience_context, ages, segment_counts):
    return [{"name": f"세그{i}", "variables": {"patience": 10, "digital_literacy": 20,
             "price_sensitivity": 30, "trust_need": 40}} for i in range(len(segment_counts))]


def broken_generator(audience_context, ages, segment_counts):
    return [{"name": "하나뿐", "variables": {}}]


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class PersonaTests(WizardMixin, TestCase):
    def test_p07_view(self, _):
        snap = self.confirm()
        res = self.client.get(f"/api/v1/plan-revisions/{snap['plan_revision_id']}/personas").json()
        self.assertEqual([s["name"] for s in res["segments"]], ["빠른 구매형", "꼼꼼한 비교형", "가격 민감형", "신중한 첫 방문형"])
        self.assertEqual(len(res["items"]), 20)
        p1 = res["items"][0]
        self.assertEqual((p1["code"], p1["is_representative"]), ("P-001", True))
        self.assertIn("타겟: 퇴근 후", p1["context"])
        self.assertIn("연령 문맥: 30대", p1["context"])

    @override_settings(EXITPLAIN_PERSONA_GENERATOR="personas.tests.test_api.custom_generator")
    def test_custom_generator(self, _):
        snap = self.confirm()
        self.assertEqual(snap["persona_generator"], "personas.tests.test_api.custom_generator")
        self.assertEqual(snap["data"]["segments"][0]["name"], "세그0")

    @override_settings(EXITPLAIN_PERSONA_GENERATOR="personas.tests.test_api.broken_generator")
    def test_broken_generator_rolls_back_confirmation(self, _):
        plan = self.ready_plan()
        res = self.client.post(f"/api/v1/plans/{plan['id']}/confirmations", {"revision": plan["revision"]},
                               format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "PERSONA_GENERATION_FAILED"))
        self.assertEqual((PlanSnapshot.objects.count(), PersonaSnapshot.objects.count()), (0, 0))


class RuleGeneratorTests(TestCase):
    """personas/rules.py: 타겟 설명·연령에 따라 수치가 달라지는지 (DB 없이)."""

    def values(self, context, ages=()):
        from personas import rules
        return {s["name"]: s["variables"] for s in rules.generate(context, list(ages), [5, 5, 5, 5])}

    def test_same_input_same_output_and_figma_names(self):
        a = self.values("반찬을 주문하는 사람")
        self.assertEqual(a, self.values("반찬을 주문하는 사람"))
        self.assertEqual(list(a), ["빠른 구매형", "꼼꼼한 비교형", "가격 민감형", "신중한 첫 방문형"])
        self.assertEqual(a["빠른 구매형"], {"patience": 22, "digital_literacy": 80, "price_sensitivity": 40, "trust_need": 30})

    def test_context_and_age_change_values(self):
        plain = self.values("반찬을 주문하는 사람")["가격 민감형"]
        coupon = self.values("쿠폰과 할인을 꼭 챙기는 대학생")["가격 민감형"]
        self.assertEqual(coupon["price_sensitivity"], min(95, plain["price_sensitivity"] + 12))   # 한 번만 반영
        senior = self.values("부모님 대신 주문", ["50plus"])["신중한 첫 방문형"]
        young = self.values("부모님 대신 주문", ["20s"])["신중한 첫 방문형"]
        self.assertLess(senior["digital_literacy"], young["digital_literacy"])

    def test_values_stay_between_5_and_95(self):
        for segment in self.values("처음 쓰는 시니어 어르신, 꼼꼼히 리뷰를 비교", ["50plus"]).values():
            self.assertTrue(all(5 <= v <= 95 for v in segment.values()))

    def test_basis_explains_rules(self):
        from personas import rules
        basis = rules.generate("퇴근 후 모바일로 처음 주문", ["30s"], [5])[0]["basis"]
        self.assertEqual(basis, ["연령: 30s", "처음 방문: '처음'", "시간 부족: '퇴근'", "모바일 사용: '모바일'"])

    @override_settings(EXITPLAIN_PERSONA_GENERATOR="personas.generators.fixture_generate")
    def test_fixture_still_available(self):
        from personas.generators import generate
        name, segments = generate("아무 설명", ["30s"], [5, 5, 5, 5])
        self.assertEqual((name, segments[0]["variables"]["patience"]), ("fixture-figma-p07", 22))