"""URL 없는 프로젝트: 받은 정적 사이트를 미리보기 주소로 사이트 확인 → 계획 → 실행 입력까지 잇는다."""
import io
import shutil
import tempfile
import zipfile
from unittest.mock import ANY, patch

from django.test import TestCase, override_settings

from common.testing import WizardMixin
from plans.models import PlanSnapshot
from plans.run_input import build_run_input
from sources.preview import origin_for

EXPLORED = {
    "project": "반찬로드", "note": "화면 4개를 둘러봤습니다.",
    "features": [{"id": "order", "name": "주문", "evidence": {"label": "주문하기", "path": "/product"},
                  "verification": "explored_readonly"}],
    "tasks": [{"id": "first-order", "name": "첫 주문", "goal": "반찬을 주문한다", "success_criteria": "주문 완료 화면",
               "success_path": None, "start_path": "/", "case": None, "feature_id": "order", "suggested": True},
              {"id": "see-cart", "name": "장바구니", "goal": "장바구니를 본다", "success_criteria": "장바구니 화면",
               "success_path": None, "start_path": "/", "case": None, "feature_id": "order", "suggested": False}],
}
STATIC = {"site/index.html": "<html></html>", "site/app.js": "const v={'/':a,'/product':b};function a(){}function b(){}"}
REACT = {"app/package.json": '{"dependencies":{"react":"18"},"scripts":{"build":"vite build"}}', "app/src/a.jsx": "x"}


def zip_upload(files):
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return SimpleUploadedFile("code.zip", buf.getvalue(), "application/zip")


@patch("discoveries.services.run_discovery", side_effect=AssertionError("코드로 확인할 때는 데모 탐색을 쓰지 않음"))
class SourceDiscoveryTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        patcher = override_settings(EXITPLAIN_SOURCE_ROOT=self.root, EXITPLAIN_PREVIEW_PORT=8090)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def snapshot(self, files=STATIC):
        res = self.client.post(f"/api/v1/projects/{self.project.id}/source-snapshots", {"file": zip_upload(files)},
                               format="multipart")
        return res.json()

    def discover_source(self, sid, **extra):
        return self.client.post(f"/api/v1/projects/{self.project.id}/discoveries",
                                {"source_snapshot_id": sid, **extra}, format="json", HTTP_PREFER="respond-async")

    def test_static_code_is_explored_through_its_preview_and_runs_there(self, _):
        s = self.snapshot()
        self.assertTrue(s["previewable"])
        origin = origin_for(s["id"])
        with patch("sources.preview.reachable", return_value=True), \
                patch("discoveries.services.run_exploration", return_value=EXPLORED) as explore:
            d = self.discover_source(s["id"]).json()
        explore.assert_called_once_with(origin + "/", [origin], login=None, progress=ANY)
        self.assertEqual((d["status"], d["source_snapshot_id"], d["entry_url"]), ("succeeded", s["id"], origin + "/"))
        self.assertIn("받은 코드를 미리보기로 여는 중이에요", [e["message"] for e in d["progress"]])

        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans", {"discovery_id": d["id"]},
                                format="json").json()
        plan = self.patch(plan, {"audience": self.STEP2["audience"]}).json()
        snapshot = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        site = snapshot.data["site"]
        self.assertEqual(site["allowed_origins"], [origin])          # 프로젝트 주소가 아니라 미리보기 출처만
        self.assertEqual(site["source"], {"snapshot_id": s["id"], "commit_sha": "", "tree_digest": s["tree_digest"],
                                          "provider": "zip"})
        run_input = build_run_input(snapshot, run_id="r1", task_id="first-order")
        self.assertEqual((run_input["target_url"], run_input["allowed_origins"]), (origin + "/", [origin]))

    def test_preview_server_down_is_a_clear_failure(self, _):
        s = self.snapshot()
        with patch("sources.preview.reachable", return_value=False), \
                patch("discoveries.services.run_exploration", side_effect=AssertionError("열리지 않으면 둘러보지 않음")):
            d = self.discover_source(s["id"]).json()
        self.assertEqual((d["status"], d["error"]["code"]), ("failed", "PREVIEW_UNAVAILABLE"))

    def test_build_needed_code_cannot_be_explored(self, _):
        s = self.snapshot(REACT)
        self.assertEqual((s["runnable"], s["previewable"]), ("needs_build", False))
        res = self.discover_source(s["id"])
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "SOURCE_NOT_RUNNABLE"))

    def test_revoked_code_login_mode_and_other_projects_are_rejected(self, _):
        s = self.snapshot()
        res = self.discover_source(s["id"], login_mode="test_account")
        self.assertEqual(res.status_code, 422)
        other = self.project.__class__.objects.create(owner=self.user, name="다른", entry_url="http://other.local/")
        res = self.client.post(f"/api/v1/projects/{other.id}/discoveries", {"source_snapshot_id": s["id"]},
                               format="json", HTTP_PREFER="respond-async")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "VALIDATION_ERROR"))
        self.client.delete(f"/api/v1/source-connections/{s['connection_id']}")
        res = self.discover_source(s["id"])
        self.assertEqual(res.json()["error"]["code"], "SOURCE_NOT_RUNNABLE")
