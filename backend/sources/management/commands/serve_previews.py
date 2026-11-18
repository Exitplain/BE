"""받은 정적 사이트 미리보기 서버. 서버에서는 systemd(exitplain-preview.service)가 띄운다.

    python manage.py serve_previews            # 127.0.0.1:8090 (EXITPLAIN_PREVIEW_BIND / _PORT)
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from sources.preview import make_server


class Command(BaseCommand):
    help = "받은 정적 사이트를 에이전트가 열 수 있게 127.0.0.1 에서 내보낸다 (http://s-<id>.localhost:<port>/)."

    def handle(self, *args, **options):
        server = make_server()
        host, port = server.server_address[:2]
        self.stdout.write(f"미리보기 서버: {host}:{port} · 폴더 {settings.EXITPLAIN_SOURCE_ROOT}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            self.stdout.write("미리보기 서버를 종료합니다.")
        finally:
            server.server_close()
