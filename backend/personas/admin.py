from django.contrib import admin

from .models import PersonaSnapshot


@admin.register(PersonaSnapshot)
class PersonaSnapshotAdmin(admin.ModelAdmin):
    list_display = ("code", "segment_name", "is_representative", "generator", "plan_snapshot")
    readonly_fields = [f.name for f in PersonaSnapshot._meta.fields]