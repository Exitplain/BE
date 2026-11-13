from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from projects.models import Project
from projects.services import normalize_entry_url
from runs.models import Run
from runs.tests.factories import make_run, make_snapshot, make_user

URL = "/api/v1/projects"


class ProjectApiBase(TestCase):
    def setUp(self):
        self.user = make_user("owner@example.com")
        self.other = make_user("other@example.com")
        self.client = APIClient()
        self.client.force_login(self.user)

    def post(self, **body):
        return self.client.post(URL, body, format="json")

    def project(self, owner=None, name="반찬로드", url="https://banchanroad.example/", **extra):
        return Project.objects.create(owner=owner or self.user, name=name, entry_url=url, **extra)

    def touch(self, project, minutes_ago):
        Project.objects.filter(pk=project.pk).update(updated_at=timezone.now() - timedelta(minutes=minutes_ago))


class NormalizeUrlTests(TestCase):
    def test_equivalent_urls_become_the_same_string(self):
        same = ["https://BanchanRoad.example", "https://banchanroad.example/", "https://banchanroad.example:443/",
                "  https://banchanroad.example/#top  "]
        self.assertEqual({normalize_entry_url(u) for u in same}, {"https://banchanroad.example/"})

    def test_keeps_path_query_and_non_default_port(self):
        self.assertEqual(normalize_entry_url("http://shop.local:8080/a/b?x=1"), "http://shop.local:8080/a/b?x=1")

    def test_rejects_anything_but_http_and_https_without_credentials(self):
        from common.exceptions import ApiError
        for bad in ["banchanroad", "ftp://a.example", "javascript:alert(1)", "https://", "https://u:p@a.example/",
                    "https://a.example:99999/", "https://a b.example/", "", "   "]:
            with self.subTest(bad=bad), self.assertRaises(ApiError) as ctx:
                normalize_entry_url(bad)
            self.assertEqual(ctx.exception.fields["entry_url"], ["HTTP 또는 HTTPS로 시작하는 주소를 입력해주세요"])


class AuthTests(ProjectApiBase):
    def test_anonymous_gets_401_in_common_error_format(self):
        res = APIClient().get(URL)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "AUTH_REQUIRED")
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_post_requires_csrf_token_for_logged_in_browser_sessions(self):
        """프런트는 GET /api/v1/auth/csrf 로 쿠키를 받은 뒤 X-CSRFToken 헤더를 붙여 POST 한다."""
        browser = APIClient(enforce_csrf_checks=True)
        browser.force_login(self.user)
        body = {"name": "반찬로드", "entry_url": "https://banchanroad.example"}
        self.assertEqual(browser.post(URL, body, format="json").status_code, 403)  # 토큰 없음
        token = browser.get("/api/v1/auth/csrf").json()["csrf_token"]
        res = browser.post(URL, body, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Project.objects.count(), 1)


