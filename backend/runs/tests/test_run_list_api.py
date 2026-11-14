from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from reports.models import ReportSnapshot
from runs.models import Run
from runs.naming import build_run_name

from .factories import make_project, make_run, make_snapshot, make_user

TASKS = [{"id": "order", "name": "첫 주문 완료"}, {"id": "search", "name": "상품 검색"}]


def url(project):
    return f"/api/v1/projects/{project.pk}/runs"


class BuildRunNameTests(TestCase):
    def test_names_follow_the_figma_pattern(self):
        self.assertEqual(build_run_name(3, "pretest", {"tasks": TASKS}), "3차·첫 주문 완료 외 1개")
        self.assertEqual(build_run_name(1, "pretest", {"tasks": TASKS[:1]}), "1차·첫 주문 완료")
        self.assertEqual(build_run_name(4, "retest", {"tasks": TASKS + [{"id": "x", "name": "세 번째"}]}),
                         "4차·첫 주문 완료 외 2개")
        self.assertEqual(build_run_name(2, "cause_repro", {"tasks": TASKS}), "원인 재현")

    def test_falls_back_to_number_only_when_snapshot_has_no_tasks(self):
        for data in (None, {}, {"tasks": []}, {"tasks": ["bad"]}):
            self.assertEqual(build_run_name(5, "pretest", data), "5차")

    def test_uses_task_id_when_name_is_missing_and_truncates_long_names(self):
        self.assertEqual(build_run_name(1, "pretest", {"tasks": [{"id": "order"}]}), "1차·order")
        self.assertLessEqual(len(build_run_name(1, "pretest", {"tasks": [{"name": "가" * 200}]})), 120)


class RunNameOnSaveTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.project = make_project(self.user)
        self.snapshot = make_snapshot(self.project, self.user)
        self.snapshot.data = {"tasks": TASKS}
        self.snapshot.save()

    def test_blank_name_is_filled_from_snapshot_on_create(self):
        run = make_run(self.project, self.user, self.snapshot, sequence_no=4)
        self.assertEqual(Run.objects.get(pk=run.pk).name, "4차·첫 주문 완료 외 1개")

    def test_explicit_name_is_kept_and_later_saves_do_not_rename(self):
        run = make_run(self.project, self.user, self.snapshot, sequence_no=1, name="내가 정한 이름")
        run.status = Run.Status.RUNNING
        run.save(update_fields=["status"])
        self.assertEqual(Run.objects.get(pk=run.pk).name, "내가 정한 이름")


class RunListApiBase(TestCase):
    def setUp(self):
        self.user = make_user("owner@example.com")
        self.other = make_user("other@example.com")
        self.project = make_project(self.user)
        self.snapshot = make_snapshot(self.project, self.user)
        self.snapshot.data = {"tasks": TASKS}
        self.snapshot.save()
        self.client = APIClient()
        self.client.force_login(self.user)

    def run_(self, n, **extra):
        return make_run(self.project, self.user, self.snapshot, sequence_no=n, **extra)

    def ago(self, run, days):
        Run.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=days))

    def get(self, project=None, **params):
        return self.client.get(url(project or self.project), params)


class AccessTests(RunListApiBase):
    def test_login_required_owner_only_and_404(self):
        self.assertEqual(APIClient().get(url(self.project)).status_code, 401)
        theirs = make_project(self.other, name="남의 것")
        forbidden = self.get(theirs)
        self.assertEqual((forbidden.status_code, forbidden.json()["error"]["code"]), (403, "FORBIDDEN"))
        self.assertEqual(self.client.get("/api/v1/projects/999999/runs").status_code, 404)


