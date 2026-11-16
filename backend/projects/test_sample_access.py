import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from projects.models import Project
from runs.models import Run
from runs.tests.factories import RunsFixtureMixin, make_observation, make_run, make_session, make_task_run, make_user
from runs.tests.fake_agent import PNG


class SampleSeedTests(TestCase):
    def seed(self, *args):
        out = io.StringIO()
        call_command("seed_sample_project", *args, stdout=out)
        return out.getvalue()

    def test_creates_a_read_only_sample_owned_by_a_system_account(self):
        self.seed("--url", "https://Banchan.example")
        project = Project.objects.get(is_sample=True)
        self.assertEqual((project.name, project.entry_url, project.allowed_origins),
                         ("반찬로드", "https://banchan.example/", ["https://banchan.example"]))
        owner = project.owner
        self.assertFalse(owner.is_active)
        self.assertFalse(owner.has_usable_password())

    def test_is_idempotent_and_updates_the_url(self):
        self.seed("--url", "https://a.example/")
        self.seed("--url", "https://b.example/")
        self.assertEqual(Project.objects.filter(is_sample=True).count(), 1)
        self.assertEqual(Project.objects.get(is_sample=True).entry_url, "https://b.example/")
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_invalid_url_is_rejected(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.seed("--url", "ftp://nope")


class GuestAccessTests(RunsFixtureMixin, TestCase):
    """로그인 전 둘러보기(L06): 샘플 프로젝트만 읽을 수 있고, 쓰기와 남의 프로젝트는 막는다."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        override = override_settings(ARTIFACT_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)
        self.owner = make_user("sample-owner@example.com")
        self.sample = Project.objects.create(owner=self.owner, name="반찬로드", entry_url="https://banchan.example/",
                                             is_sample=True)
        self.run = make_run(self.sample, self.owner, self.snapshot, sequence_no=1, status=Run.Status.COMPLETED)
        task_run = make_task_run(self.run)
        self.session = make_session(task_run, self.persona, status="success")
        self.observation = make_observation(self.session, 0)
        (self.root / "s").mkdir()
        (self.root / "s" / "step_000.png").write_bytes(PNG)
        type(self.observation).objects.filter(pk=self.observation.pk).update(artifact="s/step_000.png")
        self.guest = APIClient()
        self.private = Project.objects.create(owner=self.owner, name="비공개", entry_url="https://private.example/")
        self.private_run = make_run(self.private, self.owner, self.snapshot, sequence_no=1)

    def test_guest_can_list_sample_projects(self):
        body = self.guest.get("/api/v1/sample/projects").json()
        self.assertEqual([p["name"] for p in body["items"]], ["반찬로드"])
        self.assertTrue(body["items"][0]["is_sample"])
        self.assertEqual(body["total"], 1)
        self.assertNotIn("비공개", str(body))

    def test_archived_sample_is_hidden(self):
        Project.objects.filter(pk=self.sample.pk).update(archived_at="2026-11-14T00:00:00+00:00")
        self.assertEqual(self.guest.get("/api/v1/sample/projects").json()["items"], [])

    def test_guest_can_read_sample_project_runs_and_sessions(self):
        for path in (f"/api/v1/projects/{self.sample.pk}", f"/api/v1/projects/{self.sample.pk}/runs",
                     f"/api/v1/runs/{self.run.id}", f"/api/v1/runs/{self.run.id}/sessions",
                     f"/api/v1/runs/{self.run.id}/events"):
            with self.subTest(path=path):
                self.assertEqual(self.guest.get(path).status_code, 200)

    def test_guest_can_see_sample_screenshots(self):
        res = self.guest.get(f"/api/v1/observations/{self.observation.pk}/screenshot")
        self.assertEqual(res.status_code, 200)
        res.close()

    def test_guest_cannot_read_private_projects_or_runs(self):
        for path in (f"/api/v1/projects/{self.private.pk}", f"/api/v1/projects/{self.private.pk}/runs",
                     f"/api/v1/runs/{self.private_run.id}", f"/api/v1/runs/{self.private_run.id}/sessions"):
            with self.subTest(path=path):
                res = self.guest.get(path)
                self.assertEqual((res.status_code, res.json()["error"]["code"]), (401, "AUTH_REQUIRED"))

    def test_guest_cannot_write_anything(self):
        for method, path in (("post", "/api/v1/runs"), ("post", f"/api/v1/runs/{self.run.id}/cancel"),
                             ("post", f"/api/v1/projects/{self.sample.pk}/check"), ("post", "/api/v1/projects")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.guest, method)(path, {}, format="json").status_code, 401)

    def test_logged_in_user_can_read_sample_but_not_write_it(self):
        other = APIClient()
        other.force_login(make_user("someone@example.com"))
        self.assertEqual(other.get(f"/api/v1/runs/{self.run.id}").status_code, 200)
        self.assertEqual(other.post(f"/api/v1/projects/{self.sample.pk}/check").status_code, 403)
        self.assertEqual(other.post(f"/api/v1/runs/{self.run.id}/cancel").status_code, 403)
        self.assertEqual(other.get(f"/api/v1/runs/{self.private_run.id}").status_code, 403)

    def test_sample_is_not_in_a_users_own_project_list(self):
        other = APIClient()
        other.force_login(make_user("someone@example.com"))
        self.assertEqual(other.get("/api/v1/projects").json()["total"], 0)

    def test_unknown_ids_are_404_for_guests(self):
        self.assertEqual(self.guest.get("/api/v1/projects/999999").status_code, 404)
        self.assertEqual(self.guest.get("/api/v1/runs/00000000-0000-0000-0000-000000000000").status_code, 404)