class CreateTests(ProjectApiBase):
    def test_creates_project_for_current_user(self):
        res = self.post(name="  반찬로드  ", entry_url="HTTPS://BanchanRoad.example", journey_note="첫 주문 완료")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body["name"], body["entry_url"], body["domain"]),
                         ("반찬로드", "https://banchanroad.example/", "banchanroad.example"))
        self.assertEqual(body["allowed_origins"], ["https://banchanroad.example"])
        self.assertEqual((body["login_mode"], body["journey_note"], body["is_sample"]), ("none", "첫 주문 완료", False))
        self.assertEqual(body["check"]["status"], "unchecked")
        self.assertEqual(body["runs"], {"total": 0, "active": 0, "last_pretest": None})
        self.assertEqual(Project.objects.get(pk=body["id"]).owner, self.user)

    def test_journey_note_is_optional(self):
        res = self.post(name="스튜디오 예약", entry_url="https://studio.example")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["journey_note"], "")

    def test_empty_name_and_bad_url_use_figma_messages(self):
        """Figma C04b: 이름 비움 + 주소 'banchanroad' 를 동시에 보냈을 때 두 칸에 각각 오류가 붙는다."""
        res = self.post(name="", entry_url="banchanroad")
        self.assertEqual(res.status_code, 422)
        error = res.json()["error"]
        self.assertEqual(error["code"], "VALIDATION_ERROR")
        self.assertEqual(error["fields"]["name"], ["프로젝트 이름을 입력해주세요"])
        self.assertEqual(error["fields"]["entry_url"], ["HTTP 또는 HTTPS로 시작하는 주소를 입력해주세요"])
        self.assertEqual(Project.objects.count(), 0)

    def test_url_format_error_alone(self):
        res = self.post(name="반찬로드", entry_url="banchanroad")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error"]["fields"]["entry_url"], ["HTTP 또는 HTTPS로 시작하는 주소를 입력해주세요"])

    def test_missing_fields_are_required_errors(self):
        res = self.post()
        fields = res.json()["error"]["fields"]
        self.assertEqual(res.status_code, 422)
        self.assertEqual(fields["name"], ["프로젝트 이름을 입력해주세요"])
        self.assertEqual(fields["entry_url"], ["사이트 URL을 입력해주세요"])

    def test_length_limits_and_unknown_fields(self):
        self.assertEqual(self.post(name="가" * 101, entry_url="https://a.example").status_code, 422)
        self.assertEqual(self.post(name="a", entry_url="https://a.example/" + "x" * 500).status_code, 422)
        self.assertEqual(self.post(name="a", entry_url="https://a.example", journey_note="가" * 301).status_code, 422)
        res = self.post(name="a", entry_url="https://a.example", owner=2, is_sample=True)
        self.assertEqual(res.status_code, 422)
        self.assertEqual(sorted(res.json()["error"]["fields"]), ["is_sample", "owner"])
        self.assertEqual(Project.objects.count(), 0)

    def test_duplicate_url_for_same_owner_is_409_even_if_written_differently(self):
        self.assertEqual(self.post(name="A", entry_url="https://banchanroad.example").status_code, 201)
        res = self.post(name="B", entry_url="https://BANCHANROAD.example/")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "DUPLICATE_PROJECT")
        self.assertEqual(res.json()["error"]["fields"]["entry_url"], ["이미 같은 주소의 프로젝트가 있어요"])
        self.assertEqual(Project.objects.count(), 1)

    def test_same_url_allowed_for_other_owner_and_after_archiving(self):
        old = self.project(url="https://banchanroad.example/")
        self.assertEqual(self.post(name="중복", entry_url="https://banchanroad.example/").status_code, 409)
        Project.objects.filter(pk=old.pk).update(archived_at=timezone.now())
        self.assertEqual(self.post(name="보관 후 다시", entry_url="https://banchanroad.example/").status_code, 201)

    def test_other_users_url_does_not_block_me(self):
        self.project(owner=self.other, url="https://a.example/")
        self.assertEqual(self.post(name="내 것", entry_url="https://a.example").status_code, 201)