class ListTests(RunListApiBase):
    def test_newest_sequence_first_with_item_shape_and_total(self):
        self.run_(1, status=Run.Status.COMPLETED)
        self.run_(2, status=Run.Status.FAILED)
        running = self.run_(3, status=Run.Status.RUNNING, kind=Run.Kind.RETEST, change_note="수정 후 재테스트")
        body = self.get().json()
        self.assertEqual([r["sequence_no"] for r in body["items"]], [3, 2, 1])
        self.assertEqual((body["total"], body["next_cursor"]), (3, None))
        first = body["items"][0]
        self.assertEqual(first["id"], str(running.id))
        self.assertEqual((first["name"], first["kind"], first["status"]), ("3차·첫 주문 완료 외 1개", "retest", "running"))
        self.assertEqual((first["change_note"], first["parent_run_id"], first["report"]), ("수정 후 재테스트", None, None))
        self.assertIsNotNone(first["created_at"])

    def test_empty_project(self):
        body = self.get().json()
        self.assertEqual((body["items"], body["total"], body["next_cursor"]), ([], 0, None))

    def test_other_projects_runs_are_not_included(self):
        other_project = make_project(self.user, name="다른 사이트")
        make_run(other_project, self.user, make_snapshot(other_project, self.user), sequence_no=1)
        self.run_(1)
        self.assertEqual(self.get().json()["total"], 1)

    def test_retest_parent_is_exposed(self):
        parent = self.run_(1, status=Run.Status.COMPLETED)
        self.run_(2, kind=Run.Kind.RETEST, parent_run=parent)
        self.assertEqual(self.get().json()["items"][0]["parent_run_id"], str(parent.id))


class FilterTests(RunListApiBase):
    def setUp(self):
        super().setUp()
        self.run_(1, status=Run.Status.COMPLETED)
        self.run_(2, status=Run.Status.COMPLETED, kind=Run.Kind.CAUSE_REPRO)
        self.run_(3, status=Run.Status.RUNNING)
        self.run_(4, status=Run.Status.FAILED, kind=Run.Kind.RETEST)

    def seqs(self, **params):
        return [r["sequence_no"] for r in self.get(**params).json()["items"]]

    def test_status_filter_single_and_multiple(self):
        self.assertEqual(self.seqs(status="completed"), [2, 1])
        self.assertEqual(self.seqs(status="running,failed"), [4, 3])

    def test_kind_filter_matches_p01_and_c08_usage(self):
        self.assertEqual(self.seqs(kind="pretest,retest"), [4, 3, 1])  # P01: 사전 테스트·재테스트
        self.assertEqual(self.seqs(kind="cause_repro"), [2])
        self.assertEqual(self.seqs(), [4, 3, 2, 1])  # C08: 전체 유형

    def test_filters_combine(self):
        self.assertEqual(self.seqs(status="completed", kind="pretest"), [1])

    def test_period_filter(self):
        for run in Run.objects.all():
            self.ago(run, {1: 40, 2: 20, 3: 3, 4: 1}[run.sequence_no])
        self.assertEqual(self.seqs(period="7d"), [4, 3])
        self.assertEqual(self.seqs(period="30d"), [4, 3, 2])
        self.assertEqual(self.seqs(period="90d"), [4, 3, 2, 1])
        self.assertEqual(self.seqs(period="all"), [4, 3, 2, 1])

    def test_search_by_name_and_by_run_id(self):
        self.assertEqual(self.seqs(q="첫 주문"), [4, 3, 1])  # 원인 재현(2번)은 이름이 다르다
        self.assertEqual(self.seqs(q="3차"), [3])
        self.assertEqual(self.seqs(q="원인"), [2])
        run_id = str(Run.objects.get(sequence_no=3).id)
        self.assertEqual(self.seqs(q=run_id), [3])
        self.assertEqual(self.seqs(q=run_id[:8]), [3])
        self.assertEqual(self.seqs(q="없는검색어"), [])

    def test_total_reflects_filters(self):
        body = self.get(status="completed").json()
        self.assertEqual(body["total"], 2)

    def test_invalid_values_are_422(self):
        for params in ({"status": "done"}, {"status": "running,nope"}, {"kind": "x"}, {"period": "1y"},
                       {"limit": 0}, {"limit": 101}, {"cursor": "%%%"}, {"cursor": "bm90LWpzb24"}):
            with self.subTest(params=params):
                res = self.get(**params)
                self.assertEqual(res.status_code, 422, res.content)
                self.assertEqual(res.json()["error"]["code"], "VALIDATION_ERROR")


