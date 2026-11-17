"""실행 퍼널 단계의 화면 역할 이름 (reports/stage_names.py). 가짜 클라이언트를 써서 실제 API 호출이 없다."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from reports.models import ReportSnapshot
from reports.stage_names import StageName, StageNames, apply_role_names

DATA = {"tasks": [{"task_id": "order", "name": "첫 주문", "journey_funnel": {
    "stages": [{"id": "/", "path": "/", "name": "/", "name_source": "path", "title": "반찬로드"},
               {"id": "/product", "path": "/product", "name": "/product", "name_source": "path", "title": "반찬로드"},
               {"id": "/cart", "path": "/cart", "name": "장바구니", "name_source": "title", "title": "장바구니 | 반찬로드"}],
    "_screen_texts": {"/": "오늘 저녁은, 가볍게 이번 주 식탁 만나기", "/product": "이 식탁 담기 9,900원"}}}]}
NAMES = {"/": "홈", "/product": "상품 상세", "/cart": "장바구니"}


class FakeClient:
    def __init__(self, names=None, error=None):
        self.names, self.error, self.calls = names, error, []
        self.messages = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        stages = [StageName(path=p, name=n) for p, n in (self.names or {}).items()]
        return SimpleNamespace(parsed_output=StageNames(stages=stages), stop_reason="end_turn")


def stages(data):
    return [(s["path"], s["name"], s["name_source"]) for s in data["tasks"][0]["journey_funnel"]["stages"]]


class ApplyTests(SimpleTestCase):
    def test_model_names_each_stage_and_screen_texts_are_not_kept(self):
        data, client = copy.deepcopy(DATA), FakeClient(NAMES)
        apply_role_names(data, client=client)
        self.assertEqual(stages(data), [("/", "홈", "model"), ("/product", "상품 상세", "model"), ("/cart", "장바구니", "model")])
        self.assertNotIn("_screen_texts", data["tasks"][0]["journey_funnel"])
        sent = json.loads(client.calls[0]["messages"][0]["content"])
        self.assertEqual(sent["task_goal"], "첫 주문")
        self.assertEqual(sent["screens"][1], {"path": "/product", "title": "반찬로드", "text": "이 식탁 담기 9,900원"})
        self.assertEqual(client.calls[0]["model"], "claude-haiku-4-5")

    def test_bad_answers_keep_title_or_path_names(self):
        cases = {"빠진 경로": {"/": "홈", "/product": "상품 상세"},
                 "겹치는 이름": {"/": "홈", "/product": "홈", "/cart": "장바구니"},
                 "너무 긴 이름": {**NAMES, "/product": "상" * 30}}
        for label, names in cases.items():
            with self.subTest(label):
                data = copy.deepcopy(DATA)
                apply_role_names(data, client=FakeClient(names))
                self.assertEqual(stages(data), stages(DATA))
                self.assertNotIn("_screen_texts", data["tasks"][0]["journey_funnel"])

    def test_model_errors_never_break_the_report(self):
        data = copy.deepcopy(DATA)
        apply_role_names(data, client=FakeClient(error=RuntimeError("boom")))
        self.assertEqual(stages(data), stages(DATA))

    def test_site_check_names_apply_without_a_model(self):
        data = copy.deepcopy(DATA)
        with patch.dict("os.environ", {}, clear=True):
            apply_role_names(data, snapshot_names={"/": "홈", "/product": "상품 상세"})
        self.assertEqual(stages(data), [("/", "홈", "model"), ("/product", "상품 상세", "model"), ("/cart", "장바구니", "title")])

    def test_without_key_nothing_is_called(self):
        data = copy.deepcopy(DATA)
        with patch.dict("os.environ", {}, clear=True):
            apply_role_names(data)
        self.assertEqual(stages(data), stages(DATA))
        self.assertNotIn("_screen_texts", data["tasks"][0]["journey_funnel"])


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ReuseTests(WizardMixin, TestCase):
    def test_previous_names_in_the_project_are_reused(self, _):
        snapshot = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        earlier = copy.deepcopy(DATA)
        apply_role_names(earlier, client=FakeClient({"/": "메인", "/product": "식탁 상세", "/cart": "장바구니"}))
        ReportSnapshot.objects.create(plan_snapshot=snapshot, run_ref="r1", revision=1, status="final", source="database",
                                      data=earlier)
        data, client = copy.deepcopy(DATA), FakeClient(NAMES)
        apply_role_names(data, project_id=self.project.id, client=client)
        self.assertEqual(client.calls, [])                        # 모든 경로를 이미 알아서 다시 묻지 않는다
        self.assertEqual([n for _, n, _ in stages(data)], ["메인", "식탁 상세", "장바구니"])
