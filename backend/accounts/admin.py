from django.contrib import admin

from .models import EmailLoginCode, ExternalIdentity, Workspace, WorkspaceMember


class MemberInline(admin.TabularInline):
    model = WorkspaceMember
    extra = 0


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    inlines = [MemberInline]


@admin.register(EmailLoginCode)
class EmailLoginCodeAdmin(admin.ModelAdmin):
    list_display = ("email", "attempts", "created_at", "expires_at", "used_at")
    readonly_fields = [f.name for f in EmailLoginCode._meta.fields]   # 코드 해시는 보기만


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "email", "created_at")