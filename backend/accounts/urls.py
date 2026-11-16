from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf", views.CsrfView.as_view(), name="auth-csrf"),
    path("auth/email/request", views.EmailRequestView.as_view(), name="auth-email-request"),
    path("auth/email/verify", views.EmailVerifyView.as_view(), name="auth-email-verify"),
    path("auth/google", views.GoogleLoginView.as_view(), name="auth-google"),
    path("auth/session", views.SessionView.as_view(), name="auth-session"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("workspace", views.WorkspaceView.as_view(), name="workspace"),
]