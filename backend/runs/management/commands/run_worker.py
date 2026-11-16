"""실행 워커. 서버에서는 systemd(exitplain-worker.service)가 이 명령을 띄운다.

    python manage.py run_worker            # 계속 대기하며 queued 실행을 처리
    python manage.py run_worker --once     # 지금 대기 중인 실행 하나만 처리하고 종료
"""
import signal

from django.core.management.base import BaseCommand

from runs.worker import Worker


class Command(BaseCommand):
    help = "queued 상태의 실행을 가져와 에이전트를 실행한다."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="대기 중인 실행 하나만 처리하고 종료")
        parser.add_argument("--poll-interval", type=float, default=2.0, help="대기 중일 때 새 실행을 확인하는 간격(초)")
        parser.add_argument("--tick", type=float, default=0.5, help="실행 중 이벤트를 읽는 간격(초)")

    def handle(self, *args, once, poll_interval, tick, **options):
        worker = Worker(tick=tick)
        # systemd stop(SIGTERM)도 Ctrl+C 처럼 처리해서 에이전트 프로세스를 정리하고 끝낸다
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        recovered = worker.recover_stale()
        if recovered:
            self.stdout.write(f"중단된 실행 {len(recovered)}개를 정리했습니다.")
        self.stdout.write(f"워커 시작: {worker.ref}")
        try:
            if once:
                run = worker.run_once()
                self.stdout.write("처리할 실행이 없습니다." if run is None else f"실행 {run.pk} → {run.status}")
            else:
                worker.run_forever(poll_interval=poll_interval)
        except KeyboardInterrupt:
            self.stdout.write("워커를 종료합니다.")
