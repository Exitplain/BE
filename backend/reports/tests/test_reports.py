import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import build_run_input, build_run_inputs
from reports import sources
from reports.aggregate import build_report_data
from reports.models import ReportSnapshot
from reports.services import create_report_from_run_dir
from reports.tests.sample_runs import ORDER_PATHS, write_attempt

DROP_AT_SIGNUP = ("simulated_dropout", ["/", "/product", "/cart", "/signup"], "input_burden")
DROP_AT_PAYMENT = ("simulated_dropout", ["/", "/product", "/cart", "/signup", "/checkout"], "cost")
REVISIT = ("success", ["/", "/product", "/", "/product", "/cart", "/signup", "/checkout", "/order/success"])
TECH = ("agent_failure", ["/", "/product"])


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ReportTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def snapshot(self, seed_count=1, tasks=("order",)):
        plan = self.patch(self.create_plan(), {**self.STEP2, "task_ids": list(tasks)}).json()
        plan = self.patch(plan, {"limits": {"seed_count": seed_count}}).json()
        return PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])

    def write_all(self, snapshot, outcomes=None):
        for item in build_run_inputs(snapshot, run_id="run1"):
            write_attempt(self.root, item, outcomes or {})

    # --- 집계 규칙 ---
    def test_funnel_counts_first_reach_and_excludes_technical(self, _):
        snap = self.snapshot()
        self.write_all(snap, {"P-001": DROP_AT_SIGNUP, "P-002": REVISIT, "P-003": TECH})
        data = build_report_data(snap.data, sources.load_from_run_dir(self.root))
        task = data["tasks"][0]
        self.assertEqual((task["success"], task["dropout"], task["technical"]), (18, 1, 1))
        self.assertEqual(task["valid_sessions"], 19)                     # 기술 실패 제외
        self.assertEqual(task["achievement_rate"], round(18 / 19, 4))
        self.assertEqual([s["name"] for s in task["funnel"]],
                         ["랜딩", "탐색", "상세", "장바구니", "가입", "결제 정보", "완료"])   # Figma P12 7단계
        funnel = {s["key"]: s for s in task["funnel"]}
        self.assertEqual(funnel["landing"]["reached"], 19)               # 재방문해도 1번
        self.assertEqual(funnel["browse"]["reached"], 0)                 # 데모 경로에 탐색 화면 없음
        self.assertEqual(funnel["detail"]["reached"], 19)
        self.assertEqual(funnel["signup"]["reached"], 19)
        self.assertEqual(funnel["payment"]["reached"], 18)
        self.assertEqual(funnel["signup"]["dropouts_here"], 1)
        self.assertEqual(funnel["payment"]["rate_of_previous"], round(18 / 19, 4))
        self.assertEqual(funnel["detail"]["rate_of_previous"], 1.0)      # 지나가지 않은 탐색은 건너뛰고 랜딩과 비교
        self.assertEqual(data["technical_failures"][0]["persona_id"], "P-003")
        self.assertEqual(data["status"], "final")

    def test_partial_when_sessions_missing_or_unfinished(self, _):
        snap = self.snapshot(seed_count=2)
        write_attempt(self.root, build_run_input(snap, run_id="run1", task_id="order", seed=1),
                      {"P-020": ("incomplete", ["/"])})
        data = build_report_data(snap.data, sources.load_from_run_dir(self.root))
        task = data["tasks"][0]
        self.assertEqual(task["expected_sessions"], 40)
        self.assertEqual(task["incomplete"], 1 + 20)                     # 끝나지 않은 1 + 시드 2 전체 누락 20
        self.assertEqual(data["status"], "partial")

    def test_segment_breakdown(self, _):
        snap = self.snapshot()
        self.write_all(snap, {f"P-{i:03d}": DROP_AT_SIGNUP for i in range(1, 6)})   # 세그먼트 1 전원 이탈
        data = build_report_data(snap.data, sources.load_from_run_dir(self.root))
        seg1 = data["tasks"][0]["segments"][0]
        self.assertEqual((seg1["segment_id"], seg1["name"], seg1["achievement_rate"]),
                         ("segment_1", "빠른 구매형", 0.0))

    def test_causes_top3_and_heatmap(self, _):
        snap = self.snapshot()
        outcomes = {**{f"P-{i:03d}": DROP_AT_SIGNUP for i in (1, 2, 3)},     # 세그먼트 1: 가입에서 입력 부담
                    **{f"P-{i:03d}": DROP_AT_PAYMENT for i in (6, 7)},       # 세그먼트 2: 결제에서 비용 충격
                    "P-011": ("simulated_dropout", ["/", "/product"])}        # 주 원인 기록 없음
        self.write_all(snap, outcomes)
        task = build_report_data(snap.data, sources.load_from_run_dir(self.root))["tasks"][0]
        self.assertEqual([(c["name"], c["dropouts"]) for c in task["top_causes"]],
                         [("입력 부담", 3), ("비용 충격", 2), ("원인 미상", 1)])
        self.assertEqual(task["causes"][0]["share"], 0.5)
        rows = {r["segment_id"]: r["dropouts"] for r in task["heatmap"]["rows"]}
        stages = task["heatmap"]["stages"]
        self.assertEqual(rows["segment_1"][stages.index("signup")], 3)
        self.assertEqual(rows["segment_2"][stages.index("payment")], 2)
        self.assertEqual(rows["segment_3"][stages.index("detail")], 1)
        self.assertEqual(task["kpis"]["median_steps"], 6)

    def test_seed_ranges(self, _):
        snap = self.snapshot(seed_count=3)
        drops = [{}, {"P-001": DROP_AT_SIGNUP}, {f"P-{i:03d}": DROP_AT_SIGNUP for i in (1, 2, 3)}]
        for seed, outcomes in enumerate(drops, 1):
            write_attempt(self.root, build_run_input(snap, run_id="run1", task_id="order", seed=seed), outcomes)
        task = build_report_data(snap.data, sources.load_from_run_dir(self.root))["tasks"][0]
        self.assertEqual(task["seed_ranges"]["success"], {"min": 17, "max": 20})     # 완료 17~20명
        self.assertEqual(task["seed_ranges"]["dropout"], {"min": 0, "max": 3})
        self.assertEqual((task["success"], task["dropout"]), (56, 4))                   # 합계는 시드 3번을 더한 값
        complete = next(s for s in task["funnel"] if s["key"] == "complete")
        self.assertEqual(complete["reached_range"], {"min": 17, "max": 20})

    def test_old_events_without_stage_use_paths(self, _):
        snap = self.snapshot()
        for item in build_run_inputs(snap, run_id="run1"):
            write_attempt(self.root, item, {"P-001": DROP_AT_SIGNUP}, with_stage=False)
        task = build_report_data(snap.data, sources.load_from_run_dir(self.root))["tasks"][0]
        self.assertEqual(next(s for s in task["funnel"] if s["key"] == "signup")["dropouts_here"], 1)

    # --- 파일 읽기 ---
    def test_source_errors(self, _):
        with self.assertRaises(sources.SourceError):
            sources.load_from_run_dir(self.root / "none")
        with self.assertRaises(sources.SourceError):
            sources.load_from_run_dir(self.root)                         # events.jsonl 없음
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "input.json").write_text('{"run_id": "no-seed"}', encoding="utf-8")
        (bad / "events.jsonl").write_text("", encoding="utf-8")
        with self.assertRaises(sources.SourceError):
            sources.load_attempt(bad)

    # --- 저장·API ---
    def test_revision_increments_and_api(self, _):
        snap = self.snapshot()
        self.write_all(snap)
        r1 = create_report_from_run_dir(snap, self.root, run_ref="run1", user=self.user)
        r2 = create_report_from_run_dir(snap, self.root, run_ref="run1", user=self.user)
        self.assertEqual((r1.revision, r2.revision), (1, 2))

        res = self.client.get("/api/v1/runs/run1/reports").json()
        self.assertEqual((res["revision"], res["status"], res["kind"]), (2, "final", "prediction"))
        self.assertEqual([h["revision"] for h in res["revisions"]], [2, 1])
        self.assertEqual(self.client.get(f"/api/v1/reports/{r1.id}").json()["revision"], 1)
        self.assertEqual(self.client.get("/api/v1/runs/nope/reports").status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/api/v1/runs/run1/reports").status_code, 403)

    def test_management_command(self, _):
        snap = self.snapshot()
        self.write_all(snap)
        out = StringIO()
        call_command("build_sample_report", str(snap.id), str(self.root), "--run-ref", "sample-1", stdout=out)
        self.assertIn("status=final", out.getvalue())
        self.assertEqual(ReportSnapshot.objects.get(run_ref="sample-1").data["summary"]["success"], 20)