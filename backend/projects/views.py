from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.projects import get_owned_project
from common.views import BaseAPIView

from . import services, url_check
from .access import get_viewable_project
from .models import Project
from .serializers import ProjectCreateSerializer, ProjectListQuerySerializer


class ProjectListCreateView(BaseAPIView):
    """GET  /api/v1/projects?q=&archived=&limit=&cursor=  — C02 프로젝트 찾기 (내 프로젝트만, 수정일 최신순)
    POST /api/v1/projects {name, entry_url, journey_note?} — C03~C04 새 프로젝트
    """

    def get(self, request):
        query = ProjectListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.list_projects(request.user, **query.validated_data))

    def post(self, request):
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = services.create_project(request.user, **serializer.validated_data)
        return Response(services.project_detail(project), status=status.HTTP_201_CREATED)


class ProjectDetailView(BaseAPIView):
    """GET /api/v1/projects/{id} — C05 프로젝트 개요 (남의 프로젝트는 403, 샘플 프로젝트는 로그인 없이도 볼 수 있다)"""

    permission_classes = [AllowAny]

    def get(self, request, project_id):
        project = get_viewable_project(request.user, project_id)
        return Response(services.project_detail(project))


class SampleProjectListView(BaseAPIView):
    """GET /api/v1/sample/projects — 로그인 전 둘러보기(L06)에 쓰는 샘플 프로젝트 목록 (로그인 불필요)"""

    permission_classes = [AllowAny]

    def get(self, request):
        queryset = Project.objects.filter(is_sample=True, archived_at__isnull=True).order_by("pk")
        items = [services.project_summary(p) for p in services._with_last_pretest(queryset)]
        return Response({"items": items, "next_cursor": None, "total": len(items)})


class ProjectCheckView(BaseAPIView):
    """POST /api/v1/projects/{id}/check — 5.0 URL 확인 (허용 대상인지, 접속되는지).

    확인 결과가 접속 불가여도 요청 자체는 성공이므로 200 이다. 결과는 프로젝트에 저장된다.
    """

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        return Response({"project_id": project.pk, "check": url_check.check_project_url(project)})
