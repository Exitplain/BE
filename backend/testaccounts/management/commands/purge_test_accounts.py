"""보관 기간이 지난 테스트 계정 삭제. 서버에서 하루 한 번 실행한다 (cron 또는 systemd timer).

    python manage.py purge_test_accounts            # 삭제
    python manage.py purge_test_accounts --dry-run  # 지울 개수만 확인
"""
from django.core.management.base import BaseCommand

from testaccounts.services import purge_expired, retention_days


class Command(BaseCommand):
    help = "보관 기간(TEST_ACCOUNT_RETENTION_DAYS)이 지난 테스트 계정을 지운다."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="지우지 않고 개수만 출력")

    def handle(self, *args, dry_run=False, **options):
        count = purge_expired(dry_run=dry_run)
        verb = "지울 예정" if dry_run else "삭제"
        self.stdout.write(f"보관 기간 {retention_days()}일 지난 테스트 계정 {count}개 {verb}")