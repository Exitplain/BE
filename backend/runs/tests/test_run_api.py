import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from common.testing import WizardMixin, fake_discovery
from reports.models import ReportSnapshot
from runs.models import Run, RunEvent
from runs.worker import Worker

from .factories import (RunsFixtureMixin, make_event, make_observation, make_persona, make_run, make_session,
                        make_task_run, make_user)

FAKE_AGENT = str(Path(__file__).with_name("fake_agent.py"))
PNG_BYTES = Path(FAKE_AGENT).read_bytes() and __import__("runs.tests.fake_agent", fromlist=["PNG"]).PNG


class ApiBase(RunsFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        override = override_settings(ARTIFACT_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)
        self.other = make_user("other@example.com")
        self.client = APIClient()
        self.client.force_login(self.user)
        self.stranger = APIClient()
        self.stranger.force_login(self.other)
        self.run = make_run(self.project, self.user, self.snapshot, sequence_no=1, status=Run.Status.RUNNING)
        self.run.config = {"expected_sessions": 4}
        self.run.save()
        self.task_run = make_task_run(self.run)

    def png(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG_BYTES)
        return relative

    def session(self, code="P-001", **extra):
        persona = self.persona if code == "P-001" else make_persona(self.snapshot, code)
        return make_session(self.task_run, persona, **extra)


class RunDetailTests(ApiBase):
    def url(self, run=None):
        return f"/api/v1/runs/{(run or self.run).id}"

    def test_shape_and_progress_counts(self):
        done = self.session("P-001", status="success", steps=3)
        self.session("P-002", status="simulated_dropout")
        self.session("P-003")  # running
        body = self.client.get(self.url()).json()
        self.assertEqual((body["id"], body["project_id"], body["status"], body["kind"]),
                         (str(self.run.id), self.project.pk, "running", "pretest"))
        self.assertEqual(body["name"], "1차")
        self.assertFalse(body["cancel_requested"])
        self.assertIsNone(body["error"])
        progress = body["progress"]
        self.assertEqual((progress["expected_sessions"], progress["started_sessions"], progress["finished_sessions"]),
                         (4, 3, 2))
        self.assertEqual(progress["sessions_by_status"], {"success": 1, "simulated_dropout": 1, "running": 1})
        self.assertEqual(progress["task_runs"]["total"], 1)
        self.assertEqual(progress["task_runs"]["queued"], 1)
        self.assertEqual(done.run, self.run)

    def test_phase_follows_figma_steps(self):
        phases = {}
        for status in Run.Status.values:
            Run.objects.filter(pk=self.run.pk).update(status=status)
            phases[status] = self.client.get(self.url()).json()["phase"]
        self.assertEqual(phases, {"queued": "preparing", "running": "preparing", "finalizing": "analyzing",
                                  "completed": "done", "partially_completed": "done", "failed": "failed",
                                  "cancelled": "cancelled"})
        self.session("P-001")  # 세션이 시작되면 실행 중
        Run.objects.filter(pk=self.run.pk).update(status="running")
        self.assertEqual(self.client.get(self.url()).json()["phase"], "running")

    def test_error_and_cancel_flags(self):
        Run.objects.filter(pk=self.run.pk).update(status="failed", error_code="AGENT_CRASHED", error_message="죽음",
                                                  cancel_requested_at="2026-11-14T00:00:00+00:00")
        body = self.client.get(self.url()).json()
        self.assertEqual(body["error"], {"code": "AGENT_CRASHED", "message": "죽음"})
        self.assertTrue(body["cancel_requested"])

    def test_report_summary_is_attached_when_available(self):
        ReportSnapshot.objects.create(plan_snapshot=self.snapshot, run_ref=str(self.run.id), revision=1, status="final",
                                      source="database", data={"tasks": [{"task_id": "order", "name": "주문",
                                                                           "achievement_rate": 0.4}]})
        report = self.client.get(self.url()).json()["report"]
        self.assertEqual((report["status"], report["goal_rates"][0]["achievement_rate"]), ("final", 0.4))

    def test_access_rules(self):
        self.assertEqual(APIClient().get(self.url()).status_code, 401)
        forbidden = self.stranger.get(self.url())
        self.assertEqual((forbidden.status_code, forbidden.json()["error"]["code"]), (403, "FORBIDDEN"))
        missing = self.client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
        self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (404, "NOT_FOUND"))

    def test_query_count_does_not_grow_with_sessions(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
            return len(ctx)

        self.session("P-001")
        one = count()
        for n in range(2, 8):
            self.session(f"P-00{n}")
        self.assertEqual(count(), one)


class SessionListTests(ApiBase):
    def url(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"/api/v1/runs/{self.run.id}/sessions" + (f"?{query}" if query else "")

    def test_tiles_have_persona_latest_observation_and_screenshot_urls(self):
        session = self.session("P-001", status="success", steps=2, max_frustration=70,
                               final_screenshot=self.png("a/final.png"))
        make_observation(session, 0)
        latest = make_observation(session, 1, "http://shop.local:8080/cart")
        type(latest).objects.filter(pk=latest.pk).update(artifact=self.png("a/step_001.png"))
        body = self.client.get(self.url()).json()
        item = body["items"][0]
        self.assertEqual(item["persona"], {"code": "P-001", "segment_id": "segment_1", "segment_name": "빠른 구매형"})
        self.assertEqual((item["status"], item["steps"], item["max_frustration"]), ("success", 2, 70))
        self.assertEqual(item["latest_observation"]["step"], 1)
        self.assertEqual(item["latest_observation"]["url"], "http://shop.local:8080/cart")
        self.assertEqual(item["latest_observation"]["screenshot_url"], f"/api/v1/observations/{latest.pk}/screenshot")
        self.assertEqual(item["final_screenshot_url"], f"/api/v1/sessions/{session.pk}/final-screenshot")
        self.assertEqual((body["total"], body["next_cursor"]), (1, None))

    def test_session_without_observation_or_artifact_has_no_urls(self):
        self.session("P-001")
        quiet = self.session("P-002")
        make_observation(quiet, 0)  # artifact 가 비어 있다
        items = {i["persona"]["code"]: i for i in self.client.get(self.url()).json()["items"]}
        self.assertIsNone(items["P-001"]["latest_observation"])
        self.assertIsNone(items["P-001"]["final_screenshot_url"])
        self.assertIsNotNone(items["P-002"]["latest_observation"])
        self.assertIsNone(items["P-002"]["latest_observation"]["screenshot_url"])

    def test_status_filter_and_start_order_pagination(self):
        for n in range(1, 6):
            self.session(f"P-00{n}", status="success" if n % 2 else "simulated_dropout")
        self.assertEqual(self.client.get(self.url(status="success")).json()["total"], 3)
        first = self.client.get(self.url(limit=2)).json()
        second = self.client.get(self.url(limit=2, cursor=first["next_cursor"])).json()
        third = self.client.get(self.url(limit=2, cursor=second["next_cursor"])).json()
        codes = [i["persona"]["code"] for p in (first, second, third) for i in p["items"]]
        self.assertEqual(codes, ["P-001", "P-002", "P-003", "P-004", "P-005"])
        self.assertIsNone(third["next_cursor"])

    def test_new_sessions_appear_after_the_ones_already_seen(self):
        self.session("P-001")
        self.session("P-002")
        first = self.client.get(self.url(limit=1)).json()
        self.session("P-003")  # 첫 페이지를 본 뒤에 새 세션이 시작됐다
        second = self.client.get(self.url(limit=1, cursor=first["next_cursor"])).json()
        third = self.client.get(self.url(limit=1, cursor=second["next_cursor"])).json()
        codes = [i["persona"]["code"] for p in (first, second, third) for i in p["items"]]
        self.assertEqual(codes, ["P-001", "P-002", "P-003"])  # 빠지거나 겹치지 않는다
        self.assertIsNone(third["next_cursor"])

    def test_invalid_params_and_access(self):
        for params in ({"status": "done"}, {"limit": 0}, {"limit": 201}, {"cursor": "%%%"}):
            self.assertEqual(self.client.get(self.url(**params)).status_code, 422, params)
        self.assertEqual(self.stranger.get(self.url()).status_code, 403)
        self.assertEqual(APIClient().get(self.url()).status_code, 401)

    def test_query_count_does_not_grow_with_sessions(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
            return len(ctx)

        make_observation(self.session("P-001"), 0)
        one = count()
        for n in range(2, 8):
            make_observation(self.session(f"P-00{n}"), 0)
        self.assertEqual(count(), one)


class EventListTests(ApiBase):
    def url(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"/api/v1/runs/{self.run.id}/events" + (f"?{query}" if query else "")

    def events(self, session):
        step = make_observation(session, 0)
        type(step).objects.filter(pk=step.pk).update(artifact=self.png("s/step_000.png"))
        absolute = str(self.root / "s" / "step_000.png")
        RunEvent.objects.create(run=self.run, task_run=self.task_run, session=session, event_id="e1", sequence=1,
                                type="session.started", occurred_at=step.occurred_at, payload={"persona": {"id": "P-001"}})
        RunEvent.objects.create(run=self.run, task_run=self.task_run, session=session, event_id="e2", sequence=2,
                                type="observation.created", occurred_at=step.occurred_at,
                                payload={"step_id": "step_000", "url": "http://shop.local:8080/", "artifact": absolute,
                                         "text": "첫 화면"})
        RunEvent.objects.create(run=self.run, task_run=self.task_run, session=session, event_id="e3", sequence=3,
                                type="friction.detected", occurred_at=step.occurred_at,
                                payload={"category": "input_burden", "artifact": absolute, "frustration_score": 70})
        RunEvent.objects.create(run=self.run, task_run=self.task_run, session=session, event_id="e4", sequence=4,
                                type="session.finished", occurred_at=step.occurred_at,
                                payload={"status": "success", "final_artifact": absolute,
                                         "issues": [{"category": "input_burden", "artifact": absolute}]})
        return step

    def test_payloads_never_expose_server_paths_and_get_screenshot_urls(self):
        session = self.session("P-001", final_screenshot="s/step_000.png")
        step = self.events(session)
        body = self.client.get(self.url()).json()
        self.assertNotIn(str(self.root), json.dumps(body))  # 서버 파일 경로가 응답에 없다
        by_type = {e["type"]: e for e in body["items"]}
        self.assertEqual(by_type["observation.created"]["payload"]["screenshot_url"],
                         f"/api/v1/observations/{step.pk}/screenshot")
        self.assertEqual(by_type["observation.created"]["payload"]["text"], "첫 화면")
        self.assertEqual(by_type["session.finished"]["payload"]["screenshot_url"],
                         f"/api/v1/sessions/{session.pk}/final-screenshot")
        self.assertEqual(by_type["session.finished"]["payload"]["issues"], [{"category": "input_burden"}])
        self.assertNotIn("artifact", by_type["friction.detected"]["payload"])
        self.assertEqual(by_type["friction.detected"]["payload"]["frustration_score"], 70)
        self.assertEqual(by_type["session.started"]["session_key"], session.session_key)

    def test_after_cursor_returns_only_newer_events_in_order(self):
        self.events(self.session("P-001"))
        everything = self.client.get(self.url()).json()
        self.assertEqual([e["sequence"] for e in everything["items"]], [1, 2, 3, 4])
        self.assertEqual((everything["has_more"], everything["next_after"]), (False, everything["items"][-1]["id"]))
        second_id = everything["items"][1]["id"]
        newer = self.client.get(self.url(after=second_id)).json()
        self.assertEqual([e["sequence"] for e in newer["items"]], [3, 4])
        nothing = self.client.get(self.url(after=everything["next_after"])).json()
        self.assertEqual((nothing["items"], nothing["next_after"], nothing["has_more"]),
                         ([], everything["next_after"], False))

    def test_limit_pages_through_events(self):
        self.events(self.session("P-001"))
        first = self.client.get(self.url(limit=3)).json()
        self.assertEqual((len(first["items"]), first["has_more"]), (3, True))
        rest = self.client.get(self.url(limit=3, after=first["next_after"])).json()
        self.assertEqual(([e["sequence"] for e in rest["items"]], rest["has_more"]), ([4], False))

    def test_only_this_runs_events(self):
        self.events(self.session("P-001"))
        other_run = make_run(self.project, self.user, self.snapshot, sequence_no=2)
        make_event(make_task_run(other_run), sequence=1)
        self.assertEqual(len(self.client.get(self.url()).json()["items"]), 4)

    def test_invalid_params_and_access(self):
        for params in ({"after": -1}, {"after": "x"}, {"limit": 0}, {"limit": 501}):
            self.assertEqual(self.client.get(self.url(**params)).status_code, 422, params)
        self.assertEqual(self.stranger.get(self.url()).status_code, 403)

    def test_query_count_does_not_grow_with_events(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
            return len(ctx)

        session = self.session("P-001")
        for n in range(1, 4):
            RunEvent.objects.create(run=self.run, task_run=self.task_run, session=session, event_id=f"q{n}", sequence=n,
                                    type="observation.created", occurred_at=timezone.now(),
                                    payload={"step_id": f"step_{n:03d}"})
        few = count()
        for n in range(4, 40):
            RunEvent.objects.create(run=self.run, task_run=self.task_run, session=session, event_id=f"q{n}", sequence=n,
                                    type="observation.created", occurred_at=timezone.now(),
                                    payload={"step_id": f"step_{n:03d}"})
        self.assertEqual(count(), few)  # 이벤트가 늘어도 쿼리 수는 그대로


class CancelApiTests(ApiBase):
    def url(self, run=None):
        return f"/api/v1/runs/{(run or self.run).id}/cancel"

    def test_running_run_gets_a_cancel_request(self):
        res = self.client.post(self.url())
        self.assertEqual(res.status_code, 202)
        body = res.json()
        self.assertEqual((body["status"], body["cancel_requested"]), ("running", True))

    def test_queued_run_is_cancelled_right_away(self):
        queued = make_run(self.project, self.user, self.snapshot, sequence_no=2, status=Run.Status.QUEUED)
        body = self.client.post(self.url(queued)).json()
        self.assertEqual((body["status"], body["phase"]), ("cancelled", "cancelled"))

    def test_finished_run_is_409_and_stranger_is_403(self):
        Run.objects.filter(pk=self.run.pk).update(status="completed")
        res = self.client.post(self.url())
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "RUN_NOT_ACTIVE"))
        self.assertEqual(self.stranger.post(self.url()).status_code, 403)
        self.assertEqual(APIClient().post(self.url()).status_code, 401)


class ScreenshotTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.mine = self.session("P-001", final_screenshot=self.png("run/final.png"))
        self.observation = make_observation(self.mine, 0)
        type(self.observation).objects.filter(pk=self.observation.pk).update(artifact=self.png("run/step_000.png"))
        self.url = f"/api/v1/observations/{self.observation.pk}/screenshot"

    def test_owner_gets_the_image_with_private_cache_headers(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "image/png")
        self.assertEqual(b"".join(res.streaming_content), PNG_BYTES)
        self.assertIn("private", res["Cache-Control"])
        res.close()
        final = self.client.get(f"/api/v1/sessions/{self.mine.pk}/final-screenshot")
        self.assertEqual((final.status_code, final["Content-Type"]), (200, "image/png"))
        final.close()

    def test_browser_image_accept_header_is_not_a_406(self):
        res = self.client.get(self.url, HTTP_ACCEPT="image/png")
        self.assertEqual(res.status_code, 200)
        res.close()

    def test_other_users_and_anonymous_cannot_see_it(self):
        self.assertEqual(self.stranger.get(self.url).status_code, 403)
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        self.assertEqual(self.stranger.get(f"/api/v1/sessions/{self.mine.pk}/final-screenshot").status_code, 403)

    def test_unknown_ids_and_missing_files_are_404(self):
        self.assertEqual(self.client.get("/api/v1/observations/999999/screenshot").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/sessions/999999/final-screenshot").status_code, 404)
        (self.root / "run" / "step_000.png").unlink()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_observation_without_artifact_is_404(self):
        empty = make_observation(self.mine, 1)
        self.assertEqual(self.client.get(f"/api/v1/observations/{empty.pk}/screenshot").status_code, 404)

    def test_path_traversal_and_non_image_files_are_never_served(self):
        (self.root.parent / "secret.png").write_bytes(PNG_BYTES)
        (self.root / "notes.txt").write_text("비밀", encoding="utf-8")
        self.addCleanup((self.root.parent / "secret.png").unlink)
        for bad in ("../secret.png", "../../etc/passwd", "run/../../secret.png", "notes.txt", str(self.root.parent / "secret.png"),
                    "run"):
            type(self.observation).objects.filter(pk=self.observation.pk).update(artifact=bad)
            with self.subTest(artifact=bad):
                self.assertEqual(self.client.get(self.url).status_code, 404)


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class EndToEndTests(WizardMixin, TestCase):
    """HTTP 로 실행을 시작하고, 워커가 가짜 에이전트를 돌린 뒤, 조회·스크린샷까지 이어서 확인한다."""

    def test_start_run_work_poll_and_view_screenshots(self, _):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for override in (override_settings(ARTIFACT_ROOT=str(Path(tmp.name).resolve()),
                                           AGENT_COMMAND=[sys.executable, FAKE_AGENT], EXITPLAIN_WORKER_PROCESSES=3),):
            override.enable()
            self.addCleanup(override.disable)
        patcher = patch("runs.netguard.resolve_host", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

        snapshot_id = self.confirm()["plan_revision_id"]
        started = self.client.post("/api/v1/runs", {"plan_revision_id": snapshot_id}, format="json",
                                   HTTP_IDEMPOTENCY_KEY="click-1")
        self.assertEqual(started.status_code, 202, started.content)
        run_id = started.json()["run_id"]
        self.assertEqual(started.json()["status_url"], f"/api/v1/runs/{run_id}")
        again = self.client.post("/api/v1/runs", {"plan_revision_id": snapshot_id}, format="json",
                                 HTTP_IDEMPOTENCY_KEY="click-1")
        self.assertEqual((again.status_code, again.json()["run_id"], again["Idempotent-Replayed"]), (202, run_id, "true"))
        self.assertEqual(Run.objects.count(), 1)

        before = self.client.get(f"/api/v1/runs/{run_id}").json()
        self.assertEqual((before["status"], before["phase"]), ("queued", "preparing"))
        conflict = self.client.post("/api/v1/runs", {"plan_revision_id": snapshot_id}, format="json")
        self.assertEqual((conflict.status_code, conflict.json()["error"]["code"]), (409, "RUN_IN_PROGRESS"))

        Worker(tick=0.1, grace_seconds=30).run_once()

        after = self.client.get(f"/api/v1/runs/{run_id}").json()
        self.assertEqual((after["status"], after["phase"]), ("completed", "done"))
        self.assertEqual((after["progress"]["expected_sessions"], after["progress"]["finished_sessions"]), (120, 120))
        tiles = self.client.get(f"/api/v1/runs/{run_id}/sessions?limit=200").json()
        self.assertEqual((tiles["total"], len(tiles["items"])), (120, 120))
        shot = self.client.get(tiles["items"][0]["latest_observation"]["screenshot_url"])
        self.assertEqual((shot.status_code, shot["Content-Type"]), (200, "image/png"))
        shot.close()
        events = self.client.get(f"/api/v1/runs/{run_id}/events?limit=500").json()
        self.assertTrue(events["has_more"])  # 이벤트가 500개보다 많다
        self.assertNotIn(tmp.name.replace("\\", "\\\\"), json.dumps(events))
        runs = self.client.get(f"/api/v1/projects/{self.project.pk}/runs").json()
        self.assertEqual([r["id"] for r in runs["items"]], [run_id])


class TileStageFieldsTests(ApiBase):
    def test_tile_exposes_funnel_stages(self):
        self.session("P-001", status="success", terminal_stage="detail", stages_reached=["landing", "browse", "detail"])
        item = self.client.get(f"/api/v1/runs/{self.run.id}/sessions").json()["items"][0]
        self.assertEqual((item["terminal_stage"], item["stages_reached"]), ("detail", ["landing", "browse", "detail"]))
