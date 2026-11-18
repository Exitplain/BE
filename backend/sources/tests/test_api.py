"""코드 연결 API: zip·공개 git 으로 받고, 규칙 분석 결과(라우트·실행 가능 여부)를 진행 피드와 함께 돌려준다.

테스트 설정은 EXITPLAIN_SOURCE_BACKGROUND=False 라 같은 요청 안에서 끝까지 돈다 (응답 모양은 백그라운드와 같다).
"""
import io
import json
import shutil
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from common.progress import STALE_SECONDS
from common.testing import WizardMixin
from sources import services
from sources.models import SourceConnection, SourceSnapshot

SITE = {
    "shop/index.html": "<!doctype html><script src='/app.js'></script>",
    "shop/app.js": "function start(){return `<a href=\"/list\">목록</a>`;}\nfunction list(){go('/done');}\n"
                   "function finished(){}\nfunction render(){const v={'/':start,'/list':list,'/done':finished};}\n",
    "shop/.env": "API_KEY=sk-live-secret",
}


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class SourceApiTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        patcher = override_settings(EXITPLAIN_SOURCE_ROOT=self.root)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.url = f"/api/v1/projects/{self.project.id}/source-snapshots"

    def upload(self, files=SITE, name="shop.zip", **extra):
        return self.client.post(self.url, {"file": SimpleUploadedFile(name, zip_bytes(files), "application/zip"), **extra},
                                format="multipart")

    def test_zip_upload_is_analyzed_as_a_static_site(self):
        res = self.upload()
        self.assertEqual(res.status_code, 202, res.content)
        s = res.json()
        self.assertEqual((s["state"], s["runnable"], s["provider"], s["error"]), ("ready", "static", "zip", None))
        self.assertEqual((s["file_count"], s["skipped"]), (2, {"secret_like": 1}))
        self.assertEqual((s["framework"]["router"], s["framework"]["status"]), ("static-html", "ok"))
        self.assertEqual([r["pattern"] for r in s["analysis"]["routes"]], ["/", "/done", "/list"])
        self.assertIn({"from": "/", "to": "/list", "kind": "link", "file": "app.js", "line": 1}, s["analysis"]["edges"])
        self.assertEqual(len(s["tree_digest"]), 64)
        kinds = [e["kind"] for e in s["progress"]]
        self.assertEqual(kinds[0], "queued")
        self.assertEqual(kinds[-1], "ready")
        self.assertTrue({"unpacking", "received", "framework", "routes", "runnable"} <= set(kinds))
        body = json.dumps(s, ensure_ascii=False)
        self.assertNotIn(self.root, body)                       # 서버 경로 없음
        self.assertNotIn("sk-live-secret", body)
        tree = Path(self.root) / s["id"]
        self.assertTrue((tree / "index.html").is_file())
        self.assertFalse((tree / ".env").exists())              # 비밀값 파일은 저장하지 않음
        self.assertFalse(list((Path(self.root) / "_incoming").glob("*")))    # 올린 zip 은 지움
        self.assertEqual(self.client.get(f"/api/v1/source-snapshots/{s['id']}").json()["analysis"], s["analysis"])

    def test_same_content_gives_the_same_digest(self):
        a = self.upload().json()
        b = self.upload(files={k.replace("shop/", "other-root/"): v for k, v in SITE.items()}).json()
        self.assertEqual(a["tree_digest"], b["tree_digest"])
        self.assertEqual(a["connection_id"], b["connection_id"])   # 같은 프로젝트의 zip 연결 하나에 스냅샷이 쌓임

    def test_build_needed_project_is_analysis_only(self):
        files = {"app/package.json": json.dumps({"dependencies": {"react": "^18", "react-router-dom": "^6"},
                                                 "scripts": {"build": "vite build"}}),
                 "app/index.html": "<div id=root></div>",
                 "app/src/main.jsx": "<Routes><Route path=\"/\" element={<A/>} /><Route path=\"/b\" element={<B/>} /></Routes>"}
        s = self.upload(files).json()
        self.assertEqual((s["state"], s["runnable"], s["framework"]["router"]), ("ready", "needs_build", "react-router-v6"))
        self.assertEqual(s["progress"][-2]["message"], "빌드가 필요해 지금은 코드 분석만 해요")

    def test_prebuilt_dist_folder_is_served_statically(self):
        files = {"p/package.json": json.dumps({"scripts": {"build": "vite build"}}), "p/dist/index.html": "<html>"}
        s = self.upload(files).json()
        self.assertEqual((s["runnable"], s["static_root"]), ("static", "dist"))

    def test_subdirectory_of_a_monorepo(self):
        files = {"repo/README.md": "x", **{k.replace("shop/", "repo/web/"): v for k, v in SITE.items()}}
        s = self.upload(files, subdirectory="web").json()
        self.assertEqual((s["subdirectory"], s["runnable"]), ("web", "static"))
        missing = self.upload(files, subdirectory="nope").json()
        self.assertEqual((missing["state"], missing["error"]["code"]), ("failed", "SUBDIRECTORY_NOT_FOUND"))
        self.assertFalse((Path(self.root) / missing["id"]).exists())    # 실패하면 받은 파일을 지움

    def test_upload_validation(self):
        self.assertEqual(self.upload(name="shop.tar").json()["error"]["code"], "ZIP_INVALID")
        with override_settings(EXITPLAIN_SOURCE_MAX_UPLOAD_BYTES=10):
            res = self.upload()
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (413, "ZIP_TOO_LARGE"))
        res = self.client.post(self.url, {"file": SimpleUploadedFile("a.zip", b"x"), "evil": "1"}, format="multipart")
        self.assertIn("evil", res.json()["error"]["fields"])
        bad = self.upload(subdirectory="../x")
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (422, "SUBDIRECTORY_INVALID"))
        self.assertFalse(SourceSnapshot.objects.exists())

    def test_git_repository_is_pinned_to_a_commit(self):
        def fake_clone(url, ref, dest):
            for name, text in SITE.items():
                path = Path(dest) / name.replace("shop/", "")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            return "b" * 40
        with patch("sources.intake.clone", side_effect=fake_clone) as clone:
            res = self.client.post(self.url, {"git_url": "https://github.com/acme/shop", "ref": "main"}, format="json")
        self.assertEqual(res.status_code, 202, res.content)
        s = res.json()
        clone.assert_called_once()
        self.assertEqual(clone.call_args.args[:2], ("https://github.com/acme/shop", "main"))
        self.assertEqual((s["state"], s["commit_sha"], s["ref"], s["repository_url"]),
                         ("ready", "b" * 40, "main", "https://github.com/acme/shop"))
        self.assertIn("commit bbbbbbb 으로 고정했어요", [e["message"] for e in s["progress"]])
        self.assertFalse((Path(self.root) / s["id"] / ".env").exists())    # git 도 비밀값 파일은 지움

    def test_git_input_errors(self):
        res = self.client.post(self.url, {"git_url": "https://evil.example/a/b"}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "GIT_URL_INVALID"))
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.json()["error"]["code"], "VALIDATION_ERROR")
        with patch("sources.intake._git", side_effect=services.intake.IntakeError("GIT_TIMEOUT", "오래 걸려요")):
            s = self.client.post(self.url, {"git_url": "https://github.com/a/b"}, format="json").json()
        self.assertEqual((s["state"], s["error"]["code"]), ("failed", "GIT_TIMEOUT"))

    def test_unexpected_failure_hides_internal_details(self):
        with patch("sources.analysis.analyze", side_effect=RuntimeError("Traceback /srv/exitplain/secret")):
            s = self.upload().json()
        self.assertEqual((s["state"], s["error"]["code"]), ("failed", "SOURCE_FAILED"))
        self.assertNotIn("/srv/exitplain", json.dumps(s))

    def test_only_the_owner_can_read_or_create(self):
        s = self.upload().json()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/v1/source-snapshots/{s['id']}").status_code, 403)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/source-connections/{s['connection_id']}").status_code, 403)

    def test_list_returns_recent_snapshots_without_analysis_body(self):
        self.upload()
        items = self.client.get(self.url).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]["analysis"]), {"status", "counts"})

    def test_revoke_deletes_files_but_keeps_the_record(self):
        s = self.upload().json()
        res = self.client.delete(f"/api/v1/source-connections/{s['connection_id']}")
        self.assertEqual(res.status_code, 204)
        self.assertFalse((Path(self.root) / s["id"]).exists())
        got = self.client.get(f"/api/v1/source-snapshots/{s['id']}").json()
        self.assertEqual((got["connection_status"], got["files_available"], got["tree_digest"]),
                         ("revoked", False, s["tree_digest"]))
        again = self.upload().json()
        self.assertNotEqual(again["connection_id"], s["connection_id"])   # 해제 뒤 다시 올리면 새 연결

    @override_settings(EXITPLAIN_SOURCE_MAX_ACTIVE=1)
    def test_busy_server_rejects_new_jobs(self):
        conn = SourceConnection.objects.create(project=self.project, created_by=self.user, provider="zip")
        SourceSnapshot.objects.create(connection=conn, created_by=self.user)          # running, 방금 갱신
        res = self.upload()
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (429, "SOURCE_BUSY"))

    def test_stale_running_snapshot_is_marked_interrupted(self):
        conn = SourceConnection.objects.create(project=self.project, created_by=self.user, provider="zip")
        snap = SourceSnapshot.objects.create(connection=conn, created_by=self.user)
        SourceSnapshot.objects.filter(pk=snap.pk).update(updated_at=timezone.now() - timedelta(seconds=STALE_SECONDS + 5))
        got = self.client.get(f"/api/v1/source-snapshots/{snap.pk}").json()
        self.assertEqual((got["state"], got["error"]["code"]), ("failed", "SOURCE_INTERRUPTED"))
