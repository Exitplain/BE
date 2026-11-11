from rest_framework import status
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from . import services
from .models import Discovery
from .serializers import DiscoveryCreateSerializer, discovery_dict


class ProjectDiscoveryCreateView(BaseAPIView):
    """POST /api/v1/projects/{project_id}/discoveries — P02 사이트 확인 + P03 기능 탐색"""

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        serializer = DiscoveryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        discovery = services.create_discovery(request.user, project, serializer.validated_data)
        return Response(discovery_dict(discovery), status=status.HTTP_201_CREATED)


class DiscoveryDetailView(BaseAPIView):
    """GET /api/v1/discoveries/{id}"""

    def get(self, request, discovery_id):
        discovery = Discovery.objects.select_related("project").filter(pk=discovery_id).first()
        if discovery is None:
            raise ApiError(404, "NOT_FOUND", "탐색 결과를 찾을 수 없습니다.")
        ensure_owner(request.user, discovery.project)
        return Response(discovery_dict(discovery))