from django.urls import path

from . import views

urlpatterns = [
    path("projects/<int:project_id>/test-accounts", views.ProjectTestAccountView.as_view(),
         name="project-test-accounts"),
    path("projects/<int:project_id>/test-accounts/events", views.ProjectTestAccountEventView.as_view(),
         name="project-test-account-events"),
]