"""발견한 문제 (W03·C09): 리포트의 가상 이탈을 과업 × 이탈 화면 × 원인으로 묶고, 실행끼리 비교해 판정한다."""
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import build_run_inputs
from reports.models import ReportSnapshot
from reports.services import create_report_from_run_dir
from reports.tests.sample_runs import write_attempt

SIGNUP_DROP = ("simulated_dropout", ["/", "/product", "/cart", "/signup"], "input_burden")
PAY_DROP = ("simulated_dropout", ["/", "/product", "/cart", "/signup", "/checkout"], "cost")
TECH = ("site_failure", ["/", "/product"])


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class IssueTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.url = f"/api/v1/projects/{self.project.id}/issues"
        self.snap = None
        self.minutes = 0

    def snapshot(self, seeds=2):
        if self.snap is None:
            plan = self.patch(self.create_plan(), {**self.STEP2, "task_ids": ["order"]}).json()
            plan = self.patch(plan, {"limits": {"seed_count": seeds}}).json()
            self.snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        return self.snap

    def make_run(self, run_ref, outcomes_by_seed):
        """outcomes_by_seed: {seed: {persona_id: outcome}}. 실행마다 리포트 1개."""
        snap = self.snapshot()
        root = self.root / run_ref
        for item in build_run_inputs(snap, run_id=run_ref):
            write_attempt(root, item, outcomes_by_seed.get(item["seed"], {}))
        report = create_report_from_run_dir(snap, root, run_ref=run_ref, user=self.user)
        self.minutes += 10                                   # 실행 순서가 created_at으로 정해지게
        ReportSnapshot.objects.filter(pk=report.pk).update(created_at=report.created_at + timedelta(minutes=self.minutes))
        return report

    def items(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200, res.content)
        return {i["id"]: i for i in res.json()["items"]}

    def test_first_run_is_before_retest(self, _):
        self.make_run("run1", {1: {"P-001": SIGNUP_DROP, "P-002": SIGNUP_DROP, "P-003": TECH}, 2: {"P-001": SIGNUP_DROP}})
        items = self.items()
        self.assertEqual(list(items), ["order:/signup:input_burden"])        # 기술 실패는 문제로 세지 않음
        issue = items["order:/signup:input_burden"]
        self.assertEqual((issue["cause"], issue["stage"], issue["task"]), ("입력 부담", "/signup", "첫 주문"))
        self.assertEqual((issue["impact"], issue["dropouts"]), ([1, 2], 3))     # 시드 1에 2명, 시드 2에 1명
        self.assertEqual((issue["verdict"], issue["run_id"]), ("재테스트 전", "run1"))
        self.assertEqual(issue["project_name"], "반찬로드")
        self.assertIn("가상 방문 3개", issue["evidence"])

    def test_retest_marks_continuing_and_new_and_drops_resolved(self, _):
        self.make_run("run1", {1: {"P-001": SIGNUP_DROP, "P-002": SIGNUP_DROP}, 2: {"P-001": PAY_DROP}})
        self.make_run("run2", {1: {"P-001": SIGNUP_DROP}, 2: {"P-003": ("simulated_dropout", ["/", "/product"], "navigation")}})
        items = self.items()
        self.assertEqual(items["order:/signup:input_burden"]["verdict"], "계속 발생")
        self.assertEqual(items["order:/signup:input_burden"]["run_id"], "run2")
        self.assertEqual(items["order:/signup:input_burden"]["impact"], [0, 1])  # 최신 실행 기준
        self.assertIn("이전 실행 1번", items["order:/signup:input_burden"]["evidence"])
        new = [i for i in items.values() if i["cause_key"] == "navigation"][0]
        self.assertEqual(new["verdict"], "새로 발견")
        self.assertNotIn("order:/checkout:cost", items)                           # run2에서 사라진 문제는 빠짐
        res = self.client.get(self.url).json()
        self.assertEqual([i["verdict"] for i in res["items"]], ["계속 발생", "새로 발견"])

    def test_old_report_without_screens_uses_seven_stage_name(self, _):
        report = self.make_run("run1", {1: {"P-001": SIGNUP_DROP}})
        for row in report.data["sessions"]:
            row.pop("terminal_screen")                                         # 화면 정보가 없던 예전 리포트
        ReportSnapshot.objects.filter(pk=report.pk).update(data=report.data)
        issue = self.items()["order:stage:signup:input_burden"]
        self.assertEqual(issue["stage"], "가입")

    def test_latest_revision_only_and_no_reports(self, _):
        self.assertEqual(self.client.get(self.url).json(), {
            "count": 0, "items": [], "note": "가상 페르소나의 모의 결과입니다. 실제 사용자 이탈률이 아닙니다."})
        self.make_run("run1", {1: {"P-001": SIGNUP_DROP}})
        self.assertEqual(self.client.get(self.url).json()["count"], 1)

    def test_workspace_issues_and_owner_check(self, _):
        self.make_run("run1", {1: {"P-001": SIGNUP_DROP}})
        res = self.client.get("/api/v1/issues")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([i["project_id"] for i in res.json()["items"]], [self.project.id])
        other = APIClient()
        other.force_login(get_user_model().objects.get(username="other"))
        self.assertEqual(other.get(self.url).status_code, 403)
        self.assertEqual(other.get("/api/v1/issues").json()["count"], 0)          # 남의 프로젝트 문제는 안 보임
        self.assertEqual(APIClient().get("/api/v1/issues").status_code, 401)