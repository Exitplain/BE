"""사이트 확인 explore 모드: 입력 URL을 둘러본 결과(기능·과업, 말로 쓴 성공 기준)가 계획·실행 입력까지 이어지는지."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from common.testing import WizardMixin
from plans.models import PlanSnapshot
from plans.run_input import build_run_input

EXPLORED = {
    "project": "LecQ",
    "features": [{"id": "register-feature", "name": "회원가입", "evidence": {"label": "Register", "path": "/register"},
                  "verification": "explored_readonly"}],
    "tasks": [{"id": "start-register", "name": "무료 회원가입 시작하기", "goal": "무료로 시작하려고 가입 화면으로 간다",
               "success_criteria": "이메일·비밀번호 입력칸이 있는 회원가입 폼이 보인다", "success_path": None,
               "start_path": "/", "case": None, "feature_id": "register-feature", "suggested": True},
              {"id": "view-pricing", "name": "요금제 보기", "goal": "요금제를 비교한다",
               "success_criteria": "요금제별 가격이 보인다", "success_path": None,
               "start_path": "/", "case": None, "feature_id": "register-feature", "suggested": False}],
    "note": "화면 4개를 읽기 전용으로 둘러봤습니다.",
}


@override_settings(EXITPLAIN_DISCOVERY_MODE="explore", EXITPLAIN_APPEND_DEMO_QUERY=True)
@patch("discoveries.services.run_discovery", side_effect=AssertionError("explore 모드에서 데모 탐색을 부르면 안 됨"))
class ExploreModeTests(WizardMixin, TestCase):
    def test_explored_tasks_flow_into_run_input_with_success_criteria(self, _):
        with patch("discoveries.services.run_exploration", return_value=EXPLORED) as explore:
            discovery = self.discover()
        explore.assert_called_once_with("http://shop.local:8080/", ["http://shop.local:8080"])
        self.assertEqual((discovery["variant"], discovery["tasks"][0]["success_criteria"]),
                         ("explore", "이메일·비밀번호 입력칸이 있는 회원가입 폼이 보인다"))
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans", {"discovery_id": discovery["id"]},
                                format="json").json()
        self.assertEqual(plan["task_ids"], ["start-register"])                    # 추천 과업만 기본 선택
        plan = self.patch(plan, {"audience": self.STEP2["audience"]}).json()
        snapshot = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        run_input = build_run_input(snapshot, run_id="r1", task_id="start-register")
        self.assertEqual(run_input["target_url"], "http://shop.local:8080/")       # 데모 쿼리(?variant=) 없음
        self.assertEqual((run_input["success_path"], run_input["success_criteria"]),
                         (None, "이메일·비밀번호 입력칸이 있는 회원가입 폼이 보인다"))
        self.assertEqual(run_input["run_id"], "r1_start-register_s1")

    def test_exploration_failure_is_site_unreachable(self, _):
        with patch("discoveries.services.run_exploration", side_effect=ValueError("시작 화면을 열지 못했습니다.")):
            res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "SITE_UNREACHABLE"))
