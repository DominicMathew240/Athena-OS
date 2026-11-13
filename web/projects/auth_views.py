from __future__ import annotations

import hashlib
import os

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def client_ip(request: HttpRequest) -> str:
    if settings.TRUST_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        # The last hop is the one our own proxy appended; earlier entries are client-controlled.
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1]
    return request.META.get("REMOTE_ADDR", "unknown")


def _throttle_keys(request: HttpRequest) -> list[str]:
    username = (request.POST.get("username") or "").strip().casefold()[:150]
    digest = hashlib.sha256(username.encode()).hexdigest()[:24]
    return [f"login-fail:ip:{client_ip(request)}", f"login-fail:user:{digest}"]


class ThrottledLoginView(LoginView):
    """Sign-in page that locks an IP or username out after repeated failures."""

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if any(cache.get(key, 0) >= settings.LOGIN_MAX_FAILURES for key in _throttle_keys(request)):
            minutes = max(1, settings.LOGIN_LOCKOUT_SECONDS // 60)
            form = self.get_form()
            form.add_error(
                None, f"Too many failed sign-in attempts. Wait about {minutes} minutes and try again."
            )
            response = self.render_to_response(self.get_context_data(form=form))
            response.status_code = 429
            return response
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        for key in _throttle_keys(self.request):
            cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_throttle_keys(self.request)[1])
        return super().form_valid(form)


@require_GET
@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness probe for load balancers; reveals no configuration."""
    checks = {"database": False, "media": False}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = True
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        pass
    if settings.STORAGE_KIND == "s3":
        checks["media"] = True  # the bucket is checked when files are written, not on every probe
    else:
        try:
            settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
            checks["media"] = os.access(settings.MEDIA_ROOT, os.W_OK)
        except OSError:
            pass
    ok = all(checks.values())
    return JsonResponse({"status": "ok" if ok else "unavailable", **checks}, status=200 if ok else 503)
