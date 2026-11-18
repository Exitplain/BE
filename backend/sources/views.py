from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from . import services
from .models import SourceConnection, SourceSnapshot
from .serializers import snapshot_dict, snapshot_summary

GIT_FIELDS = {"git_url", "ref", "subdirectory"}
ZIP_FIELDS = {"file", "subdirectory"}


class ProjectSourceSnapshotView(BaseAPIView):
    """GET·POST /api/v1/projects/{id}/source-snapshots — 코드 받기 (SRC-01·02)

    - multipart: file(zip) + subdirectory? → zip 업로드
    - JSON: {"git_url", "ref"?, "subdirectory"?} → 공개 https 저장소
    둘 다 status=running 으로 바로 202. 진행·분석 결과는 GET /source-snapshots/{id}.
    """
    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        items = SourceSnapshot.objects.filter(connection__project=project).select_related("connection")[:20]
        return Response({"project_id": project.pk, "items": [snapshot_summary(s) for s in items]})

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        multipart = (request.content_type or "").startswith("multipart/")
        allowed = ZIP_FIELDS if multipart else GIT_FIELDS
        unknown = sorted(set(request.data) - allowed)
        if unknown:
            raise ApiError(422, "VALIDATION_ERROR", "허용하지 않는 필드입니다.", {k: ["허용하지 않는 필드입니다."] for k in unknown})
        subdirectory = str(request.data.get("subdirectory") or "")
        if multipart:
            snapshot = services.create_from_zip(request.user, project, request.FILES.get("file"), subdirectory)
        else:
            if not request.data.get("git_url"):
                raise ApiError(422, "VALIDATION_ERROR", "저장소 주소를 입력하거나 zip 파일을 올려주세요.",
                               {"git_url": ["필수 항목입니다."]})
            snapshot = services.create_from_git(request.user, project, request.data["git_url"],
                                                str(request.data.get("ref") or ""), subdirectory)
        return Response(snapshot_dict(snapshot), status=status.HTTP_202_ACCEPTED)


class SourceSnapshotDetailView(BaseAPIView):
    """GET /api/v1/source-snapshots/{id} — 진행·분석 결과"""

    def get(self, request, snapshot_id):
        snapshot = SourceSnapshot.objects.select_related("connection__project").filter(pk=snapshot_id).first()
        if snapshot is None:
            raise ApiError(404, "NOT_FOUND", "코드 스냅샷을 찾을 수 없습니다.")
        ensure_owner(request.user, snapshot.connection.project)
        return Response(snapshot_dict(services.mark_if_stale(snapshot)))


class SourceConnectionDetailView(BaseAPIView):
    """DELETE /api/v1/source-connections/{id} — 연결 해제 (받아 둔 파일 삭제, 기록은 남김)"""

    def delete(self, request, connection_id):
        connection = SourceConnection.objects.select_related("project").filter(pk=connection_id).first()
        if connection is None:
            raise ApiError(404, "NOT_FOUND", "코드 연결을 찾을 수 없습니다.")
        ensure_owner(request.user, connection.project)
        services.revoke(connection)
        return Response(status=status.HTTP_204_NO_CONTENT)
