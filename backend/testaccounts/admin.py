from django.contrib import admin

from .models import TestAccount


@admin.register(TestAccount)
class TestAccountAdmin(admin.ModelAdmin):
    list_display = ("project", "position", "username", "status", "checked_at")
    list_filter = ("status",)
    exclude = ("password_encrypted",)          # 관리자 화면에도 비밀번호(암호문)를 보이지 않는다
    readonly_fields = ("project", "position", "username", "status", "status_message", "checked_at")