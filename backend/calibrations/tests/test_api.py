"""K01 조정안·K02 적용·K03b 이력·되돌리기 API (CAL-05~14)."""
from unittest.mock import patch

import json

from django.test import TestCase
from rest_framework.test import APIClient

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
        self.assertEqual(self.proposal()["reason"], "NO_PREDICTION")
        snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        ReportSnapshot.objects.create(plan_snapshot=snapshot, run_ref="r1", revision=1, status="final",
                                      source="database", data=REPORT)
        self.assertEqual(self.proposal()["reason"], "NOT_CONNECTED")
        self.prepare(map_=False, report=False)
        self.assertEqual(self.proposal()["reason"], "NOT_MAPPED")

    def test_small_sample_shows_provisional_values_but_blocks_apply(self, _):
        self.prepare(base=6, cart_sessions=2)                  # 첫 단계 6세션 < 10
        body = self.proposal()
        self.assertEqual((body["available"], body["provisional"], body["basis"]["evidence"], body["basis"]["count"]),
                         (True, True, "measurement", 6))
        self.assertIn("임시값", body["message"])
        self.assertTrue(body["items"])
        res = self.client.post(f"{self.url}/calibrations",
                               {"expected_revision": 0, "keys": [body["items"][0]["key"]]}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "PROVISIONAL"))

    def test_gentle_strength_halves_the_change(self, _):
        self.prepare()
        res = self.client.get(f"{self.url}/calibration/proposal?strength=gentle")
        items = {i["key"]: i["proposed"] for i in res.json()["items"]}
        self.assertEqual(items, {"segment_1.threshold": 0.9, "*.weight.cost": 1.1})
        applied = self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "strength": "gentle",
                                                                "keys": ["segment_1.threshold"]}, format="json")
        self.assertEqual(applied.json()["params"], {"segments": {"segment_1": {"threshold_scale": 0.9}}})
        self.assertEqual(self.client.get(f"{self.url}/calibration/proposal?strength=x").status_code, 422)

    def test_no_gap_means_nothing_to_change(self, _):
        self.prepare(cart_sessions=120)                         # 60% — 예측 범위 안
        body = self.proposal()
        self.assertEqual((body["available"], body["reason"], body["items"]), (True, "NO_GAP", []))

    def test_owner_only_and_default_revision(self, _):
        self.assertEqual(self.client.get(f"{self.url}/calibration").json()["revision"], 0)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"{self.url}/calibration/proposal").status_code, 403)
        self.assertEqual(self.client.get(f"{self.url}/calibrations").status_code, 403)

    # K03 보정 전·후 비교 (CAL-15): 보정으로 돈 첫 사전 테스트가 생기면 같은 실측 기간으로 비교한다
    def test_waiting_then_ready_after_a_run_with_the_revision(self, _):
        from runs.models import Run
        self.prepare()
        self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "keys": ["segment_1.threshold"]},
                         format="json")
        waiting = self.client.get(f"{self.url}/calibration/compare").json()
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual([s["stage_id"] for s in waiting["stages"] if s["gap"]], ["/cart"])
        self.assertIsNone(self.client.get(f"{self.url}/calibrations").json()["items"][0]["error_after_pp"])

        snapshot = ReportSnapshot.objects.get().plan_snapshot
        run = Run.objects.create(project=self.project, created_by=self.user, sequence_no=2, plan_snapshot=snapshot,
                                 status=Run.Status.COMPLETED, config={"calibration_revision": 1})
        ReportSnapshot.objects.create(plan_snapshot=snapshot, run=run, run_ref=str(run.id), revision=1,
                                      status="final", source="database", data=AFTER_REPORT)
        ready = self.client.get(f"{self.url}/calibration/compare?revision=1").json()
        self.assertEqual((ready["status"], ready["after"]["run_sequence"]), ("ready", 2))
        cart = next(s for s in ready["stages"] if s["stage_id"] == "/cart")
        self.assertEqual(cart["change"], "차이 구간 해소")
        self.assertEqual((ready["before"]["gaps"], ready["after"]["gaps"]), (1, 0))
        self.assertIsNotNone(self.client.get(f"{self.url}/calibrations").json()["items"][0]["error_after_pp"])
        self.assertEqual(self.client.get(f"{self.url}/calibration/compare?revision=9").status_code, 404)


