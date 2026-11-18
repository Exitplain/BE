"""K01 조정안·K02 적용·K03b 이력·되돌리기 API (CAL-05~14)."""
from unittest.mock import patch

from django.test import TestCase

from calibrations.models import CalibrationRevision
from common.testing import WizardMixin, fake_discovery
from measurements.models import MeasureHit, MeasureSite
from plans.models import PlanSnapshot
from reports.models import ReportSnapshot


def _stage(path, name, reached, goal=False):
    return {"id": path, "path": path, "name": name, "reached": reached, "is_goal": goal}


REPORT = {"tasks": [{
    "task_id": "order", "name": "첫 주문",
    "causes": [{"key": "cost", "name": "비용 충격", "dropouts": 3}],
    "journey_funnel": {
        "valid_sessions": 20,
        "stages": [_stage("/", "첫 화면", 20), _stage("/products/:id", "상품 상세", 16), _stage("/cart", "장바구니", 12),
                   _stage("/order/success", "주문 완료", 5, goal=True)],
        "heatmap": {"stages": ["/", "/products/:id", "/cart", "/order/success"],
                    "rows": [{"segment_id": "segment_1", "name": "빠른 구매형", "dropouts": [0, 2, 1, 0]},
                             {"segment_id": "segment_2", "name": "꼼꼼한 비교형", "dropouts": [0, 2, 3, 0]}]}},
}]}


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class CalibrationApiTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.project.entry_url = "https://shop.example.com/"
        self.project.save()
        self.url = f"/api/v1/projects/{self.project.id}"

    def prepare(self, cart_sessions=60, base=200, map_=True, report=True):
        if report:
            snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
            ReportSnapshot.objects.create(plan_snapshot=snapshot, run_ref="r1", revision=1, status="final",
                                          source="database", data=REPORT)
        site = MeasureSite.objects.create(project=self.project)
        hits = []
        for path, n in (("/", base), ("/product/1", 160 * base // 200), ("/cart", cart_sessions),
                        ("/order/success", 40 * base // 200)):
            hits += [MeasureHit(site=site, sid=f"s{i:05d}abc", path=path) for i in range(n)]
        MeasureHit.objects.bulk_create(hits)
        if map_:
            res = self.client.put(f"{self.url}/measurement/mapping", {"mapping": {}}, format="json")
            self.assertEqual(res.status_code, 200, res.content)
        return site

    def proposal(self):
        res = self.client.get(f"{self.url}/calibration/proposal")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_proposal_lowers_patience_of_the_least_dropping_segment_and_raises_top_cause(self, _):
        self.prepare()
        body = self.proposal()
        self.assertTrue(body["available"])
        self.assertEqual([g["stage_id"] for g in body["basis"]["gaps"]], ["/cart"])
        items = {i["key"]: i for i in body["items"]}
        self.assertEqual(set(items), {"*.weight.cost", "segment_1.threshold"})
        self.assertEqual((items["segment_1.threshold"]["current"], items["segment_1.threshold"]["proposed"]), (1.0, 0.8))
        self.assertEqual((items["*.weight.cost"]["proposed"], items["*.weight.cost"]["label"]),
                         (1.2, "마찰 가중치 · 비용 충격"))
        self.assertIn("장바구니", items["segment_1.threshold"]["reason"])
        self.assertIsNone(body["after_error"])                                  # CAL-10: 다음 실행을 기다린다
        self.assertEqual(self.proposal(), body)                                 # 같은 입력 → 같은 결과

    def test_apply_creates_revision_history_and_restore(self, _):
        self.prepare()
        res = self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "keys": ["segment_1.threshold"]},
                               format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((res.json()["revision"], res.json()["params"]),
                         (1, {"segments": {"segment_1": {"threshold_scale": 0.8}}}))
        stale = self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "keys": ["*.weight.cost"]},
                                 format="json")
        self.assertEqual((stale.status_code, stale.json()["error"]["code"]), (409, "REVISION_CONFLICT"))

        # 보정 뒤 새 사전 테스트 전에는 같은 차이로 또 조정하지 않는다 (이중 적용 방지)
        waiting = self.proposal()
        self.assertEqual((waiting["available"], waiting["reason"], waiting["items"]), (False, "WAITING_NEXT_RUN", []))
        again = self.client.post(f"{self.url}/calibrations", {"expected_revision": 1, "keys": ["*.weight.cost"]},
                                 format="json")
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (422, "PROPOSAL_CHANGED"))

        history = self.client.get(f"{self.url}/calibrations").json()["items"]
        self.assertEqual([h["revision"] for h in history], [1])
        self.assertIn("빠른 구매형 인내심 임계치 1.00→0.80", history[0]["changes_summary"])
        self.assertEqual(history[0]["base_comparison"], "사전 테스트 vs 실측 최근 7일")
        self.assertIsNone(history[0]["error_after_pp"])

        first = CalibrationRevision.objects.get(project=self.project, revision=1)
        back = self.client.post(f"/api/v1/calibrations/{first.pk}/restore", {"expected_revision": 1}, format="json")
        self.assertEqual((back.status_code, back.json()["revision"], back.json()["restored_from"]), (201, 2, 1))
        self.assertEqual(self.client.get(f"{self.url}/calibration").json()["params"], first.params)
        reset = self.client.post(f"{self.url}/calibrations/reset", {"expected_revision": 2}, format="json")
        self.assertEqual((reset.json()["revision"], reset.json()["params"]), (3, {"segments": {}}))
        self.assertEqual(CalibrationRevision.objects.filter(project=self.project).count(), 3)   # 지우지 않는다
        # 기본 모형으로 되돌리면 기준 실행(기본 모형)과 같아져 조정안이 다시 열린다
        self.assertTrue(self.proposal()["available"])

    def test_apply_rejects_unknown_or_changed_keys(self, _):
        self.prepare()
        res = self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "keys": ["segment_9.threshold"]},
                               format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "PROPOSAL_CHANGED"))
        self.assertEqual(self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "keys": []},
                                          format="json").status_code, 422)
        self.assertEqual(self.client.post(f"{self.url}/calibrations", {"keys": ["x"]}, format="json").status_code, 422)

    def test_reasons_when_no_proposal_is_possible(self, _):
        self.assertEqual(self.proposal()["reason"], "NOT_CONNECTED")
        site = self.prepare(map_=False, report=False)
        self.assertEqual(self.proposal()["reason"], "NOT_MAPPED")
        site.delete()
        self.prepare(base=40, cart_sessions=12)
        self.assertEqual(self.proposal()["reason"], "INSUFFICIENT_SAMPLE")

    def test_no_gap_means_nothing_to_change(self, _):
        self.prepare(cart_sessions=120)                         # 60% — 예측 범위 안
        body = self.proposal()
        self.assertEqual((body["available"], body["reason"], body["items"]), (True, "NO_GAP", []))

    def test_owner_only_and_default_revision(self, _):
        self.assertEqual(self.client.get(f"{self.url}/calibration").json()["revision"], 0)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"{self.url}/calibration/proposal").status_code, 403)
        self.assertEqual(self.client.get(f"{self.url}/calibrations").status_code, 403)
