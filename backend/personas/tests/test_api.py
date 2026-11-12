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