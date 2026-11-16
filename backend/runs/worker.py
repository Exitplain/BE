"""실행 워커: queued 실행을 가져와 에이전트를 서브프로세스로 돌리고, 진행 중에 events.jsonl 을 DB 로 옮긴다.

- 에이전트(`python -m agent.cli`)는 수정하지 않고 호출만 한다. 종료 코드가 아니라 summary.json 유무로 정상 종료를 판단한다.
- 취소·시간 초과·워커 종료 때는 프로세스 트리 전체를 끝낸다 (Chromium 이 남아 메모리를 먹지 않게).
- 워커가 죽어 멈춘 실행은 시작할 때 `recover_stale` 로 정리한다.
"""
import json
import logging
import math
import os
import platform
import signal
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from . import importer
from .models import Run, TaskRun

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5
STALE_SECONDS = 120
TERMINATE_GRACE_SECONDS = 5


def repo_root():
    return Path(settings.BASE_DIR).resolve().parent  # agent 패키지가 있는 곳


def agent_command():
    configured = getattr(settings, "AGENT_COMMAND", None)
    return list(configured) if configured else [settings.AGENT_PYTHON, "-m", "agent.cli"]


def terminate_tree(proc, grace=TERMINATE_GRACE_SECONDS):
    """프로세스와 그 자식(Playwright·Chromium)을 모두 끝낸다."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        proc.wait(timeout=grace)


class _Job:
    def __init__(self, task_run, proc, log, input_path, deadline):
        self.task_run, self.proc, self.log, self.input_path, self.deadline = task_run, proc, log, input_path, deadline
        self.error_code = ""
        self.error_message = ""


class Worker:
    def __init__(self, *, tick=0.5, grace_seconds=120, tick_hook=None):
        self.ref = f"{platform.node() or 'worker'}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.tick = tick
        self.grace_seconds = grace_seconds  # 세션 시간 제한에 더하는 여유 (프로세스 시작·마무리)
        self.tick_hook = tick_hook  # 테스트용: 매 틱마다 hook(worker, run) 호출

    # ----- 가져오기 -----
    def claim_next_run(self):
        now = timezone.now()
        for candidate in Run.objects.filter(status=Run.Status.QUEUED).order_by("created_at")[:5]:
            claimed = Run.objects.filter(pk=candidate.pk, status=Run.Status.QUEUED).update(
                status=Run.Status.RUNNING, worker_ref=self.ref, started_at=now, heartbeat_at=now)
            if claimed:
                return Run.objects.get(pk=candidate.pk)
        return None

    def recover_stale(self, stale_seconds=STALE_SECONDS):
        """워커가 죽어 하트비트가 끊긴 실행을 정리한다. 이미 가져온 세션·이벤트는 보존한다."""
        limit = timezone.now() - timedelta(seconds=stale_seconds)
        recovered = []
        stale = Run.objects.filter(status__in=[Run.Status.RUNNING, Run.Status.FINALIZING]).filter(
            Q(heartbeat_at__lt=limit) | Q(heartbeat_at__isnull=True)).exclude(worker_ref=self.ref)
        for run in stale:
            for task_run in run.task_runs.filter(status__in=[TaskRun.Status.QUEUED, TaskRun.Status.RUNNING]):
                importer.finalize_task_run(task_run, error_code="WORKER_LOST", error_message="워커가 중단되어 실행이 끊겼습니다.")
            self._finish_run(run, error_code="WORKER_LOST")
            recovered.append(run.pk)
        return recovered

    # ----- 실행 -----
    def execute(self, run):
        pending = list(run.task_runs.filter(status=TaskRun.Status.QUEUED).order_by("task_key", "seed"))
        jobs = []
        capacity = max(1, settings.EXITPLAIN_WORKER_PROCESSES)
        last_beat = 0.0
        try:
            while pending or jobs:
                close_old_connections()
                cancelled = Run.objects.filter(pk=run.pk, cancel_requested_at__isnull=False).exists()
                if cancelled:
                    self._cancel_all(run, jobs, pending)
                    break
                while pending and len(jobs) < capacity:
                    job = self._start(pending.pop(0))
                    if job is not None:
                        jobs.append(job)
                for job in list(jobs):
                    if self._poll(job):
                        jobs.remove(job)
                if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                    Run.objects.filter(pk=run.pk).update(heartbeat_at=timezone.now())
                    last_beat = time.monotonic()
                if self.tick_hook:
                    self.tick_hook(self, run)
                if pending or jobs:
                    time.sleep(self.tick)
        except BaseException:  # 워커 종료(Ctrl+C·SIGTERM) 등. 남은 프로세스를 정리하고 다시 올린다
            for job in jobs:
                terminate_tree(job.proc)
                job.log.close()
                importer.finalize_task_run(job.task_run, exit_code=job.proc.returncode, error_code="WORKER_STOPPED",
                                           error_message="워커가 종료되어 실행이 중단되었습니다.")
            for task_run in pending:
                importer.finalize_task_run(task_run, error_code="WORKER_STOPPED", error_message="워커가 종료되었습니다.")
            self._finish_run(run, error_code="WORKER_STOPPED")
            raise
        return self._finish_run(run)

    def _start(self, task_run):
        root = importer.artifact_root()
        inputs_dir, logs_dir = root / "_inputs", root / "_logs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        input_path = inputs_dir / f"{task_run.agent_run_id}.json"
        input_path.write_text(json.dumps(task_run.input_json, ensure_ascii=False), encoding="utf-8")
        command = [*agent_command(), "--input", str(input_path), "--output", str(root), "--policy", settings.AGENT_POLICY]
        if settings.AGENT_MODEL:
            command += ["--model", settings.AGENT_MODEL]
        log = open(logs_dir / f"{task_run.agent_run_id}.log", "ab")
        popen_args = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            proc = subprocess.Popen(command, cwd=repo_root(), stdout=log, stderr=subprocess.STDOUT, **popen_args)
        except OSError as exc:
            log.close()
            importer.finalize_task_run(task_run, error_code="SPAWN_FAILED", error_message=f"에이전트를 시작하지 못했습니다: {exc}")
            return None
        data = task_run.input_json
        waves = math.ceil(len(data.get("personas", [])) / max(1, int(data.get("concurrency", 1))))
        deadline = time.monotonic() + float(data.get("timeout_seconds", 120)) * max(1, waves) + self.grace_seconds
        task_run.status, task_run.pid, task_run.started_at = TaskRun.Status.RUNNING, proc.pid, timezone.now()
        task_run.save(update_fields=["status", "pid", "started_at"])
        return _Job(task_run, proc, log, input_path, deadline)

    def _poll(self, job):
        """진행 중인 프로세스를 한 번 살핀다. 끝났으면 마무리하고 True."""
        task_run = job.task_run
        if not task_run.output_dir:
            found = sorted(p for p in (importer.artifact_root() / task_run.agent_run_id).glob("*") if p.is_dir())
            if found:
                task_run.output_dir = str(found[-1])
                task_run.agent_attempt_id = found[-1].name
                task_run.save(update_fields=["output_dir", "agent_attempt_id"])
        importer.import_new_events(task_run)
        returncode = job.proc.poll()
        if returncode is None and time.monotonic() > job.deadline:
            terminate_tree(job.proc)
            returncode = job.proc.returncode
            job.error_code, job.error_message = "TIMEOUT", "에이전트가 제한 시간 안에 끝나지 않아 중단했습니다."
        if returncode is None:
            return False
        self._close(job)
        if job.error_code:
            summary_missing = not (Path(task_run.output_dir) / "summary.json").is_file() if task_run.output_dir else True
            importer.finalize_task_run(task_run, exit_code=returncode,
                                       error_code=job.error_code if summary_missing else "",
                                       error_message=job.error_message if summary_missing else "")
        else:
            importer.finalize_task_run(task_run, exit_code=returncode)
        return True

    def _close(self, job):
        job.log.close()
        job.input_path.unlink(missing_ok=True)

    def _cancel_all(self, run, jobs, pending):
        for job in jobs:
            terminate_tree(job.proc)
            self._close(job)
            importer.finalize_task_run(job.task_run, exit_code=job.proc.returncode, cancelled=True)
        now = timezone.now()
        for task_run in pending:
            task_run.status, task_run.finished_at = TaskRun.Status.CANCELLED, now
            task_run.save(update_fields=["status", "finished_at"])
        jobs.clear()
        pending.clear()

    # ----- 마무리 -----
    def _finish_run(self, run, error_code=""):
        run = Run.objects.get(pk=run.pk)
        statuses = set(run.task_runs.values_list("status", flat=True))
        if run.cancel_requested_at is not None and TaskRun.Status.CANCELLED in statuses:
            final, error = Run.Status.CANCELLED, ""
        elif statuses and statuses <= {TaskRun.Status.SUCCEEDED}:
            final, error = Run.Status.COMPLETED, ""
        elif TaskRun.Status.SUCCEEDED in statuses:
            final, error = Run.Status.PARTIALLY_COMPLETED, error_code or "SOME_TASKS_FAILED"
        else:
            failed = run.task_runs.exclude(error_code="").order_by("id").first()
            final, error = Run.Status.FAILED, error_code or (failed.error_code if failed else "ALL_TASKS_FAILED")
        if final in (Run.Status.COMPLETED, Run.Status.PARTIALLY_COMPLETED):
            Run.objects.filter(pk=run.pk).update(status=Run.Status.FINALIZING, heartbeat_at=timezone.now())
            hook_error = self._call_hook(run)
            if hook_error:
                final, error = Run.Status.PARTIALLY_COMPLETED, "FINALIZE_FAILED"
        Run.objects.filter(pk=run.pk).update(status=final, error_code=error, finished_at=timezone.now(),
                                             error_message=("" if not error else self._message(error)))
        return Run.objects.get(pk=run.pk)

    @staticmethod
    def _message(code):
        return {"WORKER_LOST": "워커가 중단되어 실행이 끊겼습니다.", "WORKER_STOPPED": "워커가 종료되어 실행이 중단되었습니다.",
                "FINALIZE_FAILED": "결과 집계에 실패했습니다.", "SOME_TASKS_FAILED": "일부 과업 실행이 실패했습니다.",
                "ALL_TASKS_FAILED": "모든 과업 실행이 실패했습니다."}.get(code, f"실행이 실패했습니다 ({code}).")

    @staticmethod
    def _call_hook(run):
        """모든 TaskRun 이 끝난 뒤 B의 집계 함수 등을 호출한다. settings.EXITPLAIN_FINALIZE_HOOK = "앱.모듈:함수"."""
        path = settings.EXITPLAIN_FINALIZE_HOOK
        if not path:
            return None
        try:
            import_string(path.replace(":", "."))(Run.objects.get(pk=run.pk))
        except Exception:
            logger.exception("실행 마무리 훅 실패 (run=%s)", run.pk)
            return "FINALIZE_FAILED"
        return None

    # ----- 반복 -----
    def run_once(self):
        run = self.claim_next_run()
        return self.execute(run) if run else None

    def run_forever(self, poll_interval=2.0):
        self.recover_stale()
        while True:
            if self.run_once() is None:
                time.sleep(poll_interval)
