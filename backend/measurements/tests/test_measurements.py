"""실측 수집: 키 발급·설치 확인·수집 받기(출처 검사·개인정보 경로 가리기·제한)·경로별 집계."""
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from common.testing import WizardMixin, fake_discovery
from measurements import services
from measurements.models import MeasureHit, MeasureSite
from plans.models import PlanSnapshot
from reports.models import ReportSnapshot

ORIGIN = "https://shop.example.com"


class MeasurementTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.project.entry_url = ORIGIN + "/"
        self.project.save()
        self.url = f"/api/v1/projects/{self.project.id}/measurement"
        self.beacon = Client()  # 로그인·CSRF 없는 고객 사이트 방문자

    def issue(self):
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def send(self, key, path="/", sid="abcd1234efgh", origin=ORIGIN, **extra):
        body = json.dumps({"key": key, "sid": sid, "path": path, **extra})
        return self.beacon.post("/api/v1/collect", body, content_type="text/plain", HTTP_ORIGIN=origin)

    def test_issue_key_then_installed_after_first_hit(self):
        before = self.client.get(self.url).json()
        self.assertEqual((before["available"], before["key"], before["installed"]), (True, None, False))
        status = self.issue()
        self.assertRegex(status["key"], r"^ep_[0-9a-f]{12}$")
        self.assertIn(f'data-project="{status["key"]}"', status["snippet"])
        self.assertTrue(status["snippet"].startswith('<script src="http://testserver/api/v1/e.js"'))
        self.assertFalse(status["installed"])
        self.assertEqual(self.issue()["key"], status["key"])  # 다시 눌러도 같은 키

        self.assertEqual(self.send(status["key"]).status_code, 204)
        after = self.client.get(self.url).json()
        self.assertEqual((after["installed"], after["last_origin"], after["sessions_24h"]), (True, ORIGIN, 1))

    @override_settings(EXITPLAIN_PUBLIC_URL="https://app.exitplain.test/")
    def test_snippet_uses_public_https_url(self):
        self.assertTrue(self.issue()["snippet"].startswith('<script src="https://app.exitplain.test/api/v1/e.js"'))

    def test_wrong_origin_bad_body_and_unknown_key_are_dropped_quietly(self):
        key = self.issue()["key"]
        for res in (self.send(key, origin="https://evil.example"),
                    self.send(key, origin=""),
                    self.send("ep_000000000000"),
                    self.send(key, path="no-slash"),
                    self.send(key, sid="X!"),
                    self.send(key, step="bad step"),
                    self.beacon.post("/api/v1/collect", "not json", content_type="text/plain", HTTP_ORIGIN=ORIGIN)):
            self.assertEqual(res.status_code, 204)
        self.assertEqual(MeasureHit.objects.count(), 0)

    def test_private_looking_path_segments_are_masked_and_query_dropped(self):
        key = self.issue()["key"]
        self.send(key, path="/users/kim@example.com/orders/123456?token=abc#x")
        self.send(key, path="/item/3f2a9c1e-1111-2222-3333-444455556666/")
        self.assertEqual(sorted(MeasureHit.objects.values_list("path", flat=True)),
                         ["/item/:id", "/users/:id/orders/:id"])

    def test_steps_counts_distinct_sessions_per_path_and_named_step(self):
        key = self.issue()["key"]
        for sid in ("aaaa1111", "bbbb2222", "cccc3333"):
            self.send(key, "/", sid=sid)
        self.send(key, "/", sid="aaaa1111")  # 같은 세션이 다시 와도 1
        self.send(key, "/cart", sid="aaaa1111")
        self.send(key, "/cart", sid="aaaa1111", step="coupon")
        res = self.client.get(self.url + "/steps?days=7")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["total_sessions"], 3)
        rows = {r["step_id"]: (r["sessions"], r["events"]) for r in body["items"]}
        self.assertEqual(rows, {"/": (3, 4), "/cart": (1, 1), "coupon": (1, 1)})
        self.assertEqual(self.client.get(self.url + "/steps?days=99").status_code, 422)

    @override_settings(EXITPLAIN_MEASURE_PER_MINUTE=2)
    def test_per_minute_limit(self):
        key = self.issue()["key"]
        for i in range(4):
            self.send(key, f"/p{i}")
        self.assertEqual(MeasureHit.objects.count(), 2)

    def test_local_only_project_is_not_measurable(self):
        self.project.entry_url = "http://s-abc.localhost:8090/"
        self.project.save()
        status = self.client.get(self.url).json()
        self.assertEqual((status["available"], status["reason"]), (False, "NOT_DEPLOYED"))
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 409)

    def test_owner_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 403)

    def test_script_is_public_and_skips_automation(self):
        res = self.beacon.get("/api/v1/e.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/javascript", res["Content-Type"])
        text = res.content.decode()
        self.assertIn("navigator.webdriver", text)
        self.assertIn("/api/v1/collect", text)

    def test_normalize_path(self):
        self.assertEqual(services.normalize_path("/"), "/")
        self.assertEqual(services.normalize_path("/cart/"), "/cart")
        self.assertEqual(services.normalize_path("/a/1234/b"), "/a/:id/b")
        self.assertEqual(services.normalize_path("/a/12/b"), "/a/:id/b")
        self.assertEqual(services.normalize_path("/Cart/"), "/cart")
        self.assertIsNone(services.normalize_path("https://x/"))
        self.assertTrue(MeasureSite._meta.get_field("key").unique)


