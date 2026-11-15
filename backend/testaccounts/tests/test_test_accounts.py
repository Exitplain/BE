"""테스트 계정 (P04·P04b·P04d·P05h): 저장·암호화·검증·권한, 그리고 위저드(계정 수 = 페르소나 수) 연결."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from testaccounts import crypto
from testaccounts.models import TestAccount
from testaccounts.services import credentials_for_snapshot, record_check

LOGIN = "http://shop.local:8080/login"


def accounts(n, start=1):
    return [{"username": f"test{i:02d}@banchan.example", "password": f"pw-{i},x"} for i in range(start, start + n)]


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class TestAccountTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.id}/test-accounts"

    def put(self, items, login_url=LOGIN):
        return self.client.put(self.url, {"login_url": login_url, "accounts": items}, format="json")

    # --- 저장 ---
    def test_save_never_returns_password_and_encrypts(self, _):
        res = self.put(accounts(3))
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["count"], body["max"], body["min"], body["ready"]), (3, 20, 4, False))
        self.assertEqual(body["login_url"], LOGIN)
        self.assertEqual([i["position"] for i in body["items"]], [1, 2, 3])
        self.assertNotIn("pw-1", res.content.decode())                  # 응답에 비밀번호 없음
        stored = TestAccount.objects.get(position=1).password_encrypted
        self.assertNotIn("pw-1", stored)                                   # DB에는 암호문
        self.assertEqual(crypto.decrypt(stored), "pw-1,x")                 # 쉼표가 든 비밀번호도 그대로
        self.assertNotIn("pw-1", self.client.get(self.url).content.decode())

    def test_existing_account_keeps_password_when_omitted_and_order_changes(self, _):
        items = self.put(accounts(2)).json()["items"]
        TestAccount.objects.filter(pk=items[0]["id"]).update(status="ok")
        res = self.put([{"id": items[1]["id"], "username": items[1]["username"]},           # 순서 바꿈
                        {"id": items[0]["id"], "username": items[0]["username"]},
                        {"username": "new@banchan.example", "password": "pw-new"}])
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()["items"]
        self.assertEqual([i["username"] for i in body],
                         ["test02@banchan.example", "test01@banchan.example", "new@banchan.example"])
        self.assertEqual(body[1]["status"], "ok")                          # 안 바뀐 계정은 확인 상태 유지
        self.assertEqual(crypto.decrypt(TestAccount.objects.get(pk=items[0]["id"]).password_encrypted), "pw-1,x")
        self.assertEqual(self.put([]).json()["count"], 0)                  # 빈 목록 = 전부 삭제

    def test_changed_password_resets_check_status(self, _):
        item = self.put(accounts(1)).json()["items"][0]
        TestAccount.objects.filter(pk=item["id"]).update(status="failed")
        res = self.put([{"id": item["id"], "username": item["username"], "password": "fixed"}])
        self.assertEqual(res.json()["items"][0]["status"], "unchecked")

    def test_validation(self, _):
        cases = [(accounts(21), LOGIN, "accounts"),
                 ([{"username": "a"}], LOGIN, "accounts.0.password"),
                 ([accounts(1)[0], accounts(1)[0]], LOGIN, "accounts.1.username"),
                 ([{"id": "00000000-0000-0000-0000-000000000000", "username": "a", "password": "b"}], LOGIN, "accounts.0.id"),
                 (accounts(1), "http://evil.example/login", "login_url"),
                 (accounts(1), "javascript:alert(1)", "login_url"),
                 ([{**accounts(1)[0], "email": "x"}], LOGIN, "accounts.0.email")]
        for items, login_url, field in cases:
            with self.subTest(field=field):
                res = self.put(items, login_url)
                self.assertEqual(res.status_code, 422, res.content)
                self.assertIn(field, res.json()["error"]["fields"])
        self.assertEqual(TestAccount.objects.count(), 0)                   # 실패하면 아무것도 저장 안 됨

    def test_owner_only(self, _):
        other = APIClient()
        other.force_login(get_user_model().objects.get(username="other"))
        self.assertEqual(other.get(self.url).status_code, 403)
        self.assertEqual(other.put(self.url, {"accounts": accounts(1)}, format="json").status_code, 403)

    # --- 위저드 연결 (P05h: 계정 수 = 페르소나 수) ---
    def test_wizard_uses_account_count_as_persona_total(self, _):
        self.put(accounts(10))
        discovery = self.discover(login_mode="test_account")
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans",
                                {"discovery_id": discovery["id"]}, format="json").json()
        self.assertEqual([s["count"] for s in plan["segments"]], [3, 3, 2, 2])
        self.assertEqual(plan["options"]["capabilities"]["persona_total"], 10)
        res = self.patch(plan, {"segments": [{"id": f"segment_{i}", "count": 5} for i in range(1, 5)]})
        self.assertEqual(res.status_code, 422)                              # 20명은 안 됨 (계정 10개)
        plan = self.patch(plan, self.STEP2).json()
        snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        self.assertEqual(len(snap.data["personas"]), 10)
        pairs = snap.data["test_accounts"]["pairs"]
        self.assertEqual(pairs[0], {"persona_code": snap.data["personas"][0]["code"],
                                    "account_id": str(TestAccount.objects.get(position=1).id),
                                    "username": "test01@banchan.example"})
        self.assertNotIn("pw-", str(snap.data))                             # 스냅샷에는 비밀번호 없음
        creds = credentials_for_snapshot(snap)
        self.assertEqual((len(creds), creds[0]["password"], creds[0]["login_url"]), (10, "pw-1,x", LOGIN))
        self.assertEqual(creds[0]["account_id"], pairs[0]["account_id"])
        record_check(creds[0]["account_id"], False, "비밀번호 오류")                 # P04c 실패 표시
        first = self.client.get(self.url).json()["items"][0]
        self.assertEqual((first["status"], first["status_message"]), ("failed", "비밀번호 오류"))

    def test_accounts_changed_after_plan_blocks_confirm(self, _):
        self.put(accounts(8))
        discovery = self.discover(login_mode="test_account")
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans",
                                {"discovery_id": discovery["id"]}, format="json").json()
        plan = self.patch(plan, self.STEP2).json()
        self.put(accounts(9))                                               # 계획 중에 계정이 늘어남
        res = self.client.post(f"/api/v1/plans/{plan['id']}/confirmations", {"revision": plan["revision"]},
                               format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "SEGMENT_TOTAL"))

    def test_no_login_mode_keeps_20_personas_and_no_accounts_in_snapshot(self, _):
        self.put(accounts(5))
        snap = PlanSnapshot.objects.get(pk=self.confirm()["plan_revision_id"])
        self.assertEqual(len(snap.data["personas"]), 20)
        self.assertNotIn("test_accounts", snap.data)
        self.assertEqual(credentials_for_snapshot(snap), [])