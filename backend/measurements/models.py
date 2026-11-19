import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def new_key():
    return "ep_" + secrets.token_hex(6)


class MeasureSite(models.Model):
    """배포된 사이트에 심는 수집 스크립트의 키 (R01). 프로젝트당 하나.

    키는 공개 값이다 (사이트 HTML 에 그대로 보인다). 그래서 받는 쪽에서 출처(Origin)를 프로젝트 허용 출처와 맞춰 본다.
    """
    project = models.OneToOneField(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE, related_name="measure_site")
    key = models.CharField(max_length=20, unique=True, default=new_key)
    created_at = models.DateTimeField(auto_now_add=True)
    last_received_at = models.DateTimeField(null=True, blank=True)
    last_origin = models.CharField(max_length=200, blank=True, default="")
    # R05 단계 매핑 {실측 단계 ID(경로 또는 exitplain.step 이름): 예측 실행 퍼널 단계 ID(경로) | null(매핑 안 함)}
    mapping = models.JSONField(default=dict, blank=True)
    mapping_saved_at = models.DateTimeField(null=True, blank=True)


class MeasureImport(models.Model):
    """스크립트를 못 심는 사이트의 CSV 로그 올리기 1건 (measurements/csv_import.py). 지우면 그 줄들도 함께 지운다."""
    site = models.ForeignKey(MeasureSite, on_delete=models.CASCADE, related_name="imports")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=200, blank=True, default="")
    sha256 = models.CharField(max_length=64)                 # 같은 파일을 두 번 올리지 않게
    rows_total = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    sessions = models.PositiveIntegerField(default=0)
    first_at = models.DateTimeField(null=True, blank=True)
    last_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MeasureHit(models.Model):
    """실제 방문자의 화면 이동 1건. 익명 세션 id(sid)·경로·선택 단계 이름·화면 너비 구분만 남긴다 (입력값·쿠키·IP 없음).

    CSV 로 올린 줄은 source_import 가 있고 created_at 이 로그의 시각이다 (세션 id 는 해시해서 남긴다)."""
    site = models.ForeignKey(MeasureSite, on_delete=models.CASCADE, related_name="hits")
    sid = models.CharField(max_length=40)
    path = models.CharField(max_length=300)
    step = models.CharField(max_length=64, blank=True, default="")
    device = models.CharField(max_length=8, blank=True, default="")   # mobile | desktop | "" (화면 너비로만 나눔)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    source_import = models.ForeignKey(MeasureImport, null=True, blank=True, on_delete=models.CASCADE,
                                      related_name="hits")

    class Meta:
        indexes = [models.Index(fields=["site", "created_at"])]
