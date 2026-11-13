from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from projects.models import Project
from runs.tests.factories import make_user

PUBLIC = ["93.184.216.34"]


def reply(status=200, location=None):
    response = MagicMock()
    response.status_code = status
    response.headers = {"Location": location} if location else {}
    return response


class UrlCheckTests(TestCase):
    def setUp(self):
        self.user = make_user("owner@example.com")
        self.client = APIClient()
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="반찬로드", entry_url="https://banchanroad.example/")
        resolver = patch("runs.netguard.resolve_host", return_value=PUBLIC)
        resolver.start()
        self.addCleanup(resolver.stop)

    def check(self, project=None, client=None):
        return (client or self.client).post(f"/api/v1/projects/{(project or self.project).pk}/check")

    def run_check(self, *responses_or_errors):
        with patch("projects.url_check.requests.get", side_effect=list(responses_or_errors)) as get:
            res = self.check()
        return res, get

    def test_reachable_site_is_ok_and_saved(self):
        res, get = self.run_check(reply(200))
        self.assertEqual(res.status_code, 200)
        check = res.json()["check"]
        self.assertEqual((check["status"], check["http_status"], check["reason"]), ("ok", 200, None))
        self.assertGreaterEqual(check["elapsed_ms"], 0)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.project.refresh_from_db()
        self.assertEqual((self.project.check_status, self.project.check_http), ("ok", 200))
        self.assertIsNotNone(self.project.check_checked_at)
        detail = self.client.get(f"/api/v1/projects/{self.project.pk}").json()["check"]
        self.assertEqual((detail["status"], detail["http_status"]), ("ok", 200))

    def test_redirects_are_followed_and_each_hop_is_checked(self):
        res, get = self.run_check(reply(301, "/shop"), reply(302, "https://www.banchanroad.example/home"), reply(200))
        self.assertEqual(res.json()["check"]["status"], "ok")
        self.assertEqual([c.args[0] for c in get.call_args_list], [
            "https://banchanroad.example/", "https://banchanroad.example/shop", "https://www.banchanroad.example/home"])

    def test_redirect_into_internal_network_is_blocked(self):
        def resolve(host, port):
            return ["169.254.169.254"] if host == "evil.example" else PUBLIC

        with patch("runs.netguard.resolve_host", side_effect=resolve):
            res, get = self.run_check(reply(302, "http://evil.example/latest/meta-data"))
        check = res.json()["check"]
        self.assertEqual((check["status"], check["reason"]), ("failed", "BLOCKED_TARGET"))
        self.assertEqual(get.call_count, 1)  # 내부 주소로는 요청하지 않는다

    def test_too_many_redirects(self):
        res, get = self.run_check(*[reply(302, "/again") for _ in range(10)])
        self.assertEqual(res.json()["check"]["reason"], "TOO_MANY_REDIRECTS")
        self.assertEqual(get.call_count, 4)

    def test_http_error_keeps_the_status_code(self):
        res, _ = self.run_check(reply(503))
        check = res.json()["check"]
        self.assertEqual((check["status"], check["http_status"], check["reason"]), ("failed", 503, "HTTP_ERROR"))
        self.assertIn("503", check["message"])

    def test_network_failures_get_friendly_reasons(self):
        cases = {
            "TIMEOUT": requests.exceptions.ConnectTimeout("x"),
            "TLS_ERROR": requests.exceptions.SSLError("x"),
            "DNS_FAILED": requests.exceptions.ConnectionError("Failed to resolve 'x' (Name or service not known)"),
            "CONNECTION_FAILED": requests.exceptions.ConnectionError("Connection refused"),
        }
        for reason, error in cases.items():
            with self.subTest(reason=reason):
                res, _ = self.run_check(error)
                check = res.json()["check"]
                self.assertEqual((check["status"], check["reason"]), ("failed", reason))
                self.assertNotIn("Connection refused", check["message"])  # 내부 오류 문구는 숨긴다

    def test_private_address_is_never_requested(self):
        self.project.entry_url = "http://10.0.0.5:8080/"
        self.project.save()
        with patch("runs.netguard.resolve_host", return_value=["10.0.0.5"]):
            res, get = self.run_check()
        self.assertEqual(res.json()["check"]["reason"], "BLOCKED_TARGET")
        get.assert_not_called()

    def test_allowed_private_origin_can_be_checked(self):
        self.project.entry_url = "http://shop.local:8080/"
        self.project.save()
        with patch("runs.netguard.resolve_host", return_value=["10.0.0.5"]), \
                override_settings(ALLOWED_TARGET_ORIGINS=["http://shop.local:8080"]):
            res, _ = self.run_check(reply(200))
        self.assertEqual(res.json()["check"]["status"], "ok")

    def test_recheck_overwrites_previous_result(self):
        self.run_check(reply(503))
        res, _ = self.run_check(reply(200))
        self.assertEqual(res.json()["check"]["status"], "ok")
        self.project.refresh_from_db()
        self.assertEqual((self.project.check_status, self.project.check_http), ("ok", 200))

    def test_failed_check_clears_stale_http_status(self):
        self.run_check(reply(200))
        res, _ = self.run_check(requests.exceptions.ConnectTimeout("x"))
        self.assertIsNone(res.json()["check"]["http_status"])
        self.project.refresh_from_db()
        self.assertEqual((self.project.check_status, self.project.check_http), ("failed", None))

    def test_access_rules(self):
        stranger = APIClient()
        stranger.force_login(make_user("other@example.com"))
        with patch("projects.url_check.requests.get") as get:
            self.assertEqual(self.check(client=stranger).status_code, 403)
            self.assertEqual(self.check(client=APIClient()).status_code, 401)
            missing = self.client.post("/api/v1/projects/999999/check")
        self.assertEqual(missing.status_code, 404)
        get.assert_not_called()
