from unittest.mock import patch

from django.test import TestCase

from agent.preflight import FEATURES
from common.testing import WizardMixin, fake_discovery


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class DiscoveryTests(WizardMixin, TestCase):
    def test_create_uses_project_url_by_default(self, _):
        d = self.discover()
        self.assertEqual(d["entry_url"], "http://shop.local:8080/")
        self.assertEqual({f["id"] for f in d["features"]}, set(FEATURES))
        self.assertFalse(d["expired"])

    def test_get_detail(self, _):
        d = self.discover()
        res = self.client.get(f"/api/v1/discoveries/{d['id']}")
        self.assertEqual(res.json()["id"], d["id"])

    def test_rejects_other_origin_and_bad_scheme(self, _):
        url = f"/api/v1/projects/{self.project.id}/discoveries"
        self.assertEqual(self.client.post(url, {"entry_url": "http://evil.example/"}, format="json").status_code, 422)
        self.assertEqual(self.client.post(url, {"entry_url": "ftp://shop.local:8080/"}, format="json").status_code, 422)

    def test_rejects_unknown_field(self, _):
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {"foo": 1}, format="json")
        self.assertIn("foo", res.json()["error"]["fields"])

    def test_test_account_mode_needs_accounts(self, _):
        # 테스트 계정이 4개 미만이면 시작할 수 없다 (계정 등록은 testaccounts 테스트에서 확인)
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries",
                               {"login_mode": "test_account"}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "TEST_ACCOUNTS_REQUIRED"))

    def test_site_unreachable_is_503(self, mock):
        mock.side_effect = RuntimeError("browser crashed")
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "SITE_UNREACHABLE"))

    def blank_project(self):
        """주소 없이 만든 프로젝트 (주소는 새 테스트 1단계에서 입력한다)."""
        self.project.entry_url = ""
        self.project.allowed_origins = []
        self.project.save(update_fields=["entry_url", "allowed_origins"])

    def test_project_without_url_registers_the_entered_url(self, _):
        self.blank_project()
        with patch("runs.netguard.resolve_host", return_value=["93.184.216.34"]):
            d = self.discover(entry_url="HTTP://Shop.Local:8080")
        self.assertEqual(d["entry_url"], "http://shop.local:8080/")       # 프로젝트에 저장된 정규화 주소로 확인한다
        self.project.refresh_from_db()
        self.assertEqual((self.project.entry_url, self.project.allowed_origins),
                         ("http://shop.local:8080/", ["http://shop.local:8080"]))

    def test_project_without_url_needs_an_url(self, _):
        self.blank_project()
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {}, format="json")
        self.assertEqual(res.status_code, 422, res.content)
        self.assertEqual(res.json()["error"]["fields"]["entry_url"], ["사이트 URL을 입력해주세요"])
        self.project.refresh_from_db()
        self.assertEqual(self.project.entry_url, "")
