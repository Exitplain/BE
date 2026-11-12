"""샘플 실행 폴더로 리포트를 집계하는 명령 (백엔드 A의 워커가 생기기 전 확인용).

    python manage.py build_sample_report <plan_revision_id> <실행 결과 폴더> --run-ref sample-1

폴더 안에서 events.jsonl이 있는 하위 폴더를 모두 찾아 읽는다.
각 폴더의 input.json run_id는 build_run_input 형식({run}_{task}_s{seed})이어야 한다.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from common.exceptions import ApiError
from plans.models import PlanSnapshot
from reports.services import create_report_from_run_dir


class Command(BaseCommand):
    help = "실행 결과 파일(events.jsonl)로 ReportSnapshot을 만든다"

    def add_arguments(self, parser):
        parser.add_argument("plan_revision_id")
        parser.add_argument("run_dir")
        parser.add_argument("--run-ref", default=None, help="리포트를 묶을 실행 ID (기본: 폴더 이름)")

    def handle(self, *args, **opts):
        snapshot = PlanSnapshot.objects.filter(pk=opts["plan_revision_id"]).first()
        if snapshot is None:
            raise CommandError("확정 스냅샷(plan_revision_id)을 찾을 수 없습니다.")
        run_ref = opts["run_ref"] or opts["run_dir"].rstrip("/\\").replace("\\", "/").split("/")[-1]
        try:
            report = create_report_from_run_dir(snapshot, opts["run_dir"], run_ref=run_ref)
        except ApiError as exc:
            raise CommandError(exc.message) from exc
        self.stdout.write(self.style.SUCCESS(
            f"report_id={report.id} run_ref={report.run_ref} revision={report.revision} status={report.status}"))
        self.stdout.write(json.dumps(report.data["summary"], ensure_ascii=False, indent=2))