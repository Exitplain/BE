"""과업 진행 순서: 계획에서 고른 순서(화면 왼쪽 → 오른쪽)대로 돌리고, 실행 상세에 그 순서와 상태를 내려준다."""
from django.test import TestCase
from rest_framework.test import APIClient

from runs import queries
from runs.models import Run, TaskRun

from .factories import RunsFixtureMixin, make_run, make_task_run

TASKS = [{"id": "delivery", "name": "배송 지역 확인"}, {"id": "coupon", "name": "첫 주문 쿠폰 받기"},
         {"id": "menu", "name": "이번 주 메뉴 보기"}, {"id": "add", "name": "식탁 담기"}]


class TaskOrderTests(RunsFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.snapshot.data = {"tasks": TASKS}
        self.snapshot.save()
        self.run = make_run(self.project, self.user, self.snapshot, status=Run.Status.RUNNING)
        # 만든 순서·알파벳순과 다르게 넣는다 (예전 워커는 task_key 알파벳순이었다)
        for key in ("menu", "add", "delivery", "coupon"):
            for seed in (2, 1):
                make_task_run(self.run, key, seed)

    def test_worker_order_follows_the_plan_then_seed(self):
        ordered = queries.ordered_task_runs(self.run, TaskRun.objects.filter(run=self.run))
        self.assertEqual([(t.task_key, t.seed) for t in ordered],
                         [("delivery", 1), ("delivery", 2), ("coupon", 1), ("coupon", 2),
                          ("menu", 1), ("menu", 2), ("add", 1), ("add", 2)])

    def test_run_detail_lists_tasks_in_that_order_with_status(self):
        TaskRun.objects.filter(run=self.run, task_key="delivery").update(status=TaskRun.Status.SUCCEEDED)
        TaskRun.objects.filter(run=self.run, task_key="coupon", seed=1).update(status=TaskRun.Status.RUNNING)
        client = APIClient()
        client.force_login(self.user)
        tasks = client.get(f"/api/v1/runs/{self.run.id}").json()["tasks"]
        self.assertEqual([(t["position"], t["task_id"], t["name"], t["status"], t["seeds"]) for t in tasks], [
            (1, "delivery", "배송 지역 확인", "done", 2),
            (2, "coupon", "첫 주문 쿠폰 받기", "running", 2),
            (3, "menu", "이번 주 메뉴 보기", "queued", 2),
            (4, "add", "식탁 담기", "queued", 2),
        ])
