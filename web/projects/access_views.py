"""The access screen and its JSON API: an administrator gives and takes away roles.

Everything here needs the ``admin`` level (a superuser). Roles are given console-wide or for one project; every
change is written to ``RoleChange`` by :mod:`projects.access_admin`. Usernames appear only in these responses
(an administrator needs them to pick a person); they are never written into the record.
"""

from __future__ import annotations

import functools
import uuid

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from . import access_admin, permissions
from .api_common import HttpError, json_errors, json_payload, no_store, reject_unknown
from .models import Project, RoleChange
from .permissions import access

MAX_PEOPLE = 500
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

ROLE_SUMMARY = {
    permissions.VIEWER: "Can look at everything, change nothing.",
    permissions.LABELER: "Can also add pictures, draw boxes, save snapshots and run a check screen.",
    permissions.TRAINER: "Can also teach models, move one to trial, set up check screens and delete a project.",
    permissions.APPROVER: "Can also approve a model for use or go back to the previous one.",
}


def _errors(view):
    """Turn a refused role change into the plain-language JSON the rest of the console uses."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except access_admin.AccessError as error:
            raise HttpError(error.message, error.status) from error

    return wrapped


def _person(user_id: int):
    user = get_object_or_404(get_user_model(), pk=user_id, is_active=True)
    return user


def _refuse_superuser(user) -> None:
    if user.is_superuser:
        raise HttpError(
            "This person is a superuser, who can already do everything on every project, so a role would change "
            "nothing. Nothing was changed.",
            409,
        )


def _role_and_note(request: HttpRequest, *, allow_missing_role: bool = False) -> tuple[str | None, str]:
    payload = json_payload(request)
    reject_unknown(payload, {"role", "note"})
    if "role" not in payload and not allow_missing_role:
        raise HttpError('Say which role to give, or "role": null to take it away.', 400)
    return access_admin.clean_role(payload.get("role")), access_admin.clean_note(payload.get("note"))


def _summary(user) -> dict:
    return access_admin.people_summary([user])[0]


@access(GET="admin")
@require_GET
@ensure_csrf_cookie
def access_page(request: HttpRequest) -> HttpResponse:
    response = render(request, "projects/access.html")
    response["Cache-Control"] = "no-store"
    return response


@access(GET="admin")
@require_GET
def access_overview(request: HttpRequest) -> JsonResponse:
    """Everyone who can sign in, the console-wide role they hold, and the roles they hold on single projects."""
    User = get_user_model()
    users = list(User.objects.filter(is_active=True).order_by(User.USERNAME_FIELD)[: MAX_PEOPLE + 1])
    truncated = len(users) > MAX_PEOPLE
    users = users[:MAX_PEOPLE]
    return no_store(
        JsonResponse(
            {
                "roles": [
                    {"key": role, "title": permissions.ROLE_TITLES[role], "summary": ROLE_SUMMARY[role]}
                    for role in permissions.ROLES
                ],
                "unassigned_role": permissions.unassigned_role(),
                "people": access_admin.people_summary(users),
                "people_truncated": truncated,
                "projects": [{"id": str(p.pk), "name": p.name} for p in Project.objects.order_by("name", "id")],
            }
        )
    )


@access(GET="admin", PUT="admin")
@require_http_methods(["PUT"])
@json_errors
@_errors
def access_console_role(request: HttpRequest, user_id: int) -> JsonResponse:
    """Set (or clear with ``"role": null``) a person's console-wide role."""
    user = _person(user_id)
    _refuse_superuser(user)
    role, note = _role_and_note(request)
    change = access_admin.set_console_role(user, role, actor=request.user, note=note)
    return no_store(
        JsonResponse(
            {
                "changed": change is not None,
                "change": access_admin.change_data(change) if change else None,
                "person": _summary(user),
            }
        )
    )


def _project_from(value) -> Project:
    try:
        key = uuid.UUID(str(value))
    except ValueError:
        raise HttpError("Say which project, using its id.", 400) from None
    return get_object_or_404(Project, pk=key)


@access(GET="admin", PUT="admin", DELETE="admin")
@require_http_methods(["PUT", "DELETE"])
@json_errors
@_errors
def access_project_role(request: HttpRequest, user_id: int) -> JsonResponse:
    """Set a person's role on one project (PUT ``{"project_id", "role"}``), or take it away so their
    console-wide role applies there (DELETE ``?project=<id>``)."""
    user = _person(user_id)
    _refuse_superuser(user)
    if request.method == "DELETE":
        project = _project_from(request.GET.get("project", ""))
        note = access_admin.clean_note(request.GET.get("note", ""))
        role = None
    else:
        payload = json_payload(request)
        reject_unknown(payload, {"project_id", "role", "note"})
        if "role" not in payload:
            raise HttpError('Say which role to give. To take a role away, use DELETE.', 400)
        project = _project_from(payload.get("project_id"))
        role, note = access_admin.clean_role(payload["role"]), access_admin.clean_note(payload.get("note"))
    change = access_admin.set_project_role(project, user, role, actor=request.user, note=note)
    return no_store(
        JsonResponse(
            {
                "changed": change is not None,
                "change": access_admin.change_data(change) if change else None,
                "person": _summary(user),
            }
        )
    )


@access(GET="admin")
@require_GET
@json_errors
def access_changes(request: HttpRequest) -> JsonResponse:
    """The record of role changes, newest first. Filter with ``project`` (an id) and/or ``person`` (``u3``)."""
    changes = RoleChange.objects.select_related("user", "actor", "project")
    project = request.GET.get("project", "").strip()
    person = request.GET.get("person", "").strip()
    if project:
        changes = changes.filter(project_ref=project)
    if person:
        changes = changes.filter(user_ref=person)
    try:
        limit = min(max(int(request.GET.get("limit", PAGE_SIZE)), 1), MAX_PAGE_SIZE)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError as error:
        raise HttpError("limit and offset must be whole numbers.", 400) from error
    total = changes.count()
    rows = list(changes[offset : offset + limit])
    return no_store(
        JsonResponse(
            {
                "total": total,
                "offset": offset,
                "limit": limit,
                "changes": [access_admin.change_data(row) for row in rows],
            }
        )
    )
