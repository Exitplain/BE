from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner
from common.views import BaseAPIView
from plans.models import PlanSnapshot

from .services import persona_dict


class PlanRevisionPersonasView(BaseAPIView):
    """GET /api/v1/plan-revisions/{id}/personas — P07 페르소나 확인"""

    def get(self, request, snapshot_id):
        snapshot = PlanSnapshot.objects.select_related("plan__project").filter(pk=snapshot_id).first()
        if snapshot is None:
            raise ApiError(404, "NOT_FOUND", "확정된 계획을 찾을 수 없습니다.")
        ensure_owner(request.user, snapshot.plan.project)
        return Response({
            "segments": snapshot.data.get("segments", []),
            "items": [persona_dict(p) for p in snapshot.personas.all()],
            "generator": snapshot.persona_generator,
            "total_sessions": snapshot.data.get("total_sessions"),
        })