from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "entry_url", "check_status", "is_sample", "archived_at", "created_at")
    list_filter = ("is_sample", "check_status", "login_mode")
    search_fields = ("name", "entry_url", "owner__username")
    raw_id_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")
