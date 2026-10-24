"""Access roles for the console: who may look, label, teach and approve.

The console-wide role of a person is a plain Django auth Group, so an administrator can manage it from the
shell (``manage.py athena_roles``), the access screen or any Django user tool:

* ``Athena Viewer``   look at everything (every GET), change nothing.
* ``Athena Labeler``  Viewer + pictures, boxes, labels, review suggestions, auto-suggest, snapshots,
                      export, run a check screen, record feedback and review decisions.
* ``Athena Trainer``  Labeler + start/cancel teaching, move a model to trial, set up check screens,
                      delete a project.
* ``Athena Approver`` Trainer + approve a model for use / go back to the previous model.

Superusers can do everything. A signed-in user who is in none of the four groups is "unassigned": they
get ``ATHENA_UNASSIGNED_ROLE`` (``labeler`` by default, or ``viewer``), but can never approve or roll back.

A role can also be given for **one project** (``ProjectRole``). On that project it *replaces* the person's
console-wide role, up or down; on every other project the console-wide role still applies. Superusers are
never lowered. Changing who holds a role is the one action only a superuser may take (``admin``), and every
change is written to ``RoleChange`` by ``projects.access_admin``.

Enforcement is one decorator, :func:`access`, put on every view; it reads ``project_id`` from the URL so the project's own role applies. ``ConsoleLoginRequiredMiddleware`` refuses
(403) any view that was not declared that way, and ``tests_roles`` walks the URL configuration to prove
every non-GET route names its required role.
"""

from __future__ import annotations

import functools
import json
import uuid
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

VIEWER, LABELER, TRAINER, APPROVER = "viewer", "labeler", "trainer", "approver"
ROLES = (VIEWER, LABELER, TRAINER, APPROVER)  # lowest to highest; each includes everything before it
GROUP_NAMES = {
    VIEWER: "Athena Viewer",
    LABELER: "Athena Labeler",
    TRAINER: "Athena Trainer",
    APPROVER: "Athena Approver",
}
ROLE_TITLES = {role: role.capitalize() for role in ROLES}
UNASSIGNED_CHOICES = (LABELER, VIEWER)

# What a view can ask for. Each action is allowed to its own role and everything above it.
VIEW, LABEL, TRAIN, APPROVE = "view", "label", "train", "approve"
ACTION_ROLE = {VIEW: VIEWER, LABEL: LABELER, TRAIN: TRAINER, APPROVE: APPROVER}
# Changing who holds a role. Not a role: only a superuser may do it, whatever roles they hold or lack.
ADMIN = "admin"
ADMIN_REQUIRED = "administrator"
ADMIN_MESSAGE = "Only an administrator can change who holds which role. Ask an administrator."
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def rank(role: str) -> int:
    return ROLES.index(role)


def unassigned_role() -> str:
    value = str(getattr(settings, "ATHENA_UNASSIGNED_ROLE", LABELER)).strip().lower()
    return value if value in UNASSIGNED_CHOICES else VIEWER  # an unreadable value fails closed


def requires_second_person() -> bool:
    return bool(getattr(settings, "ATHENA_APPROVAL_REQUIRES_SECOND_PERSON", False))


def assigned_roles(user) -> list[str]:
    """Roles the user was given through the Athena groups, lowest first."""
    if not getattr(user, "is_authenticated", False):
        return []
    names = set(user.groups.filter(name__in=list(GROUP_NAMES.values())).values_list("name", flat=True))
    return [role for role in ROLES if GROUP_NAMES[role] in names]


def _project_key(project) -> str | None:
    """A project (or its id) as a string id, or ``None`` when there is nothing to look up."""
    if project is None:
        return None
    key = getattr(project, "pk", project)
    try:
        return str(uuid.UUID(str(key)))
    except ValueError:
        return None


def project_roles(user) -> dict[str, str]:
    """Roles the user was given on single projects: ``{project id: role}``."""
    if not getattr(user, "is_authenticated", False):
        return {}
    from .models import ProjectRole

    return {str(pid): role for pid, role in ProjectRole.objects.filter(user=user).values_list("project_id", "role")}


