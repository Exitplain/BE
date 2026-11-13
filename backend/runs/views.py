import mimetypes

from django.http import FileResponse
from django.urls import reverse
from rest_framework import status
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView
from plans.models import PlanSnapshot

from . import importer, queries, services
from .models import Observation, Run, Session
from .serializers import (EventListQuerySerializer, RunCreateSerializer, RunListQuerySerializer,
                          SessionListQuerySerializer)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def get_owned_run(user, run_id):
    run = Run.objects.select_related("project").filter(pk=run_id).first()
    if run is None:
        raise ApiError(404, "NOT_FOUND", "실행을 찾을 수 없습니다.")
    ensure_owner(user, run.project)
    return run


class ProjectRunListView(BaseAPIView):
    """GET /api/v1/projects/{project_id}/runs?q=&status=&kind=&period=&limit=&cursor=

    P01 테스트 목록(kind=pretest,retest)과 C08 진단 기록(전체 유형)이 같이 쓴다.
    status·kind 는 쉼표로 여러 값, period 는 7d|30d|90d|all. 번호 최신순.
    """

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        query = RunListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        return Response(services.list_runs(
            project, q=params["q"], statuses=params["status"], kinds=params["kind"], period=params["period"],
            limit=params["limit"], cursor=params["cursor"]))


class RunCreateView(BaseAPIView):
    """POST /api/v1/runs {plan_revision_id, base_run_id?, change_note?}  + 헤더 Idempotency-Key (P07~P10, P17)

    202 {run_id, status, status_url}. 같은 Idempotency-Key 로 다시 보내면 새로 만들지 않고 기존 실행을 돌려준다
    (응답 헤더 Idempotent-Replayed: true). base_run_id 가 있으면 재테스트.
    """

    def post(self, request):
        serializer = RunCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        snapshot = PlanSnapshot.objects.select_related("plan__project").filter(pk=data["plan_revision_id"]).first()
        if snapshot is None:
            raise ApiError(404, "NOT_FOUND", "확정된 계획을 찾을 수 없습니다.")
        project = snapshot.plan.project
        ensure_owner(request.user, project)
        base_id = data["base_run_id"]
        parent = Run.objects.filter(pk=base_id).first() if base_id else None
        run, created = services.create_run(
            project, snapshot, request.user, idempotency_key=request.headers.get("Idempotency-Key"),
            kind=Run.Kind.RETEST if base_id else Run.Kind.PRETEST, parent_run=parent, change_note=data["change_note"])
        response = Response({"run_id": str(run.id), "status": run.status,
                             "status_url": reverse("run-detail", args=[run.id])}, status=status.HTTP_202_ACCEPTED)
        if not created:
            response["Idempotent-Replayed"] = "true"
        return response


class RunDetailView(BaseAPIView):
    """GET /api/v1/runs/{id} — 상태·단계(phase)·진행 상황. P08~P10 이 2~3초마다 부른다."""

    def get(self, request, run_id):
        return Response(queries.run_detail(get_owned_run(request.user, run_id)))


class RunCancelView(BaseAPIView):
    """POST /api/v1/runs/{id}/cancel — 취소 요청. 대기 중이면 바로 취소, 진행 중이면 워커가 멈춘다 (202)."""

    def post(self, request, run_id):
        run = services.request_cancel(get_owned_run(request.user, run_id))
        return Response(queries.run_detail(run), status=status.HTTP_202_ACCEPTED)


class RunSessionListView(BaseAPIView):
    """GET /api/v1/runs/{id}/sessions?status=&limit=&cursor= — P11 세션 타일 (시작한 순서)"""

    def get(self, request, run_id):
        run = get_owned_run(request.user, run_id)
        query = SessionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        return Response(queries.list_sessions(run, statuses=params["status"], limit=params["limit"], cursor=params["cursor"]))


class RunEventListView(BaseAPIView):
    """GET /api/v1/runs/{id}/events?after=&limit= — 새 이벤트만 (think-aloud·진행 갱신). next_after 를 다음 after 로."""

    def get(self, request, run_id):
        run = get_owned_run(request.user, run_id)
        query = EventListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(queries.list_events(run, after=query.validated_data["after"], limit=query.validated_data["limit"]))


class IgnoreClientContentNegotiation(BaseContentNegotiation):
    """이미지 요청의 Accept(image/*)가 JSON 렌더러와 맞지 않아 406 이 나지 않게 한다."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class _ScreenshotView(BaseAPIView):
    content_negotiation_class = IgnoreClientContentNegotiation

    @staticmethod
    def serve(relative_path):
        """ARTIFACT_ROOT 안의 이미지 파일만 내보낸다. 경로 조작·루트 밖·이미지가 아닌 파일은 404."""
        not_found = ApiError(404, "NOT_FOUND", "스크린샷을 찾을 수 없습니다.")
        if not relative_path:
            raise not_found
        root = importer.artifact_root()
        path = (root / relative_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise not_found from None
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            raise not_found
        response = FileResponse(path.open("rb"), content_type=mimetypes.guess_type(path.name)[0] or "image/png")
        response["Cache-Control"] = "private, max-age=3600"  # 같은 스크린샷은 바뀌지 않는다
        return response


class ObservationScreenshotView(_ScreenshotView):
    """GET /api/v1/observations/{id}/screenshot — 세션이 본 화면 (소유자만)"""

    def get(self, request, observation_id):
        observation = Observation.objects.select_related("session__run__project").filter(pk=observation_id).first()
        if observation is None:
            raise ApiError(404, "NOT_FOUND", "스크린샷을 찾을 수 없습니다.")
        ensure_owner(request.user, observation.session.run.project)
        return self.serve(observation.artifact)


class SessionFinalScreenshotView(_ScreenshotView):
    """GET /api/v1/sessions/{id}/final-screenshot — 세션이 마지막으로 본 화면 (소유자만)"""

    def get(self, request, session_id):
        session = Session.objects.select_related("run__project").filter(pk=session_id).first()
        if session is None:
            raise ApiError(404, "NOT_FOUND", "스크린샷을 찾을 수 없습니다.")
        ensure_owner(request.user, session.run.project)
        return self.serve(session.final_screenshot)
