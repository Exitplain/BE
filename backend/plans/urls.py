from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/plans", views.ProjectPlanCreateView.as_view(), name="plan-create"),
    path("plans/<uuid:plan_id>", views.PlanDetailView.as_view(), name="plan-detail"),
    path("plans/<uuid:plan_id>/confirmations", views.PlanConfirmationView.as_view(), name="plan-confirm"),
    path("plan-revisions/<uuid:snapshot_id>", views.PlanRevisionDetailView.as_view(), name="plan-revision-detail"),
    path("plan-revisions/<uuid:snapshot_id>/run-inputs", views.PlanRevisionRunInputsView.as_view(),
         name="plan-revision-run-inputs"),
]