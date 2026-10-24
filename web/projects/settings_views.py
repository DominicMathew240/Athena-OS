"""``/api/console/training-downloads``: may the teaching helper download ready-made models?

An administrator (superuser) switches it; Trainers and above can read it so the Teach panel can say whether it is
on. The web process only stores the choice (see :mod:`projects.console_settings`); it never fetches weights.
"""

from __future__ import annotations

import functools

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from . import console_settings, permissions
from .api_common import HttpError, json_errors, json_payload, no_store, reject_unknown
from .permissions import access

ADMIN_ONLY = "Only an administrator can change this setting."  # the 403 for a PUT from anyone else (a view's own words)


def _errors(view):
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except console_settings.SettingError as error:
            raise HttpError(error.message, error.status, **error.extra) from error

    return wrapped


@access(GET="train", PUT="admin", messages={permissions.ADMIN: ADMIN_ONLY})
@require_http_methods(["GET", "PUT"])
@json_errors
@_errors
def training_downloads(request: HttpRequest) -> JsonResponse:
    """GET: the state and the licence text. PUT ``{"allow": bool, "acknowledge_terms": true, "note": "...",
    "based_on": bool?}``: change it (turning on needs the tick and a note of at least 3 characters)."""
    can_change = permissions.can(request.user, permissions.ADMIN)
    if request.method == "GET":
        return no_store(JsonResponse(console_settings.describe(can_change=can_change)))
    payload = json_payload(request)
    reject_unknown(payload, {"allow", "acknowledge_terms", "note", "based_on"})
    if "allow" not in payload:
        raise HttpError("Say whether to allow downloads: “allow”: true or false.", 400)
    based_on = payload.get("based_on")
    if based_on is not None and not isinstance(based_on, bool):
        raise HttpError("“based_on” must be true or false: what you saw when you opened the page.", 400)
    event = console_settings.set_training_downloads(
        payload["allow"],
        actor=request.user,
        acknowledge_terms=payload.get("acknowledge_terms", False),
        note=payload.get("note", ""),
        based_on=based_on,
    )
    body = console_settings.describe(can_change=can_change)
    body["changed"] = event is not None
    body["event"] = console_settings.event_data(event) if event is not None else None
    return no_store(JsonResponse(body))
