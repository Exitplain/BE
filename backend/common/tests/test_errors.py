from unittest.mock import patch

from django.test import TestCase

from common.testing import WizardMixin, fake_discovery


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ErrorFormatTests(WizardMixin, TestCase):
    def test_anonymous_gets_401_in_spec_format(self, _):
        self.client.logout()
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {}, format="json")
        self.assertEqual(res.status_code, 401)
        body = res.json()
        self.assertEqual(body["error"]["code"], "AUTH_REQUIRED")
        self.assertTrue(body["request_id"].startswith("req_"))

    def test_other_users_project_is_403(self, _):
        self.client.force_login(self.other)
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (403, "FORBIDDEN"))

    def test_missing_project_is_404(self, _):
        res = self.client.post("/api/v1/projects/9999/discoveries", {}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_invalid_json_is_400(self, _):
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", "{bad",
                               content_type="application/json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (400, "INVALID_JSON"))

    def test_validation_error_has_fields(self, _):
        res = self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", {"variant": "prod"}, format="json")
        self.assertEqual(res.status_code, 422)
        self.assertIn("variant", res.json()["error"]["fields"])