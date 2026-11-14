"""백엔드 A의 실행(Run·Session)과 B의 리포트 연결 (11/16: A의 실제 데이터로 집계 검증).

A의 워커가 가짜 에이전트(runs/tests/fake_agent.py)를 실제 프로세스로 돌리고,
실행이 끝나면 finalize_run 훅으로 리포트가 자동으로 만들어지는지 확인한다.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from reports.models import ReportSnapshot
from reports.services import create_report_for_run
from runs import services as run_services
from runs.models import Run, Session
from runs.report_link import latest_report_summaries
from runs.worker import Worker

FAKE_AGENT = str(Path(__file__).resolve().parents[2] / "runs" / "tests" / "fake_agent.py")


class RunReportTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, kwargs in (("discoveries.services.run_discovery", {"side_effect": fake_discovery}),
                               ("runs.netguard.resolve_host", {"return_value": []})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        override = override_settings(ARTIFACT_ROOT=str(Path(tmp.name).resolve()),
                                     AGENT_COMMAND=[sys.executable, FAKE_AGENT], EXITPLAIN_WORKER_PROCESSES=3,
                                     EXITPLAIN_FINALIZE_HOOK="reports.services:finalize_run")
        override.enable()
        self.addCleanup(override.disable)

    def finished_run(self):
        plan = self.patch(self.ready_plan(), {"limits": {"seed_count": 1}}).json()
        snapshot = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        run = run_services.create_run(self.project, snapshot, self.user)[0]
        return Worker(tick=0.1, grace_seconds=30).run_once()

    def test_worker_finishes_run_and_report_is_created_from_db(self):
        run = self.finished_run()
        self.assertEqual(run.status, Run.Status.COMPLETED)
        report = ReportSnapshot.objects.get(run=run)                      # 훅이 자동으로 만든 리포트
        self.assertEqual((report.run_ref, report.source, report.revision), (str(run.id), "database", 1))
        data = report.data
        self.assertEqual(data["summary"]["recorded_sessions"], 40)       # 페르소나 20 × 과업 2 × 시드 1
        order = data["tasks"][0]
        self.assertEqual((order["success"], order["dropout"]), (10, 10))  # 가짜 에이전트: 짝수 성공·홀수 이탈
        self.assertEqual(order["top_causes"][0]["name"], "입력 부담")
        funnel = {s["key"]: s["reached"] for s in order["funnel"]}
        self.assertEqual((funnel["landing"], funnel["browse"], funnel["detail"]), (20, 20, 20))
        self.assertEqual(len(data["sessions"]), 40)
        # A의 실행 목록이 run_ref로 이 리포트를 찾는다
        self.assertEqual(latest_report_summaries([run.id])[str(run.id)]["id"], str(report.id))

    def test_session_detail_reads_db_and_uses_screenshot_api(self):
        run = self.finished_run()
        report = ReportSnapshot.objects.get(run=run)
        res = self.client.get(f"/api/v1/reports/{report.id}/sessions/order/1/P-002")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["status"], "simulated_dropout")
        self.assertEqual(body["primary_cause"]["name"], "입력 부담")
        timeline = body["timeline"]
        self.assertEqual([s["stage_name"] for s in timeline], ["랜딩", "탐색", "상세"])
        self.assertEqual(timeline[1]["frictions"][0]["name"], "입력 부담")
        self.assertEqual(timeline[0]["action"]["kind"], "click")
        self.assertRegex(timeline[0]["screenshot"], r"^/api/v1/observations/\d+/screenshot$")
        shot = self.client.get(timeline[0]["screenshot"])                  # A의 서빙 API로 열림
        self.assertEqual(shot.status_code, 200)
        shot.close()                                                       # 파일을 닫아야 Windows에서 임시 폴더가 지워짐
        self.assertEqual([p["score"] for p in body["chart"]["points"]], [0.0, 20.0, 40.0])
        self.assertRegex(body["final_screenshot"], r"^/api/v1/sessions/\d+/final-screenshot$")

    def test_reaggregate_and_running_sessions_are_incomplete(self):
        run = self.finished_run()
        Session.objects.filter(run=run, task_id="coupon").update(status="running")
        report = create_report_for_run(run)
        self.assertEqual(report.revision, 2)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.data["tasks"][1]["incomplete"], 20)