"""보정 값 검사·세그먼트 적용(CAL-02)과 실행 반영(CAL-03·04)."""
from unittest.mock import patch

from django.test import TestCase

from calibrations import params as cal
from calibrations.models import CalibrationRevision
from common.testing import WizardMixin, fake_discovery
from runs.models import Run, TaskRun


class ParamsTests(TestCase):
    def test_validate_snaps_to_step_and_rejects_bad_values(self):
        ok = cal.validate({"segments": {"*": {"weight_scale": {"cost": 1.13}},
                                        "segment_1": {"threshold_scale": 0.87}}})
        self.assertEqual(ok, {"segments": {"*": {"weight_scale": {"cost": 1.15}},
                                           "segment_1": {"threshold_scale": 0.85}}})
        with self.assertRaises(cal.ParamsError) as ctx:
            cal.validate({"segments": {"segment_1": {"threshold_scale": 3, "weight_scale": {"mood": 1.1, "cost": 9}}}})
        self.assertEqual(set(ctx.exception.fields), {"params.segments.segment_1.threshold_scale",
                                                     "params.segments.segment_1.weight_scale.mood",
                                                     "params.segments.segment_1.weight_scale.cost"})
        for bad in ([], {"x": 1}, {"segments": []}, {"segments": {"s": {"other": 1}}}):
            with self.subTest(bad=bad), self.assertRaises(cal.ParamsError):
                cal.validate(bad)

    def test_all_and_segment_values_multiply_and_are_clamped(self):
        params = {"segments": {"*": {"threshold_scale": 0.9, "weight_scale": {"cost": 1.2}},
                               "segment_1": {"threshold_scale": 0.5, "weight_scale": {"cost": 3.0, "trust": 1.1}}}}
        self.assertEqual(cal.for_segment(params, "segment_2"), (0.9, {"cost": 1.2}))
        threshold, weights = cal.for_segment(params, "segment_1")
        self.assertEqual(threshold, 0.5)                       # 0.45 → 하한 0.5
        self.assertEqual(weights, {"cost": 3.0, "trust": 1.1})  # 3.6 → 상한 3.0
        self.assertEqual(cal.for_segment({}, "segment_1"), (1.0, {}))

    def test_apply_to_persona_only_touches_calibrated_values(self):
        persona = {"id": "P-001", "patience": 40}
        cal.apply_to_persona(persona, {"segments": {"*": {"threshold_scale": 0.8}}}, "segment_1")
        self.assertEqual(persona, {"id": "P-001", "patience": 40, "frustration_threshold": 48.0})
        untouched = {"id": "P-002"}
        cal.apply_to_persona(untouched, {}, "segment_1")
        self.assertEqual(untouched, {"id": "P-002"})


@patch("runs.netguard.resolve_host", return_value=["93.184.216.34"])
@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class RunCalibrationTests(WizardMixin, TestCase):
    def start(self, snapshot_id):
        res = self.client.post("/api/v1/runs", {"plan_revision_id": snapshot_id}, format="json")
        self.assertEqual(res.status_code, 202, res.content)
        return Run.objects.get(pk=res.json()["run_id"])

    def test_run_freezes_the_current_revision_and_old_runs_stay_the_same(self, *_):
        snapshot_id = self.confirm()["plan_revision_id"]
        before = self.start(snapshot_id)
        self.assertEqual(before.config["calibration_revision"], 0)
        default = TaskRun.objects.filter(run=before).first().input_json["personas"][0]
        self.assertEqual((default["weight_scale"], default["frustration_threshold"]), ({}, 60.0))   # 기본 모형
        Run.objects.filter(pk=before.pk).update(status=Run.Status.COMPLETED)

        segment = default["id"]
        segment_id = next(p["segment_id"] for p in before.plan_snapshot.data["personas"] if p["code"] == segment)
        CalibrationRevision.objects.create(project=self.project, revision=1, created_by=self.user, params={
            "segments": {"*": {"weight_scale": {"cost": 1.2}}, segment_id: {"threshold_scale": 0.8}}})
        after = self.start(snapshot_id)
        self.assertEqual(after.config["calibration_revision"], 1)
        personas = {p["id"]: p for p in TaskRun.objects.filter(run=after).first().input_json["personas"]}
        self.assertEqual((personas[segment]["frustration_threshold"], personas[segment]["weight_scale"]),
                         (48.0, {"cost": 1.2}))
        # 이미 만든 실행의 입력은 그대로 (CAL-03)
        self.assertEqual(TaskRun.objects.filter(run=before).first().input_json["personas"][0]["weight_scale"], {})
        detail = self.client.get(f"/api/v1/runs/{after.id}").json()
        self.assertEqual(detail["calibration_revision"], 1)



class FrustrationOffTests(TestCase):
    def test_frustration_off_wins_over_calibration(self):
        from plans.run_input import NO_DROPOUT_THRESHOLD, _agent_persona
        persona = {"code": "P-001", "context": "c", "segment_id": "segment_1", "variables": {"patience": 40}}
        params = {"segments": {"*": {"threshold_scale": 0.5, "weight_scale": {"cost": 2.0}}}}
        off = _agent_persona(persona, False, params)
        self.assertEqual((off["frustration_threshold"], off["weight_scale"]), (NO_DROPOUT_THRESHOLD, {"cost": 2.0}))
        self.assertEqual(_agent_persona(persona, True, params)["frustration_threshold"], 30.0)
