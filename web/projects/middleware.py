from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.http import urlencode

PUBLIC_PATHS = ("/login/", "/healthz")


class ConsoleLoginRequiredMiddleware:
    """Require a signed-in user for the console and every API route.

    Browsers are redirected to the login page; API callers get a JSON 401 so the
    console's fetch calls can show a clear message instead of parsing HTML.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.static_prefix = "/" + settings.STATIC_URL.strip("/") + "/"

    def __call__(self, request: HttpRequest):
        path = request.path
        if (
            request.user.is_authenticated
            or path in PUBLIC_PATHS
            or path.startswith(self.static_prefix)
        ):
            return self.get_response(request)
        if path.startswith("/api/"):
            response = JsonResponse({"error": "Sign in to continue."}, status=401)
            response["Cache-Control"] = "no-store"
            return response
        target = path if url_has_allowed_host_and_scheme(path, allowed_hosts=None) else "/"
        query = urlencode({"next": target}) if target != "/" else ""
        return redirect(f"{settings.LOGIN_URL}?{query}" if query else settings.LOGIN_URL)
