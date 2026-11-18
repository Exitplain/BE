from django.urls import path

from . import views

urlpatterns = [
    path("e.js", views.script, name="measure-script"),
    path("collect", views.collect, name="measure-collect"),
    path("projects/<int:project_id>/measurement", views.MeasurementView.as_view(), name="measurement"),
    path("projects/<int:project_id>/measurement/mapping", views.MeasurementMappingView.as_view(),
         name="measurement-mapping"),
    path("projects/<int:project_id>/measurement/steps", views.MeasurementStepsView.as_view(), name="measurement-steps"),
]
