from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from agent.shop_tasks import DEFAULT_TASK_IDS
from common.testing import WizardMixin, fake_discovery
from discoveries.models import Discovery
from personas.models import PersonaSnapshot
from plans.models import PlanSnapshot


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class PlanCreateTests(WizardMixin, TestCase):
    def test_create_defaults(self, _):
        plan = self.create_plan()
        self.assertEqual(plan["revision"], 1)
        self.assertEqual(plan["task_ids"], list(DEFAULT_TASK_IDS))
        self.assertEqual([s["count"] for s in plan["segments"]], [5, 5, 5, 5])
        self.assertEqual(plan["limits"]["concurrency"], 3)
        self.assertEqual(plan["task_funnels"][0]["stages"][0], {"id": "stage_landing", "name": "랜딩", "order": 0})

    def test_etag_header(self, _):
        plan = self.create_plan()
        self.assertEqual(self.client.get(f"/api/v1/plans/{plan['id']}")["ETag"], '"1"')

    def test_expired_discovery_rejected(self, _):
        d = self.discover()
        Discovery.objects.filter(pk=d["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        res = self.client.post(f"/api/v1/projects/{self.project.id}/plans", {"discovery_id": d["id"]}, format="json")
        self.assertEqual(res.json()["error"]["code"], "DISCOVERY_EXPIRED")


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class PlanStep2Tests(WizardMixin, TestCase):
    def test_tasks_audience_saved(self, _):
        plan = self.ready_plan()
        self.assertEqual(plan["revision"], 2)
        self.assertEqual([f["task_id"] for f in plan["task_funnels"]], ["order", "coupon"])
        self.assertEqual(plan["custom_tasks"][0], {"id": "custom_1", "text": "배송지 바꾸기", "state": "draft"})

    def test_if_match_required_and_conflict(self, _):
        plan = self.create_plan()
        res = self.client.patch(f"/api/v1/plans/{plan['id']}", self.STEP2, format="json")
        self.assertEqual(res.status_code, 428)
        self.patch(plan, self.STEP2)
        res = self.patch(plan, self.STEP2)  # revision 1 재사용
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "REVISION_CONFLICT"))

    def test_blank_audience_context(self, _):
        res = self.patch(self.create_plan(), {"audience": {"context": "  ", "ages": []}})
        self.assertIn("audience.context", res.json()["error"]["fields"])

    def test_age_rules(self, _):
        plan = self.create_plan()
        self.assertEqual(self.patch(plan, {"audience": {"context": "x", "ages": ["any", "20s"]}}).status_code, 422)
        self.assertEqual(self.patch(plan, {"audience": {"context": "x", "ages": ["60plus"]}}).status_code, 422)

    def test_unknown_task_and_field(self, _):
        plan = self.create_plan()
        self.assertEqual(self.patch(plan, {"task_ids": ["refund"]}).status_code, 422)
        self.assertIn("foo", self.patch(plan, {"foo": 1}).json()["error"]["fields"])

    def test_custom_task_limit(self, _):
        res = self.patch(self.create_plan(), {"custom_tasks": [f"과업{i}" for i in range(6)]})
        self.assertEqual(res.status_code, 422)


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class PlanStep3Tests(WizardMixin, TestCase):
    def test_rename_and_reorder_funnel(self, _):
        plan = self.ready_plan()
        funnels = deepcopy(plan["task_funnels"])
        stages = funnels[0]["stages"]
        stages[0]["name"] = "첫 화면"
        stages[1]["order"], stages[2]["order"] = 2, 1
        res = self.patch(plan, {"task_funnels": funnels})
        self.assertEqual(res.status_code, 200, res.content)
        saved = res.json()["task_funnels"][0]["stages"]
        self.assertEqual([s["id"] for s in saved[:3]], ["stage_landing", "stage_cart", "stage_product"])
        self.assertEqual(saved[0]["name"], "첫 화면")

    def test_cannot_add_stage(self, _):
        plan = self.ready_plan()
        funnels = deepcopy(plan["task_funnels"])
        funnels[0]["stages"].append({"id": "stage_extra", "name": "추가", "order": 6})
        self.assertEqual(self.patch(plan, {"task_funnels": funnels}).status_code, 422)

    def test_stage_name_errors(self, _):  # Figma P05g
        plan = self.ready_plan()
        funnels = deepcopy(plan["task_funnels"])
        funnels[0]["stages"][0]["name"] = ""
        self.assertEqual(self.patch(plan, {"task_funnels": funnels}).status_code, 422)
        funnels[0]["stages"][0]["name"] = funnels[0]["stages"][1]["name"]
        self.assertIn("task_funnels.0", self.patch(plan, {"task_funnels": funnels}).json()["error"]["fields"])

    def test_funnels_must_cover_selected_tasks(self, _):
        plan = self.ready_plan()
        self.assertEqual(self.patch(plan, {"task_funnels": plan["task_funnels"][:1]}).status_code, 422)

    def test_segment_total(self, _):  # Figma P05e
        plan = self.ready_plan()
        segs = [{"id": f"segment_{i}", "count": c} for i, c in enumerate([5, 5, 5, 4], 1)]
        res = self.patch(plan, {"segments": segs})
        self.assertEqual(res.json()["error"]["message"], "합계가 20명이 되도록 맞춰주세요 · 지금 19명")
        segs[3]["count"], segs[0]["count"] = 4, 6
        self.assertEqual(self.patch(plan, {"segments": segs}).status_code, 200)

    def test_limits_and_frustration(self, _):
        plan = self.ready_plan()
        self.assertEqual(self.patch(plan, {"limits": {"concurrency": 5}}).status_code, 422)
        self.assertEqual(self.patch(plan, {"limits": {"seed_count": 4}}).status_code, 422)
        res = self.patch(plan, {"limits": {"seed_count": 2, "cost_cap": 3.5}, "frustration_enabled": False})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["limits"]["seed_count"], 2)
        self.assertEqual(res.json()["limits"]["max_steps"], 40)  # 안 보낸 값은 유지
        self.assertFalse(res.json()["frustration_enabled"])

    def test_reestimate(self, _):
        plan = self.ready_plan()
        funnels = deepcopy(plan["task_funnels"])
        funnels[0]["stages"][0]["name"] = "바뀐 이름"
        plan = self.patch(plan, {"task_funnels": funnels}).json()
        res = self.client.post(f"/api/v1/plans/{plan['id']}/task-funnels/order/reestimate",
                               HTTP_IF_MATCH=f'"{plan["revision"]}"')
        self.assertEqual(res.json()["task_funnels"][0]["stages"][0]["name"], "랜딩")


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ConfirmationTests(WizardMixin, TestCase):
    def test_review_blocking_before_audience(self, _):
        plan = self.create_plan()
        self.assertEqual(plan["review"]["blocking"][0]["code"], "NO_AUDIENCE")
        res = self.client.post(f"/api/v1/plans/{plan['id']}/confirmations", {"revision": 1}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "NO_AUDIENCE"))

    def test_review_summary(self, _):
        review = self.ready_plan()["review"]
        self.assertEqual(review["blocking"], [])
        self.assertEqual(review["total_sessions"], 20 * 2 * 3)

    def test_confirm_creates_immutable_snapshot(self, _):
        snap = self.confirm()
        data = snap["data"]
        self.assertEqual(snap["plan_revision"], 2)
        self.assertEqual(snap["persona_generator"], "fixture-figma-p07")
        self.assertEqual(len(data["personas"]), 20)
        self.assertEqual([s["representative"] for s in data["segments"]], ["P-001", "P-006", "P-011", "P-016"])
        self.assertEqual(data["tasks"][0]["funnel"][0]["id"], "stage_landing")
        self.assertEqual(data["total_sessions"], 120)
        self.assertEqual(PersonaSnapshot.objects.count(), 20)

        # 같은 revision 재확정은 409, 계획을 고친 뒤 확정하면 새 스냅샷
        res = self.client.post(f"/api/v1/plans/{snap['plan_id']}/confirmations", {"revision": 2}, format="json")
        self.assertEqual(res.json()["error"]["code"], "ALREADY_CONFIRMED")
        plan = self.client.get(f"/api/v1/plans/{snap['plan_id']}").json()
        plan = self.patch(plan, {"frustration_enabled": False}).json()
        snap2 = self.confirm(plan)
        self.assertNotEqual(snap2["plan_revision_id"], snap["plan_revision_id"])
        self.assertTrue(PlanSnapshot.objects.get(pk=snap["plan_revision_id"]).data["frustration_enabled"])

    def test_confirm_after_discovery_expired(self, _):
        plan = self.ready_plan()
        Discovery.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        res = self.client.post(f"/api/v1/plans/{plan['id']}/confirmations", {"revision": plan["revision"]},
                               format="json")
        self.assertEqual(res.json()["error"]["code"], "DISCOVERY_EXPIRED")

    def test_snapshot_detail_and_owner_check(self, _):
        snap = self.confirm()
        self.assertEqual(self.client.get(f"/api/v1/plan-revisions/{snap['plan_revision_id']}").status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/v1/plan-revisions/{snap['plan_revision_id']}").status_code, 403)