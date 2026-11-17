"""테스트 계정 비밀번호 KMS 봉투 암호화 (AWS 없이 가짜 KMS로 확인)."""
import os
from io import StringIO
from unittest.mock import patch

from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from testaccounts import crypto
from testaccounts.models import TestAccount
from testaccounts.services import credentials_for_snapshot

LOGIN = "http://shop.local:8080/login"
KEY = "arn:aws:kms:ap-northeast-2:111122223333:key/test"


class FakeKMS:
    """GenerateDataKey/Decrypt만 흉내. 암호화 컨텍스트가 다르면 진짜 KMS처럼 거부한다."""

    def __init__(self):
        self.keys, self.calls, self.down = {}, [], False

    def _check(self, op):
        self.calls.append(op)
        if self.down:
            raise EndpointConnectionError(endpoint_url="https://kms.ap-northeast-2.amazonaws.com")

    def generate_data_key(self, *, KeyId, KeySpec, EncryptionContext):
        self._check("GenerateDataKey")
        assert (KeyId, KeySpec) == (KEY, "AES_256")
        plaintext, blob = os.urandom(32), os.urandom(16)
        self.keys[blob] = (plaintext, dict(EncryptionContext))
        return {"Plaintext": plaintext, "CiphertextBlob": blob, "KeyId": KeyId}

    def decrypt(self, *, CiphertextBlob, EncryptionContext):
        self._check("Decrypt")
        plaintext, context = self.keys.get(CiphertextBlob, (None, None))
        if plaintext is None or context != EncryptionContext:
            raise ClientError({"Error": {"Code": "InvalidCiphertextException", "Message": "x"}}, "Decrypt")
        return {"Plaintext": plaintext, "KeyId": KEY}


class KmsMixin:
    def setUp(self):
        super().setUp()
        self.kms = FakeKMS()
        patcher = patch("testaccounts.crypto._kms", return_value=self.kms)
        patcher.start()
        self.addCleanup(patcher.stop)


@override_settings(TEST_ACCOUNT_KMS_KEY_ID=KEY)
class CryptoTests(KmsMixin, TestCase):
    def test_round_trip_and_format(self):
        token = crypto.encrypt("pw-123", project_id=7)
        self.assertTrue(token.startswith("kms1:"))
        self.assertNotIn("pw-123", token)
        self.assertEqual(crypto.decrypt(token, project_id=7), "pw-123")
        self.assertEqual(self.kms.calls, ["GenerateDataKey", "Decrypt"])

    def test_other_project_cannot_decrypt(self):
        token = crypto.encrypt("pw-123", project_id=7)
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt(token, project_id=8)

    def test_kms_down_is_key_unavailable(self):
        self.kms.down = True
        with self.assertRaises(crypto.KeyUnavailable):
            crypto.encrypt("pw", project_id=1)

    def test_access_denied_cause_is_logged_without_password(self):
        """화면 문구는 하나라 원인은 로그로 본다 (운영에서 IAM 역할에 kms 권한이 없던 경우)."""
        denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                             "GenerateDataKey")
        with patch.object(self.kms, "generate_data_key", side_effect=denied), \
                self.assertLogs("testaccounts.crypto", "WARNING") as logs, self.assertRaises(crypto.KeyUnavailable):
            crypto.encrypt("secret-pw", project_id=1)
        self.assertIn("GenerateDataKey", logs.output[0])
        self.assertIn("AccessDeniedException", logs.output[0])
        self.assertNotIn("secret-pw", logs.output[0])

    def test_old_fernet_value_still_readable(self):
        with override_settings(TEST_ACCOUNT_KMS_KEY_ID=""):
            old = crypto.encrypt("legacy-pw")
        self.assertEqual(crypto.decrypt(old, project_id=1), "legacy-pw")
        self.assertTrue(crypto.needs_reencrypt(old))

    def test_without_kms_setting_uses_fernet(self):
        with override_settings(TEST_ACCOUNT_KMS_KEY_ID=""):
            token = crypto.encrypt("pw")
        self.assertFalse(token.startswith("kms1:"))
        self.assertEqual(self.kms.calls, [])


@override_settings(TEST_ACCOUNT_KMS_KEY_ID=KEY)
@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class KmsApiTests(KmsMixin, WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/projects/{self.project.id}/test-accounts"

    def accounts(self, n):
        return [{"username": f"kms{i}@banchan.example", "password": f"kms-pw-{i}"} for i in range(1, n + 1)]

    def test_saved_with_kms_and_used_for_run(self, _):
        res = self.client.put(self.url, {"login_url": LOGIN, "accounts": self.accounts(4)}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(all(a.password_encrypted.startswith("kms1:") for a in TestAccount.objects.all()))
        discovery = self.discover(login_mode="test_account")
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans",
                                {"discovery_id": discovery["id"]}, format="json").json()
        plan = self.patch(plan, self.STEP2).json()
        snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        creds = credentials_for_snapshot(snap)
        self.assertEqual([c["password"] for c in creds], [f"kms-pw-{i}" for i in range(1, 5)])

    def test_kms_down_returns_503_and_saves_nothing(self, _):
        self.kms.down = True
        res = self.client.put(self.url, {"login_url": LOGIN, "accounts": self.accounts(2)}, format="json")
        self.assertEqual(res.status_code, 503, res.content)
        self.assertEqual(res.json()["error"]["code"], "TEST_ACCOUNT_KEY_UNAVAILABLE")
        self.assertEqual(TestAccount.objects.count(), 0)


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ReencryptCommandTests(KmsMixin, WizardMixin, TestCase):
    def test_moves_old_values_to_kms(self, _):
        url = f"/api/v1/projects/{self.project.id}/test-accounts"
        self.client.put(url, {"login_url": LOGIN, "accounts": [
            {"username": "a@banchan.example", "password": "pw-a"},
            {"username": "b@banchan.example", "password": "pw-b"}]}, format="json")   # KMS 끈 상태 → Fernet
        with override_settings(TEST_ACCOUNT_KMS_KEY_ID=KEY):
            out = StringIO()
            call_command("reencrypt_test_accounts", "--dry-run", stdout=out)
            self.assertIn("옮길 테스트 계정 2개", out.getvalue())
            call_command("reencrypt_test_accounts", stdout=out)
            self.assertIn("KMS로 옮김 2개", out.getvalue())
            accounts = list(TestAccount.objects.order_by("position"))
            self.assertTrue(all(a.password_encrypted.startswith("kms1:") for a in accounts))
            self.assertEqual(crypto.decrypt(accounts[0].password_encrypted, project_id=self.project.id), "pw-a")
            call_command("reencrypt_test_accounts", stdout=out)                   # 다시 돌려도 안전
            self.assertIn("KMS로 옮김 0개", out.getvalue())

    def test_requires_kms_setting(self, _):
        with self.assertRaises(CommandError):
            call_command("reencrypt_test_accounts", "--check", stdout=StringIO())

    @override_settings(TEST_ACCOUNT_KMS_KEY_ID=KEY)
    def test_check_reports_connection_failure(self, _):
        self.kms.down = True
        with self.assertRaises(CommandError):
            call_command("reencrypt_test_accounts", "--check", stdout=StringIO())