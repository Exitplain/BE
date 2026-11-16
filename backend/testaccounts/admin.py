from django.contrib import admin

from .models import TestAccount, TestAccountEvent


@admin.register(TestAccount)
class TestAccountAdmin(admin.ModelAdmin):
    list_display = ("project", "position", "username", "status", "checked_at")
    list_filter = ("status",)
    exclude = ("password_encrypted",)          # 관리자 화면에도 비밀번호(암호문)를 보이지 않는다
    readonly_fields = ("project", "position", "username", "status", "status_message", "checked_at", "last_used_at")


@admin.register(TestAccountEvent)
class TestAccountEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "project", "action", "username", "actor", "detail")
    list_filter = ("action",)
    search_fields = ("username",)

    def has_add_permission(self, request):          # 감사 기록은 고치거나 새로 쓰지 못하게
        return False

    def has_change_permission(self, request, obj=None):
        return False