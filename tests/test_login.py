"""테스트 계정 로그인 (RunInput.login_url + 실행 때만 받는 계정)과 읽기 전용(read_only) 쓰기 차단.

로컬 합성 사이트만 쓴다: /login(폼) → /home(로그인 필요, 쓰기 버튼) → /done.
"""
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs

import pytest
from pydantic import ValidationError

from agent import RunInput, execute_run
from agent.cli import CREDENTIALS_ENV, read_credentials
from agent.schemas import Action, Persona

USER, SECRET = "tester@example.test", "s3cret-PW!xyz"

LOGIN_PAGE = """<!doctype html><title>로그인</title><form method="post" action="/login">
<label>이메일 <input type="email" name="email"></label><label>비밀번호 <input type="password" name="password"></label>
<button type="submit">로그인</button></form>"""
HOME_PAGE = """<!doctype html><title>홈</title><h1>내 공부방</h1>
<button id="make" onclick="fetch('/api/make',{method:'POST'}).finally(()=>{document.body.dataset.made='1'})">만들기</button>
<a href="/done">완료</a>"""


@contextmanager
def login_site():
    writes = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body="", headers=()):
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def logged_in(self):
            return "session=ok" in (self.headers.get("Cookie") or "")

        def do_GET(self):
            if self.path.startswith("/login"):
                self.send(200, LOGIN_PAGE)
            elif self.path.startswith("/home"):
                self.send(200, HOME_PAGE) if self.logged_in() else self.send(303, headers=[("Location", "/login")])
            elif self.path.startswith("/done"):
                self.send(200, "<!doctype html><title>완료</title><p>완료</p>")
            else:
                self.send(404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            if self.path == "/login":
                form = parse_qs(body)
                if form.get("email") == [USER] and form.get("password") == [SECRET]:
                    self.send(303, headers=[("Location", "/home"), ("Set-Cookie", "session=ok; Path=/")])
                else:
                    self.send(200, LOGIN_PAGE + "<p role=alert>비밀번호가 틀렸어요</p>")
            elif self.path == "/api/make":
                writes.append(self.path)
                self.send(200, "{}")
            else:
                self.send(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", writes
    finally:
        server.shutdown()


class MakeThenFinish:
    """홈에서 '만들기'(쓰기 요청)를 누른 뒤 '완료'로 간다. 모델 없이 정해진 행동만."""
    name = "scripted_fixture"

    def __init__(self):
        self.clicked = False

    async def decide(self, observation, persona, goal, history):
        elements = observation["elements"]
        text = "만들기" if not self.clicked else "완료"
        self.clicked = True
        target = next(e for e in elements if text in e["text"])
        return Action(kind="click", target_id=target["id"], value=None, explanation=text), {}


def config(base, **kwargs):
    return RunInput(run_id="login", target_url=base + "/home", allowed_origins=[base], goal="로그인 후 완료",
                    success_path="/done", login_url=base + "/login", personas=[Persona(id="A")], max_steps=5, **kwargs)


def files_text(folder):
    return "".join(p.read_text(encoding="utf-8", errors="ignore") for p in Path(folder).rglob("*") if p.suffix in {".json", ".jsonl"})


async def test_login_then_read_only_blocks_writes_and_never_records_the_secret(tmp_path):
    with login_site() as (base, writes):
        report = await execute_run(config(base, read_only=True), MakeThenFinish, tmp_path,
                                   credentials={"A": {"username": USER, "password": SECRET}})
    session = report["sessions"][0]
    assert session["status"] == "success"
    assert writes == [] and session["blocked_writes"] == 1          # 쓰기 요청은 서버에 닿지 않았다
    events = [json.loads(line) for line in (Path(report["output_dir"]) / "events.jsonl").read_text("utf-8").splitlines()]
    assert [e["payload"] for e in events if e["type"] == "login.completed"] == [{"ok": True, "has_account": True}]
    first = next(e for e in events if e["type"] == "observation.created")
    assert first["payload"]["url"].endswith("/home")                  # 로그인 화면은 관찰·스크린샷에 없다
    text = files_text(tmp_path)
    assert SECRET not in text and USER not in text                    # 입력·이벤트·결과 어디에도 계정이 없다


async def test_without_read_only_writes_reach_the_site(tmp_path):
    with login_site() as (base, writes):
        report = await execute_run(config(base), MakeThenFinish, tmp_path,
                                   credentials={"A": {"username": USER, "password": SECRET}})
    assert report["sessions"][0]["status"] == "success"
    assert writes == ["/api/make"] and report["sessions"][0]["blocked_writes"] == 0


@pytest.mark.parametrize("credentials,reason", [({"A": {"username": USER, "password": "wrong-password"}}, "Test account login failed"),
                                                ({}, "No test account for persona")])
async def test_failed_or_missing_login_is_blocked_auth_not_dropout(tmp_path, credentials, reason):
    with login_site() as (base, _):
        report = await execute_run(config(base, read_only=True), MakeThenFinish, tmp_path, credentials=credentials)
    session = report["sessions"][0]
    assert (session["status"], session["reason"], session["steps"]) == ("blocked_auth", reason, 0)
    assert "wrong-password" not in files_text(tmp_path)


def test_login_url_must_be_on_an_allowed_origin():
    with pytest.raises(ValidationError, match="login_url"):
        RunInput(run_id="x", target_url="http://a.test/", allowed_origins=["http://a.test"], goal="g", success_path="/",
                 login_url="http://evil.test/login", personas=[Persona(id="A")])


def test_credentials_come_from_env_once_and_are_removed(monkeypatch):
    monkeypatch.setenv(CREDENTIALS_ENV, json.dumps({"P-001": {"username": USER, "password": SECRET}}))
    assert read_credentials() == {"P-001": {"username": USER, "password": SECRET}}
    assert CREDENTIALS_ENV not in os.environ
    monkeypatch.setenv(CREDENTIALS_ENV, "not json")
    with pytest.raises(SystemExit) as stop:
        read_credentials()
    assert SECRET not in str(stop.value)
