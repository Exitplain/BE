from django.urls import path

from . import views

urlpatterns = [
    path("runs/<str:run_ref>/reports", views.RunReportView.as_view(), name="run-reports"),
    path("reports/<uuid:report_id>", views.ReportDetailView.as_view(), name="report-detail"),
    path("reports/<uuid:report_id>/sessions", views.ReportSessionListView.as_view(), name="report-sessions"),
    path("reports/<uuid:report_id>/sessions/<slug:task_id>/<int:seed>/<slug:persona_id>",
        views.ReportSessionDetailView.as_view(), name="report-session-detail"),
    path("reports/<uuid:report_id>/export.md", views.ReportMarkdownView.as_view(), name="report-export-md"),
    path("reports/<uuid:report_id>/export.pdf", views.ReportPdfView.as_view(), name="report-export-pdf"),
    path("reports/<uuid:report_id>/share-links", views.ReportShareLinkView.as_view(), name="report-share-links"),
    path("share-links/<uuid:link_id>", views.ShareLinkDetailView.as_view(), name="share-link-detail"),
    path("shared/<slug:token>", views.SharedReportView.as_view(), name="shared-report"),
    path("projects/<int:project_id>/dashboard", views.ProjectDashboardView.as_view(), name="project-dashboard"),
]