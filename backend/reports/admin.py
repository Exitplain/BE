from django.contrib import admin

from .models import ReportSnapshot, ShareLink


@admin.register(ReportSnapshot)
class ReportSnapshotAdmin(admin.ModelAdmin):
    list_display = ("run_ref", "revision", "status", "source", "plan_snapshot", "created_at")
    list_filter = ("status", "source")
    readonly_fields = [f.name for f in ReportSnapshot._meta.fields]


@admin.register(ShareLink)
class ShareLinkAdmin(admin.ModelAdmin):
    list_display = ("report", "created_by", "created_at", "expires_at", "revoked_at")
    readonly_fields = [f.name for f in ShareLink._meta.fields]