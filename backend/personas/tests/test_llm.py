"""LLM 페르소나 생성기 (personas/llm.py). 가짜 클라이언트를 써서 실제 API 호출이 없다."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from common.testing import WizardMixin, fake_discovery
from personas import llm
from personas.generators import GenerationError
from plans.models import PlanSnapshot


def seg(name, age, situation="시험 2주 전, 휴대폰으로 강의 녹음을 정리하는 대학생", **values):
    variables = {"patience": 40, "digital_literacy": 70, "price_sensitivity": 60, "trust_need": 50, **values}
    return llm.SegmentDraft(name=name, age_group=age, situation=situation, variables=llm.Variables(**variables),
                            basis=["시험 기간이라 시간이 없음"])


class FakeClient:
    def __init__(self, segments=None, stop_reason="end_turn"):
        self.segments, self.stop_reason, self.calls = segments, stop_reason, []
        self.messages = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = llm.Draft(segments=self.segments) if self.segments is not None else None
        return SimpleNamespace(parsed_output=parsed, stop_reason=self.stop_reason)


FOUR = [seg("시험 기간 벼락치기 대학생", "20s"), seg("강의 녹음 정리하는 복학생", "20s"),
        seg("퇴근 후 야간 수업 듣는 직장인", "30s", patience=25), seg("자격증 준비하는 사회초년생", "30s")]


class GenerateTests(SimpleTestCase):
    def test_age_groups_go_first_and_segments_come_back_with_situations(self):
        client = FakeClient(FOUR)
        result = llm.generate("강의자료를 정리하려는 대학생", ["20s", "30s"], [3, 3, 2, 2], client=client)
        request = json.loads(client.calls[0]["messages"][0]["content"])
        self.assertEqual([a["id"] for a in request["age_groups"]], ["20s", "30s"])           # 고른 연령대만
        self.assertEqual((request["segment_count"], request["people_per_segment"]), (4, [3, 3, 2, 2]))
        self.assertIn("Age groups first", client.calls[0]["system"])
        self.assertIn("Never derive abilities from age alone", client.calls[0]["system"])
        self.assertEqual(client.calls[0]["output_format"], llm.Draft)
        self.assertEqual([r["name"] for r in result][:1], ["시험 기간 벼락치기 대학생"])
        self.assertEqual(result[2]["variables"]["patience"], 25)
        self.assertEqual((result[0]["age_group"], result[0]["situation"][:5]), ("20s", "시험 2주"))

    def test_any_age_lets_the_model_pick_from_all_groups(self):
        client = FakeClient([seg("a", "20s"), seg("b", "40s"), seg("c", "50plus"), seg("d", "30s")])
        llm.generate("부모님 선물을 고르는 사람", ["any"], [3, 3, 2, 2], client=client)
        request = json.loads(client.calls[0]["messages"][0]["content"])
        self.assertFalse(request["age_groups_given_by_user"])
        self.assertEqual(len(request["age_groups"]), 4)

    def test_invalid_drafts_are_rejected_not_fixed(self):
        cases = {
            "개를 요청했는데": FOUR[:3],                                              # 수가 다름
            "고르지 않은 연령대": FOUR[:3] + [seg("다른 연령", "50plus")],           # 고르지 않은 연령대
            "5~95": FOUR[:3] + [seg("극단값", "30s", trust_need=100)],              # 범위 밖
            "겹칩니다": FOUR[:3] + [seg(FOUR[0].name, "20s")],                      # 이름 중복
        }
        for message, segments in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(GenerationError, message):
                llm.generate("대학생", ["20s", "30s"], [3, 3, 2, 2], client=FakeClient(segments))

    def test_refusal_or_missing_key_is_an_explicit_failure(self):
        with self.assertRaisesRegex(GenerationError, "결과를 주지"):
            llm.generate("대학생", ["20s"], [3, 3, 2, 2], client=FakeClient(None, stop_reason="refusal"))
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(GenerationError, "ANTHROPIC_API_KEY"):
            llm.generate("대학생", ["20s"], [3, 3, 2, 2])


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
@override_settings(EXITPLAIN_PERSONA_GENERATOR="personas.llm.generate")
class ConfirmWithLlmTests(WizardMixin, TestCase):
    def test_confirmed_personas_carry_situation_and_single_age_context(self, _):
        audience = {"context": "강의자료를 정리하려는 대학생과 사회초년생", "ages": ["20s", "30s"]}
        with patch.object(llm, "_client", return_value=FakeClient(FOUR)):
            plan = self.patch(self.ready_plan(), {"audience": audience}).json()
            snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        data = snap.data
        self.assertEqual(data["persona_generator"], "claude-v1")
        self.assertEqual([s["name"] for s in data["segments"]], [s.name for s in FOUR])
        self.assertEqual(data["segments"][2]["age_group"], "30s")
        first = data["personas"][0]
        self.assertIn("시험 2주 전", first["context"])
        self.assertTrue(first["context"].endswith("연령 문맥: 20대"))        # 세그먼트의 연령대 하나만

    def test_generation_failure_rolls_back_confirmation(self, _):
        with patch.object(llm, "_client", return_value=FakeClient(FOUR[:2])):
            plan = self.ready_plan()
            res = self.client.post(f"/api/v1/plans/{plan['id']}/confirmations", {"revision": plan["revision"]},
                                   format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "PERSONA_GENERATION_FAILED"))
        self.assertEqual(PlanSnapshot.objects.count(), 0)