AFTER_REPORT = {"tasks": [{
    **REPORT["tasks"][0],
    "journey_funnel": {**REPORT["tasks"][0]["journey_funnel"],
                       "stages": [_stage("/", "첫 화면", 20), _stage("/products/:id", "상품 상세", 16),
                                  _stage("/cart", "장바구니", 8), _stage("/order/success", "주문 완료", 5, goal=True)]},
}]}


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ObservationTests(WizardMixin, TestCase):
    """K04 사람 관측 (CAL-16~18)."""

    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.id}/human-observations"

    def report(self):
        snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        data = {"tasks": [{**REPORT["tasks"][0], "success": 5, "dropout": 15,
                           "journey_funnel": {**REPORT["tasks"][0]["journey_funnel"], "stages": [
                               {**_stage("/", "첫 화면", 20), "dropouts_here": 0, "order": 0},
                               {**_stage("/cart", "장바구니", 12), "dropouts_here": 7, "order": 1},
                               {**_stage("/order/success", "주문 완료", 5, True), "dropouts_here": 0, "order": 2}]}}]}
        ReportSnapshot.objects.create(plan_snapshot=snapshot, run_ref="r1", revision=1, status="final",
                                      source="database", data=data)

    def post(self, **body):
        return self.client.post(self.url, {"task_id": "order", **body}, format="json")

    def test_save_compare_and_overwrite(self, _):
        self.report()
        res = self.post(participant_code="H-001", outcome="dropout", stage_id="/cart", cause="cost")
        self.assertEqual((res.status_code, res.json()["replaced"]), (201, False))
        self.post(participant_code="H-002", outcome="success")
        self.post(participant_code="H-003", outcome="dropout", stage_id="/", cause="unknown")
        body = self.client.get(self.url).json()
        self.assertEqual(body["tasks"][0]["predicted"],
                         {"outcome": "dropout", "stage_id": "/cart", "stage_name": "장바구니", "cause": "cost",
                          "cause_name": "비용 충격", "pain_top": []})
        rows = {r["participant_code"]: r["match"] for r in body["items"]}
        self.assertEqual(rows["H-001"], {"outcome": True, "stage": True, "cause": True, "pain_screen": None, "pain_cause": None})
        self.assertEqual(rows["H-002"], {"outcome": False, "stage": None, "cause": None, "pain_screen": None, "pain_cause": None})
        self.assertEqual(rows["H-003"], {"outcome": True, "stage": False, "cause": None, "pain_screen": None,
                                          "pain_cause": None})   # 원인 미상은 원인 분모에서 뺌
        self.assertEqual(body["summary"], {"outcome": {"matched": 2, "total": 3}, "stage": {"matched": 1, "total": 2},
                                           "cause": {"matched": 1, "total": 1},
                                           "pain_screen": {"matched": 0, "total": 0},
                                           "pain_cause": {"matched": 0, "total": 0}})
        again = self.post(participant_code="H-002", outcome="dropout", stage_id="/cart", cause="trust")
        self.assertEqual((again.status_code, again.json()["replaced"]), (200, True))
        self.assertEqual(len(again.json()["items"]), 3)

    def test_validation(self, _):
        self.assertEqual(self.post(participant_code="H-1", outcome="success").json()["error"]["code"], "NO_PREDICTION")
        self.report()
        for body, field in ((dict(participant_code="참여자 1", outcome="success"), "participant_code"),
                            (dict(participant_code="H-1", outcome="success", stage_id="/cart"), "outcome"),
                            (dict(participant_code="H-1", outcome="dropout", stage_id="/nope", cause="cost"), "stage_id"),
                            (dict(participant_code="H-1", outcome="dropout", stage_id="/cart", cause="mood"), "cause"),
                            (dict(participant_code="H-1", outcome="success", name="김"), "name")):
            with self.subTest(field=field):
                res = self.post(**body)
                self.assertEqual(res.status_code, 422, res.content)
                self.assertIn(field, res.json()["error"]["fields"])

    def test_import_recorder_file(self, _):
        self.report()
        record = {"schema": "human-record-1", "participant_id": "P07", "outcome": "give_up",
                  "events": [{"type": "view", "path": "/"}, {"type": "view", "path": "/Cart/"}]}
        res = self.client.post(self.url + "/import", {"task_id": "order", "record": record}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        row = res.json()["items"][0]
        self.assertEqual((row["participant_code"], row["outcome"], row["stage_id"], row["cause"], row["source"]),
                         ("P07", "dropout", "/cart", "unknown", "recorder"))
        bad = self.client.post(self.url + "/import", {"task_id": "order", "record": {"schema": "x"}}, format="json")
        self.assertEqual(bad.status_code, 422)

    def test_delete_and_owner_only(self, _):
        self.report()
        row = self.post(participant_code="H-1", outcome="success").json()["items"][0]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/human-observations/{row['id']}").status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.delete(f"/api/v1/human-observations/{row['id']}").status_code, 204)


SCREENS = {"order": {"/": {"sessions": 0, "causes": [], "shot": None},
                     "/cart": {"sessions": 9, "causes": ["cost", "navigation"], "shot": 101},
                     "/order/success": {"sessions": 2, "causes": ["trust"], "shot": None}}}


@patch("calibrations.observations._screens_by_task", return_value=SCREENS)
@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ParticipantLinkTests(WizardMixin, TestCase):
    """참여자 응답 링크 (PART-01~06): 로그인 없이 누르기만 해서 답하고, 사람 관측으로 쌓인다."""

    def setUp(self):
        super().setUp()
        self.base = f"/api/v1/projects/{self.project.id}"
        self.public = APIClient()

    def report(self):
        ObservationTests.report(self)

    def make(self, **body):
        res = self.client.post(f"{self.base}/participant-links", {"task_id": "order", **body}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def answer(self, token, **body):
        return self.public.post(f"/api/v1/participant-links/{token}/responses",
                                {"client_id": "browser-0001", **body}, format="json")

    def test_public_page_shows_only_task_screens_and_choices(self, *_):
        self.report()
        link = self.make(purpose="evaluation", max_responses=2)
        self.assertEqual((link["path"][:3], link["state"], link["task_name"]), ("/r/", "open", "첫 주문"))
        page = self.public.get(f"/api/v1/participant-links/{link['token']}")
        self.assertEqual(page.status_code, 200, page.content)
        body = page.json()
        self.assertEqual([s["id"] for s in body["screens"]], ["/", "/cart", "/order/success"])
        self.assertEqual(body["screens"][1]["screenshot_url"],
                         f"/api/v1/participant-links/{link['token']}/screens/101")
        self.assertEqual(len(body["reasons"]), 8)
        self.assertNotIn("pain_top", json.dumps(body))                        # 예측 수치는 보이지 않는다
        # 다른 캡처 번호는 열 수 없다
        self.assertEqual(self.public.get(f"/api/v1/participant-links/{link['token']}/screens/999").status_code, 404)

    def test_answers_become_observations_and_compare_with_model_friction(self, *_):
        self.report()
        link = self.make(purpose="evaluation", max_responses=2)
        first = self.answer(link["token"], outcome="success", pain_stage_id="/cart", pain_cause="cost", discomfort=3)
        self.assertEqual((first.status_code, first.json()["participant_code"]), (201, "R-001"))
        again = self.answer(link["token"], outcome="success")
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "ALREADY_SUBMITTED"))
        second = self.public.post(f"/api/v1/participant-links/{link['token']}/responses",
                                  {"client_id": "browser-0002", "outcome": "dropout", "pain_stage_id": "/",
                                   "pain_cause": "trust", "discomfort": 5}, format="json")
        self.assertEqual(second.status_code, 201, second.content)
        full = self.answer(link["token"], outcome="success")
        self.assertEqual((full.status_code, full.json()["error"]["code"]), (410, "LINK_FULL"))

        body = self.client.get(f"{self.base}/human-observations").json()
        rows = {r["participant_code"]: r for r in body["items"]}
        self.assertEqual((rows["R-001"]["source"], rows["R-001"]["purpose"], rows["R-001"]["discomfort"]),
                         ("participant_link", "evaluation", 3))
        self.assertEqual((rows["R-001"]["match"]["pain_screen"], rows["R-001"]["match"]["pain_cause"]), (True, True))
        self.assertEqual((rows["R-002"]["stage_id"], rows["R-002"]["cause"]), ("/", "trust"))   # 그만둔 화면 = 고른 화면
        self.assertEqual((rows["R-002"]["match"]["pain_screen"], rows["R-002"]["match"]["pain_cause"]), (False, False))
        self.assertEqual(body["summary"]["pain_screen"], {"matched": 1, "total": 2})
        self.assertEqual(body["tasks"][0]["predicted"]["pain_top"], ["/cart", "/order/success"])
        links = self.client.get(f"{self.base}/participant-links").json()["items"]
        self.assertEqual((links[0]["responses"], links[0]["state"]), (2, "full"))

    def test_validation_disable_and_owner_only(self, *_):
        self.assertEqual(self.client.post(f"{self.base}/participant-links", {"task_id": "order"},
                                          format="json").status_code, 409)       # 사전 테스트 없음
        self.report()
        link = self.make()
        for body in ({"outcome": "dropout"}, {"outcome": "success", "discomfort": 9},
                     {"outcome": "success", "pain_cause": "mood"}, {"outcome": "success", "note": "글"}):
            with self.subTest(body=body):
                self.assertEqual(self.answer(link["token"], **body).status_code, 422)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f"/api/v1/participant-links/{link['id']}/disable").status_code, 403)
        self.client.force_login(self.user)
        closed = self.client.post(f"/api/v1/participant-links/{link['id']}/disable").json()
        self.assertEqual(closed["state"], "disabled")
        self.assertEqual(self.public.get(f"/api/v1/participant-links/{link['token']}").status_code, 410)
        self.assertEqual(self.public.get("/api/v1/participant-links/nope").status_code, 404)

