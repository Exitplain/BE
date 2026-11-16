import os
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from runs import services
from runs.models import Observation, Run, RunEvent, Session, TaskRun
from runs.worker import Worker

FAKE_AGENT = str(Path(__file__).with_name("fake_agent.py"))
CALLS = []


def hook_ok(run):
    CALLS.append(("ok", run.status))


def hook_fail(run):
    raise RuntimeError("집계 실패")


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class WorkerBase(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 클래스 데코레이터는 하위 클래스의 테스트에 적용되지 않으므로 setUp 에서 시작한다
        for target, kwargs in (("discoveries.services.run_discovery", {"side_effect": fake_discovery}),
                               ("runs.netguard.resolve_host", {"return_value": []})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        override = override_settings(ARTIFACT_ROOT=str(Path(self.tmp.name).resolve()),
                                     AGENT_COMMAND=[sys.executable, FAKE_AGENT], EXITPLAIN_WORKER_PROCESSES=3)
        override.enable()
        self.addCleanup(override.disable)
        CALLS.clear()

    def new_run(self):
        snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        return services.create_run(self.project, snapshot, self.user)[0]

    def worker(self, **kwargs):
        return Worker(tick=0.1, grace_seconds=kwargs.pop("grace_seconds", 30), **kwargs)

    def agent_env(self, **env):
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)


class CompletionTests(WorkerBase):
    def test_runs_every_task_and_imports_all_results(self):
        run = self.new_run()
        finished = self.worker().run_once()
        self.assertEqual(finished.pk, run.pk)
        self.assertEqual(finished.status, Run.Status.COMPLETED)
        self.assertEqual((finished.error_code, finished.cancel_requested_at), ("", None))
        self.assertIsNotNone(finished.started_at)
        self.assertIsNotNone(finished.finished_at)
        self.assertTrue(finished.worker_ref)
        task_runs = list(finished.task_runs.all())
        self.assertEqual({t.status for t in task_runs}, {"succeeded"})
        for task_run in task_runs:
            self.assertEqual((task_run.exit_code, task_run.error_code), (0, ""))
            self.assertTrue(task_run.agent_attempt_id)
            self.assertIsNotNone(task_run.summary)
            self.assertGreater(task_run.events_offset, 0)
        self.assertEqual(Session.objects.filter(run=run).count(), 120)  # 페르소나 20 × (과업 2 × 시드 3)
        self.assertEqual(Session.objects.filter(run=run, status="running").count(), 0)
        self.assertEqual(Observation.objects.count(), 120 * 3)
        self.assertGreater(RunEvent.objects.filter(run=run).count(), 120 * 3)

    def test_screenshots_exist_under_artifact_root_and_input_files_are_cleaned_up(self):
        self.new_run()
        self.worker().run_once()
        root = Path(self.tmp.name).resolve()
        observation = Observation.objects.first()
        self.assertTrue((root / observation.artifact).is_file())
        self.assertEqual(list((root / "_inputs").glob("*")), [])
        self.assertTrue(any((root / "_logs").glob("*.log")))

    def test_nothing_to_do_returns_none_and_finished_runs_are_not_picked_again(self):
        self.assertIsNone(self.worker().run_once())
        self.new_run()
        self.worker().run_once()
        self.assertIsNone(self.worker().run_once())

    def test_oldest_queued_run_goes_first(self):
        first = self.new_run()
        Run.objects.filter(pk=first.pk).update(status=Run.Status.COMPLETED)
        second = self.new_run()
        claimed = self.worker().claim_next_run()
        self.assertEqual(claimed.pk, second.pk)
        self.assertEqual(claimed.status, Run.Status.RUNNING)

    def test_technical_failures_still_count_as_a_normal_finish(self):
        """agent.cli 는 기술 실패가 있으면 종료 코드 1 이다. 크래시가 아니므로 실행은 완료로 본다."""
        self.agent_env(FAKE_AGENT_MODE="technical")
        run = self.new_run()
        finished = self.worker().run_once()
        self.assertEqual(finished.status, Run.Status.COMPLETED)
        self.assertEqual({t.exit_code for t in finished.task_runs.all()}, {1})
        self.assertEqual(Session.objects.filter(run=run, status="site_failure").count(), 6)


class FailureTests(WorkerBase):
    def test_all_tasks_crashing_fails_the_run_but_keeps_what_was_seen(self):
        self.agent_env(FAKE_AGENT_MODE="crash")
        run = self.new_run()
        finished = self.worker().run_once()
        self.assertEqual((finished.status, finished.error_code), (Run.Status.FAILED, "AGENT_CRASHED"))
        self.assertEqual({t.status for t in finished.task_runs.all()}, {"failed"})
        self.assertEqual(Session.objects.filter(run=run, status="agent_failure").count(), 6)  # 과업마다 첫 세션만 시작했다
        self.assertEqual(Observation.objects.filter(session__run=run).count(), 6)  # 죽기 전까지 본 화면은 보존

    def test_some_tasks_failing_gives_partially_completed(self):
        self.agent_env(FAKE_AGENT_CRASH_MATCH="_coupon_")
        finished = (self.new_run(), self.worker().run_once())[1]
        self.assertEqual((finished.status, finished.error_code), (Run.Status.PARTIALLY_COMPLETED, "SOME_TASKS_FAILED"))
        statuses = {(t.task_key, t.status) for t in finished.task_runs.all()}
        self.assertEqual(statuses, {("order", "succeeded"), ("coupon", "failed")})

    def test_agent_that_cannot_be_started_fails_cleanly(self):
        run = self.new_run()
        with override_settings(AGENT_COMMAND=["/definitely/not/here/agent"]):
            finished = self.worker().run_once()
        self.assertEqual((finished.status, finished.error_code), (Run.Status.FAILED, "SPAWN_FAILED"))
        self.assertEqual({t.error_code for t in run.task_runs.all()}, {"SPAWN_FAILED"})

    def test_agent_that_never_finishes_is_killed_at_the_deadline(self):
        self.agent_env(FAKE_AGENT_MODE="slow", FAKE_AGENT_SLEEP="60")
        run = self.new_run()
        for task_run in run.task_runs.all():  # 페르소나 1명·시간 제한 1초로 줄인다
            task_run.input_json = {**task_run.input_json, "personas": task_run.input_json["personas"][:1],
                                   "timeout_seconds": 1, "concurrency": 1}
            task_run.save()
        started = time.monotonic()
        finished = self.worker(grace_seconds=0).run_once()
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual((finished.status, finished.error_code), (Run.Status.FAILED, "TIMEOUT"))
        for task_run in finished.task_runs.all():
            self.assertFalse(pid_alive(task_run.pid), "시간 초과 뒤에도 에이전트 프로세스가 남아 있다")


class CancelTests(WorkerBase):
    def test_cancel_stops_processes_marks_everything_cancelled_and_shows_progress_before(self):
        self.agent_env(FAKE_AGENT_MODE="slow", FAKE_AGENT_SLEEP="60")
        run = self.new_run()
        seen = {}

        def tick_hook(worker, current):
            if "sessions_while_running" not in seen and Session.objects.filter(run=current).exists():
                seen["sessions_while_running"] = Session.objects.filter(run=current).count()
                seen["pids"] = list(TaskRun.objects.filter(run=current, status="running").values_list("pid", flat=True))
                services.request_cancel(current)  # 사용자가 취소를 누른 것과 같다

        started = time.monotonic()
        finished = self.worker(tick_hook=tick_hook).run_once()
        self.assertLess(time.monotonic() - started, 30)  # 60초 기다리지 않고 끝났다
        self.assertGreater(seen["sessions_while_running"], 0)  # 실행 중에도 세션이 DB 에 반영됐다 (폴링용)
        self.assertEqual(finished.status, Run.Status.CANCELLED)
        self.assertEqual({t.status for t in finished.task_runs.all()}, {"cancelled"})
        self.assertEqual(Session.objects.filter(run=run).exclude(status="cancelled").count(), 0)
        for pid in seen["pids"]:
            self.assertFalse(pid_alive(pid), f"취소 뒤에도 에이전트 프로세스 {pid} 가 남아 있다")

    def test_cancelled_queued_run_is_never_picked_up(self):
        run = self.new_run()
        services.request_cancel(run)
        self.assertIsNone(self.worker().run_once())


class RecoveryTests(WorkerBase):
    def make_stuck(self, heartbeat_age):
        run = self.new_run()
        Run.objects.filter(pk=run.pk).update(status=Run.Status.RUNNING, worker_ref="dead-worker:1:abc",
                                             heartbeat_at=timezone.now() - timedelta(seconds=heartbeat_age))
        run.task_runs.update(status=TaskRun.Status.RUNNING)
        return run

    def test_run_with_old_heartbeat_is_closed_as_worker_lost(self):
        run = self.make_stuck(heartbeat_age=600)
        self.assertEqual(self.worker().recover_stale(), [run.pk])
        run.refresh_from_db()
        self.assertEqual((run.status, run.error_code), (Run.Status.FAILED, "WORKER_LOST"))
        self.assertEqual({t.error_code for t in run.task_runs.all()}, {"WORKER_LOST"})
        self.assertIsNotNone(run.finished_at)

    def test_run_with_recent_heartbeat_is_left_alone(self):
        run = self.make_stuck(heartbeat_age=10)
        self.assertEqual(self.worker().recover_stale(), [])
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.RUNNING)

    def test_own_run_is_not_recovered(self):
        run = self.make_stuck(heartbeat_age=600)
        worker = self.worker()
        Run.objects.filter(pk=run.pk).update(worker_ref=worker.ref)
        self.assertEqual(worker.recover_stale(), [])

    def test_interrupting_the_worker_cleans_up_processes_and_closes_the_run(self):
        self.agent_env(FAKE_AGENT_MODE="slow", FAKE_AGENT_SLEEP="60")
        run = self.new_run()
        pids = []

        def interrupt(worker, current):
            if TaskRun.objects.filter(run=current, status="running", pid__isnull=False).exists():
                pids.extend(TaskRun.objects.filter(run=current, status="running").values_list("pid", flat=True))
                raise KeyboardInterrupt  # systemd stop(SIGTERM)과 같은 경로

        with self.assertRaises(KeyboardInterrupt):
            self.worker(tick_hook=interrupt).run_once()
        run.refresh_from_db()
        self.assertEqual((run.status, run.error_code), (Run.Status.FAILED, "WORKER_STOPPED"))
        self.assertTrue(pids)
        for pid in pids:
            self.assertFalse(pid_alive(pid))


