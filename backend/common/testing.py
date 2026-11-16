"""여러 앱 테스트가 같이 쓰는 준비 코드."""
from copy import deepcopy

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from common.projects import project_model


def fake_discovery(variant):
    """agent.preflight.discover_site와 같은 모양. 4개 기능이 모두 보인 경우."""
    from agent.preflight import FEATURES, TASK_FEATURE
    from agent.shop_tasks import DEFAULT_TASK_IDS, TASKS
    features = [{"id": fid, "name": m["name"], "evidence": {"label": m["hint"], "path": m["path"]},
                 "verification": "visible_entry_only"} for fid, m in FEATURES.items()]
    tasks = [{**deepcopy(t), "feature_id": TASK_FEATURE[t["id"]], "suggested": t["id"] in DEFAULT_TASK_IDS}
             for t in TASKS.values()]
    return {"project": "반찬로드", "features": features, "tasks": tasks, "note": "fixture"}


class WizardMixin:
    """setUp에서 사용자 2명·프로젝트 1개를 만들고 위저드를 단계별로 진행하는 도우미."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("daeun", password="pw")
        self.other = User.objects.create_user("other", password="pw")
        self.project = project_model().objects.create(owner=self.user, name="반찬로드",
                                                      entry_url="http://shop.local:8080/")
        self.client = APIClient()
        self.client.force_login(self.user)

    def discover(self, **body):
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", body, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def create_plan(self):
        discovery = self.discover()
        res = self.client.post(f"/api/v1/projects/{self.project.id}/plans",
                               {"discovery_id": discovery["id"]}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def patch(self, plan, body, revision=None):
        rev = plan["revision"] if revision is None else revision
        return self.client.patch(f"/api/v1/plans/{plan['id']}", body, format="json", HTTP_IF_MATCH=f'"{rev}"')

    STEP2 = {"task_ids": ["order", "coupon"], "custom_tasks": ["배송지 바꾸기"],
             "additional_request": "쿠폰 적용 후 총액이 바로 보이는지",
             "audience": {"context": "퇴근 후 모바일로 저녁 반찬을 처음 주문하는 1~2인 가구", "ages": ["30s"]}}

    # 페르소나 기본값은 10명(3·3·2·2)이다. 결과 집계를 20명 기준 데이터로 보는 테스트는 20명을 고른다.
    PANEL_20 = {"segments": [{"id": f"segment_{i}", "count": 5} for i in range(1, 5)]}

    def ready_plan(self):
        res = self.patch(self.create_plan(), {**self.STEP2, **self.PANEL_20})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def multi_seed_plan(self, seed_count=3):
        """시드 기본값은 1이다. 과업 × 시드 동작을 보는 테스트는 시드를 여러 개로 명시한다."""
        res = self.patch(self.ready_plan(), {"limits": {"seed_count": seed_count}})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def confirm(self, plan=None):
        plan = plan or self.ready_plan()
        res = self.client.post(f"/api/v1/plans/{plan['id']}/confirmations", {"revision": plan["revision"]},
                               format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()