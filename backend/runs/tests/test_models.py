from typing import get_args

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from agent.schemas import Terminal
from runs.models import Observation, Run, RunEvent, Session, TaskRun

from .factories import (RunsFixtureMixin, make_event, make_observation, make_persona, make_project, make_run,
                        make_session, make_snapshot, make_task_run, make_user)


class RunConstraintTests(RunsFixtureMixin, TestCase):
    def test_defaults(self):
        run = make_run(self.project, self.user, self.snapshot)
        self.assertEqual(run.status, Run.Status.QUEUED)
        self.assertEqual(run.kind, Run.Kind.PRETEST)
        self.assertIsNone(run.cancel_requested_at)
        self.assertTrue(run.is_active)

    def test_sequence_no_is_unique_per_project(self):
        make_run(self.project, self.user, self.snapshot, sequence_no=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_run(self.project, self.user, self.snapshot, sequence_no=1)

    def test_same_sequence_no_allowed_in_other_project(self):
        other = make_project(self.user, name="다른 사이트")
        make_run(self.project, self.user, self.snapshot, sequence_no=1)
        make_run(other, self.user, make_snapshot(other, self.user), sequence_no=1)
        self.assertEqual(Run.objects.filter(sequence_no=1).count(), 2)

    def test_idempotency_key_unique_per_project_but_null_repeats(self):
        make_run(self.project, self.user, self.snapshot, sequence_no=1, idempotency_key="k1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_run(self.project, self.user, self.snapshot, sequence_no=2, idempotency_key="k1")
        make_run(self.project, self.user, self.snapshot, sequence_no=3)
        make_run(self.project, self.user, self.snapshot, sequence_no=4)  # NULL 두 개는 허용
        self.assertEqual(Run.objects.filter(idempotency_key__isnull=True).count(), 2)

    def test_parent_run_is_set_null_on_delete(self):
        parent = make_run(self.project, self.user, self.snapshot, sequence_no=1)
        child = make_run(self.project, self.user, self.snapshot, sequence_no=2, kind=Run.Kind.RETEST, parent_run=parent)
        parent.delete()
        child.refresh_from_db()
        self.assertIsNone(child.parent_run)

    def test_final_status_is_not_active(self):
        run = make_run(self.project, self.user, self.snapshot, status=Run.Status.COMPLETED)
        self.assertFalse(run.is_active)
        self.assertEqual(set(Run.ACTIVE_STATUSES) & set(Run.FINAL_STATUSES), set())


class ProtectTests(RunsFixtureMixin, TestCase):
    def test_plan_snapshot_with_runs_cannot_be_deleted(self):
        make_run(self.project, self.user, self.snapshot)
        with self.assertRaises(ProtectedError):
            self.snapshot.delete()

    def test_persona_snapshot_with_sessions_cannot_be_deleted(self):
        run = make_run(self.project, self.user, self.snapshot)
        make_session(make_task_run(run), self.persona)
        with self.assertRaises(ProtectedError):
            self.persona.delete()


class TaskRunTests(RunsFixtureMixin, TestCase):
    def test_defaults(self):
        task_run = make_task_run(make_run(self.project, self.user, self.snapshot))
        self.assertEqual(task_run.status, TaskRun.Status.QUEUED)
        self.assertEqual(task_run.events_offset, 0)
        self.assertIsNone(task_run.pid)

    def test_task_and_seed_unique_per_run(self):
        run = make_run(self.project, self.user, self.snapshot)
        make_task_run(run, "order", 1)
        make_task_run(run, "order", 2)
        make_task_run(run, "search", 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_task_run(run, "order", 1)

    def test_agent_run_id_matches_build_run_input_format(self):
        from plans.run_input import RUN_ID_PATTERN
        run = make_run(self.project, self.user, self.snapshot)
        task_run = make_task_run(run, "order", 3)
        self.assertEqual(task_run.agent_run_id, f"{run.id}_order_s3")
        self.assertRegex(task_run.agent_run_id, RUN_ID_PATTERN)


class SessionTests(RunsFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.run = make_run(self.project, self.user, self.snapshot)
        self.task_run = make_task_run(self.run)

    def test_defaults(self):
        session = make_session(self.task_run, self.persona)
        self.assertEqual(session.status, Session.Status.RUNNING)
        self.assertFalse(session.is_terminal)
        self.assertEqual((session.steps, session.input_tokens, session.output_tokens), (0, 0, 0))
        self.assertEqual(session.issues, [])

    def test_session_key_unique_per_task_run(self):
        make_session(self.task_run, self.persona)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_session(self.task_run, self.persona)

    def test_same_persona_can_appear_in_each_seed(self):
        make_session(self.task_run, self.persona)
        make_session(make_task_run(self.run, "order", 2), self.persona)
        self.assertEqual(Session.objects.count(), 2)

    def test_terminal_statuses_match_agent_schema(self):
        """에이전트가 내보내는 종료 상태(agent.schemas.Terminal)와 어긋나면 여기서 바로 알 수 있다."""
        self.assertEqual({s.value for s in Session.TERMINAL_STATUSES}, set(get_args(Terminal)))

    def test_technical_statuses_exclude_the_three_outcome_statuses(self):
        outcome = {Session.Status.SUCCESS, Session.Status.SIMULATED_DROPOUT, Session.Status.AGENT_FAILURE}
        self.assertEqual(set(Session.TECHNICAL_STATUSES) & outcome, set())
        self.assertEqual(len(Session.TECHNICAL_STATUSES), 4)
        self.assertTrue(set(Session.TECHNICAL_STATUSES) <= set(Session.TERMINAL_STATUSES))


class ObservationAndEventTests(RunsFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.run = make_run(self.project, self.user, self.snapshot)
        self.task_run = make_task_run(self.run)
        self.session = make_session(self.task_run, self.persona)

    def test_observation_step_unique_and_ordered(self):
        make_observation(self.session, 2, "http://shop.local:8080/cart")
        make_observation(self.session, 0)
        make_observation(self.session, 1, "http://shop.local:8080/product")
        self.assertEqual([o.step for o in self.session.observations.all()], [0, 1, 2])
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_observation(self.session, 1)

    def test_event_id_is_unique(self):
        first = make_event(self.task_run, self.session, sequence=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RunEvent.objects.create(run=self.run, task_run=self.task_run, event_id=first.event_id, sequence=2,
                                    type="x", occurred_at=first.occurred_at)

    def test_event_sequence_unique_per_task_run_but_not_across(self):
        make_event(self.task_run, self.session, sequence=1)
        make_event(make_task_run(self.run, "order", 2), sequence=1)  # 다른 프로세스는 1부터 다시 시작
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_event(self.task_run, self.session, sequence=1)

    def test_polling_cursor_returns_only_newer_events_in_order(self):
        e1, e2, e3 = (make_event(self.task_run, self.session, sequence=n) for n in (1, 2, 3))
        newer = list(RunEvent.objects.filter(run=self.run, id__gt=e1.id).values_list("id", flat=True))
        self.assertEqual(newer, [e2.id, e3.id])


class CascadeTests(RunsFixtureMixin, TestCase):
    def test_deleting_run_removes_everything_under_it(self):
        run = make_run(self.project, self.user, self.snapshot)
        task_run = make_task_run(run)
        session = make_session(task_run, self.persona)
        make_observation(session)
        make_event(task_run, session)
        run.delete()
        for model in (TaskRun, Session, Observation, RunEvent):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        self.assertTrue(type(self.persona).objects.filter(pk=self.persona.pk).exists())  # 스냅샷은 남는다

    def test_other_users_data_is_untouched(self):
        other_user = make_user("other@example.com")
        other_project = make_project(other_user, name="남의 사이트")
        other_run = make_run(other_project, other_user, make_snapshot(other_project, other_user))
        run = make_run(self.project, self.user, self.snapshot)
        run.delete()
        self.assertTrue(Run.objects.filter(pk=other_run.pk).exists())
