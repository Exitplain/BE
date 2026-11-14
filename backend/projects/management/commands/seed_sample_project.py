"""샘플 프로젝트(반찬로드)를 만든다. 로그인 전 둘러보기(L06)에서 누구나 읽을 수 있다. 여러 번 실행해도 안전하다.

    python manage.py seed_sample_project [--url https://...] [--name 반찬로드]

소유자는 로그인할 수 없는 시스템 계정이라 누구도 이 프로젝트를 수정하거나 실행할 수 없다(읽기 전용).
주소는 --url, 없으면 settings.SAMPLE_PROJECT_URL. 샘플 실행·리포트 데이터는 이 명령이 만들지 않는다(계획 스냅샷이 필요).
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from projects.models import Project
from projects.services import normalize_entry_url, origin_of


class Command(BaseCommand):
    help = "로그인 없이 볼 수 있는 샘플 프로젝트를 만든다."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=None)
        parser.add_argument("--name", default="반찬로드")

    def handle(self, *args, **options):
        raw_url = options["url"] or settings.SAMPLE_PROJECT_URL
        try:
            url = normalize_entry_url(raw_url)
        except Exception as exc:  # ApiError(422)
            raise CommandError(f"샘플 주소가 올바르지 않습니다: {raw_url}") from exc
        User = get_user_model()
        owner, created_owner = User.objects.get_or_create(
            **{User.USERNAME_FIELD: settings.SAMPLE_OWNER_EMAIL}, defaults={"is_active": False, "email": settings.SAMPLE_OWNER_EMAIL})
        if created_owner:
            owner.set_unusable_password()
            owner.save()
        project, created = Project.objects.update_or_create(
            owner=owner, is_sample=True, name=options["name"],
            defaults={"entry_url": url, "allowed_origins": [origin_of(url)], "archived_at": None})
        self.stdout.write(f"샘플 프로젝트 {'생성' if created else '갱신'}: id={project.pk} {project.name} {project.entry_url}")
