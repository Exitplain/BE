"""읽기 전용 접근 규칙: 내 프로젝트이거나 샘플 프로젝트(로그인 전 둘러보기 L06)면 볼 수 있다.

쓰기(실행 시작·취소, URL 확인 등)는 이 규칙을 쓰지 않고 소유자만 허용한다.
로그인하지 않은 사용자가 샘플이 아닌 프로젝트를 요청하면 401(AUTH_REQUIRED), 로그인했지만 남의 프로젝트면 403이다.
"""
from rest_framework.exceptions import NotAuthenticated

from common.exceptions import ApiError
from common.projects import ensure_owner


def ensure_viewable(user, project):
    if getattr(project, "is_sample", False):
        return project
    if not user or not user.is_authenticated:
        raise NotAuthenticated()
    return ensure_owner(user, project)


def get_viewable_project(user, project_id):
    from .models import Project

    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        raise ApiError(404, "NOT_FOUND", "프로젝트를 찾을 수 없습니다.")
    return ensure_viewable(user, project)
