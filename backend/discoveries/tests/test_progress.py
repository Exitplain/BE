"""사이트 확인 진행 피드: Prefer: respond-async 로 시작하면 202 + running, 진행 사건이 쌓이고 GET 으로 읽는다.

테스트 설정은 EXITPLAIN_DISCOVERY_BACKGROUND=False 라 같은 요청 안에서 끝까지 돈다 (응답 모양은 백그라운드와 같다).
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from common.testing import WizardMixin, fake_discovery
from discoveries import services
from discoveries.models import Discovery

from .test_explore_mode import EXPLORED

SECRET = "pw-1,x"


def exploring(events, result=EXPLORED, error=None):
    """둘러보는 동안 진행 사건을 보내는 가짜 run_exploration."""
    def fake(entry_url, allowed_origins, login=None, progress=None):
        for event in events:
            progress(event)
        if error:
            raise error
        return result
    return fake


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class DiscoveryProgressTests(WizardMixin, TestCase):
    def start(self, **body):
        return self.client.post(f"/api/v1/projects/{self.project.id}/discoveries", body, format="json",
                                HTTP_PREFER="respond-async")

    def test_async_start_returns_202_with_ordered_progress(self, _):
        res = self.start()
        self.assertEqual(res.status_code, 202, res.content)
        d = res.json()
        self.assertEqual((d["status"], d["error"]), ("succeeded", None))
        self.assertEqual([e["kind"] for e in d["progress"]], ["queued", "demo_started", "demo_finished", "succeeded"])
        self.assertEqual([e["seq"] for e in d["progress"]], [1, 2, 3, 4])
        self.assertTrue(d["features"])
        got = self.client.get(f"/api/v1/discoveries/{d['id']}").json()
        self.assertEqual(got["progress"], d["progress"])

    def test_sync_start_keeps_201_and_records_progress(self, _):
        d = self.discover()
        self.assertEqual(d["status"], "succeeded")
        self.assertEqual(d["progress"][-1]["kind"], "succeeded")

    @override_settings(EXITPLAIN_DISCOVERY_MODE="explore")
    def test_explore_events_are_kept_in_order(self, _):
        events = [{"kind": "browser_started", "message": "브라우저를 열었어요"},
                  {"kind": "page_read", "message": "'홈' 화면을 읽었어요", "path": "/"},
                  {"kind": "write_blocked", "message": "POST /api/save 요청은 막았어요", "method": "POST", "path": "/api/save"},
                  {"kind": "ignored", "message": ""}]                               # 문구 없는 사건은 버린다
        with patch("discoveries.services.run_exploration", side_effect=exploring(events)):
            d = self.start().json()
        kinds = [e["kind"] for e in d["progress"]]
        self.assertEqual(kinds, ["queued", "browser_started", "page_read", "write_blocked", "succeeded"])
        self.assertEqual(d["progress"][3]["path"], "/api/save")
        self.assertTrue(all(isinstance(e["t"], float) for e in d["progress"]))

    @override_settings(EXITPLAIN_DISCOVERY_MODE="explore")
    def test_failure_is_recorded_and_blocks_plan_creation(self, _):
        boom = RuntimeError("Traceback /srv/exitplain/secret/path")
        with patch("discoveries.services.run_exploration", side_effect=exploring([], error=boom)):
            res = self.start()
        d = res.json()
        self.assertEqual((res.status_code, d["status"], d["error"]["code"]), (202, "failed", "SITE_UNREACHABLE"))
        self.assertEqual(d["progress"][-1]["kind"], "failed")
        self.assertNotIn("/srv/exitplain", json.dumps(d, ensure_ascii=False))     # 내부 예외 문구는 내보내지 않는다
        plan = self.client.post(f"/api/v1/projects/{self.project.id}/plans", {"discovery_id": d["id"]}, format="json")
        self.assertEqual((plan.status_code, plan.json()["error"]["code"]), (409, "DISCOVERY_NOT_READY"))

    @override_settings(EXITPLAIN_DISCOVERY_MODE="explore")
    def test_no_features_is_a_failed_discovery(self, _):
        empty = {**EXPLORED, "features": []}
        with patch("discoveries.services.run_exploration", side_effect=exploring([], result=empty)):
            d = self.start().json()
        self.assertEqual((d["status"], d["error"]["code"]), ("failed", "NO_FEATURES_FOUND"))

    @override_settings(EXITPLAIN_DISCOVERY_MODE="explore")
    def test_progress_keeps_only_recent_events(self, _):
        events = [{"kind": "page_read", "message": f"화면 {i}"} for i in range(150)]
        with patch("discoveries.services.run_exploration", side_effect=exploring(events)):
            d = self.start().json()
        self.assertEqual(len(d["progress"]), services.PROGRESS_LIMIT)
        self.assertEqual(d["progress"][-1]["kind"], "succeeded")
        self.assertEqual(d["progress"][-1]["seq"], 152)                          # 시작 1 + 사건 150 + 완료 1

    @override_settings(EXITPLAIN_DISCOVERY_MODE="explore")
    def test_test_account_values_never_reach_progress(self, _):
        accounts = [{"username": f"test{i:02d}@lecq.example", "password": f"pw-{i},x"} for i in range(1, 5)]
        self.client.put(f"/api/v1/projects/{self.project.id}/test-accounts",
                        {"login_url": "http://shop.local:8080/login", "accounts": accounts}, format="json")
        events = [{"kind": "login_started", "message": "테스트 계정으로 로그인하는 중이에요"},
                  {"kind": "login_succeeded", "message": "로그인했어요", "path": "/home"}]
        with patch("discoveries.services.run_exploration", side_effect=exploring(events)) as explore:
            d = self.start(login_mode="test_account").json()
        self.assertEqual(explore.call_args.kwargs["login"]["password"], SECRET)
        body = json.dumps(d, ensure_ascii=False)
        self.assertNotIn(SECRET, body)
        self.assertNotIn("test01@lecq.example", body)

    def test_stale_running_discovery_is_marked_interrupted(self, _):
        d = Discovery.objects.create(project=self.project, created_by=self.user, entry_url="http://shop.local:8080/",
                                     variant="problem", status=Discovery.Status.RUNNING, progress=[],
                                     expires_at=timezone.now() + timedelta(minutes=15))
        Discovery.objects.filter(pk=d.pk).update(updated_at=timezone.now() - timedelta(seconds=services.STALE_SECONDS + 5))
        got = self.client.get(f"/api/v1/discoveries/{d.pk}").json()
        self.assertEqual((got["status"], got["error"]["code"]), ("failed", "DISCOVERY_INTERRUPTED"))
        self.assertEqual(got["progress"][-1]["kind"], "failed")

    @override_settings(EXITPLAIN_DISCOVERY_MAX_ACTIVE=1)
    def test_busy_server_rejects_new_async_discovery(self, _):
        Discovery.objects.create(project=self.project, created_by=self.user, entry_url="http://shop.local:8080/",
                                 variant="problem", status=Discovery.Status.RUNNING,
                                 expires_at=timezone.now() + timedelta(minutes=15))
        res = self.start()
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (429, "DISCOVERY_BUSY"))

    def test_validation_errors_do_not_create_rows(self, _):
        res = self.start(entry_url="http://evil.example/")
        self.assertEqual(res.status_code, 422)
        self.assertFalse(Discovery.objects.exists())


class ProgressLogTests(WizardMixin, TestCase):
    def test_heartbeat_saves_without_new_events(self):
        d = Discovery.objects.create(project=self.project, created_by=self.user, entry_url="http://shop.local:8080/",
                                     variant="problem", status=Discovery.Status.RUNNING, progress=[],
                                     expires_at=timezone.now() + timedelta(minutes=15))
        old = timezone.now() - timedelta(minutes=1)
        Discovery.objects.filter(pk=d.pk).update(updated_at=old)
        d.refresh_from_db()
        log = services.ProgressLog(d)
        log.flush()                                        # 새 사건도 없고 방금 만들었으니 저장하지 않는다
        d.refresh_from_db()
        self.assertEqual(d.updated_at, old)
        log.saved_at -= services.HEARTBEAT_SECONDS
        log.flush()                                        # 하트비트 간격이 지나면 사건이 없어도 저장
        d.refresh_from_db()
        self.assertGreater(d.updated_at, old)
        log.add({"kind": "page_read", "message": "홈"})
        log.flush()
        d.refresh_from_db()
        self.assertEqual([e["message"] for e in d.progress], ["홈"])
