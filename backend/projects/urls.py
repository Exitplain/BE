from django.urls import path

from . import views

urlpatterns = [
    path("projects", views.ProjectListCreateView.as_view(), name="project-list-create"),
    path("projects/<int:project_id>", views.ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<int:project_id>/check", views.ProjectCheckView.as_view(), name="project-check"),
]
