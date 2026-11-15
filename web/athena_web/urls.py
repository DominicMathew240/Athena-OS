from django.contrib.auth.views import LogoutView
from django.urls import include, path

from projects.auth_views import SetupView, ThrottledLoginView, healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("setup/", SetupView.as_view(), name="setup"),
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", include("projects.urls")),
]