def project_role(user, project) -> str | None:
    """The role given to ``user`` on ``project`` alone, or ``None`` when they have none there."""
    key = _project_key(project)
    if key is None or not getattr(user, "is_authenticated", False):
        return None
    from .models import ProjectRole

    return ProjectRole.objects.filter(project_id=key, user=user).values_list("role", flat=True).first()


def console_role_of(user) -> str | None:
    """The role that applies on a project where the person has no role of their own."""
    if not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser:
        return APPROVER
    roles = assigned_roles(user)
    return roles[-1] if roles else unassigned_role()


def role_of(user, project=None) -> str | None:
    """The role that applies to ``user`` right now (``None`` for an anonymous caller).

    With a ``project`` (a ``Project`` or its id) a role given for that project replaces the console-wide one.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser:
        return APPROVER
    return project_role(user, project) or console_role_of(user)


def can(user, action: str, project=None) -> bool:
    if action == ADMIN:
        return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))
    role = role_of(user, project)
    return role is not None and rank(role) >= rank(ACTION_ROLE[action])


def opaque_id(user) -> str | None:
    return f"u{user.pk}" if getattr(user, "is_authenticated", False) else None


def describe(user, project=None) -> dict[str, Any]:
    """What the signed-in person may do (on ``project`` when given). Carries no username or e-mail address."""
    role = role_of(user, project)
    assigned = assigned_roles(user)
    superuser = bool(getattr(user, "is_superuser", False))
    scoped = project_role(user, project) is not None and not superuser
    return {
        "id": opaque_id(user),
        "role": role,
        "roles": assigned,
        "assigned": bool(assigned) or superuser,
        "superuser": superuser,
        "scope": "project" if scoped else "console",
        "project_id": _project_key(project) if scoped else None,
        "project_roles": project_roles(user),
        "can_view": can(user, VIEW, project),
        "can_label": can(user, LABEL, project),
        "can_train": can(user, TRAIN, project),
        "can_approve": can(user, APPROVE, project),
        "can_administer": can(user, ADMIN),
        "approval_requires_second_person": requires_second_person(),
    }


def capabilities_by_project(user) -> dict[str, dict[str, Any]]:
    """For each project where the person has a role of their own, what that role allows (for the page)."""
    if getattr(user, "is_superuser", False):
        return {}
    result = {}
    for project_id, role in project_roles(user).items():
        result[project_id] = {
            "role": role,
            "title": ROLE_TITLES[role],
            "label": rank(role) >= rank(LABELER),
            "train": rank(role) >= rank(TRAINER),
            "approve": rank(role) >= rank(APPROVER),
        }
    return result


def audit_tag(user, project=None) -> str:
    """One line for the audit trail: the role the person acted under (on ``project``) and their opaque id."""
    return f"[Acted as {ROLE_TITLES.get(role_of(user, project) or '', 'unknown role')}, user {opaque_id(user)}.]"


def denial_message(role: str | None, action: str) -> str:
    if action == ADMIN:
        return ADMIN_MESSAGE
    needed = ROLE_TITLES[ACTION_ROLE[action]]
    if role == VIEWER:
        return f"Your account can look but not change things. Ask an administrator for the {needed} role."
    if action == APPROVE:
        return (
            "Your account cannot approve a model for use or go back to the previous model. "
            "Ask an administrator for the Approver role."
        )
    if action == TRAIN:
        return (
            "Your account can label pictures but cannot teach the computer, move a model to trial or set up "
            "check screens. Ask an administrator for the Trainer role."
        )
    return f"Your account is not allowed to do that. Ask an administrator for the {needed} role."


def _wants_page(request: HttpRequest) -> bool:
    """A person who followed a link or typed an address (not a script calling the API) should get a page."""
    return not request.path.startswith("/api/") and "text/html" in request.headers.get("Accept", "")


def denied(request: HttpRequest, action: str, project=None, message: str | None = None) -> HttpResponse:
    """The refusal. ``message`` is a view's own sentence for a signed-in person (see :func:`access`)."""
    if not request.user.is_authenticated:
        response = JsonResponse({"error": "Sign in to continue."}, status=401)
    else:
        role = role_of(request.user, project)
        message = message or denial_message(role, action)
        if _wants_page(request):
            from django.template.loader import render_to_string

            response = HttpResponse(
                render_to_string(
                    "projects/message.html",
                    {"code": "403", "title": "Not allowed", "heading": "You cannot open this page", "message": message},
                ),
                status=403,
            )
        else:
            response = JsonResponse(
                {
                    "error": message,
                    "your_role": role,
                    "required_role": ADMIN_REQUIRED if action == ADMIN else ACTION_ROLE[action],
                },
                status=403,
            )
    response["Cache-Control"] = "no-store"
    return response


