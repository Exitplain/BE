import contextlib
import io
import json
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from runs import importer
from runs.models import Observation, RunEvent, Session, TaskRun

from . import fake_agent
from .factories import RunsFixtureMixin, make_persona, make_run, make_task_run


class ImporterBase(RunsFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        override = override_settings(ARTIFACT_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)
        self.second = make_persona(self.snapshot, "P-002")
        self.run = make_run(self.project, self.user, self.snapshot)
        self.task_run = make_task_run(self.run, "order", 1)

    def config(self, personas=("P-001", "P-002")):
        return {"run_id": self.task_run.agent_run_id, "target_url": "http://shop.local:8080/",
                "personas": [{"id": p, "context": "처음 방문한 사용자"} for p in personas]}

    def run_fake_agent(self, mode="ok", personas=("P-001", "P-002")):
        """가짜 에이전트를 같은 프로세스에서 실행해 결과 폴더를 만들고 TaskRun 에 연결한다."""
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                fake_agent.run(self.config(personas), str(self.root), mode)
            except SystemExit:
                pass
        (attempt,) = (self.root / self.task_run.agent_run_id).iterdir()
        self.task_run.output_dir = str(attempt)
        self.task_run.save(update_fields=["output_dir"])
        return attempt

    def events_path(self):
        return Path(self.task_run.output_dir) / "events.jsonl"


class ImportEventsTests(ImporterBase):
    def test_imports_sessions_observations_and_events(self):
        self.run_fake_agent()
        imported = importer.import_new_events(self.task_run)
        lines = self.events_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(imported, len(lines))
        self.assertEqual(RunEvent.objects.filter(run=self.run).count(), len(lines))
        sessions = {s.session_key: s for s in Session.objects.filter(task_run=self.task_run)}
        self.assertEqual(set(sessions), {"session_P-001", "session_P-002"})
        first, second = sessions["session_P-001"], sessions["session_P-002"]
        self.assertEqual((first.status, second.status), ("success", "simulated_dropout"))
        self.assertEqual((first.persona_snapshot, second.persona_snapshot), (self.persona, self.second))
        self.assertEqual((first.run, first.task_id, first.seed), (self.run, "order", 1))
        self.assertEqual((first.steps, first.model_calls, first.policy), (3, 0, "scripted_fixture"))
        self.assertEqual(first.max_frustration, 70)
        self.assertEqual(first.issues[0]["category"], "input_burden")
        self.assertIsNotNone(first.started_at)
        self.assertIsNotNone(first.finished_at)
        self.assertEqual(self.task_run.events_offset, self.events_path().stat().st_size)

    def test_observations_keep_order_url_and_relative_artifact_path(self):
        self.run_fake_agent()
        importer.import_new_events(self.task_run)
        session = Session.objects.get(session_key="session_P-001")
        observations = list(session.observations.all())
        self.assertEqual([o.step for o in observations], [0, 1, 2])
        self.assertEqual([o.url for o in observations], ["http://shop.local:8080/", "http://shop.local:8080/product",
                                                          "http://shop.local:8080/cart"])
        self.assertEqual(observations[1].required_fields, 7)
        self.assertEqual(observations[0].artifact.split("/")[-1], "step_000.png")
        self.assertFalse(Path(observations[0].artifact).is_absolute())
        self.assertTrue((self.root / observations[0].artifact).is_file())
        self.assertTrue((self.root / session.final_screenshot).is_file())

    def test_reading_twice_does_not_duplicate(self):
        self.run_fake_agent()
        importer.import_new_events(self.task_run)
        self.assertEqual(importer.import_new_events(self.task_run), 0)
        TaskRun.objects.filter(pk=self.task_run.pk).update(events_offset=0)
        self.task_run.refresh_from_db()
        self.assertEqual(importer.import_new_events(self.task_run), 0)  # 처음부터 다시 읽어도 event_id 로 걸러진다
        self.assertEqual(RunEvent.objects.count(), len(self.events_path().read_text(encoding="utf-8").splitlines()))
        self.assertEqual(Observation.objects.count(), 6)

    def test_incremental_import_ignores_a_line_still_being_written(self):
        self.run_fake_agent()
        raw = self.events_path().read_bytes()
        lines = raw.splitlines(keepends=True)
        half = len(lines) // 2
        self.events_path().write_bytes(b"".join(lines[:half]) + lines[half][:20])  # 마지막 줄이 쓰다 만 상태
        first = importer.import_new_events(self.task_run)
        self.assertEqual(first, half)
        self.assertEqual(self.task_run.events_offset, len(b"".join(lines[:half])))
        self.events_path().write_bytes(raw)  # 에이전트가 마저 씀
        self.assertEqual(importer.import_new_events(self.task_run), len(lines) - half)
        self.assertEqual(RunEvent.objects.count(), len(lines))
        self.assertEqual(Session.objects.get(session_key="session_P-002").status, "simulated_dropout")

    def test_no_file_yet_is_not_an_error(self):
        self.assertEqual(importer.import_new_events(self.task_run), 0)
        self.task_run.output_dir = str(self.root / "missing")
        self.assertEqual(importer.import_new_events(self.task_run), 0)

    def test_unknown_persona_is_reported_but_events_are_kept(self):
        self.run_fake_agent("nopersona", personas=("P-001",))
        importer.import_new_events(self.task_run)
        self.assertEqual(Session.objects.count(), 0)
        self.assertEqual(self.task_run.error_code, "UNKNOWN_PERSONA")
        self.assertGreater(RunEvent.objects.count(), 0)

    def test_corrupt_line_is_skipped_and_the_rest_is_imported(self):
        self.run_fake_agent()
        lines = self.events_path().read_text(encoding="utf-8").splitlines()
        self.events_path().write_text("\n".join([lines[0], "{이건 깨진 줄", *lines[1:]]) + "\n", encoding="utf-8")
        imported = importer.import_new_events(self.task_run)
        self.assertEqual(imported, len(lines))
        self.assertEqual(self.task_run.error_code, "BAD_EVENT_LINE")
        self.assertEqual(Session.objects.filter(status="success").count(), 1)

    def test_unknown_event_types_are_stored_without_breaking(self):
        self.run_fake_agent()
        extra = {"event_id": "extra1", "type": "future.something", "session_id": "session_P-001", "sequence": 999,
                 "occurred_at": "2026-11-14T00:00:00+00:00", "payload": {"x": 1}}
        with self.events_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(extra) + "\n")
        importer.import_new_events(self.task_run)
        self.assertTrue(RunEvent.objects.filter(event_id="extra1", type="future.something").exists())

    def test_relative_artifact_outside_root_is_dropped(self):
        self.assertEqual(importer.relative_artifact(""), "")
        self.assertEqual(importer.relative_artifact(str(self.root.parent / "elsewhere.png")), "")
        self.assertEqual(importer.relative_artifact(str(self.root / "a" / "b.png")), "a/b.png")


