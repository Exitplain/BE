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

    def test_login_connection_not_supported_yet(self, _):
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries",
                               {"login_mode": "test_account"}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "LOGIN_CONNECTION_UNAVAILABLE"))

    def test_site_unreachable_is_503(self, mock):
        mock.side_effect = RuntimeError("browser crashed")
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "SITE_UNREACHABLE"))