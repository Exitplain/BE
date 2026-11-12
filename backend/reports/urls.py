from django.urls import path

from . import views

urlpatterns = [
    path("runs/<str:run_ref>/reports", views.RunReportView.as_view(), name="run-reports"),
    path("reports/<uuid:report_id>", views.ReportDetailView.as_view(), name="report-detail"),
]