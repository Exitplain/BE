from django.contrib import admin

from .models import Observation, Run, RunEvent, Session, TaskRun


class TaskRunInline(admin.TabularInline):
    model = TaskRun
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("task_key", "seed", "status", "exit_code", "pid", "started_at", "finished_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("sequence_no", "name", "project", "kind", "status", "created_by", "created_at", "finished_at")
    list_filter = ("status", "kind")
    search_fields = ("id", "name", "project__name")
    raw_id_fields = ("project", "created_by", "parent_run", "plan_snapshot")
    readonly_fields = ("id", "created_at")
    inlines = [TaskRunInline]


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    list_display = ("run", "task_key", "seed", "status", "exit_code", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("agent_run_id", "run__id")
    raw_id_fields = ("run",)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("session_key", "run", "task_id", "seed", "status", "steps", "max_frustration")
    list_filter = ("status", "task_id")
    search_fields = ("session_key", "run__id")
    raw_id_fields = ("run", "task_run", "persona_snapshot")


@admin.register(RunEvent)
class RunEventAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "type", "sequence", "occurred_at")
    list_filter = ("type",)
    raw_id_fields = ("run", "task_run", "session")
    readonly_fields = ("created_at",)


admin.site.register(Observation)