class FinalizeHookTests(WorkerBase):
    def test_hook_is_called_after_all_tasks_with_the_run(self):
        self.new_run()
        with override_settings(EXITPLAIN_FINALIZE_HOOK="runs.tests.test_worker:hook_ok"):
            finished = self.worker().run_once()
        self.assertEqual(finished.status, Run.Status.COMPLETED)
        self.assertEqual(CALLS, [("ok", Run.Status.FINALIZING)])  # 훅이 불릴 때 상태는 집계 중이다

    def test_failing_hook_downgrades_to_partially_completed_but_keeps_results(self):
        run = self.new_run()
        with override_settings(EXITPLAIN_FINALIZE_HOOK="runs.tests.test_worker:hook_fail"):
            finished = self.worker().run_once()
        self.assertEqual((finished.status, finished.error_code), (Run.Status.PARTIALLY_COMPLETED, "FINALIZE_FAILED"))
        self.assertEqual(Session.objects.filter(run=run).count(), 120)

    def test_hook_is_skipped_when_the_run_failed_completely(self):
        self.agent_env(FAKE_AGENT_MODE="crash")
        self.new_run()
        with override_settings(EXITPLAIN_FINALIZE_HOOK="runs.tests.test_worker:hook_ok"):
            self.worker().run_once()
        self.assertEqual(CALLS, [])
