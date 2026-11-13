from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.http import urlencode

PUBLIC_PATHS = ("/", "/login/", "/healthz")
# Views that need no role: they are about signing in or out (anyone signed in may sign out).
UNGUARDED_URL_NAMES = frozenset({"login", "logout", "healthz", "home"})


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


    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        """Fail closed: a console view that never declared its role with ``@access`` is refused.

        This is the safety net behind ``projects.permissions.access``; a forgotten decorator can never
        quietly open a write route. Sign-in, sign-out and the health probe are the only exceptions.
        """
        if getattr(view_func, "athena_access", None) is not None or not request.user.is_authenticated:
            return None
        match = request.resolver_match
        if request.path in PUBLIC_PATHS or (match and match.url_name in UNGUARDED_URL_NAMES):
            return None
        response = JsonResponse(
            {"error": "This page has no access rule yet, so it is closed. Tell your administrator."}, status=403
        )
        response["Cache-Control"] = "no-store"
        return response