def access(*, messages: dict[str, str] | None = None, **by_method: str):
    """Declare the role a view needs. ``@access()`` means "any signed-in user may GET"; name the others:

        @access(POST="label", DELETE="train")      # GET stays "view"
        @access(GET="label")                       # a GET that exports data
        @access(GET="train", PUT="admin", messages={"admin": "Only an administrator can change this setting."})

    ``messages`` maps an access level (``"label"``, ``"admin"``...) to the sentence a signed-in person who is refused
    *that level on this view* is told, instead of the standard one (:func:`denial_message`). Other levels on the same
    view, the "Sign in" answer and the 403 status are unchanged, and the declaration that ``ConsoleLoginRequiredMiddleware``
    and ``tests_roles`` read (``athena_access``) does not carry the words.

    Put it outermost (above ``require_http_methods``). A method that is not named here is passed straight
    on, so the view's own method check answers 405 for it; ``tests_roles`` proves no route answers a
    write that it has not declared.
    """
    declared = {method.upper(): action for method, action in by_method.items()}
    bad = {action for action in declared.values() if action not in ACTION_ROLE and action != ADMIN}
    if bad:
        raise ValueError(f"Unknown access level {sorted(bad)}; use one of {sorted([*ACTION_ROLE, ADMIN])}.")
    declared.setdefault("GET", VIEW)
    words = dict(messages) if isinstance(messages, dict) else messages
    if words is not None and (
        not isinstance(words, dict)
        or any(level not in ACTION_ROLE and level != ADMIN for level in words)
        or any(not isinstance(text, str) or not text.strip() for text in words.values())
    ):
        raise ValueError(f"messages must map an access level ({sorted([*ACTION_ROLE, ADMIN])}) to a non-empty sentence.")
    words = words or {}

    def decorate(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            method = request.method
            action = declared.get(method) or (declared["GET"] if method in SAFE_METHODS else None)
            project_id = kwargs.get("project_id")  # a role given for this project applies here
            if action is not None and not can(request.user, action, project_id):
                return denied(request, action, project_id, words.get(action))
            return view(request, *args, **kwargs)

        wrapped.athena_access = {**declared}
        return wrapped

    return decorate


def _project_of(request: HttpRequest):
    """The project a page is about, when its URL says so (``project_id``, or an app's web name)."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return None
    if "project_id" in match.kwargs:
        return match.kwargs["project_id"]
    slug = match.kwargs.get("slug")
    if slug:
        from .models import InferenceApp

        return InferenceApp.objects.filter(slug=slug).values_list("project_id", flat=True).first()
    return None


def context(request: HttpRequest) -> dict[str, Any]:
    """Template context: ``athena_access`` (see :func:`describe`) so pages can mark what is switched off.

    On a page about one project the role is the one that applies to that project. The console itself shows
    the console-wide role and also carries ``athena_project_access`` (a JSON object keyed by project id) for
    the projects where the person has a role of their own, so the page can switch when a project is opened.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    info = describe(user, _project_of(request))
    info["role_title"] = ROLE_TITLES[info["role"]]
    scoped = capabilities_by_project(user)
    return {
        "athena_access": info,
        "athena_project_access": json.dumps(scoped, sort_keys=True) if scoped else "",
    }
