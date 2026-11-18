"""받은 정적 사이트 미리보기 (sources/preview.py): 127.0.0.1 에서 스냅샷 폴더만 읽기 전용으로 내보낸다."""
import json
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from runs import netguard
from sources import preview


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


@override_settings(EXITPLAIN_PREVIEW_PORT=8090)
class PreviewServerTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.sid = uuid.uuid4()
        tree = self.root / str(self.sid)
        (tree / "web" / "assets").mkdir(parents=True)
        (tree / "web" / "index.html").write_text("<h1>홈</h1>", encoding="utf-8")
        (tree / "web" / "assets" / "app.js").write_text("go('/cart')", encoding="utf-8")
        (tree / "web" / ".secret").write_text("x", encoding="utf-8")
        (tree / "README.md").write_text("밖", encoding="utf-8")
        preview.write_manifest(tree, "web")
        self.server = preview.make_server(bind="127.0.0.1", port=0, source_root=self.root)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def get(self, path, host=None, method="GET"):
        host = host or f"s-{self.sid.hex}.localhost:{self.port}"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"Host": host}, method=method)
        try:
            with opener().open(req, timeout=5) as res:
                return res.status, res.read().decode(), res.headers.get("Content-Type")
        except urllib.error.HTTPError as exc:
            return exc.code, "", None

    def test_serves_files_and_spa_routes_from_the_manifest_folder_only(self):
        self.assertEqual(self.get("/")[:2], (200, "<h1>홈</h1>"))
        status, body, ctype = self.get("/assets/app.js")
        self.assertEqual((status, body), (200, "go('/cart')"))
        self.assertIn("javascript", ctype)
        self.assertEqual(self.get("/order/success")[:2], (200, "<h1>홈</h1>"))     # 화면 주소는 index.html
        self.assertEqual(self.get("/assets/missing.png")[0], 404)                   # 없는 파일은 404

    def test_hidden_files_traversal_writes_and_unknown_hosts_are_refused(self):
        self.assertEqual(self.get("/.secret")[0], 404)
        self.assertEqual(self.get(f"/{preview.MANIFEST}")[0], 404)
        self.assertEqual(self.get("/../README.md")[0], 404)
        self.assertEqual(self.get("/%2e%2e/README.md")[0], 404)
        self.assertEqual(self.get("/", method="POST")[0], 405)
        self.assertEqual(self.get("/", host=f"s-{uuid.uuid4().hex}.localhost:{self.port}")[0], 404)
        self.assertEqual(self.get("/", host=f"evil.example:{self.port}")[0], 404)

    def test_deleted_files_close_the_preview(self):
        shutil.rmtree(self.root / str(self.sid))
        self.assertEqual(self.get("/")[0], 404)

    def test_reachable_checks_through_the_loopback(self):
        with override_settings(EXITPLAIN_PREVIEW_PORT=self.port):
            self.assertTrue(preview.reachable(preview.origin_for(self.sid)))
            self.assertFalse(preview.reachable(preview.origin_for(uuid.uuid4())))

    def test_manifest_pointing_outside_is_ignored(self):
        (self.root / str(self.sid) / preview.MANIFEST).write_text(json.dumps({"root": "../.."}), encoding="utf-8")
        self.assertEqual(self.get("/")[0], 404)


@override_settings(EXITPLAIN_PREVIEW_PORT=8090, ALLOWED_TARGET_ORIGINS=[])
class NetguardPreviewTests(SimpleTestCase):
    def test_only_the_preview_origin_shape_on_the_preview_port_is_exempt(self):
        sid = uuid.uuid4()
        self.assertIsNone(netguard.check_origin(f"http://s-{sid.hex}.localhost:8090"))
        with patch("runs.netguard.resolve_host", return_value=["127.0.0.1"]):
            for bad in (f"http://s-{sid.hex}.localhost:8000", f"https://s-{sid.hex}.localhost:8090",
                        "http://localhost:8090", f"http://x{sid.hex}.localhost:8090"):
                with self.subTest(bad), self.assertRaises(netguard.TargetNotAllowed):
                    netguard.check_origin(bad)
