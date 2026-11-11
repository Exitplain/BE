"""Controlled evaluation fixture. Never imported by the action policy."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit, parse_qs
import html


@contextmanager
def demo_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            p = urlsplit(self.path)
            if p.path == "/success":
                token = html.escape(parse_qs(p.query).get("token", [""])[0])
                body = f'<h1>완료</h1><div id="result">{token}</div>' + '''<div id="storage"></div><script>document.querySelector('#storage').textContent='saved:'+localStorage.getItem('participant')+';cookie:'+document.cookie;</script>'''
            else:
                count = 8 if p.path == "/problem" else 1
                fields = '<label>식별자<input name="token" required></label>'
                fields += ''.join(f'<input required name="field{i}" aria-label="추가 필드 {i}">' for i in range(count-1))
                body = f'<h1>테스트 주문</h1><form action="/success">{fields}<button>모의 주문</button></form>' + '''<div id="initial"></div><script>document.querySelector('#initial').textContent='initial:'+localStorage.getItem('participant');document.querySelector('form').addEventListener('submit',()=>{let v=document.querySelector('[name=token]').value;localStorage.setItem('participant',v);document.cookie='participant='+v+';path=/';});</script>'''
            data = ('<!doctype html><meta charset="utf-8">' + body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
