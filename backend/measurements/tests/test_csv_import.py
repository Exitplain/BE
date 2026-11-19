"""실측 CSV 올리기: 형식 검사·세션 id 해시·30일 창·중복 파일·지우기·예시 파일."""
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from common.testing import WizardMixin
from measurements.models import MeasureHit, MeasureImport


def stamp(**delta):
    return (timezone.localtime(timezone.now() - timedelta(**delta), timezone.get_fixed_timezone(540))
            .strftime("%Y-%m-%d %H:%M:%S"))


class CsvImportTests(WizardMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = f"/api/v1/projects/{self.project.id}/measurement"

    def upload(self, text, name="log.csv", encoding="utf-8"):
        file = SimpleUploadedFile(name, text.encode(encoding), content_type="text/csv")
        return self.client.post(self.url + "/imports", {"file": file}, format="multipart")

    def test_rows_become_hits_with_hashed_sessions_and_log_times(self):
        text = ("session_id,timestamp,path,step,device\n"
                f"user@example.com,{stamp(hours=3)},/,,desktop\n"
                f"user@example.com,{stamp(hours=3, seconds=-20)},https://shop.example.com/Product/12?ref=ad,,desktop\n"
                f"b27d90,{stamp(days=1)},/cart,cart,mobile\n"
                f"b27d90,{stamp(days=1)},/cart,cart,mobile\n"              # 같은 줄 두 번 → 한 번만
                f"c1,{stamp(days=40)},/,,\n"                                  # 30일 밖
                "c2,어제,/,,\n"                                                # 시각 형식 틀림
                f"c3,{stamp(hours=1)},cart,,\n")                               # 경로 형식 틀림
        res = self.upload(text)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body["rows_total"], body["rows_imported"], body["sessions"]), (7, 3, 2))
        self.assertEqual(body["skipped"], {"invalid": 2, "outside_window": 1, "duplicate": 1})
        self.assertEqual([e["line"] for e in body["errors"]], [7, 8])
        hits = MeasureHit.objects.order_by("created_at")
        self.assertEqual([h.path for h in hits], ["/cart", "/", "/product/:id"])
        self.assertNotIn("user@example.com", {h.sid for h in hits})               # 세션 id 는 해시만
        self.assertTrue(all(h.sid.startswith("c") and len(h.sid) == 32 for h in hits))
        self.assertLess(hits[0].created_at, timezone.now() - timedelta(hours=20))  # 올린 시각이 아니라 로그 시각
        status = self.client.get(self.url).json()
        self.assertEqual(status["imports"]["sessions"], 2)
        self.assertFalse(status["installed"])                                     # 스크립트 설치와는 별개
        self.assertEqual(self.client.get(self.url + "/steps?days=7").json()["total_sessions"], 2)

    def test_same_file_twice_is_409_and_delete_removes_its_rows(self):
        text = f"session_id,timestamp,path\na,{stamp(hours=1)},/\n"
        first = self.upload(text).json()
        self.assertEqual(self.upload(text).status_code, 409)
        self.assertEqual(len(self.client.get(self.url + "/imports").json()["items"]), 1)
        self.assertEqual(self.client.delete(f"{self.url}/imports/{first['id']}").status_code, 204)
        self.assertEqual((MeasureHit.objects.count(), MeasureImport.objects.count()), (0, 0))

    def test_excel_cp949_unix_times_and_screen_width(self):
        ms = int((timezone.now() - timedelta(hours=2)).timestamp() * 1000)
        text = f"Session_ID,Timestamp,Path,Screen_Width\n방문1,{ms},/,390\n방문1,{ms // 1000 + 5},/cart,390\n"
        body = self.upload(text, encoding="cp949").json()
        self.assertEqual(body["rows_imported"], 2)
        self.assertEqual(set(MeasureHit.objects.values_list("device", flat=True)), {"mobile"})

    def test_missing_columns_or_no_valid_rows_are_422(self):
        res = self.upload("sid,time,url\na,b,/\n")
        self.assertEqual(res.status_code, 422)
        self.assertIn("session_id", res.json()["error"]["message"])
        res = self.upload(f"session_id,timestamp,path\na,{stamp(days=60)},/\n")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (422, "NO_VALID_ROWS"))
        self.assertEqual(MeasureImport.objects.count(), 0)

    def test_template_is_a_csv_with_the_expected_header(self):
        res = self.client.get(self.url + "/csv-template")
        self.assertEqual(res.status_code, 200)
        self.assertIn("attachment", res["Content-Disposition"])
        self.assertTrue(res.content.decode("utf-8-sig").startswith("session_id,timestamp,path,step,device\n"))

    def test_other_users_cannot_upload(self):
        self.client.logout()
        self.assertIn(self.upload("x").status_code, (401, 403))