class PaginationTests(RunListApiBase):
    def test_cursor_walks_every_page_without_overlap(self):
        for n in range(1, 8):
            self.run_(n)
        seen, cursor, pages = [], "", 0
        while True:
            body = self.get(limit=3, **({"cursor": cursor} if cursor else {})).json()
            pages += 1
            seen += [r["sequence_no"] for r in body["items"]]
            self.assertEqual(body["total"], 7)
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, [7, 6, 5, 4, 3, 2, 1])
        self.assertEqual(pages, 3)

    def test_cursor_keeps_working_with_filters(self):
        for n in range(1, 7):
            self.run_(n, status=Run.Status.COMPLETED if n % 2 else Run.Status.FAILED)
        first = self.get(status="completed", limit=2).json()
        second = self.get(status="completed", limit=2, cursor=first["next_cursor"]).json()
        self.assertEqual([r["sequence_no"] for r in first["items"] + second["items"]], [5, 3, 1])
        self.assertIsNone(second["next_cursor"])


class ReportLinkTests(RunListApiBase):
    def report(self, run, revision, rate, status="final"):
        return ReportSnapshot.objects.create(
            plan_snapshot=self.snapshot, run_ref=str(run.id), revision=revision, status=status, source="database",
            data={"tasks": [{"task_id": "order", "name": "첫 주문 완료", "achievement_rate": rate, "funnel": []},
                            {"task_id": "search", "name": "상품 검색", "achievement_rate": None}]})

    def test_latest_report_revision_is_attached_with_goal_rates(self):
        done = self.run_(1, status=Run.Status.COMPLETED)
        self.report(done, 1, 0.2)
        latest = self.report(done, 2, 0.25, status="partial")
        item = self.get().json()["items"][0]
        self.assertEqual(item["report"]["id"], str(latest.id))
        self.assertEqual((item["report"]["revision"], item["report"]["status"]), (2, "partial"))
        self.assertEqual(item["report"]["goal_rates"], [
            {"task_id": "order", "name": "첫 주문 완료", "achievement_rate": 0.25},
            {"task_id": "search", "name": "상품 검색", "achievement_rate": None}])

    def test_report_carries_b_summary_values_for_the_diagnosis_list(self):
        done = self.run_(1, status=Run.Status.COMPLETED)
        ReportSnapshot.objects.create(
            plan_snapshot=self.snapshot, run_ref=str(done.id), revision=1, status="final", source="database",
            data={"summary": {"achievement_rate": 0.4, "success": 8, "dropout": 10, "technical": 2, "incomplete": 0},
                  "tasks": [{"task_id": "order", "name": "첫 주문 완료", "achievement_rate": 0.4,
                             "causes": [{"key": "input_burden", "name": "입력 부담", "dropouts": 6},
                                        {"key": "cost", "name": "비용 충격", "dropouts": 4},
                                        {"key": "trust", "name": "신뢰 부족", "dropouts": 0}]}]})
        report = self.get().json()["items"][0]["report"]
        self.assertEqual((report["achievement_rate"], report["success"], report["dropout"], report["technical"],
                          report["incomplete"]), (0.4, 8, 10, 2, 0))
        self.assertEqual([c["key"] for c in report["top_causes"]], ["input_burden", "cost"])  # 0건은 뺀다
        self.assertEqual(report["goal_rates"][0]["achievement_rate"], 0.4)  # 기존 값도 그대로
        self.assertIn("created_at", report)

    def test_run_without_report_has_null_and_reports_of_other_runs_do_not_leak(self):
        with_report = self.run_(1, status=Run.Status.COMPLETED)
        self.run_(2, status=Run.Status.RUNNING)
        self.report(with_report, 1, 0.3)
        items = {r["sequence_no"]: r for r in self.get().json()["items"]}
        self.assertIsNotNone(items[1]["report"])
        self.assertIsNone(items[2]["report"])

    def test_query_count_does_not_grow_with_number_of_runs(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.get().status_code, 200)
            return len(ctx)

        first = self.run_(1, status=Run.Status.COMPLETED)
        self.report(first, 1, 0.2)
        one = count_queries()
        for n in range(2, 10):
            run = self.run_(n, status=Run.Status.COMPLETED)
            self.report(run, 1, 0.3)
        self.assertEqual(count_queries(), one)  # 실행 9개·리포트 9개여도 쿼리 수가 같다 (N+1 없음)
