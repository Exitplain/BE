"""테스트 계정 감사 기록과 보관 기간 자동 삭제 (LLM 담당자 보안 피드백)."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from testaccounts.models import TestAccount, TestAccountEvent
from testaccounts.services import credentials_for_snapshot, purge_expired, record_check

LOGIN = "http://shop.local:8080/login"


def accounts(n):
    return [{"username": f"test{i:02d}@banchan.example", "password": f"secret-pw-{i}"} for i in range(1, n + 1)]


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class AuditTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.id}/test-accounts"

    def put(self, items):
        return self.client.put(self.url, {"login_url": LOGIN, "accounts": items}, format="json")

    def actions(self):
        return list(TestAccountEvent.objects.order_by("id").values_list("action", "username"))

    def test_add_update_remove_are_logged_without_passwords(self, _):
        items = self.put(accounts(2)).json()["items"]
        self.put([{"id": items[0]["id"], "username": items[0]["username"], "password": "new-secret"}])
        self.assertEqual(self.actions(), [
            ("added", "test01@banchan.example"), ("added", "test02@banchan.example"),
            ("removed", "test02@banchan.example"), ("updated", "test01@banchan.example")])
        updated = TestAccountEvent.objects.get(action="updated")
        self.assertEqual((updated.detail, updated.actor), ("비밀번호 변경", self.user))
        dump = str(list(TestAccountEvent.objects.values()))
        self.assertNotIn("secret", dump)                                    # 비밀번호는 어디에도 없음

    def test_unchanged_account_is_not_logged(self, _):
        items = self.put(accounts(1)).json()["items"]
        self.put([{"id": items[0]["id"], "username": items[0]["username"]}])
        self.assertEqual(TestAccountEvent.objects.count(), 1)               # 처음 등록만

    def test_events_api_owner_only(self, _):
        self.put(accounts(1))
        res = self.client.get(f"{self.url}/events")
        self.assertEqual(res.status_code, 200, res.content)
        item = res.json()["items"][0]
        self.assertEqual((item["action"], item["action_name"], item["actor"]), ("added", "등록", self.user.email))
        other = APIClient()
        other.force_login(get_user_model().objects.get(username="other"))
        self.assertEqual(other.get(f"{self.url}/events").status_code, 403)

    def test_use_and_check_are_logged_and_refresh_retention(self, _):
        self.put(accounts(4))
        discovery = self.discover(login_mode="test_account")
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans",
                                {"discovery_id": discovery["id"]}, format="json").json()
        plan = self.patch(plan, self.STEP2).json()
        snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        creds = credentials_for_snapshot(snap, run_id="run-1")
        record_check(creds[0]["account_id"], False, "비밀번호 오류")
        used = TestAccountEvent.objects.filter(action="used")
        self.assertEqual((used.count(), used.first().detail, used.first().actor), (4, "run run-1", None))
        self.assertEqual(TestAccountEvent.objects.get(action="checked").detail, "실패: 비밀번호 오류")
        self.assertTrue(all(a.last_used_at for a in TestAccount.objects.all()))
        self.assertNotIn("secret", str(list(TestAccountEvent.objects.values())))


@override_settings(TEST_ACCOUNT_RETENTION_DAYS=90)
@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class RetentionTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.id}/test-accounts"
        self.client.put(self.url, {"login_url": LOGIN, "accounts": accounts(3)}, format="json")
        self.old, self.recently_used, self.fresh = TestAccount.objects.order_by("position")
        long_ago = timezone.now() - timedelta(days=91)
        TestAccount.objects.filter(pk__in=[self.old.pk, self.recently_used.pk]).update(updated_at=long_ago)
        TestAccount.objects.filter(pk=self.recently_used.pk).update(last_used_at=timezone.now() - timedelta(days=1))

    def test_response_shows_retention_and_expiry(self, _):
        body = self.client.get(self.url).json()
        self.assertEqual(body["retention_days"], 90)
        self.assertTrue(all(i["expires_at"] for i in body["items"]))

    def test_purge_removes_only_inactive_accounts_and_logs(self, _):
        self.assertEqual(purge_expired(dry_run=True), 1)
        self.assertEqual(TestAccount.objects.count(), 3)                    # dry-run은 안 지움
        self.assertEqual(purge_expired(), 1)
        self.assertEqual(set(TestAccount.objects.values_list("pk", flat=True)), {self.recently_used.pk, self.fresh.pk})
        event = TestAccountEvent.objects.get(action="expired")
        self.assertEqual((event.username, event.actor), (self.old.username, None))

    def test_management_command(self, _):
        out = StringIO()
        call_command("purge_test_accounts", "--dry-run", stdout=out)
        self.assertIn("1개 지울 예정", out.getvalue())
        call_command("purge_test_accounts", stdout=out)
        self.assertEqual(TestAccount.objects.count(), 2)