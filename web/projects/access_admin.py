"""Changing who holds which role, and writing down every change.

One place does both so the access screen, the ``athena_roles`` command and anything else cannot change a
role without leaving a ``RoleChange`` row:

* :func:`set_console_role` - the person's console-wide role (an Athena group), or ``None`` for no role;
* :func:`set_project_role` - the person's role on one project, or ``None`` to fall back to the console-wide one.

A call that would change nothing writes nothing and returns ``None``. Names are never stored in the record:
people are kept as opaque ids (``u3``) and projects as an id plus the name they had at the time.

Group changes made by some other tool (the Django shell, a user-admin add-on) are caught by a signal and
recorded with ``via="other"``; the functions here switch that signal off while they work so nothing is
recorded twice.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterable

from django.contrib.auth.models import Group
from django.db import transaction
from django.utils import timezone

from . import permissions
from .models import Project, ProjectRole, RoleChange


class AccessError(Exception):
    """A role change that was refused, with a sentence a person can read."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_recording_ourselves: contextvars.ContextVar[bool] = contextvars.ContextVar("athena_role_change_open", default=False)


def clean_role(value) -> str | None:
    """``None``/empty means "no role of this kind"; anything else must be one of the four role names."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value.strip().lower() not in permissions.ROLES:
        raise AccessError(f"Unknown role. Use one of: {', '.join(permissions.ROLES)}, or none.")
    return value.strip().lower()


def clean_note(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AccessError("The note must be text.")
    text = " ".join(value.split())
    if len(text) > RoleChange._meta.get_field("note").max_length:
        raise AccessError("The note is too long (300 characters at most).")
    return text


def _ref(user) -> str:
    return permissions.opaque_id(user) or ""


def ensure_groups() -> dict[str, Group]:
    return {role: Group.objects.get_or_create(name=permissions.GROUP_NAMES[role])[0] for role in permissions.ROLES}


def console_role(user) -> str | None:
    """The role the person holds console-wide through a group, or ``None`` (not the unassigned default)."""
    roles = permissions.assigned_roles(user)
    return roles[-1] if roles else None


def _record(*, scope, via, actor, user, project=None, before, after, note) -> RoleChange:
    return RoleChange.objects.create(
        scope=scope,
        via=via,
        actor=actor if getattr(actor, "pk", None) else None,
        actor_ref=_ref(actor) if getattr(actor, "pk", None) else "",
        user=user,
        user_ref=_ref(user),
        project=project,
        project_ref=str(project.pk) if project else "",
        project_name=project.name if project else "",
        before=before or "",
        after=after or "",
        note=note,
        created_at=timezone.now(),
    )


@contextlib.contextmanager
def _ours():
    token = _recording_ourselves.set(True)
    try:
        yield
    finally:
        _recording_ourselves.reset(token)


def set_console_role(user, role, *, actor=None, via=RoleChange.VIA_SCREEN, note="") -> RoleChange | None:
    """Give ``user`` exactly one console-wide role (or none). Returns the record, or ``None`` if unchanged."""
    role = clean_role(role)
    note = clean_note(note)
    with transaction.atomic(), _ours():
        before = console_role(user)
        extra = permissions.assigned_roles(user)
        if before == role and len(extra) == (1 if role else 0):
            return None
        groups = ensure_groups()
        user.groups.remove(*groups.values())
        if role:
            user.groups.add(groups[role])
        return _record(
            scope=RoleChange.CONSOLE, via=via, actor=actor, user=user, before=before, after=role, note=note
        )


def set_project_role(project: Project, user, role, *, actor=None, via=RoleChange.VIA_SCREEN, note="") -> RoleChange | None:
    """Give ``user`` a role on ``project`` alone (or take it away with ``None``). ``None`` if unchanged."""
    role = clean_role(role)
    note = clean_note(note)
    with transaction.atomic(), _ours():
        row = ProjectRole.objects.select_for_update().filter(project=project, user=user).first()
        before = row.role if row else None
        if before == role:
            return None
        if role is None:
            row.delete()
        elif row is None:
            ProjectRole.objects.create(
                project=project, user=user, role=role, assigned_by=actor if getattr(actor, "pk", None) else None
            )
        else:
            row.role = role
            row.assigned_by = actor if getattr(actor, "pk", None) else None
            row.updated_at = timezone.now()
            row.save(update_fields=["role", "assigned_by", "updated_at"])
        return _record(
            scope=RoleChange.PROJECT, via=via, actor=actor, user=user, project=project,
            before=before, after=role, note=note,
        )


# -- changes made by some other tool ----------------------------------------------------------------
def record_group_change(sender, instance, action, reverse, pk_set, **kwargs) -> None:
    """``m2m_changed`` handler for ``User.groups``: write down changes to the Athena groups.

    It runs *before* the change (``pre_add``/``pre_remove``/``pre_clear``) so it can compare with who holds
    the group now, and so a call that changes nothing records nothing. It is silent while this module is
    making a change itself.
    """
    if _recording_ourselves.get() or action not in {"pre_add", "pre_remove", "pre_clear"}:
        return
    from django.contrib.auth import get_user_model

    User = get_user_model()
    role_of_group = {name: role for role, name in permissions.GROUP_NAMES.items()}
    adding = action == "pre_add"
    if reverse:  # group.user_set.add(user, ...): ``instance`` is the group
        role = role_of_group.get(instance.name)
        if role is None:
            return
        members = set(instance.user_set.values_list("pk", flat=True))
        wanted = members if action == "pre_clear" else set(pk_set or ())
        for user in User.objects.filter(pk__in=(wanted - members) if adding else (wanted & members)):
            _record_other(user, before=None if adding else role, after=role if adding else None)
        return
    held = set(instance.groups.filter(name__in=list(role_of_group)).values_list("name", flat=True))
    if action == "pre_clear":
        touched = held
    else:
        named = set(Group.objects.filter(pk__in=pk_set or (), name__in=list(role_of_group)).values_list("name", flat=True))
        touched = (named - held) if adding else (named & held)
    for name in sorted(touched, key=lambda n: permissions.rank(role_of_group[n])):
        role = role_of_group[name]
        _record_other(instance, before=None if adding else role, after=role if adding else None)


def _record_other(user, *, before, after) -> None:
    """One row for a group added or removed outside this module. ``before``/``after`` are the group's role."""
    _record(
        scope=RoleChange.CONSOLE, via=RoleChange.VIA_OTHER, actor=None, user=user, before=before, after=after,
        note="Changed outside the access screen.",
    )


