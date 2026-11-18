from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/calibration", views.CalibrationView.as_view(), name="calibration"),
    path("projects/<int:project_id>/calibration/proposal", views.ProposalView.as_view(), name="calibration-proposal"),
    path("projects/<int:project_id>/calibration/compare", views.CompareView.as_view(), name="calibration-compare"),
    path("projects/<int:project_id>/calibrations", views.CalibrationListView.as_view(), name="calibrations"),
    path("projects/<int:project_id>/calibrations/reset", views.ResetView.as_view(), name="calibration-reset"),
    path("calibrations/<int:revision_id>/restore", views.RestoreView.as_view(), name="calibration-restore"),
    path("projects/<int:project_id>/human-observations", views.ObservationListView.as_view(), name="human-observations"),
    path("projects/<int:project_id>/human-observations/import", views.ObservationImportView.as_view(),
         name="human-observations-import"),
    path("human-observations/<int:observation_id>", views.ObservationDetailView.as_view(), name="human-observation"),
]