@patch("calibrations.observations._screens_by_task", return_value=SCREENS)
@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class HumanProposalTests(WizardMixin, TestCase):
    """CAL-19: 실측이 없어도 보정용 사람 관측(가장 불편했던 화면)으로 조정안을 만든다. 10명 미만은 임시값."""

    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.id}"

    def observe(self, n, screen, cause, purpose="calibration", start=0):
        from calibrations.models import HumanObservation
        for i in range(start, start + n):
            HumanObservation.objects.create(project=self.project, created_by=self.user, participant_code=f"R-{i:03d}",
                                            task_id="order", outcome="success", pain_stage_id=screen,
                                            pain_cause=cause, purpose=purpose)

    def test_people_pick_a_screen_the_model_missed(self, *_):
        ObservationTests.report(self)
        self.observe(4, "/", "value")                          # 첫 화면: 모델 마찰 없음
        self.observe(1, "/cart", "cost", start=4)
        body = self.client.get(f"{self.url}/calibration/proposal").json()
        self.assertEqual((body["provisional"], body["basis"]["evidence"], body["basis"]["count"]),
                         (True, "observations", 5))
        items = {i["key"]: i for i in body["items"]}
        self.assertEqual(items["*.weight.value"]["proposed"], 1.1)          # 사람 근거는 작게 (k=0.1)
        self.assertIn("5명 중 4명", items["*.weight.value"]["reason"])
        # 모델 마찰 상위 화면 /order/success 는 아무도 고르지 않았다 → 그 화면 원인(trust)을 낮춘다
        self.assertEqual(items["*.weight.trust"]["proposed"], 0.9)
        self.assertNotIn("*.weight.cost", items)                           # /cart 는 1명(20%)이 골라 그대로
        self.observe(5, "/", "value", start=5)
        self.observe(5, "/", "value", purpose="evaluation", start=10)      # 평가용은 쓰지 않는다
        real = self.client.get(f"{self.url}/calibration/proposal").json()
        self.assertEqual((real["provisional"], real["basis"]["count"]), (False, 10))
        keys = [i["key"] for i in real["items"]]
        res = self.client.post(f"{self.url}/calibrations", {"expected_revision": 0, "keys": keys}, format="json")
        self.assertEqual(res.status_code, 201, res.content)

    def test_no_evidence_at_all(self, *_):
        ObservationTests.report(self)
        body = self.client.get(f"{self.url}/calibration/proposal").json()
        self.assertEqual((body["available"], body["reason"]), (False, "NOT_CONNECTED"))
        self.assertIn("참여자 응답 링크", body["message"])
