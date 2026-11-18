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


class MeasurementMappingView(BaseAPIView):
    """GET·PUT /api/v1/projects/{id}/measurement/mapping — 실측 단계 → 예측 퍼널 단계 (R05)

    GET: 최근 7일 단계마다 저장한 매핑(없으면 자동 추천: 경로가 같으면 exact, 비슷하면 similar)
    PUT {"mapping": {step_id: stage_id | null}} — 통째로 바꾼다
    """

    def _site(self, project):
        site = services.site_for(project)
        if site is None:
            raise ApiError(404, "NOT_FOUND", "아직 수집 스크립트를 발급하지 않았어요.")
        return site

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        return Response(services.mapping_dict(project, self._site(project)))

    def put(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        site = self._site(project)
        unknown = sorted(set(request.data) - {"mapping"})
        if unknown:
            raise ApiError(422, "VALIDATION_ERROR", "허용하지 않는 필드입니다.",
                           {k: ["허용하지 않는 필드입니다."] for k in unknown})
        return Response(services.save_mapping(project, site, request.data.get("mapping")))


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
