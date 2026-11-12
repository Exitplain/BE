from django.contrib import admin

from .models import PlanSnapshot, TestPlan


@admin.register(TestPlan)
class TestPlanAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "revision", "created_at", "updated_at")
    readonly_fields = ("revision",)


@admin.register(PlanSnapshot)
class PlanSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "plan", "plan_revision", "persona_generator", "confirmed_at")
    readonly_fields = [f.name for f in PlanSnapshot._meta.fields]