from __future__ import annotations

import hashlib
import os

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import AIConnection
from .vision import client, secrets


def needs_initial_setup() -> bool:
    """True on a fresh install before the first administrator account is created."""
    return not get_user_model().objects.exists()


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

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if needs_initial_setup():
            return redirect(reverse("setup"))
        return super().dispatch(request, *args, **kwargs)

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


class SetupView(View):
    """First-run onboarding: create the initial admin account and default AI configuration."""

    template_name = "registration/setup.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not needs_initial_setup():
            return redirect("/" if request.user.is_authenticated else settings.LOGIN_URL)
        return super().dispatch(request, *args, **kwargs)

    def _context(self, **extra) -> dict:
        return {
            "default_qwen_url": "http://127.0.0.1:11434/v1",
            "default_qwen_model": settings.VISION.get("MODEL") or "qwen2.5vl:7b",
            **extra,
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self._context(ai_mode="local_qwen"))

    def _validate_account_fields(self, email: str, username: str, password: str, password_confirm: str) -> list[str]:
        """Validate initial administrator email, username, and password."""
        errors: list[str] = []
        if not email:
            errors.append("Enter your email address.")
        else:
            try:
                validate_email(email)
            except ValidationError:
                errors.append("Enter a valid email address.")

        if not username:
            errors.append("Enter a username.")
        elif len(username) > 150:
            errors.append("Username must be 150 characters or fewer.")

        if not password:
            errors.append("Choose a password.")
        elif password != password_confirm:
            errors.append("The two passwords do not match.")
        else:
            try:
                validate_password(password)
            except ValidationError as exc:
                errors.extend(exc.messages)
        return errors

    def _validate_ai_config(self, ai_mode: str, base_url: str, model: str, api_key: str) -> tuple[dict | None, list[str]]:
        """Validate and normalize the selected initial AI connection option."""
        if ai_mode == "skip":
            return None, []
        if ai_mode == "local_qwen":
            target_url = base_url or "http://127.0.0.1:11434/v1"
            target_model = model or settings.VISION.get("MODEL") or "qwen2.5vl:7b"
            label, key = "Local Qwen (Ollama)", ""
        elif ai_mode == "custom":
            if not base_url or not model:
                return None, ["Enter both the API base URL and model name, or choose Local Qwen / Skip."]
            target_url, target_model, label, key = base_url, model, "Custom AI", api_key
        else:
            return None, ["Choose a valid AI setup option."]

        try:
            validated_url = client.validate_base_url(target_url)
        except client.VisionError as exc:
            return None, [exc.message]
        return {"label": label, "base_url": validated_url, "model": target_model, "api_key": key}, []

    def post(self, request: HttpRequest) -> HttpResponse:
        email = (request.POST.get("email") or "").strip()
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        password_confirm = request.POST.get("password_confirm") or ""
        ai_mode = (request.POST.get("ai_mode") or "local_qwen").strip()
        base_url = (request.POST.get("base_url") or "").strip()
        model = (request.POST.get("model") or "").strip()[:160]
        api_key = (request.POST.get("api_key") or "").strip()

        if not username and email and "@" in email:
            username = email.split("@", 1)[0][:150]

        errors = self._validate_account_fields(email, username, password, password_confirm)
        validated_ai, ai_errors = self._validate_ai_config(ai_mode, base_url, model, api_key)
        errors.extend(ai_errors)

        if errors:
            return render(
                request,
                self.template_name,
                self._context(
                    errors=errors,
                    email=email,
                    username=username,
                    ai_mode=ai_mode,
                    base_url=base_url,
                    model=model,
                ),
                status=400,
            )

        User = get_user_model()
        with transaction.atomic():
            if User.objects.exists():
                return redirect(settings.LOGIN_URL)
            user = User.objects.create_superuser(username=username, email=email, password=password)
            if validated_ai is not None:
                key = validated_ai["api_key"]
                AIConnection.objects.create(
                    user=user,
                    use_own=True,
                    label=validated_ai["label"],
                    base_url=validated_ai["base_url"],
                    model=validated_ai["model"],
                    api_key_encrypted=secrets.encrypt(key) if key else "",
                    key_hint=secrets.hint(key) if key else "",
                )
        login(request, user)
        return redirect("/")


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
