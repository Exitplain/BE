"""runs 테스트용 준비 코드. 모델을 직접 만들어서 위저드를 거치지 않는다."""
from datetime import timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.utils import timezone

from discoveries.models import Discovery
from personas.models import PersonaSnapshot
from plans.models import PlanSnapshot, TestPlan
from projects.models import Project
from runs.models import Observation, Run, RunEvent, Session, TaskRun

_event_seq = count(1)


def make_user(name="owner@example.com"):
    return get_user_model().objects.create_user(name, password="pw")


def make_project(owner, name="반찬로드", **extra):
    return Project.objects.create(owner=owner, name=name, entry_url="http://shop.local:8080/", **extra)


def make_snapshot(project, user, revision=1):
    discovery = Discovery.objects.create(project=project, created_by=user, entry_url=project.entry_url,
                                         variant="problem", expires_at=timezone.now() + timedelta(minutes=15))
    plan = TestPlan.objects.create(project=project, discovery=discovery, created_by=user)
    return PlanSnapshot.objects.create(plan=plan, plan_revision=revision, data={}, persona_generator="fixture-test",
                                       confirmed_by=user)


def make_persona(snapshot, code="P-001"):
    return PersonaSnapshot.objects.create(plan_snapshot=snapshot, code=code, segment_id="segment_1",
                                          segment_name="빠른 구매형", context="처음 방문한 사용자", generator="fixture-test")


def make_run(project, user, snapshot, sequence_no=1, **extra):
    return Run.objects.create(project=project, created_by=user, plan_snapshot=snapshot, sequence_no=sequence_no, **extra)


def make_task_run(run, task_key="order", seed=1):
    return TaskRun.objects.create(run=run, task_key=task_key, seed=seed,
                                  agent_run_id=f"{run.id}_{task_key}_s{seed}")


def make_session(task_run, persona, session_key=None, **extra):
    return Session.objects.create(run=task_run.run, task_run=task_run, task_id=task_run.task_key, seed=task_run.seed,
                                  persona_snapshot=persona, session_key=session_key or f"session_{persona.code}", **extra)


def make_observation(session, step=0, url="http://shop.local:8080/"):
    return Observation.objects.create(session=session, step=step, step_id=f"step_{step:03d}", url=url,
                                      occurred_at=timezone.now())


def make_event(task_run, session=None, sequence=None, type="session.started"):
    sequence = sequence or next(_event_seq)
    return RunEvent.objects.create(run=task_run.run, task_run=task_run, session=session,
                                   event_id=f"evt{task_run.pk}{sequence:08d}", sequence=sequence, type=type,
                                   occurred_at=timezone.now())


class RunsFixtureMixin:
    """setUp 에서 사용자·프로젝트·확정 스냅샷·페르소나 1명을 만든다."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(self.user)
        self.snapshot = make_snapshot(self.project, self.user)
        self.persona = make_persona(self.snapshot)
