from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/source-snapshots", views.ProjectSourceSnapshotView.as_view(), name="source-snapshots"),
    path("source-snapshots/<uuid:snapshot_id>", views.SourceSnapshotDetailView.as_view(), name="source-snapshot-detail"),
    path("source-connections/<int:connection_id>", views.SourceConnectionDetailView.as_view(), name="source-connection-detail"),
]
