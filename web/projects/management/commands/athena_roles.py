"""Manage who may do what in the console: create the four role groups, list members, assign or revoke.

    manage.py athena_roles                       create the groups (safe to repeat) and list members
    manage.py athena_roles --assign alice trainer
    manage.py athena_roles --revoke alice trainer   (or: --revoke alice  to remove every Athena role)
    manage.py athena_roles --project "Pallet check" --assign alice viewer     (a role on one project only)
    manage.py athena_roles --project "Pallet check" --revoke alice

Roles: viewer, labeler, trainer, approver. A user holds one role at a time; assigning a new one replaces the
old, so the command never leaves someone with two. A role given with --project applies to that project only
and replaces the person's console-wide role there. Superusers can always do everything and are listed as such.
Every change is written to the record of role changes (shown on the console's access screen).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from projects import access_admin, permissions
from projects.models import Project, ProjectRole, RoleChange


class Command(BaseCommand):
    help = "Create the Athena role groups, list their members, and assign or revoke a role."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--assign", nargs=2, metavar=("USERNAME", "ROLE"), help="Give USERNAME exactly one role.")
        parser.add_argument("--project", metavar="NAME_OR_ID", help="Apply --assign/--revoke to this project only.")
        parser.add_argument(
            "--revoke",
            nargs="+",
            metavar=("USERNAME", "ROLE"),
            help="Remove ROLE from USERNAME, or every Athena role when ROLE is left out.",
        )

    def handle(self, *args, **options) -> None:
        if options["assign"] and options["revoke"]:
            raise CommandError("Use either --assign or --revoke, not both.")
        groups = self.ensure_groups()
        User = get_user_model()
        if options["project"] and not (options["assign"] or options["revoke"]):
            raise CommandError("--project needs --assign or --revoke.")
        if options["project"]:
            self.project_change(User, options)
            self.report(User, groups)
            return
        if options["assign"]:
            username, role = options["assign"]
            self.assign(User, groups, username, self.role_name(role))
        elif options["revoke"]:
            if len(options["revoke"]) > 2:
                raise CommandError("--revoke takes a username and, optionally, one role.")
            username = options["revoke"][0]
            role = self.role_name(options["revoke"][1]) if len(options["revoke"]) == 2 else None
            self.revoke(User, groups, username, role)
        self.report(User, groups)

    # -- helpers ------------------------------------------------------------------
    def project_change(self, User, options) -> None:
        project = self.project(options["project"])
        if options["assign"]:
            username, role = options["assign"]
            role = self.role_name(role)
        else:
            if len(options["revoke"]) > 1:
                raise CommandError("With --project, --revoke takes only a username: it removes that person's role on the project.")
            username, role = options["revoke"][0], None
        user = self.user(User, username)
        change = access_admin.set_project_role(project, user, role, via=RoleChange.VIA_COMMAND)
        if role is None and change is None:
            raise CommandError(f"“{username}” has no role of their own on “{project.name}”.")
        if role is None:
            self.stdout.write(self.style.SUCCESS(f"“{username}” has no role of their own on “{project.name}” any more; their console-wide role applies there."))
        elif change is None:
            self.stdout.write(f"“{username}” was already a {permissions.ROLE_TITLES[role]} on “{project.name}”; nothing changed.")
        else:
            self.stdout.write(self.style.SUCCESS(f"“{username}” is now a {permissions.ROLE_TITLES[role]} on “{project.name}”."))

    @staticmethod
    def project(value: str) -> Project:
        import uuid

        try:
            return Project.objects.get(pk=uuid.UUID(value))
        except (ValueError, Project.DoesNotExist):
            pass
        matches = list(Project.objects.filter(name=value)[:2])
        if not matches:
            raise CommandError(f"There is no project named “{value}”. Use its exact name or its id.")
        if len(matches) > 1:
            raise CommandError(f"More than one project is named “{value}”. Use its id instead.")
        return matches[0]

    @staticmethod
    def role_name(value: str) -> str:
        role = value.strip().lower()
        if role not in permissions.ROLES:
            raise CommandError(f"Unknown role “{value}”. Use one of: {', '.join(permissions.ROLES)}.")
        return role

    def ensure_groups(self) -> dict[str, Group]:
        groups = {}
        for role in permissions.ROLES:
            group, created = Group.objects.get_or_create(name=permissions.GROUP_NAMES[role])
            groups[role] = group
            if created:
                self.stdout.write(f"Created group “{group.name}”.")
        return groups

    @staticmethod
    def user(User, username: str):
        try:
            return User.objects.get(**{User.USERNAME_FIELD: username})
        except User.DoesNotExist:
            raise CommandError(f"There is no user named “{username}”.") from None

    def assign(self, User, groups: dict[str, Group], username: str, role: str) -> None:
        user = self.user(User, username)
        access_admin.set_console_role(user, role, via=RoleChange.VIA_COMMAND)
        self.stdout.write(self.style.SUCCESS(f"“{username}” is now a {permissions.ROLE_TITLES[role]}."))

    def revoke(self, User, groups: dict[str, Group], username: str, role: str | None) -> None:
        user = self.user(User, username)
        if role is None:
            access_admin.set_console_role(user, None, via=RoleChange.VIA_COMMAND)
            fallback = permissions.ROLE_TITLES[permissions.unassigned_role()]
            self.stdout.write(
                self.style.SUCCESS(
                    f"Removed every Athena role from “{username}”. They now count as unassigned ({fallback} on this console)."
                )
            )
            return
        if not user.groups.filter(pk=groups[role].pk).exists():
            raise CommandError(f"“{username}” does not hold the {permissions.ROLE_TITLES[role]} role.")
        remaining = [r for r in permissions.assigned_roles(user) if r != role]
        access_admin.set_console_role(user, remaining[-1] if remaining else None, via=RoleChange.VIA_COMMAND)
        self.stdout.write(self.style.SUCCESS(f"“{username}” no longer holds the {permissions.ROLE_TITLES[role]} role."))

    def report(self, User, groups: dict[str, Group]) -> None:
        self.stdout.write("")
        unassigned = permissions.ROLE_TITLES[permissions.unassigned_role()]
        self.stdout.write(f"Users in no Athena group count as: {unassigned} (ATHENA_UNASSIGNED_ROLE); they can never approve.")
        second = "on" if permissions.requires_second_person() else "off"
        self.stdout.write(f"Approval needs a second person (ATHENA_APPROVAL_REQUIRES_SECOND_PERSON): {second}.")
        self.stdout.write("")
        held: set[int] = set()
        for role in reversed(permissions.ROLES):
            names = sorted(groups[role].user_set.values_list(User.USERNAME_FIELD, flat=True))
            held.update(groups[role].user_set.values_list("pk", flat=True))
            self.stdout.write(f"{permissions.ROLE_TITLES[role]} ({len(names)}): {', '.join(names) or '-'}")
        supers = sorted(User.objects.filter(is_superuser=True, is_active=True).values_list(User.USERNAME_FIELD, flat=True))
        self.stdout.write(f"Superusers, who can do everything ({len(supers)}): {', '.join(supers) or '-'}")
        others = sorted(
            User.objects.filter(is_active=True, is_superuser=False)
            .exclude(pk__in=held)
            .values_list(User.USERNAME_FIELD, flat=True)
        )
        self.stdout.write(f"No Athena role, so {unassigned} ({len(others)}): {', '.join(others) or '-'}")
        self.stdout.write("")
        rows = ProjectRole.objects.select_related("project", "user").order_by("project__name", "user__" + User.USERNAME_FIELD)
        self.stdout.write(f"Roles on single projects ({len(rows)}):")
        for row in rows:
            self.stdout.write(f"  {row.project.name}: {row.user.get_username()} is {permissions.ROLE_TITLES[row.role]}")
        if not rows:
            self.stdout.write("  -")
