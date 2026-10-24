"""Athena console settings.

Production-safe by default: DEBUG is off unless ``DJANGO_DEBUG=1``, a secret key and
an allowed-host list are mandatory, cookies are Secure, and HTTPS is enforced. Set
``DJANGO_DEBUG=1`` for local development (loopback hosts, no TLS requirement).
Every deployment knob is an ``ATHENA_*`` environment variable; see web/DEPLOYMENT.md.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name, "")
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(default or [])


DEBUG = _flag("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is disabled.")
    SECRET_KEY = "local-development-only-not-for-deployment"
elif not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-")):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be at least 50 characters and must not be a django-insecure key."
    )

if DEBUG:
    ALLOWED_HOSTS = _list("ATHENA_ALLOWED_HOSTS", ["127.0.0.1", "localhost", "[::1]", "testserver"])
else:
    ALLOWED_HOSTS = _list("ATHENA_ALLOWED_HOSTS")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ATHENA_ALLOWED_HOSTS must list the console's host names (comma-separated, no '*')."
        )
CSRF_TRUSTED_ORIGINS = _list("ATHENA_CSRF_TRUSTED_ORIGINS")

ROOT_URLCONF = "athena_web.urls"
WSGI_APPLICATION = "athena_web.wsgi.application"
ASGI_APPLICATION = "athena_web.asgi.application"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "projects.apps.ProjectsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "projects.middleware.ConsoleLoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
]

if not DEBUG:  # runserver serves static files itself in development
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "projects.permissions.context",
            ],
        },
    }
]

DATA_HOME = Path(os.environ.get("ATHENA_DATA_HOME", Path.home() / ".athena-os"))
MEDIA_ROOT = Path(os.environ.get("ATHENA_MEDIA_ROOT", DATA_HOME / "media"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("ATHENA_DB_PATH", DATA_HOME / "projects.sqlite3"),
        "OPTIONS": {
            "timeout": 20,
            # WAL lets the web process read while the auto-label worker writes.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

# Login throttle state must be shared between gunicorn workers, so it lives on disk.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(DATA_HOME / "cache"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_AGE = int(os.environ.get("ATHENA_SESSION_HOURS", "12")) * 3600
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# The console reads the CSRF token from a <meta> tag, so the cookie can be HttpOnly.
CSRF_COOKIE_HTTPONLY = True
LOGIN_MAX_FAILURES = int(os.environ.get("ATHENA_LOGIN_MAX_FAILURES", "8"))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("ATHENA_LOGIN_LOCKOUT_SECONDS", "900"))
TRUST_FORWARDED_FOR = _flag("ATHENA_TRUST_FORWARDED_FOR", False)

# Access roles (docs/guides/access-roles.md). A signed-in user who is in none of the four "Athena ..." groups
# gets this role: "labeler" (default) or "viewer". They can never approve or roll back a model.
ATHENA_UNASSIGNED_ROLE = os.environ.get("ATHENA_UNASSIGNED_ROLE", "labeler").strip().lower() or "labeler"
if ATHENA_UNASSIGNED_ROLE not in {"labeler", "viewer"}:
    raise ImproperlyConfigured("ATHENA_UNASSIGNED_ROLE must be 'labeler' or 'viewer'.")
# When on, the person who started the training run for a model cannot approve that model.
ATHENA_APPROVAL_REQUIRES_SECOND_PERSON = _flag("ATHENA_APPROVAL_REQUIRES_SECOND_PERSON", False)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("ATHENA_STATIC_ROOT", BASE_DIR / "staticfiles"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
DATA_UPLOAD_MAX_NUMBER_FILES = 20
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700

# --- Transport and browser security -------------------------------------------------
BEHIND_PROXY = _flag("ATHENA_BEHIND_PROXY", False)
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _flag("ATHENA_SECURE_SSL", not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
SECURE_HSTS_SECONDS = int(
    os.environ.get("ATHENA_HSTS_SECONDS", "31536000" if SECURE_SSL_REDIRECT else "0")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = False
# Preloading is a one-way public commitment for the whole domain; operators opt in deliberately.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CSP = {
    "default-src": [CSP.NONE],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:", "blob:"],
    "connect-src": [CSP.SELF],
    "font-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "base-uri": [CSP.NONE],
    "frame-ancestors": [CSP.NONE],
}

# --- Auto-labelling -----------------------------------------------------------------
# The web process only queues jobs; the model runs in `manage.py autolabel_worker`.
AUTOLABEL = {
    # "sam" runs SAM + CLIP in the worker; "disabled" hides the feature in the console.
    "BACKEND": os.environ.get("ATHENA_AUTOLABEL_BACKEND", "sam"),
    "SAM_MODEL": os.environ.get("ATHENA_SAM_MODEL", "facebook/sam-vit-base"),
    "CLASSIFIER_MODEL": os.environ.get("ATHENA_CLIP_MODEL", "openai/clip-vit-base-patch32"),
    "DEVICE": os.environ.get("ATHENA_AUTOLABEL_DEVICE", "auto"),
    "POINTS_PER_SIDE": int(os.environ.get("ATHENA_SAM_POINTS_PER_SIDE", "16")),
    "JOB_TIMEOUT_SECONDS": int(os.environ.get("ATHENA_AUTOLABEL_JOB_TIMEOUT", "3600")),
    # Model weights are licensed separately; set to 0 to forbid network downloads.
    "ALLOW_DOWNLOAD": _flag("ATHENA_AUTOLABEL_ALLOW_DOWNLOAD", True),
}

TRAINING = {"BACKENDS": ["projects.training.dfine:DFineBackend"]}  # opt-in D-FINE tool; the default backend is unchanged

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": os.environ.get("ATHENA_LOG_LEVEL", "INFO")},
    "loggers": {
        "django": {"level": os.environ.get("ATHENA_LOG_LEVEL", "INFO")},
        # Per-request model-hub chatter would drown the worker's own job log.
        "httpx": {"level": "WARNING"},
        "huggingface_hub": {"level": "WARNING"},
    },
}
