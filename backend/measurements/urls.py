from django.urls import path

from . import views

urlpatterns = [
    path("e.js", views.script, name="measure-script"),
    path("collect", views.collect, name="measure-collect"),
    path("projects/<int:project_id>/measurement", views.MeasurementView.as_view(), name="measurement"),
    path("projects/<int:project_id>/measurement/mapping", views.MeasurementMappingView.as_view(),
         name="measurement-mapping"),
    path("projects/<int:project_id>/measurement/funnel", views.MeasurementFunnelView.as_view(),
         name="measurement-funnel"),
    path("projects/<int:project_id>/measurement/steps", views.MeasurementStepsView.as_view(), name="measurement-steps"),
    path("projects/<int:project_id>/measurement/imports", views.MeasurementImportsView.as_view(),
         name="measurement-imports"),
    path("projects/<int:project_id>/measurement/imports/<int:import_id>", views.MeasurementImportDetailView.as_view(),
         name="measurement-import-detail"),
    path("projects/<int:project_id>/measurement/csv-template", views.MeasurementCsvTemplateView.as_view(),
         name="measurement-csv-template"),
]
