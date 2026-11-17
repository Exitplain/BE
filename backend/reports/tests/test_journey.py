"""실행 퍼널 (reports/journey.py): 실제로 지나간 화면으로 과업마다 단계를 만든다."""
from django.test import SimpleTestCase

from reports.aggregate import build_report_data
from reports.journey import journey_funnel, screen_id

SEG = ("segment_1", "빠른 구매형")
BASE = "http://shop.local:8080"


def row(paths, kind="success", seed=1, segment=SEG, titles=None):
    titles = titles or {}
    return {"kind": kind, "seed": seed, "segment": segment,
            "screens": [{"url": BASE + p, "title": titles.get(p.split("?")[0], "")} for p in paths]}


ORDER = ["/", "/product?item=a", "/cart", "/signup", "/checkout", "/order/success"]


class ScreenIdTests(SimpleTestCase):
    def test_query_and_ids_are_grouped(self):
        self.assertEqual(screen_id(BASE + "/product?item=a#top"), "/product")
        self.assertEqual(screen_id(BASE + "/Products/12/"), "/products/:id")
        self.assertEqual(screen_id("/orders/3f2b8c1e-1a2b-4c3d-9e8f-0a1b2c3d4e5f"), "/orders/:id")
        self.assertEqual(screen_id("/item/a8f3k2m9x7q1w5e2r"), "/item/:id")       # 긴 해시
        self.assertEqual(screen_id("/delivery/result"), "/delivery/result")      # 일반 단어는 그대로
        self.assertEqual(screen_id(""), "/")


class JourneyFunnelTests(SimpleTestCase):
    def test_only_visited_screens_become_stages_in_visit_order(self):
        """목록 화면이 없는 쇼핑몰: '탐색' 같은 0명 단계가 생기지 않는다."""
        rows = [row(ORDER) for _ in range(7)] + [row(ORDER[:4], "dropout") for _ in range(3)]
        funnel = journey_funnel(rows, success_path="/order/success")
        self.assertEqual([s["id"] for s in funnel["stages"]],
                         ["/", "/product", "/cart", "/signup", "/checkout", "/order/success"])
        self.assertTrue(all(s["reached"] > 0 for s in funnel["stages"]))
        self.assertEqual([s["reached"] for s in funnel["stages"]], [10, 10, 10, 10, 7, 7])
        self.assertEqual(funnel["stages"][4]["rate_of_previous"], 0.7)
        signup = funnel["stages"][3]
        self.assertEqual((signup["dropouts_here"], funnel["stages"][-1]["is_goal"]), (3, True))

    def test_each_task_gets_its_own_stages(self):
        """배송지 조회 과업: 쇼핑 단계(결제 정보 등)에 끼워 넣지 않고 지나간 화면 그대로."""
        titles = {"/delivery": "배송 가능 지역", "/delivery/result": "조회 결과"}
        rows = [row(["/", "/delivery", "/delivery/result"], titles=titles) for _ in range(4)]
        funnel = journey_funnel(rows, success_path="/delivery/result")
        self.assertEqual([(s["id"], s["name"]) for s in funnel["stages"]],
                         [("/", "/"), ("/delivery", "배송 가능 지역"), ("/delivery/result", "조회 결과")])

    def test_goal_screen_is_last_even_when_reached_early_by_index(self):
        rows = [row(["/", "/order/success"]), row(["/", "/product", "/cart", "/order/success"])]
        funnel = journey_funnel(rows, success_path="/order/success")
        self.assertEqual(funnel["stages"][-1]["id"], "/order/success")

    def test_rare_detours_are_grouped_as_other(self):
        """10명 중 1명만 들른 화면은 단계로 만들지 않고, 거기서 이탈하면 'other' 로 센다."""
        rows = [row(ORDER) for _ in range(9)] + [row(["/", "/story"], "dropout")]
        funnel = journey_funnel(rows, success_path="/order/success")
        self.assertNotIn("/story", [s["id"] for s in funnel["stages"]])
        self.assertEqual(funnel["other"], {"screens": 1, "dropouts": 1})
        self.assertEqual(funnel["heatmap"]["stages"][-1], "other")
        self.assertEqual(funnel["heatmap"]["rows"][0]["dropouts"][-1], 1)

    def test_screens_on_a_successful_path_are_kept_even_if_rare(self):
        rows = [row(["/", "/coupon", "/order/success"])] + [row(["/", "/product"], "dropout") for _ in range(9)]
        funnel = journey_funnel(rows, success_path="/order/success")
        self.assertIn("/coupon", [s["id"] for s in funnel["stages"]])

    def test_shared_titles_fall_back_to_paths(self):
        """SPA 처럼 여러 화면의 제목이 같으면 이름으로 구분이 안 되니 경로를 쓴다."""
        same = {"/": "반찬로드", "/product": "반찬로드", "/cart": "장바구니"}
        funnel = journey_funnel([row(["/", "/product", "/cart"], titles=same)])
        self.assertEqual([s["name"] for s in funnel["stages"]], ["/", "/product", "장바구니"])

    def test_common_site_name_is_removed_from_titles(self):
        titles = {"/cart": "장바구니 | 반찬로드", "/signup": "회원가입 | 반찬로드", "/checkout": "배송 정보 | 반찬로드"}
        funnel = journey_funnel([row(["/cart", "/signup", "/checkout"], titles=titles)])
        self.assertEqual([s["name"] for s in funnel["stages"]], ["장바구니", "회원가입", "배송 정보"])

    def test_seed_ranges_and_segments(self):
        other = ("segment_2", "꼼꼼한 비교형")
        rows = [row(ORDER, seed=1), row(ORDER[:3], "dropout", seed=2, segment=other)]
        funnel = journey_funnel(rows, success_path="/order/success", seeds=2)
        cart = next(s for s in funnel["stages"] if s["id"] == "/cart")
        goal = funnel["stages"][-1]
        self.assertEqual((cart["reached_range"], goal["reached_range"]), ({"min": 1, "max": 1}, {"min": 0, "max": 1}))
        rows_by_segment = {r["segment_id"]: r["dropouts"] for r in funnel["heatmap"]["rows"]}
        self.assertEqual(rows_by_segment["segment_2"][funnel["heatmap"]["stages"].index("/cart")], 1)

    def test_no_screens_means_empty_funnel(self):
        self.assertEqual(journey_funnel([])["stages"], [])


class ReportIntegrationTests(SimpleTestCase):
    def test_task_report_includes_journey_funnel_and_keeps_seven_stage_funnel(self):
        snapshot = {"personas": [{"code": f"P-00{i}", "segment_id": "segment_1", "segment_name": "빠른 구매형"}
                                 for i in range(1, 4)],
                    "limits": {"seed_count": 1},
                    "tasks": [{"id": "delivery", "name": "배송지 조회", "success_path": "/delivery/result"}]}
        records = [{"task_id": "delivery", "seed": 1, "persona_id": f"P-00{i}", "status": "success", "reason": "",
                    "steps": 3, "duration_seconds": 1.0, "stages": ["landing", "payment", "payment"],
                    "screens": [{"url": BASE + p, "title": ""} for p in ("/", "/delivery", "/delivery/result")],
                    "causes": [], "primary_cause": None} for i in range(1, 4)]
        task = build_report_data(snapshot, records)["tasks"][0]
        self.assertEqual(len(task["funnel"]), 7)                                   # 기존 화면 호환
        self.assertEqual([s["id"] for s in task["journey_funnel"]["stages"]], ["/", "/delivery", "/delivery/result"])
