"""Markdown·PDF 내보내기 (11/16·17), 공유 링크 (11/17)."""
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from common.testing import WizardMixin, fake_discovery
from plans.models import PlanSnapshot
from plans.run_input import build_run_inputs
from reports import exporters
from reports.models import ShareLink
from reports.services import create_report_from_run_dir
from reports.tests.sample_runs import write_attempt

DROP = ("simulated_dropout", ["/", "/product", "/cart", "/signup"], "input_burden")
TECH = ("site_failure", ["/", "/product"])


@patch("discoveries.services.run_discovery", side_effect=fake_discovery)
class ExportTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def make_report(self):
        plan = self.patch(self.create_plan(), {**self.STEP2, "task_ids": ["order"]}).json()
        plan = self.patch(plan, {"limits": {"seed_count": 1}}).json()
        snap = PlanSnapshot.objects.get(pk=self.confirm(plan)["plan_revision_id"])
        for item in build_run_inputs(snap, run_id="run1"):
            write_attempt(self.root, item, {"P-001": DROP, "P-002": DROP, "P-003": TECH})
        return create_report_from_run_dir(snap, self.root, run_ref="run1", user=self.user)

    # --- Markdown ---
    def test_markdown_download(self, _):
        report = self.make_report()
        res = self.client.get(f"/api/v1/reports/{report.id}/export.md", HTTP_ACCEPT="text/markdown")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn('filename="exitplain-report-run1-r1.md"', res["Content-Disposition"])
        text = res.content.decode("utf-8")
        self.assertIn("# Exitplain 진단 리포트", text)
        self.assertIn("| 달성률 | 89.5% |", text)                       # 17 / (17 + 2), 기술 실패 1건 제외
        self.assertIn("| 가입 | 19 | 19 | 100.0% | 2 |", text)          # 퍼널 7단계 표
        self.assertIn("1. 입력 부담: 이탈 2명 (100.0%)", text)
        self.assertIn("## 기술 실패 (1건, 이탈률 계산에서 제외)", text)

    def test_markdown_to_html_escapes_and_builds_tables(self, _):
        page = exporters.markdown_to_html("# 제목\n\n| a | b |\n|---|---|\n| <x> | 1\\|2 |\n\n- 하나\n1. 둘\n> 안내")
        self.assertIn("<h1>제목</h1>", page)
        self.assertIn("<td>&lt;x&gt;</td><td>1|2</td>", page)
        self.assertIn("<ul><li>하나</li></ul>", page)
        self.assertIn("<ol><li>둘</li></ol>", page)
        self.assertIn("<blockquote>안내</blockquote>", page)

    # --- PDF ---
    def test_pdf_download(self, _):
        report = self.make_report()
        try:
            exporters.render_pdf("<p>확인</p>")
        except exporters.PdfUnavailable:
            raise SkipTest("Chromium이 설치되지 않은 환경")
        res = self.client.get(f"/api/v1/reports/{report.id}/export.pdf", HTTP_ACCEPT="application/pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))

    def test_pdf_unavailable_is_clear_error(self, _):
        report = self.make_report()
        with patch("reports.exporters.render_pdf", side_effect=exporters.PdfUnavailable("no browser")):
            res = self.client.get(f"/api/v1/reports/{report.id}/export.pdf")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (503, "PDF_UNAVAILABLE"))

    def test_export_owner_only(self, _):
        report = self.make_report()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/v1/reports/{report.id}/export.md").status_code, 403)

    # --- 공유 링크 ---
    def test_share_link_flow(self, _):
        report = self.make_report()
        res = self.client.post(f"/api/v1/reports/{report.id}/share-links", {"expires_in_days": 3}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        link = res.json()
        self.assertEqual(link["state"], "active")
        self.assertEqual(self.client.get(f"/api/v1/reports/{report.id}/share-links").json()["items"][0]["id"],
                         link["id"])

        anonymous = APIClient()                                             # 로그인 안 한 사람
        shared = anonymous.get(link["path"])
        self.assertEqual(shared.status_code, 200)
        self.assertTrue(shared.json()["shared"])
        self.assertEqual(shared.json()["data"]["summary"]["success"], 17)

        self.assertEqual(self.client.delete(f"/api/v1/share-links/{link['id']}").status_code, 204)
        self.assertEqual(anonymous.get(link["path"]).status_code, 404)    # 취소하면 안 열림

    def test_share_link_expiry_and_validation(self, _):
        report = self.make_report()
        link = self.client.post(f"/api/v1/reports/{report.id}/share-links", {}, format="json").json()
        ShareLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        res = APIClient().get(link["path"])
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (410, "SHARE_LINK_EXPIRED"))
        self.assertEqual(APIClient().get("/api/v1/shared/not-a-real-token").status_code, 404)
        url = f"/api/v1/reports/{report.id}/share-links"
        self.assertEqual(self.client.post(url, {"expires_in_days": 31}, format="json").status_code, 422)
        self.assertEqual(APIClient().post(url, {}, format="json").status_code, 401)   # 만들기는 로그인 필요
        self.client.force_login(self.other)
        self.assertEqual(self.client.delete(f"/api/v1/share-links/{link['id']}").status_code, 403)