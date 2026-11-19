"""백엔드 A의 Project 모델과 만나는 유일한 지점.

A의 모델 경로·필드명이 정해지면 settings.EXITPLAIN_PROJECT_MODEL과 아래 세 함수만 고친다.
B 앱이 Project에서 쓰는 값: 소유자, 대상 URL, 허용 출처.
"""
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings

from .exceptions import ApiError


def project_model():
    return apps.get_model(settings.EXITPLAIN_PROJECT_MODEL)


def project_owner_id(project):
    return project.owner_id


def project_target(project):
    origins = list(getattr(project, "allowed_origins", None) or [])
    if not origins and project.entry_url:   # 주소 없이 만든 프로젝트는 허용 주소도 없다 ("://" 같은 값을 만들지 않는다)
        p = urlsplit(project.entry_url)
        origins = [f"{p.scheme}://{p.netloc}"]
    return {"entry_url": project.entry_url, "allowed_origins": origins}


def ensure_entry_url(project, raw_url):
    """주소 없이 만든 프로젝트에 테스트 1단계에서 받은 주소를 등록한다 (이미 주소가 있으면 그대로).

    구현은 A 의 projects.services.attach_entry_url. B 앱은 이 함수로만 부른다 (이 파일이 A 와 만나는 유일한 지점).
    """
    from projects.services import attach_entry_url
    return attach_entry_url(project, raw_url)


def ensure_owner(user, project):
    if project_owner_id(project) != user.id:
        raise ApiError(403, "FORBIDDEN", "이 프로젝트에 접근할 권한이 없습니다.")
    return project


def get_owned_project(user, project_id):
    project = project_model().objects.filter(pk=project_id).first()
    if project is None:
        raise ApiError(404, "NOT_FOUND", "프로젝트를 찾을 수 없습니다.")
    return ensure_owner(user, project)