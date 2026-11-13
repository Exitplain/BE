from rest_framework import status
from rest_framework.response import Response

from common.projects import get_owned_project
from common.views import BaseAPIView

from . import services
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
    """GET /api/v1/projects/{id} — C05 프로젝트 개요 (다른 사람의 프로젝트는 403)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        return Response(services.project_detail(project))
