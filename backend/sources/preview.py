"""받은 정적 사이트를 에이전트가 열 수 있게 서버 안에서만 내보내는 미리보기 (URL 없는 프로젝트, SRC-06).

- 127.0.0.1 에만 열고(EXITPLAIN_PREVIEW_BIND), 스냅샷마다 출처를 따로 준다:
  http://s-<스냅샷 id hex>.localhost:<EXITPLAIN_PREVIEW_PORT>/
  Chromium 은 *.localhost 를 루프백으로 푼다. 스냅샷끼리 출처가 달라 쿠키·저장소가 섞이지 않고, /assets/… 같은 절대 경로도 그대로 된다.
- 정적 파일만 읽어 보낸다. 코드를 실행하지 않는다. 없는 경로는 index.html 로 돌려 SPA 주소도 열린다.
- 스냅샷을 받을 때 쓴 manifest(MANIFEST)에 적힌 폴더만 내보낸다. 연결을 해제해 파일이 지워지면 그 주소도 닫힌다.
- 점(.)으로 시작하는 경로·폴더 밖으로 나가는 경로는 404.
"""
import json
import mimetypes
import re
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings

MANIFEST = ".exitplain-preview.json"
HOST = re.compile(r"^s-([0-9a-f]{32})\.localhost$")


def origin_for(snapshot_id):
    hexid = snapshot_id.hex if hasattr(snapshot_id, "hex") else str(snapshot_id).replace("-", "")
    return f"http://s-{hexid}.localhost:{settings.EXITPLAIN_PREVIEW_PORT}"


def is_preview_origin(origin):
    """미리보기 주소 형식 + 정해진 포트인지 (netguard 가 내부 주소 차단의 예외로 쓴다)."""
    p = urlsplit(origin)
    return p.scheme == "http" and bool(HOST.match(p.hostname or "")) and p.port == settings.EXITPLAIN_PREVIEW_PORT


def write_manifest(tree, served_root):
    """served_root: 받은 코드 기준으로 내보낼 폴더 (하위 폴더 + 빌드 결과물 폴더)."""
    (Path(tree) / MANIFEST).write_text(json.dumps({"root": served_root}), encoding="utf-8")


def served_dir(source_root, hexid):
    """스냅샷 id → 내보낼 폴더. manifest 가 없거나 폴더 밖을 가리키면 None."""
    tree = (Path(source_root) / f"{hexid[:8]}-{hexid[8:12]}-{hexid[12:16]}-{hexid[16:20]}-{hexid[20:]}").resolve()
    try:
        root = json.loads((tree / MANIFEST).read_text("utf-8")).get("root") or ""
    except (OSError, ValueError):
        return None
    base = (tree / root).resolve()
    if base != tree and tree not in base.parents:
        return None
    return base if base.is_dir() else None


def resolve_file(base, url_path):
    """요청 경로 → 보낼 파일. 숨김·폴더 밖은 None, 없는 화면 경로는 index.html (SPA)."""
    parts = [p for p in unquote(urlsplit(url_path).path).split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        return None
    target = base.joinpath(*parts).resolve() if parts else base
    if target != base and base not in target.parents:
        return None
    if target.is_dir():
        target = target / "index.html"
    if target.is_file():
        return target
    last = parts[-1] if parts else ""
    if "." in last:               # 파일처럼 생긴 경로가 없으면 404 (이미지·스크립트)
        return None
    index = base / "index.html"
    return index if index.is_file() else None


def make_handler(source_root):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ExitplainPreview"

        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            m = HOST.match((self.headers.get("Host") or "").split(":")[0].lower())
            base = served_dir(source_root, m.group(1)) if m else None
            path = resolve_file(base, self.path) if base else None
            if path is None:
                return self._send(404, b"not found")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        do_HEAD = do_GET

        def _deny(self):
            self._send(405, b"read only")

        do_POST = do_PUT = do_DELETE = do_PATCH = _deny

    return Handler


def make_server(bind=None, port=None, source_root=None):
    return ThreadingHTTPServer((bind or settings.EXITPLAIN_PREVIEW_BIND, settings.EXITPLAIN_PREVIEW_PORT if port is None else port),
                               make_handler(source_root or settings.EXITPLAIN_SOURCE_ROOT))


@lru_cache(maxsize=1)
def _opener():
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def reachable(origin, timeout=3):
    """미리보기 서버가 이 스냅샷을 내보내고 있는지 (사이트 확인 전에 확인). *.localhost 를 직접 127.0.0.1 로 보낸다."""
    import urllib.request
    p = urlsplit(origin)
    req = urllib.request.Request(f"http://127.0.0.1:{p.port}/", headers={"Host": p.netloc}, method="HEAD")
    try:
        with _opener().open(req, timeout=timeout) as res:
            return res.status == 200
    except Exception:
        return False
