from django.conf import settings
from django.db import models


class CalibrationRevision(models.Model):
    """프로젝트의 보정 값 한 판 (CAL-02, docs/실측_보정_요구사항.md). 만든 뒤에는 바꾸지 않는다.

    가장 큰 revision 이 현재 보정이다. 되돌리기도 이전 값을 복사한 새 revision 으로 남긴다 (CAL-14).
    params: {"segments": {segment_id 또는 "*": {"threshold_scale": 0.5~2.0, "weight_scale": {원인: 0.25~3.0}}}}
    """
    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE,
                                related_name="calibration_revisions")
    revision = models.PositiveIntegerField()
    params = models.JSONField(default=dict)
    changes = models.JSONField(default=list, blank=True)     # 이번에 바꾼 행 (현재→제안, 근거)
    basis = models.JSONField(default=dict, blank=True)       # 기준 비교 (리포트·차수·기간·평균 오차)
    restored_from = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revision"]
        constraints = [models.UniqueConstraint(fields=["project", "revision"], name="uniq_calibration_revision")]