def people_summary(users: Iterable) -> list[dict]:
    """Who holds what, for the access screen. Usernames are shown to administrators only."""
    users = list(users)
    held = {user.pk: permissions.assigned_roles(user) for user in users}
    by_user: dict[int, list] = {}
    for row in ProjectRole.objects.filter(user__in=users).select_related("project").order_by("project__name"):
        by_user.setdefault(row.user_id, []).append(
            {"project_id": str(row.project_id), "project_name": row.project.name, "role": row.role}
        )
    people = []
    for user in users:
        roles = held[user.pk]
        people.append(
            {
                "id": permissions.opaque_id(user),
                "user_id": user.pk,
                "username": user.get_username(),
                "superuser": user.is_superuser,
                "console_role": roles[-1] if roles else None,
                "also_in_groups": roles[:-1],
                "effective_console_role": permissions.console_role_of(user),
                "project_roles": by_user.get(user.pk, []),
            }
        )
    return people


def change_data(change: RoleChange) -> dict:
    """A record as the screen shows it. The person's name is looked up live; it is not stored."""
    return {
        "id": change.pk,
        "at": change.created_at.isoformat(),
        "scope": change.scope,
        "via": change.via,
        "via_title": change.get_via_display(),
        "who": {"ref": change.user_ref, "username": change.user.get_username() if change.user else None},
        "by": {
            "ref": change.actor_ref or None,
            "username": change.actor.get_username() if change.actor else None,
        },
        "project": (
            {"id": change.project_ref, "name": change.project.name if change.project else change.project_name,
             "deleted": change.project is None}
            if change.project_ref
            else None
        ),
        "before": change.before or None,
        "after": change.after or None,
        "note": change.note,
    }
