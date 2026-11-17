"""테스트 계정으로 로그인한 사이트 확인(explore) → 계획 → 실행 입력·워커·로그인 결과까지 계정이 이어지는 흐름."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from common.testing import WizardMixin
from plans.models import PlanSnapshot
from plans.run_input import build_run_input
from runs import importer
from runs import services as run_services
from runs.worker import Worker
from testaccounts.models import TestAccount

LOGIN = "http://shop.local:8080/login"
SECRET = "pw-1,x"
EXPLORED = {
    "project": "LecQ", "note": "테스트 계정으로 로그인해 화면 10개를 둘러봤습니다.",
    "features": [{"id": "study-room", "name": "공부방", "evidence": {"label": "공부방", "path": "/study-room"},
                  "verification": "explored_readonly"}],
    "tasks": [{"id": "open-study-room", "name": "공부방 열기", "goal": "공부방으로 간다", "success_criteria": "Study Room 화면이 보인다",
               "success_path": None, "start_path": "/home", "case": None, "feature_id": "study-room", "suggested": True,
               "requires_login": True},
              {"id": "open-tutor", "name": "AI 튜터 열기", "goal": "AI 튜터를 연다", "success_criteria": "질문 기록이 보인다",
               "success_path": None, "start_path": "/home", "case": None, "feature_id": "study-room", "suggested": False,
               "requires_login": True}],
}


def accounts(n):
    return [{"username": f"test{i:02d}@lecq.example", "password": f"pw-{i},x"} for i in range(1, n + 1)]


@override_settings(EXITPLAIN_DISCOVERY_MODE="explore")
@patch("discoveries.services.run_discovery", side_effect=AssertionError("explore 모드에서는 쓰지 않음"))
class ExploreLoginTests(WizardMixin, TestCase):
    def put_accounts(self, login_url=LOGIN, n=4):
        res = self.client.put(f"/api/v1/projects/{self.project.id}/test-accounts",
                              {"login_url": login_url, "accounts": accounts(n)}, format="json")
        self.assertEqual(res.status_code, 200, res.content)

    def test_discovery_logs_in_with_the_first_account_and_marks_it_checked(self, _):
        self.put_accounts()
        with patch("discoveries.services.run_exploration", return_value=EXPLORED) as explore:
            discovery = self.discover(login_mode="test_account")
        login = explore.call_args.kwargs["login"]
        self.assertEqual(login, {"url": LOGIN, "username": "test01@lecq.example", "password": SECRET})
        self.assertNotIn(SECRET, json.dumps(discovery, ensure_ascii=False))           # 응답에 비밀번호 없음
        first = TestAccount.objects.get(position=1)
        self.assertEqual((first.status, first.status_message), ("ok", "사이트 확인 중 로그인 성공"))

    def test_login_failure_during_discovery_is_reported_on_the_account(self, _):
        self.put_accounts()
        with patch("discoveries.services.run_exploration", side_effect=ValueError("테스트 계정으로 로그인하지 못했습니다.")):
            res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {"login_mode": "test_account"},
                                   format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "TEST_ACCOUNT_LOGIN_FAILED"))
        self.assertEqual(TestAccount.objects.get(position=1).status, "failed")

    def test_login_url_is_required(self, _):
        self.put_accounts(login_url="")
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {"login_mode": "test_account"}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "LOGIN_URL_REQUIRED"))

    def confirmed_snapshot(self):
        self.put_accounts()
        with patch("discoveries.services.run_exploration", return_value=EXPLORED):
            discovery = self.discover(login_mode="test_account")
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans", {"discovery_id": discovery["id"]},
                                format="json").json()
        plan = self.patch(plan, {"audience": self.STEP2["audience"]}).json()
        return PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])

    def test_run_input_logs_in_read_only_without_account_values(self, _):
        snapshot = self.confirmed_snapshot()
        run_input = build_run_input(snapshot, run_id="r1", task_id="open-study-room")
        self.assertEqual((run_input["login_url"], run_input["read_only"]), (LOGIN, True))
        self.assertEqual(run_input["target_url"], "http://shop.local:8080/home")
        self.assertNotIn(SECRET, json.dumps(run_input))
        self.assertNotIn("test01@lecq.example", json.dumps(run_input))

    def test_worker_passes_accounts_only_through_the_environment(self, _):
        snapshot = self.confirmed_snapshot()
        with patch("runs.netguard.resolve_host", return_value=[]):
            run = run_services.create_run(self.project, snapshot, self.user)[0]
        task_run = run.task_runs.first()
        with patch("runs.worker.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            Worker(tick=0.1)._start(task_run)
        env = popen.call_args.kwargs["env"]
        creds = json.loads(env["DROPSPOT_CREDENTIALS"])
        self.assertEqual(creds["P-001"], {"username": "test01@lecq.example", "password": SECRET})
        self.assertNotIn(SECRET, " ".join(popen.call_args.args[0]))                    # 명령줄에 없음
        self.assertNotIn(SECRET, (importer.artifact_root() / "_inputs" / f"{task_run.agent_run_id}.json").read_text("utf-8"))

    def test_agent_login_result_updates_the_paired_account(self, _):
        snapshot = self.confirmed_snapshot()
        with patch("runs.netguard.resolve_host", return_value=[]):
            run = run_services.create_run(self.project, snapshot, self.user)[0]
        task_run = run.task_runs.first()
        batch = importer._Batch(task_run)
        persona = snapshot.data["personas"][1]
        sid = f"session_{persona['code']}"
        importer._apply(batch, {"type": "session.started", "event_id": "e1", "session_id": sid, "sequence": 1,
                                "occurred_at": "2026-11-17T00:00:00+00:00", "payload": {"persona": {"id": persona["code"]}}})
        importer._apply(batch, {"type": "login.completed", "event_id": "e2", "session_id": sid, "sequence": 2,
                                "occurred_at": "2026-11-17T00:00:01+00:00", "payload": {"ok": False, "has_account": True}})
        second = TestAccount.objects.get(position=2)
        self.assertEqual((second.status, second.status_message), ("failed", "에이전트 로그인 실패"))
