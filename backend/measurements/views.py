from pathlib import Path

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import get_owned_project
from common.views import BaseAPIView

from . import services

SCRIPT = (Path(__file__).with_name("e.js")).read_text(encoding="utf-8")


@require_GET
def script(request):
    """GET /api/v1/e.js — 고객 사이트가 불러가는 수집 스크립트 (로그인 없음)"""
    res = HttpResponse(SCRIPT, content_type="text/javascript; charset=utf-8")
    res["Cache-Control"] = "public, max-age=3600"
    res["Access-Control-Allow-Origin"] = "*"
    return res


@csrf_exempt
@require_POST
def collect(request):
    """POST /api/v1/collect — sendBeacon(text/plain) 으로 받는다. 받든 버리든 204 (로그인·쿠키 없음)"""
    services.collect(request.body, request.headers.get("Origin"))
    res = HttpResponse(status=204)
    res["Access-Control-Allow-Origin"] = "*"
    return res


class MeasurementView(BaseAPIView):
    """GET·POST /api/v1/projects/{id}/measurement — 설치 상태 (R01~R04). POST 는 키 발급 (이미 있으면 그대로)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        return Response(services.status_dict(request, project, services.site_for(project)))

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        if not services.deployed(project):
            raise ApiError(409, "NOT_DEPLOYED", "실측 분석은 실제 방문자가 오는 배포 주소가 있어야 해요.")
        site = services.site_for(project, create=True)
        return Response(services.status_dict(request, project, site))


class MeasurementStepsView(BaseAPIView):
    """GET /api/v1/projects/{id}/measurement/steps?days=7 — 경로·단계별 고유 세션 수 (R05)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        site = services.site_for(project)
        if site is None:
            raise ApiError(404, "NOT_FOUND", "아직 수집 스크립트를 발급하지 않았어요.")
        try:
            days = int(request.query_params.get("days", 7))
        except ValueError:
            days = 0
        if not 1 <= days <= 30:
            raise ApiError(422, "VALIDATION_ERROR", "days 는 1~30 이어야 합니다.", {"days": ["1~30"]})
        return Response({"project_id": project.pk, **services.steps(site, days)})
