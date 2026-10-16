from django.contrib.auth.views import LogoutView
from django.urls import include, path

from projects.auth_views import ThrottledLoginView, healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", include("projects.urls")),
]