class ListTests(ProjectApiBase):
    def test_lists_only_my_active_projects_newest_first_with_total(self):
        first = self.project(name="반찬로드", url="https://banchanroad.example/")
        second = self.project(name="스튜디오 예약", url="https://studio.example/")
        self.project(name="보관됨", url="https://old.example/", archived_at=timezone.now())
        self.project(owner=self.other, name="남의 프로젝트", url="https://other.example/")
        self.touch(first, 30)
        self.touch(second, 5)
        body = self.client.get(URL).json()
        self.assertEqual([p["name"] for p in body["items"]], ["스튜디오 예약", "반찬로드"])
        self.assertEqual((body["total"], body["next_cursor"]), (2, None))
        self.assertEqual(body["items"][0]["domain"], "studio.example")

    def test_archived_flag_shows_only_archived(self):
        self.project(name="활성")
        self.project(name="보관됨", url="https://old.example/", archived_at=timezone.now())
        body = self.client.get(URL, {"archived": "true"}).json()
        self.assertEqual([p["name"] for p in body["items"]], ["보관됨"])

    def test_search_by_name_or_url_ignoring_case(self):
        self.project(name="반찬로드", url="https://banchanroad.example/")
        self.project(name="Studio Booking", url="https://reserve.example/")
        self.project(name="중고폰 매입", url="https://phonebuy.example/")
        names = lambda q: sorted(p["name"] for p in self.client.get(URL, {"q": q}).json()["items"])  # noqa: E731
        self.assertEqual(names("반찬"), ["반찬로드"])
        self.assertEqual(names("studio"), ["Studio Booking"])
        self.assertEqual(names("PHONEBUY"), ["중고폰 매입"])
        self.assertEqual(names(".example"), ["Studio Booking", "반찬로드", "중고폰 매입"])
        self.assertEqual(names("없는검색어"), [])
        body = self.client.get(URL, {"q": "없는검색어"}).json()
        self.assertEqual((body["items"], body["total"]), ([], 0))

    def test_cursor_pagination_walks_all_pages_without_overlap(self):
        projects = [self.project(name=f"P{i}", url=f"https://p{i}.example/") for i in range(5)]
        for i, p in enumerate(projects):
            self.touch(p, i)  # P0 가 가장 최근
        seen, cursor, pages = [], "", 0
        while True:
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            body = self.client.get(URL, params).json()
            pages += 1
            seen += [p["name"] for p in body["items"]]
            self.assertEqual(body["total"], 5)
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, ["P0", "P1", "P2", "P3", "P4"])
        self.assertEqual(pages, 3)

    def test_same_timestamp_is_broken_by_id_so_nothing_is_skipped(self):
        stamp = timezone.now() - timedelta(minutes=1)
        for i in range(3):
            Project.objects.filter(pk=self.project(name=f"T{i}", url=f"https://t{i}.example/").pk).update(updated_at=stamp)
        first = self.client.get(URL, {"limit": 2}).json()
        second = self.client.get(URL, {"limit": 2, "cursor": first["next_cursor"]}).json()
        got = [p["name"] for p in first["items"] + second["items"]]
        self.assertEqual(sorted(got), ["T0", "T1", "T2"])
        self.assertIsNone(second["next_cursor"])

    def test_bad_query_values_are_422(self):
        for params in ({"limit": 0}, {"limit": 101}, {"limit": "abc"}, {"cursor": "%%%"}, {"cursor": "bm90LWpzb24"}):
            with self.subTest(params=params):
                res = self.client.get(URL, params)
                self.assertEqual(res.status_code, 422, res.content)
                self.assertEqual(res.json()["error"]["code"], "VALIDATION_ERROR")

    def test_last_pretest_is_the_highest_sequence_pretest_only(self):
        project = self.project()
        snapshot = make_snapshot(project, self.user)
        make_run(project, self.user, snapshot, sequence_no=1, status=Run.Status.COMPLETED)
        newest = make_run(project, self.user, snapshot, sequence_no=2, status=Run.Status.RUNNING)
        make_run(project, self.user, snapshot, sequence_no=3, kind=Run.Kind.CAUSE_REPRO, status=Run.Status.COMPLETED)
        item = self.client.get(URL).json()["items"][0]
        self.assertEqual(item["last_pretest"]["run_id"], str(newest.id))
        self.assertEqual((item["last_pretest"]["sequence_no"], item["last_pretest"]["status"]), (2, "running"))
        self.assertIsNone(item["last_pretest"]["finished_at"])

    def test_project_without_runs_has_null_last_pretest(self):
        self.project()
        self.assertIsNone(self.client.get(URL).json()["items"][0]["last_pretest"])

    def test_query_count_does_not_grow_with_number_of_projects(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(URL).status_code, 200)
            return len(ctx)

        self.project(name="P0", url="https://p0.example/")
        one = count_queries()
        for i in range(1, 9):
            self.project(name=f"P{i}", url=f"https://p{i}.example/")
        self.assertEqual(count_queries(), one)  # 프로젝트가 9개가 돼도 쿼리 수가 같다 (N+1 없음)


class DetailTests(ProjectApiBase):
    def test_owner_sees_overview_with_run_summary(self):
        project = self.project(journey_note="첫 주문 완료", check_status="ok", check_http=200, check_ms=312)
        snapshot = make_snapshot(project, self.user)
        make_run(project, self.user, snapshot, sequence_no=1, status=Run.Status.COMPLETED)
        make_run(project, self.user, snapshot, sequence_no=2, status=Run.Status.RUNNING)
        make_run(project, self.user, snapshot, sequence_no=3, status=Run.Status.QUEUED, kind=Run.Kind.RETEST)
        body = self.client.get(f"{URL}/{project.pk}").json()
        self.assertEqual(body["id"], project.pk)
        self.assertEqual(body["journey_note"], "첫 주문 완료")
        self.assertEqual(body["check"], {"status": "ok", "http_status": 200, "elapsed_ms": 312, "checked_at": None})
        self.assertEqual((body["runs"]["total"], body["runs"]["active"]), (3, 2))
        self.assertEqual(body["runs"]["last_pretest"]["sequence_no"], 2)
        self.assertNotIn("owner", body)

    def test_other_users_project_is_403_and_missing_is_404(self):
        theirs = self.project(owner=self.other)
        forbidden = self.client.get(f"{URL}/{theirs.pk}")
        self.assertEqual((forbidden.status_code, forbidden.json()["error"]["code"]), (403, "FORBIDDEN"))
        missing = self.client.get(f"{URL}/999999")
        self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (404, "NOT_FOUND"))

    def test_archived_project_is_still_readable(self):
        project = self.project(archived_at=timezone.now())
        body = self.client.get(f"{URL}/{project.pk}").json()
        self.assertIsNotNone(body["archived_at"])
