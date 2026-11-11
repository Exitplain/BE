"""Serve the local evaluation storefront; no production hosting or payment backend."""
import argparse
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .frontend import FRONTEND_ROOT
from threading import Thread, Event
from urllib.parse import urlsplit

ROOT = FRONTEND_ROOT / "demo-shop"


class ShopHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in {"/", "/product", "/cart", "/signup", "/checkout", "/order/success", "/story", "/lab", "/delivery", "/delivery/result", "/benefits", "/coupon/success", "/account", "/subscription", "/subscription/skipped"}:
            self.path = "/index.html"
        return super().do_GET()


@contextmanager
def shop_server(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(ShopHandler, directory=str(ROOT)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()
    with shop_server(args.port) as base:
        print(f"반찬로드 테스트 웹: {base}/lab", flush=True)
        try:
            Event().wait()
        except KeyboardInterrupt:
            pass



if __name__ == "__main__":
    main()