def _stage(path, name, goal=False):
    return {"id": path, "path": path, "name": name, "is_goal": goal}


REPORT_DATA = {"tasks": [
    {"task_id": "order", "name": "첫 주문", "journey_funnel": {"stages": [
        _stage("/", "첫 화면"), _stage("/products/:id", "상품 상세"), _stage("/cart", "장바구니"),
        _stage("/order/success", "주문 완료", goal=True)]}},
    {"task_id": "coupon", "name": "쿠폰 주문", "journey_funnel": {"stages": [
        _stage("/", "첫 화면"), _stage("/coupon", "쿠폰")]}},
]}


class MeasureHelpers(WizardMixin):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.project.entry_url = ORIGIN + "/"
        self.project.save()
        self.url = f"/api/v1/projects/{self.project.id}/measurement"
        self.beacon = Client()

    def issue(self):
        return self.client.post(self.url, {}, format="json").json()["key"]

    def send(self, key, path, sid="abcd1234efgh", **extra):
        body = json.dumps({"key": key, "sid": sid, "path": path, **extra})
        self.beacon.post("/api/v1/collect", body, content_type="text/plain", HTTP_ORIGIN=ORIGIN)

    REPORT = REPORT_DATA

    def report(self):
        snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        ReportSnapshot.objects.create(plan_snapshot=snapshot, run_ref="r1", revision=1, status="final",
                                      source="database", data=self.REPORT)


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class MappingTests(MeasureHelpers, TestCase):

    def test_collected_paths_use_the_same_stage_ids_as_the_agent_funnel(self, _):
        key = self.issue()
        self.send(key, "/Product/12")
        self.send(key, "/product/34/")
        self.assertEqual(set(MeasureHit.objects.values_list("path", flat=True)), {"/product/:id"})

    def test_without_a_prediction_report_there_is_nothing_to_map_to(self, _):
        key = self.issue()
        self.send(key, "/")
        body = self.client.get(self.url + "/mapping").json()
        self.assertEqual((body["reason"], body["stages"], body["rows"][0]["stage_id"]), ("NO_PREDICTION", [], None))

    def test_suggests_exact_and_similar_then_saves(self, _):
        self.report()
        key = self.issue()
        for path in ("/", "/product/1", "/cart", "/order/success", "/faq"):
            self.send(key, path)
        self.send(key, "/cart", step="coupon_applied")
        body = self.client.get(self.url + "/mapping").json()
        self.assertEqual([s["id"] for s in body["stages"]], ["/", "/products/:id", "/cart", "/order/success", "/coupon"])
        self.assertEqual(body["stages"][0]["tasks"], ["첫 주문", "쿠폰 주문"])
        rows = {r["step_id"]: (r["stage_id"], r["suggestion"]) for r in body["rows"]}
        self.assertEqual(rows, {"/": ("/", "exact"), "/product/:id": ("/products/:id", "similar"),
                                "/cart": ("/cart", "exact"), "/order/success": ("/order/success", "exact"),
                                "/faq": (None, None), "coupon_applied": (None, None)})
        self.assertEqual(body["mapped"], 4)

        res = self.client.put(self.url + "/mapping", {"mapping": {"/": "/", "coupon_applied": "/coupon", "/faq": None}},
                              format="json")
        self.assertEqual(res.status_code, 200, res.content)
        rows = {r["step_id"]: (r["stage_id"], r["saved"]) for r in res.json()["rows"]}
        self.assertEqual(rows["coupon_applied"], ("/coupon", True))
        self.assertEqual(rows["/faq"], (None, True))
        self.assertEqual(rows["/cart"], ("/cart", False))      # 저장 안 한 행은 계속 추천값
        self.assertIsNotNone(res.json()["saved_at"])
        self.assertIsNotNone(self.client.get(self.url).json()["mapping_saved_at"])

    def test_rejects_unknown_stage_and_extra_fields(self, _):
        self.report()
        self.issue()
        res = self.client.put(self.url + "/mapping", {"mapping": {"/": "/nope"}}, format="json")
        self.assertEqual(res.status_code, 422)
        self.assertIn("mapping./", res.json()["error"]["fields"])
        self.assertEqual(self.client.put(self.url + "/mapping", {"mapping": {}, "x": 1}, format="json").status_code, 422)
        self.assertEqual(self.client.put(self.url + "/mapping", {"mapping": []}, format="json").status_code, 422)

    def test_mapping_needs_issued_key_and_owner(self, _):
        self.assertEqual(self.client.get(self.url + "/mapping").status_code, 404)
        self.issue()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url + "/mapping").status_code, 403)


