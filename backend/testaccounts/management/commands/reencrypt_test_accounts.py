"""KMS를 켠 뒤, 예전(Fernet) 방식으로 저장된 테스트 계정 비밀번호를 KMS 방식으로 다시 저장한다.

    python manage.py reencrypt_test_accounts --check    # KMS 연결만 확인 (암호화→복호화 한 번)
    python manage.py reencrypt_test_accounts --dry-run  # 옮길 개수만 확인
    python manage.py reencrypt_test_accounts            # 옮기기

여러 번 돌려도 된다 (이미 KMS 방식인 것은 건너뛴다).
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from testaccounts import crypto
from testaccounts.models import TestAccount


class Command(BaseCommand):
    help = "테스트 계정 비밀번호를 KMS 봉투 암호화로 다시 저장한다."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="KMS 연결만 확인")
        parser.add_argument("--dry-run", action="store_true", help="옮기지 않고 개수만 출력")

    def handle(self, *args, check=False, dry_run=False, **options):
        if not crypto.uses_kms():
            raise CommandError("TEST_ACCOUNT_KMS_KEY_ID가 설정되지 않았습니다 (.env 확인).")
        try:
            sample = crypto.encrypt("kms-check", project_id=0)
            ok = crypto.decrypt(sample, project_id=0) == "kms-check"
        except (crypto.KeyUnavailable, crypto.DecryptError) as exc:
            raise CommandError(f"KMS 연결 실패: {exc} (IAM 역할 권한·키 ID·리전 확인)") from exc
        if not ok:
            raise CommandError("KMS 확인 실패: 암호화한 값이 그대로 풀리지 않습니다.")
        self.stdout.write("KMS 연결 확인 OK")
        if check:
            return
        targets = [a for a in TestAccount.objects.only("pk", "project_id", "password_encrypted")
                   if crypto.needs_reencrypt(a.password_encrypted)]
        if dry_run:
            self.stdout.write(f"KMS로 옮길 테스트 계정 {len(targets)}개")
            return
        moved, failed = 0, 0
        for account in targets:
            try:
                password = crypto.decrypt(account.password_encrypted, project_id=account.project_id)
            except crypto.DecryptError:
                failed += 1          # 예전 키로도 못 푸는 값 → 고객이 다시 입력해야 함
                continue
            with transaction.atomic():
                TestAccount.objects.filter(pk=account.pk).update(
                    password_encrypted=crypto.encrypt(password, project_id=account.project_id))
            moved += 1
        self.stdout.write(f"KMS로 옮김 {moved}개, 풀 수 없어 건너뜀 {failed}개")