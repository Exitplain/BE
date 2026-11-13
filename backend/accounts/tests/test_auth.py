import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.google import GoogleTokenError
from accounts.models import EmailLoginCode, ExternalIdentity, Workspace, WorkspaceMember

GOOGLE_USER = {"sub": "google-123", "email": "maker@example.com", "name": "메이커"}


def fake_google(token):
    if token != "good-token":
        raise GoogleTokenError("Google 토큰을 확인하지 못했습니다.")
    return GOOGLE_USER


class EmailCodeLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def request_code(self, email="Maker@Example.com"):
        res = self.client.post("/api/v1/auth/email/request", {"email": email}, format="json")
        self.assertEqual(res.status_code, 202, res.content)
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def verify(self, code, email="maker@example.com"):
        return self.client.post("/api/v1/auth/email/verify", {"email": email, "code": code}, format="json")

    def test_full_login_creates_user_workspace_and_session(self):
        code = self.request_code()
        self.assertEqual(mail.outbox[0].to, ["maker@example.com"])          # 소문자로 정리
        res = self.verify(code)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["user"]["email"], "maker@example.com")
        self.assertEqual(body["workspace"]["name"], "maker의 워크스페이스")
        self.assertEqual(body["workspace"]["role"], "owner")
        self.assertIn("session_expires_at", body)
        self.assertTrue(self.client.get("/api/v1/auth/session").json()["authenticated"])
        user = get_user_model().objects.get(username="maker@example.com")
        self.assertFalse(user.has_usable_password())

    def test_code_is_stored_hashed_and_single_use(self):
        code = self.request_code()
        self.assertNotIn(code, EmailLoginCode.objects.get().code_hash)
        self.assertEqual(self.verify(code).status_code, 200)
        self.client.logout()
        self.assertEqual(self.verify(code).json()["error"]["code"], "CODE_NOT_REQUESTED")

    def test_wrong_code_counts_attempts_then_locks(self):
        code = self.request_code()
        wrong = "000000" if code != "000000" else "111111"
        res = self.verify(wrong)
        self.assertEqual(res.json()["error"]["message"], "코드가 맞지 않습니다. 다시 한번 입력해 주세요")
        self.assertEqual(res.json()["error"]["fields"]["remaining_attempts"], ["4"])
        for _ in range(4):
            self.verify(wrong)
        self.assertEqual(self.verify(code).json()["error"]["code"], "TOO_MANY_ATTEMPTS")

    def test_expired_code(self):
        code = self.request_code()
        EmailLoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(code).json()["error"]["code"], "CODE_EXPIRED")

    def test_resend_cooldown(self):
        self.request_code()
        res = self.client.post("/api/v1/auth/email/request", {"email": "maker@example.com"}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (429, "RESEND_TOO_SOON"))
        EmailLoginCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        self.request_code()
        self.assertEqual(len(mail.outbox), 2)

    def test_newest_code_wins(self):
        old = self.request_code()
        EmailLoginCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        new = self.request_code()
        if old != new:
            self.assertEqual(self.verify(old).status_code, 400)
        self.assertEqual(self.verify(new).status_code, 200)

    def test_input_validation(self):
        res = self.client.post("/api/v1/auth/email/request", {"email": "not-an-email"}, format="json")
        self.assertEqual(res.status_code, 422)
        self.request_code()
        self.assertEqual(self.verify("12ab56").status_code, 422)

    def test_logout_invalidates_session(self):
        self.verify(self.request_code())
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 204)
        self.assertFalse(self.client.get("/api/v1/auth/session").json()["authenticated"])
        self.assertEqual(self.client.get("/api/v1/workspace").status_code, 401)

    def test_login_requires_csrf_token(self):
        strict = APIClient(enforce_csrf_checks=True)
        res = strict.post("/api/v1/auth/email/request", {"email": "a@b.com"}, format="json")
        self.assertEqual(res.status_code, 403)
        token = strict.get("/api/v1/auth/csrf").json()["csrf_token"]
        res = strict.post("/api/v1/auth/email/request", {"email": "a@b.com"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(res.status_code, 202)

    @override_settings(SESSION_COOKIE_AGE=1)
    def test_session_expiry_returns_401(self):
        self.verify(self.request_code())
        from django.contrib.sessions.models import Session
        Session.objects.update(expire_date=timezone.now() - timedelta(seconds=1))
        res = self.client.get("/api/v1/workspace")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (401, "AUTH_REQUIRED"))


@override_settings(EXITPLAIN_GOOGLE_VERIFIER="accounts.tests.test_auth.fake_google")
class GoogleLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_google_login_creates_identity(self):
        res = self.client.post("/api/v1/auth/google", {"id_token": "good-token"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["user"]["name"], "메이커")
        self.assertEqual(res.json()["workspace"]["name"], "메이커의 워크스페이스")
        self.assertEqual(ExternalIdentity.objects.get().subject, "google-123")
        # 두 번째 로그인은 같은 사용자
        self.client.logout()
        self.client.post("/api/v1/auth/google", {"id_token": "good-token"}, format="json")
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_google_links_to_existing_email_user(self):
        user = get_user_model().objects.create(username="maker@example.com", email="maker@example.com")
        self.client.post("/api/v1/auth/google", {"id_token": "good-token"}, format="json")
        self.assertEqual(ExternalIdentity.objects.get().user, user)

    def test_bad_token(self):
        res = self.client.post("/api/v1/auth/google", {"id_token": "bad"}, format="json")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (400, "GOOGLE_TOKEN_INVALID"))

    @override_settings(EXITPLAIN_GOOGLE_VERIFIER="accounts.google.verify_id_token", GOOGLE_OAUTH_CLIENT_ID="")
    def test_missing_client_id_is_clear_error(self):
        res = self.client.post("/api/v1/auth/google", {"id_token": "x"}, format="json")
        self.assertIn("GOOGLE_OAUTH_CLIENT_ID", res.json()["error"]["message"])


class WorkspaceSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="u@example.com", email="u@example.com")
        self.client = APIClient()
        self.client.force_login(self.user)

    def test_get_creates_workspace_and_shows_run_defaults(self):
        body = self.client.get("/api/v1/workspace").json()
        self.assertEqual(body["workspace"]["name"], "u의 워크스페이스")
        self.assertEqual(body["me"]["email"], "u@example.com")
        self.assertEqual(body["run_defaults"]["viewport_width"], 390)

    def test_patch_names(self):
        res = self.client.patch("/api/v1/workspace", {"workspace_name": "반찬로드 팀", "my_name": "정은"},
                                format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["workspace"]["name"], res.json()["me"]["name"]), ("반찬로드 팀", "정은"))

    def test_patch_validation(self):
        self.assertEqual(self.client.patch("/api/v1/workspace", {}, format="json").status_code, 422)
        self.assertEqual(self.client.patch("/api/v1/workspace", {"workspace_name": ""}, format="json").status_code, 422)
        self.assertEqual(self.client.patch("/api/v1/workspace", {"email": "x@y.com"}, format="json").status_code, 422)

    def test_only_owner_renames_workspace(self):
        self.client.get("/api/v1/workspace")
        WorkspaceMember.objects.update(role=WorkspaceMember.Role.MEMBER)
        res = self.client.patch("/api/v1/workspace", {"workspace_name": "바꿈"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Workspace.objects.get().name, "u의 워크스페이스")