def _reached(path, name, reached, goal=False):
    return {**_stage(path, name, goal), "reached": reached}


FUNNEL_DATA = {"tasks": [
    {"task_id": "order", "name": "첫 주문", "journey_funnel": {"valid_sessions": 20, "stages": [
        _reached("/", "첫 화면", 20), _reached("/products/:id", "상품 상세", 16), _reached("/cart", "장바구니", 12),
        _reached("/order/success", "주문 완료", 5, goal=True)]}},
]}


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class FunnelTests(MeasureHelpers, TestCase):
    REPORT = FUNNEL_DATA

    def visit(self, key, path, n, start=0, width=390):
        for i in range(start, start + n):
            self.send(key, path, sid=f"visitor{i:04d}", w=width)

    def test_actual_funnel_and_prediction_band(self, _):
        self.report()
        key = self.issue()
        self.visit(key, "/", 10)
        self.visit(key, "/product/7", 8)
        self.visit(key, "/cart", 3, width=1280)
        self.visit(key, "/order/success", 2)
        res = self.client.get(self.url + "/funnel?days=7")
        self.assertEqual(res.status_code, 200, res.content)
        task = res.json()["tasks"][0]
        self.assertEqual((task["base_sessions"], task["collecting"], task["mapped_stages"]), (10, True, 4))
        rows = {r["id"]: r for r in task["stages"]}
        self.assertEqual([rows[k]["actual"]["rate"] for k in ("/", "/products/:id", "/cart", "/order/success")],
                         [100.0, 80.0, 30.0, 20.0])
        low, high = services.wilson(12, 20)
        self.assertEqual((rows["/cart"]["predicted"]["low"], rows["/cart"]["predicted"]["high"]),
                         (round(low, 1), round(high, 1)))
        self.assertEqual(task["gaps"], ["/cart"])                       # 30% 는 예측 범위(약 39~78%) 밖
        self.assertAlmostEqual(rows["/cart"]["error"], round(30 - (low + high) / 2, 1), places=1)
        self.assertEqual(task["max_drop"]["actual"]["label"], "상품 상세→장바구니")
        self.assertIsNotNone(task["mean_error"])
        self.assertEqual((rows["/"]["error"], rows["/"]["compared"], task["compared_stages"]), (None, False, 3))

        mobile = self.client.get(self.url + "/funnel?days=7&device=mobile").json()["tasks"][0]
        self.assertIsNone({r["id"]: r for r in mobile["stages"]}["/cart"]["actual"])   # 장바구니는 데스크톱만 왔다

    def test_unmapped_stage_has_no_actual_and_is_left_out(self, _):
        self.report()
        key = self.issue()
        self.visit(key, "/", 5)
        self.visit(key, "/cart", 2)
        self.client.put(self.url + "/mapping", {"mapping": {"/": "/", "/cart": None}}, format="json")
        rows = {r["id"]: r for r in self.client.get(self.url + "/funnel").json()["tasks"][0]["stages"]}
        self.assertEqual(rows["/"]["actual"]["sessions"], 5)
        self.assertIsNone(rows["/cart"]["actual"])
        self.assertFalse(rows["/cart"]["gap"])

    def test_no_report_and_bad_params(self, _):
        self.issue()
        body = self.client.get(self.url + "/funnel").json()
        self.assertEqual((body["reason"], body["tasks"]), ("NO_PREDICTION", []))
        self.assertEqual(self.client.get(self.url + "/funnel?days=5").status_code, 422)
        self.assertEqual(self.client.get(self.url + "/funnel?device=tv").status_code, 422)

    def test_device_comes_from_width_only(self, _):
        self.assertEqual((services.device_of(390), services.device_of(1280), services.device_of("x"),
                          services.device_of(True)), ("mobile", "desktop", "", ""))
