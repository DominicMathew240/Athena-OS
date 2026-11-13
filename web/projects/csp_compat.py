"""Content-Security-Policy for Django versions without ``django.middleware.csp`` (before 6.0).

Same setting (``SECURE_CSP``, a dict of directive -> list of sources), same header, so the policy is identical
whichever Django runs the console. Importable from settings: no models, no app registry.
"""

from __future__ import annotations

from django.conf import settings


class CSP:
    NONE = "'none'"
    SELF = "'self'"
    UNSAFE_INLINE = "'unsafe-inline'"
    UNSAFE_EVAL = "'unsafe-eval'"


def build_policy(config: dict) -> str:
    parts = []
    for directive, sources in config.items():
        values = sources if isinstance(sources, (list, tuple)) else [sources]
        parts.append(" ".join([directive, *[str(value) for value in values]]).strip())
    return "; ".join(parts)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        config = getattr(settings, "SECURE_CSP", None)
        if config and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = build_policy(config)
        return response
