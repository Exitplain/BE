from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/runs", views.ProjectRunListView.as_view(), name="project-run-list"),
    path("runs", views.RunCreateView.as_view(), name="run-create"),
    path("runs/<uuid:run_id>", views.RunDetailView.as_view(), name="run-detail"),
    path("runs/<uuid:run_id>/cancel", views.RunCancelView.as_view(), name="run-cancel"),
    path("runs/<uuid:run_id>/sessions", views.RunSessionListView.as_view(), name="run-sessions"),
    path("runs/<uuid:run_id>/events", views.RunEventListView.as_view(), name="run-events"),
    path("observations/<int:observation_id>/screenshot", views.ObservationScreenshotView.as_view(),
         name="observation-screenshot"),
    path("sessions/<int:session_id>/final-screenshot", views.SessionFinalScreenshotView.as_view(),
         name="session-final-screenshot"),
]
