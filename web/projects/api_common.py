"""Small helpers shared by the training and inference JSON views."""

from __future__ import annotations

import functools
import json
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404

from .models import Project

MAX_REQUEST_BYTES = 32_768


class HttpError(Exception):
    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


def no_store(response):
    response["Cache-Control"] = "no-store"
    return response


def image_project(project_id) -> Project:
    return get_object_or_404(Project, id=project_id, modality="image")


def json_payload(request: HttpRequest, *, allow_empty: bool = False) -> dict[str, Any]:
    if allow_empty and not request.body and request.content_type != "application/json":
        return {}
    if request.content_type != "application/json":
        raise HttpError("Send the request as JSON (Content-Type: application/json).", 415)
    try:
        body = request.body
    except RequestDataTooBig as error:
        raise HttpError("The request is too large.", 413) from error
    if len(body) > MAX_REQUEST_BYTES:
        raise HttpError("The request is too large.", 413)
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HttpError("The request body is not valid JSON.", 400) from error
    if not isinstance(payload, dict):
        raise HttpError("The request body must be a JSON object.", 400)
    return payload


def reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    extra = set(payload) - allowed
    if extra:
        raise HttpError(f"Unsupported field: {', '.join(sorted(map(str, extra)))}.", 400)


def json_errors(view):
    """Turn ``HttpError`` / ``LifecycleError`` into plain-language JSON responses."""
    from .training.lifecycle import LifecycleError

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except (HttpError, LifecycleError) as error:
            return no_store(JsonResponse({"error": error.message, **error.extra}, status=error.status))

    return wrapped
