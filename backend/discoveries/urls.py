from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/discoveries", views.ProjectDiscoveryCreateView.as_view(), name="discovery-create"),
    path("discoveries/<uuid:discovery_id>", views.DiscoveryDetailView.as_view(), name="discovery-detail"),
]