class FinalizeTests(ImporterBase):
    def test_normal_finish_stores_summary_and_marks_succeeded(self):
        self.run_fake_agent()
        importer.import_new_events(self.task_run)
        importer.finalize_task_run(self.task_run, exit_code=0)
        self.task_run.refresh_from_db()
        self.assertEqual(self.task_run.status, TaskRun.Status.SUCCEEDED)
        self.assertEqual(self.task_run.summary["metrics"]["started"], 2)
        self.assertEqual((self.task_run.exit_code, self.task_run.error_code), (0, ""))
        self.assertIsNotNone(self.task_run.finished_at)

    def test_finalize_imports_whatever_was_left_unread(self):
        self.run_fake_agent()
        importer.finalize_task_run(self.task_run, exit_code=0)  # 중간에 한 번도 읽지 않았어도 마무리하며 모두 가져온다
        self.assertEqual(Session.objects.filter(status__in=["success", "simulated_dropout"]).count(), 2)

    def test_exit_code_1_with_summary_is_still_a_normal_finish(self):
        """agent.cli 는 기술 실패(site_failure 등)가 있으면 1 로 끝난다. 크래시가 아니다."""
        self.run_fake_agent("technical")
        importer.finalize_task_run(self.task_run, exit_code=1)
        self.task_run.refresh_from_db()
        self.assertEqual(self.task_run.status, TaskRun.Status.SUCCEEDED)
        self.assertEqual(self.task_run.exit_code, 1)
        self.assertEqual(Session.objects.get(session_key="session_P-002").status, "site_failure")

    def test_crash_without_summary_fails_and_closes_dangling_sessions(self):
        self.run_fake_agent("crash")
        importer.finalize_task_run(self.task_run, exit_code=3)
        self.task_run.refresh_from_db()
        self.assertEqual((self.task_run.status, self.task_run.error_code), (TaskRun.Status.FAILED, "AGENT_CRASHED"))
        self.assertIn("3", self.task_run.error_message)
        session = Session.objects.get(session_key="session_P-001")
        self.assertEqual(session.status, "agent_failure")
        self.assertEqual(session.observations.count(), 1)  # 죽기 전까지 본 화면은 보존된다

    def test_cancel_marks_task_run_and_running_sessions_cancelled(self):
        self.run_fake_agent("crash")
        importer.finalize_task_run(self.task_run, exit_code=-15, cancelled=True)
        self.task_run.refresh_from_db()
        self.assertEqual(self.task_run.status, TaskRun.Status.CANCELLED)
        self.assertEqual(Session.objects.get(session_key="session_P-001").status, "cancelled")

    def test_process_that_never_wrote_anything_fails_cleanly(self):
        importer.finalize_task_run(self.task_run, exit_code=2, error_code="SPAWN_FAILED", error_message="시작하지 못함")
        self.task_run.refresh_from_db()
        self.assertEqual((self.task_run.status, self.task_run.error_code), (TaskRun.Status.FAILED, "SPAWN_FAILED"))

    def test_broken_summary_is_reported(self):
        attempt = self.run_fake_agent()
        (attempt / "summary.json").write_text("{깨짐", encoding="utf-8")
        importer.finalize_task_run(self.task_run, exit_code=0)
        self.task_run.refresh_from_db()
        self.assertEqual((self.task_run.status, self.task_run.error_code), (TaskRun.Status.FAILED, "BAD_SUMMARY"))
