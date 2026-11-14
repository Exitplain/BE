"""세션 목록·상세 (11/14), 진단 기록 목록·대시보드 (11/15)."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import build_run_inputs
from reports import services
from reports.services import create_report_from_run_dir
from reports.tests.sample_runs import write_attempt

DROP = ("simulated_dropout", ["/", "/product", "/cart", "/signup"], "input_burden")
TECH = ("agent_failure", ["/", "/product"])


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class SessionApiTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.override = override_settings(EXITPLAIN_RUN_OUTPUT_DIR=str(self.root))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()
        super().tearDown()

    def make_report(self, run_ref="run1", outcomes=None):
        plan = self.patch(self.create_plan(), {**self.STEP2, "task_ids": ["order"]}).json()
        plan = self.patch(plan, {"limits": {"seed_count": 1}}).json()
        snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        for item in build_run_inputs(snap, run_id=run_ref):
            write_attempt(self.root, item, outcomes or {"P-001": DROP, "P-002": TECH})
        return create_report_from_run_dir(snap, self.root, run_ref=run_ref, user=self.user)

    # --- 세션 목록 ---
    def test_session_list_and_filters(self, _):
        report = self.make_report()
        url = f"/api/v1/reports/{report.id}/sessions"
        self.assertEqual(self.client.get(url).json()["count"], 20)
        rows = self.client.get(url, {"kind": "dropout"}).json()["items"]
        self.assertEqual([(r["persona_id"], r["terminal_stage"], r["primary_cause"]) for r in rows],
                         [("P-001", "signup", "input_burden")])
        self.assertEqual(self.client.get(url, {"kind": "technical"}).json()["items"][0]["persona_id"], "P-002")
        self.assertEqual(self.client.get(url, {"segment_id": "segment_2"}).json()["count"], 5)

    # --- 세션 상세 ---
    def test_session_detail_timeline_and_chart(self, _):
        report = self.make_report()
        res = self.client.get(f"/api/v1/reports/{report.id}/sessions/order/1/P-001")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["status"], body["terminal_stage"]), ("simulated_dropout", "signup"))
        self.assertEqual(body["primary_cause"], {"key": "input_burden", "name": "입력 부담"})
        self.assertEqual(body["persona"]["segment_name"], "빠른 구매형")
        self.assertEqual(body["task"]["id"], "order")
        timeline = body["timeline"]
        self.assertEqual([s["stage_name"] for s in timeline], ["랜딩", "상세", "장바구니", "가입"])
        self.assertEqual(timeline[0]["action"]["kind"], "click")
        self.assertIsNone(timeline[-1]["action"])                          # 이탈한 화면에는 다음 행동 없음
        self.assertEqual(timeline[-1]["frictions"][0]["name"], "입력 부담")
        self.assertEqual(timeline[1]["screenshot"], "session_P-001/step_001.png")
        self.assertEqual(body["chart"]["threshold"], 60.0)
        self.assertEqual([p["score"] for p in body["chart"]["points"]], [0.0, 10.0, 20.0, 30.0])

    def test_session_detail_errors(self, _):
        report = self.make_report()
        base = f"/api/v1/reports/{report.id}/sessions"
        self.assertEqual(self.client.get(f"{base}/order/2/P-001").status_code, 404)      # 리포트에 없는 시드
        self.assertEqual(self.client.get(f"{base}/coupon/1/P-001").status_code, 404)     # 확정 안 한 과업
        for folder in self.root.iterdir():                                               # 파일이 지워진 경우
            for f in folder.rglob("events.jsonl"):
                f.unlink()
        res = self.client.get(f"{base}/order/1/P-001")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "SESSION_FILES_MISSING"))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"{base}/order/1/P-001").status_code, 403)

    # --- 진단 기록 목록·대시보드 ---
    def test_project_reports_and_dashboard(self, _):
        first = self.make_report("run1")
        self.make_report("run2", outcomes={f"P-{i:03d}": DROP for i in range(1, 6)})
        create_report_from_run_dir(first.plan_snapshot, self.root, run_ref="run1", user=self.user)   # run1 재집계

        # 진단 기록 목록 API는 A의 /projects/{id}/runs로 합쳤다. 목록에 넣을 요약(report_summary)과 묶는 규칙은 여기서 확인
        items = [services.report_summary(r) for r in services.project_reports(self.project)]
        self.assertEqual(len(items), 2)                                     # 실행마다 최신 revision 1개
        self.assertEqual((items[0]["run_ref"], items[0]["revision"]), ("run1", 2))
        self.assertEqual(items[1]["top_causes"], [{"key": "input_burden", "name": "입력 부담", "dropouts": 5}])
        self.assertEqual(self.client.get(f"/api/v1/projects/{self.project.id}/reports").status_code, 404)

        dash = self.client.get(f"/api/v1/projects/{self.project.id}/dashboard").json()
        self.assertEqual(dash["runs"], 2)
        self.assertEqual(dash["latest"]["run_ref"], "run1")
        self.assertEqual([t["run_ref"] for t in dash["trend"]], ["run2", "run1"])   # 오래된 것 → 최신
        self.assertEqual(dash["trend"][0]["achievement_rate"], 0.75)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/v1/projects/{self.project.id}/dashboard").status_code, 403)

    def test_empty_dashboard(self, _):
        dash = self.client.get(f"/api/v1/projects/{self.project.id}/dashboard").json()
        self.assertEqual((dash["runs"], dash["latest"], dash["trend"]), (0, None, []))