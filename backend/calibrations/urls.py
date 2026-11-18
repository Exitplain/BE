from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/calibration", views.CalibrationView.as_view(), name="calibration"),
    path("projects/<int:project_id>/calibration/proposal", views.ProposalView.as_view(), name="calibration-proposal"),
    path("projects/<int:project_id>/calibrations", views.CalibrationListView.as_view(), name="calibrations"),
    path("projects/<int:project_id>/calibrations/reset", views.ResetView.as_view(), name="calibration-reset"),
    path("calibrations/<int:revision_id>/restore", views.RestoreView.as_view(), name="calibration-restore"),
]
