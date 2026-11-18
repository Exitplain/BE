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
    path("projects/<int:project_id>/participant-links", views.ParticipantLinkListView.as_view(),
         name="participant-links"),
    path("participant-links/<int:link_id>/disable", views.ParticipantLinkDisableView.as_view(),
         name="participant-link-disable"),
    path("participant-links/<str:token>", views.PublicParticipantView.as_view(), name="participant-public"),
    path("participant-links/<str:token>/responses", views.PublicParticipantResponseView.as_view(),
         name="participant-responses"),
    path("participant-links/<str:token>/screens/<int:observation_id>", views.PublicParticipantScreenView.as_view(),
         name="participant-screen"),
]
