"""처음 보는 URL 사이트 확인 (agent/explore.py): 읽기 전용 둘러보기 + 기능·과업 제안 검사. 모델은 가짜를 쓴다."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from agent.explore import FeatureDraft, Proposal, TaskDraft, _check, crawl, discover_url

PAGES = {
    "/": """<title>홈</title><h1>공부 도우미</h1><a href="/notes">노트</a><a href="https://other.test/x">밖</a>
<button onclick="location.href='/pricing'">요금제 보기</button>
<button onclick="fetch('/api/save',{method:'POST'})">저장</button>""",
    "/notes": """<title>노트</title><h2>내 노트</h2><a href="/notes/1">첫 노트</a>""",
    "/notes/1": "<title>노트 1</title><p>본문</p>",
    "/pricing": """<title>요금제</title><h2>무료 · 프로</h2><form><input name=a><input type=password name=b></form>""",
}


@contextmanager
def site():
    writes = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = PAGES.get(self.path.split("?")[0])
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<!doctype html>" + (body or "")).encode())

        def do_POST(self):
            writes.append(self.path)
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", writes
    finally:
        server.shutdown()


async def test_crawl_follows_links_and_buttons_without_writing():
    with site() as (base, writes):
        pages, blocked = await crawl(base + "/", [base], max_pages=10, max_depth=2)
    paths = [p["path"] for p in pages]
    assert paths[0] == "/" and set(paths) == {"/", "/notes", "/notes/1", "/pricing"}     # 링크 + 버튼으로 간 화면
    home = pages[0]
    assert {"label": "요금제 보기", "path": "/pricing", "via": "button"} in home["links"]
    assert all(l["path"] != "/x" for l in home["links"])                                   # 다른 출처 링크는 빠짐
    assert writes == [] and blocked["writes"] >= 1                                         # '저장' 버튼의 POST는 막힘
    pricing = next(p for p in pages if p["path"] == "/pricing")
    assert pricing["forms"] == [{"inputs": 2, "password": True}]


LOGIN_PAGES = {
    "/login": """<title>로그인</title><form method="post" action="/login"><input type="email" name="email">
<input type="password" name="password"><button type="submit">로그인</button></form>""",
    "/home": """<title>홈</title><h1>내 공부방</h1><a href="/notes">노트</a>
<button onclick="document.cookie='session=; Max-Age=0; Path=/'; location.href='/login'">로그아웃</button>
<button onclick="fetch('/api/save',{method:'POST'})">저장</button>""",
    "/notes": "<title>노트</title><h2>내 노트</h2>",
}
USER, SECRET = "tester@example.test", "s3cret-PW!xyz"


@contextmanager
def login_site():
    writes = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body="", headers=()):
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<!doctype html>" + body).encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path != "/login" and "session=ok" not in (self.headers.get("Cookie") or ""):
                return self.send(303, headers=[("Location", "/login")])
            self.send(200 if path in LOGIN_PAGES else 404, LOGIN_PAGES.get(path, ""))

        def do_POST(self):
            from urllib.parse import parse_qs
            body = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
            if self.path == "/login" and body.get("email") == [USER] and body.get("password") == [SECRET]:
                return self.send(303, headers=[("Location", "/home"), ("Set-Cookie", "session=ok; Path=/")])
            if self.path != "/login":
                writes.append(self.path)
            self.send(200, LOGIN_PAGES["/login"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", writes
    finally:
        server.shutdown()


async def test_crawl_logged_in_relogs_after_logout_and_keeps_secrets_out():
    with login_site() as (base, writes):
        login = {"url": base + "/login", "username": USER, "password": SECRET}
        pages, blocked = await crawl(base + "/", [base], login=login)
    paths = [p["path"] for p in pages]
    assert paths[0] == "/home" and "/notes" in paths and "/login" not in paths      # 로그인 뒤 첫 화면부터
    assert blocked["relogins"] >= 1                                                  # 로그아웃 버튼 뒤 다시 로그인
    home = pages[0]
    assert all(l["label"] != "로그아웃" for l in home["links"])                      # 로그아웃 길은 적지 않음
    assert writes == [] and blocked["writes"] >= 1
    assert SECRET not in str(pages) and USER not in str(pages)


async def test_crawl_with_wrong_password_fails_clearly():
    with login_site() as (base, _):
        with pytest.raises(ValueError, match="로그인하지 못했습니다"):
            await crawl(base + "/", [base], login={"url": base + "/login", "username": USER, "password": "wrong"})


def proposal(**overrides):
    base = dict(site_name="공부 도우미",
                features=[FeatureDraft(id="notes", name="노트", evidence_path="/notes", evidence_label="노트")],
                tasks=[TaskDraft(id="open-note", name="노트 열기", feature_id="notes", goal="첫 노트를 연다",
                                 success_criteria="노트 본문이 보인다", start_path="/", suggested=True),
                       TaskDraft(id="see-pricing", name="요금제 보기", feature_id="notes", goal="요금제를 본다",
                                 success_criteria="무료·프로 요금제가 보인다", start_path="/", suggested=False)])
    base.update(overrides)
    return Proposal(**base)


def test_proposal_must_cite_explored_pages_and_valid_ids():
    paths = {"/", "/notes"}
    _check(proposal(), paths)
    bad_task = TaskDraft(id="x-task", name="x", feature_id="notes", goal="g", success_criteria="c", start_path="/admin", suggested=True)
    cases = {
        "둘러보지 않은": proposal(features=[FeatureDraft(id="notes", name="노트", evidence_path="/secret", evidence_label="x")]),
        "시작 화면": proposal(tasks=[proposal().tasks[0], bad_task]),
        "하이픈": proposal(tasks=[proposal().tasks[0].model_copy(update={"id": "Open_Note"}), proposal().tasks[1]]),
        "과업 2~8개": proposal(tasks=proposal().tasks[:1]),
        "추천 과업": proposal(tasks=[t.model_copy(update={"suggested": False}) for t in proposal().tasks]),
    }
    for message, value in cases.items():
        with pytest.raises(ValueError, match=message):
            _check(value, paths)


async def test_discover_url_returns_wizard_shape_with_success_criteria():
    calls = []

    class FakeClient:
        messages = SimpleNamespace(parse=lambda **kw: calls.append(kw) or SimpleNamespace(
            parsed_output=proposal(), stop_reason="end_turn"))

    with site() as (base, _):
        result = await discover_url(base + "/", [base], client=FakeClient())
    assert result["project"] == "공부 도우미"
    assert result["features"][0] == {"id": "notes", "name": "노트", "evidence": {"label": "노트", "path": "/notes"},
                                     "verification": "explored_readonly"}
    task = result["tasks"][0]
    assert (task["success_criteria"], task["success_path"], task["start_path"], task["suggested"]) == \
        ("노트 본문이 보인다", None, "/", True)
    assert "pages" in calls[0]["messages"][0]["content"] and calls[0]["output_format"] is Proposal
    assert {p["path"] for p in result["explored"]} == {"/", "/notes", "/notes/1", "/pricing"}
