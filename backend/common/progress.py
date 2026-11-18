"""오래 걸리는 작업(사이트 확인·코드 받기)의 진행 기록과 백그라운드 실행.

작업 함수는 별도 스레드에서 돌고(이벤트 루프 안일 수도 있어 ORM 을 쓰지 않는다), 진행 사건을 progress(event) 로 알린다.
바깥 스레드가 FLUSH_SECONDS 마다 모델의 progress·updated_at 에 저장한다. 모델에는 progress(JSON 목록)와
updated_at(auto_now) 이 있어야 한다. updated_at 은 멈춤 감지(STALE_SECONDS)에 쓴다.
"""
import logging
import threading
import time

from django.db import connections

logger = logging.getLogger(__name__)

PROGRESS_LIMIT = 100          # 화면에 보여 줄 최근 사건 수
FLUSH_SECONDS = 0.5           # 진행 사건을 DB 에 쓰는 간격
HEARTBEAT_SECONDS = 5         # 새 사건이 없어도 이 간격으로 updated_at 을 갱신한다
STALE_SECONDS = 120           # 이만큼 갱신이 없으면 중단된 것으로 본다 (서버 재시작 등)


def next_seq(events):
    return max((e.get("seq", 0) for e in events), default=0) + 1


class ProgressLog:
    """진행 사건 모음. add 는 어느 스레드에서 불러도 되고, flush 는 ORM 을 쓸 수 있는 스레드에서만 부른다."""

    def __init__(self, instance):
        self.instance = instance
        self.lock = threading.Lock()
        self.pending = []
        self.seq = next_seq(instance.progress) - 1
        self.started = time.monotonic()
        self.saved_at = time.monotonic()

    def add(self, event):
        message = str(event.get("message") or "")[:200]
        if not message:
            return
        with self.lock:
            self.seq += 1
            self.pending.append({**event, "message": message, "seq": self.seq,
                                 "t": round(time.monotonic() - self.started, 1)})

    def take(self):
        """모인 사건을 instance.progress 에 붙인다 (저장은 하지 않는다). 새 사건이 있었으면 True."""
        with self.lock:
            new, self.pending = self.pending, []
        if new:
            self.instance.progress = (self.instance.progress + new)[-PROGRESS_LIMIT:]
        return bool(new)

    def flush(self):
        """새 사건이 있거나 마지막 저장 뒤 HEARTBEAT_SECONDS 가 지났으면 저장한다."""
        if self.take() or time.monotonic() - self.saved_at >= HEARTBEAT_SECONDS:
            self.instance.save(update_fields=["progress", "updated_at"])
            self.saved_at = time.monotonic()


def run_tracked(instance, work, name):
    """work(progress) 를 별도 스레드에서 끝까지 돌리며 진행을 저장한다. (결과, 예외 또는 None, ProgressLog)."""
    log = ProgressLog(instance)
    box = {}

    def target():
        try:
            box["result"] = work(log.add)
        except BaseException as exc:          # noqa: BLE001 - 어떤 실패든 상태로 남긴다
            box["error"] = exc

    worker = threading.Thread(target=target, name=name, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(FLUSH_SECONDS)
        log.flush()
    return box.get("result"), box.get("error"), log


def start_background(fn, *args, name):
    """요청을 기다리지 않고 fn(*args) 를 돌린다. 끝나면 이 스레드의 DB 연결을 닫는다."""
    def target():
        try:
            fn(*args)
        except Exception:
            logger.exception("백그라운드 작업 오류 %s", name)
        finally:
            connections.close_all()
    threading.Thread(target=target, name=name, daemon=True).start()
