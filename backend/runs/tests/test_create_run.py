from unittest.mock import patch

from django.test import TestCase, override_settings

from common.exceptions import ApiError
from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import RunInputError
from projects.models import Project
from runs import netguard, services
from runs.models import Run, TaskRun

PUBLIC = ["93.184.216.34"]


class NetguardTests(TestCase):
    def check(self, origin, resolved=PUBLIC):
        with patch("runs.netguard.resolve_host", return_value=resolved):
            netguard.check_origin(origin)

    def blocked(self, origin, resolved=PUBLIC, message=None):
        with self.assertRaises(netguard.TargetNotAllowed) as ctx:
            self.check(origin, resolved)
        if message:
            self.assertIn(message, str(ctx.exception))

    def test_public_hosts_and_unresolvable_names_pass(self):
        self.check("https://banchanroad.example")
        self.check("http://does-not-exist.example", resolved=[])  # 풀리지 않으면 접속도 못 한다

    def test_cloud_metadata_is_always_blocked_even_if_allow_listed(self):
        self.blocked("http://169.254.169.254", message="메타데이터")
        self.blocked("http://metadata.example", resolved=["169.254.169.254"], message="메타데이터")
        self.blocked("http://metadata.example", resolved=["::ffff:169.254.169.254"], message="메타데이터")
        self.blocked("http://[fd00:ec2::254]", message="메타데이터")
        with override_settings(ALLOWED_TARGET_ORIGINS=["http://169.254.169.254"]):
            self.blocked("http://169.254.169.254", message="메타데이터")

    def test_private_loopback_and_link_local_are_blocked_by_default(self):
        for origin in ["http://127.0.0.1:8080", "http://10.0.0.5", "http://192.168.0.1", "http://172.16.3.4",
                       "http://[::1]:8080", "http://100.64.0.1"]:
            with self.subTest(origin=origin):
                self.blocked(origin, message="내부 네트워크")
        self.blocked("http://localhost:8080", resolved=["127.0.0.1", "::1"], message="내부 네트워크")

    def test_one_private_answer_among_public_ones_is_enough_to_block(self):
        self.blocked("http://tricky.example", resolved=["93.184.216.34", "10.0.0.1"])

    def test_private_target_can_be_allow_listed_exactly(self):
        with override_settings(ALLOWED_TARGET_ORIGINS=["http://shop.local:8080", "HTTP://LOCALHOST:9000"]):
            self.check("http://shop.local:8080", resolved=["10.1.2.3"])
            self.check("http://localhost:9000", resolved=["127.0.0.1"])
            self.blocked("http://shop.local:8081", resolved=["10.1.2.3"])  # 포트가 다르면 다른 origin
            self.blocked("http://other.local:8080", resolved=["10.1.2.3"])

    def test_non_http_schemes_are_rejected(self):
        self.blocked("ftp://banchanroad.example")
        self.blocked("file:///etc/passwd")


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class CreateRunTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch("runs.netguard.resolve_host", return_value=[])  # shop.local 은 DNS 로 풀지 않는다
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot(self):
        return PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])

    def create(self, snapshot, **kwargs):
        return services.create_run(self.project, snapshot, self.user, **kwargs)

    def test_creates_queued_run_with_one_task_run_per_task_and_seed(self, _):
        snapshot = self.snapshot()
        before = Project.objects.get(pk=self.project.pk).updated_at
        run, created = self.create(snapshot)
        self.assertTrue(created)
        self.assertEqual((run.status, run.kind, run.sequence_no, run.created_by), ("queued", "pretest", 1, self.user))
        self.assertEqual(run.plan_snapshot, snapshot)
        self.assertTrue(run.name.startswith("1차·"), run.name)
        task_runs = list(run.task_runs.all())
        self.assertEqual(len(task_runs), 6)  # 과업 2 × 시드 3
        self.assertEqual(sorted({(t.task_key, t.seed) for t in task_runs}),
                         [("coupon", 1), ("coupon", 2), ("coupon", 3), ("order", 1), ("order", 2), ("order", 3)])
        order_1 = next(t for t in task_runs if (t.task_key, t.seed) == ("order", 1))
        self.assertEqual(order_1.agent_run_id, f"{run.id}_order_s1")
        self.assertEqual(order_1.input_json["run_id"], order_1.agent_run_id)
        self.assertEqual(len(order_1.input_json["personas"]), 20)
        self.assertTrue(all(t.status == "queued" for t in task_runs))
        self.assertEqual(run.config["expected_sessions"], 120)  # 20명 × 6
        self.assertGreater(Project.objects.get(pk=self.project.pk).updated_at, before)

    def test_sequence_numbers_increase_per_project(self, _):
        snapshot = self.snapshot()
        first, _ = self.create(snapshot)
        Run.objects.filter(pk=first.pk).update(status=Run.Status.COMPLETED)
        second, _ = self.create(snapshot)
        self.assertEqual((first.sequence_no, second.sequence_no), (1, 2))
        self.assertTrue(second.name.startswith("2차·"))

    def test_same_idempotency_key_returns_the_same_run_without_duplicates(self, _):
        snapshot = self.snapshot()
        first, created_first = self.create(snapshot, idempotency_key="click-1")
        again, created_again = self.create(snapshot, idempotency_key="click-1")
        self.assertEqual((created_first, created_again, again.pk), (True, False, first.pk))
        self.assertEqual((Run.objects.count(), TaskRun.objects.count()), (1, 6))

    def test_idempotency_key_cannot_be_reused_for_another_plan(self, _):
        first = self.snapshot()
        self.create(first, idempotency_key="k")
        second = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        with self.assertRaises(ApiError) as ctx:
            self.create(second, idempotency_key="k")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (409, "IDEMPOTENCY_KEY_REUSED"))

    def test_only_one_active_run_per_project_by_default(self, _):
        snapshot = self.snapshot()
        first, _ = self.create(snapshot)
        with self.assertRaises(ApiError) as ctx:
            self.create(snapshot)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (409, "RUN_IN_PROGRESS"))
        self.assertEqual(Run.objects.count(), 1)
        with override_settings(EXITPLAIN_MAX_ACTIVE_RUNS_PER_PROJECT=0):
            self.assertEqual(self.create(snapshot)[0].sequence_no, 2)

    def test_snapshot_of_another_project_is_rejected(self, _):
        snapshot = self.snapshot()
        other = Project.objects.create(owner=self.user, name="다른 사이트", entry_url="http://other.local/")
        with self.assertRaises(ApiError) as ctx:
            services.create_run(other, snapshot, self.user)
        self.assertEqual(ctx.exception.code, "PLAN_PROJECT_MISMATCH")

    def test_retest_needs_a_finished_run_of_the_same_project(self, _):
        snapshot = self.snapshot()
        base, _ = self.create(snapshot)
        with self.assertRaises(ApiError) as ctx:  # 아직 끝나지 않은 실행
            self.create(snapshot, kind=Run.Kind.RETEST, parent_run=base)
        self.assertEqual(ctx.exception.code, "INVALID_BASE_RUN")
        Run.objects.filter(pk=base.pk).update(status=Run.Status.COMPLETED)
        base.refresh_from_db()
        retest, _ = self.create(snapshot, kind=Run.Kind.RETEST, parent_run=base, change_note="가입 단계 줄임")
        self.assertEqual((retest.kind, retest.parent_run, retest.change_note, retest.sequence_no),
                         ("retest", base, "가입 단계 줄임", 2))

    def test_retest_without_base_or_base_without_retest_is_rejected(self, _):
        snapshot = self.snapshot()
        with self.assertRaises(ApiError):
            self.create(snapshot, kind=Run.Kind.RETEST)
        done = Run.objects.create(project=self.project, created_by=self.user, plan_snapshot=snapshot, sequence_no=1,
                                  status=Run.Status.COMPLETED)
        with self.assertRaises(ApiError):
            self.create(snapshot, kind=Run.Kind.PRETEST, parent_run=done)

    def test_failed_or_other_project_run_cannot_be_a_retest_base(self, _):
        snapshot = self.snapshot()
        failed = Run.objects.create(project=self.project, created_by=self.user, plan_snapshot=snapshot, sequence_no=1,
                                    status=Run.Status.FAILED)
        with self.assertRaises(ApiError):
            self.create(snapshot, kind=Run.Kind.RETEST, parent_run=failed)
        other = Project.objects.create(owner=self.user, name="다른 사이트", entry_url="http://other.local/")
        foreign = Run.objects.create(project=other, created_by=self.user, plan_snapshot=snapshot, sequence_no=1,
                                     status=Run.Status.COMPLETED)
        with self.assertRaises(ApiError):
            self.create(snapshot, kind=Run.Kind.RETEST, parent_run=foreign)

    def test_blocked_target_creates_nothing(self, _):
        snapshot = self.snapshot()
        with patch("runs.netguard.resolve_host", return_value=["169.254.169.254"]):
            with self.assertRaises(ApiError) as ctx:
                self.create(snapshot)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (422, "TARGET_NOT_ALLOWED"))
        self.assertIn("entry_url", ctx.exception.fields)
        self.assertEqual((Run.objects.count(), TaskRun.objects.count()), (0, 0))

    def test_private_target_needs_to_be_allow_listed(self, _):
        snapshot = self.snapshot()
        with patch("runs.netguard.resolve_host", return_value=["10.1.2.3"]):
            with self.assertRaises(ApiError):
                self.create(snapshot)
            with override_settings(ALLOWED_TARGET_ORIGINS=["http://shop.local:8080"]):
                self.assertTrue(self.create(snapshot)[1])

    def test_invalid_agent_input_creates_nothing(self, _):
        snapshot = self.snapshot()
        with patch("runs.services.build_run_inputs", side_effect=RunInputError("페르소나가 아직 없는 스냅샷입니다.")):
            with self.assertRaises(ApiError) as ctx:
                self.create(snapshot)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (422, "RUN_INPUT_INVALID"))
        self.assertEqual(Run.objects.count(), 0)


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class CancelTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch("runs.netguard.resolve_host", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def new_run(self):
        snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        return services.create_run(self.project, snapshot, self.user)[0]

    def test_queued_run_is_cancelled_immediately_with_its_task_runs(self, _):
        run = services.request_cancel(self.new_run())
        self.assertEqual(run.status, Run.Status.CANCELLED)
        self.assertIsNotNone(run.cancel_requested_at)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(set(run.task_runs.values_list("status", flat=True)), {"cancelled"})

    def test_running_run_only_gets_a_request_for_the_worker(self, _):
        run = self.new_run()
        Run.objects.filter(pk=run.pk).update(status=Run.Status.RUNNING)
        run = services.request_cancel(run)
        self.assertEqual(run.status, Run.Status.RUNNING)
        self.assertIsNotNone(run.cancel_requested_at)
        self.assertIsNone(run.finished_at)

    def test_cancel_twice_keeps_the_first_request_time(self, _):
        run = self.new_run()
        Run.objects.filter(pk=run.pk).update(status=Run.Status.RUNNING)
        first = services.request_cancel(run).cancel_requested_at
        self.assertEqual(services.request_cancel(run).cancel_requested_at, first)

    def test_finished_run_cannot_be_cancelled(self, _):
        run = self.new_run()
        Run.objects.filter(pk=run.pk).update(status=Run.Status.COMPLETED)
        with self.assertRaises(ApiError) as ctx:
            services.request_cancel(run)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (409, "RUN_NOT_ACTIVE"))
