from django.urls import path

from . import views

urlpatterns = [
    path("runs/<str:run_ref>/reports", views.RunReportView.as_view(), name="run-reports"),
    path("reports/<uuid:report_id>", views.ReportDetailView.as_view(), name="report-detail"),
    path("reports/<uuid:report_id>/sessions", views.ReportSessionListView.as_view(), name="report-sessions"),
    path("reports/<uuid:report_id>/sessions/<slug:task_id>/<int:seed>/<slug:persona_id>",
        views.ReportSessionDetailView.as_view(), name="report-session-detail"),
    path("projects/<int:project_id>/reports", views.ProjectReportListView.as_view(), name="project-reports"),
    path("projects/<int:project_id>/dashboard", views.ProjectDashboardView.as_view(), name="project-dashboard"),
]