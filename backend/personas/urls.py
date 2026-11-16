from django.urls import path

from . import views

urlpatterns = [
    path("plan-revisions/<uuid:snapshot_id>/personas", views.PlanRevisionPersonasView.as_view(),
         name="plan-revision-personas"),
]