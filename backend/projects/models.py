"""[임시] 백엔드 A 담당. B 앱을 혼자 테스트하려고 둔 최소 모델이다.

A의 projects 앱이 들어오면 이 앱은 지우고 settings.EXITPLAIN_PROJECT_MODEL을 A의 모델로 바꾼다.
필드 이름은 docs/API_SPEC.md 8절(C02~C04: name, entry_url)을 따랐다.
"""
from django.conf import settings
from django.db import models


class Project(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=100)
    entry_url = models.URLField(max_length=500)
    allowed_origins = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name