"""Roles per project, the administrator's access screen and API, and the record of role changes.

* a role given for one project replaces the console-wide role on that project only (up and down);
* only a superuser can change roles, and every change (screen, API, command, another tool) is recorded;
* records keep no names, cannot be edited or deleted, and outlive the project or person they are about.
"""

from __future__ import annotations

import io
import json
import re
import uuid

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from . import access_admin, permissions
from .models import ModelEvent, Project, ProjectRole, RoleChange
from .permissions import APPROVE, LABEL, TRAIN, VIEW
from .tests_roles import RoleBase, User, client_for, make_user


class ProjectRoleBase(RoleBase):
    def setUp(self) -> None:
        super().setUp()
        self.other = Project.objects.create(name="Spare line", modality="image", task="Detect", labels=["damaged"])
        self.admin = self.users["superuser"]
        RoleChange._base_manager.all().delete()  # the fixtures' own role grants are not what these tests are about

    def give(self, user, project, role, **kwargs):
        return access_admin.set_project_role(project, user, role, actor=self.admin, **kwargs)

    def status(self, who, method: str, url: str, payload=None) -> int:
        client = client_for(who) if not hasattr(who, "get") else who
        return self.send(client, method, url, payload).status_code

    def base(self, project) -> str:
        return f"/api/projects/{project.id}"


# ---------------------------------------------------------------------------------------
class ProjectScopedRoleTests(ProjectRoleBase):
    def test_a_project_role_replaces_the_console_role_on_that_project_only_when_raised(self) -> None:
        viewer = self.users["viewer"]
        mine, theirs = self.base(self.project), self.base(self.other)
        self.assertEqual(self.status(viewer, "post", f"{mine}/annotations/review", {}), 403)
        self.give(viewer, self.project, "labeler")
        self.assertNotIn(self.status(viewer, "post", f"{mine}/annotations/review", {}), (401, 403))
        self.assertEqual(self.status(viewer, "post", f"{theirs}/annotations/review", {}), 403)  # still a Viewer there
        self.assertEqual(self.status(viewer, "post", f"{mine}/training/runs", {}), 403)  # Labeler is not Trainer

    def test_a_project_role_can_also_lower_the_console_role_on_that_project(self) -> None:
        approver = self.users["approver"]
        mine, theirs = self.base(self.project), self.base(self.other)
        self.give(approver, self.project, "viewer")
        self.assertEqual(self.status(approver, "post", f"{mine}/annotations/review", {}), 403)
        self.assertEqual(self.status(approver, "delete", f"/api/projects/{self.project.id}"), 403)
        self.assertNotIn(self.status(approver, "post", f"{theirs}/annotations/review", {}), (401, 403))
        self.assertEqual(self.status(approver, "get", f"{mine}/models"), 200)  # looking is never taken away

    def test_each_project_role_allows_exactly_its_own_level(self) -> None:
        user = self.users["unassigned"]
        probes = {
            VIEW: ("get", f"{self.base(self.project)}/models", None),
            LABEL: ("post", f"{self.base(self.project)}/annotations/review", {}),
            TRAIN: ("post", f"{self.base(self.project)}/training/runs", {}),
            APPROVE: ("post", f"{self.base(self.project)}/models/{self.model.id}/approve", {}),
        }
        allowed = {"viewer": {VIEW}, "labeler": {VIEW, LABEL}, "trainer": {VIEW, LABEL, TRAIN}, "approver": set(probes)}
        for role, actions in allowed.items():
            self.give(user, self.project, role)
            for action, (method, url, payload) in probes.items():
                with self.subTest(role=role, action=action):
                    code = self.status(user, method, url, payload)
                    if action in actions:
                        self.assertNotIn(code, (401, 403))
                    else:
                        self.assertEqual(code, 403)
                        body = self.send(client_for(user), method, url, payload).json()
                        self.assertEqual(body["your_role"], role)

    def test_taking_the_project_role_away_returns_the_person_to_their_console_role(self) -> None:
        viewer = self.users["viewer"]
        url = f"{self.base(self.project)}/annotations/review"
        self.give(viewer, self.project, "trainer")
        self.assertNotIn(self.status(viewer, "post", url, {}), (401, 403))
        self.give(viewer, self.project, None)
        self.assertEqual(self.status(viewer, "post", url, {}), 403)
        self.assertFalse(ProjectRole.objects.exists())

    def test_the_unassigned_default_applies_on_projects_without_a_role(self) -> None:
        newcomer = self.users["unassigned"]
        self.give(newcomer, self.project, "viewer")
        self.assertEqual(permissions.role_of(newcomer, self.project), "viewer")
        self.assertEqual(permissions.role_of(newcomer, self.other), "labeler")
        self.assertEqual(permissions.role_of(newcomer), "labeler")

    def test_a_superuser_is_never_lowered(self) -> None:
        ProjectRole.objects.create(project=self.project, user=self.admin, role="viewer")  # even if one is written
        self.assertEqual(permissions.role_of(self.admin, self.project), "approver")
        self.assertTrue(permissions.can(self.admin, APPROVE, self.project))

    def test_one_person_has_at_most_one_role_per_project(self) -> None:
        user = self.users["viewer"]
        ProjectRole.objects.create(project=self.project, user=user, role="viewer")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectRole.objects.create(project=self.project, user=user, role="trainer")

    def test_approval_is_recorded_with_the_role_held_on_that_project(self) -> None:
        model = self.train_candidate_after_approved()
        self.assertEqual(self.status(self.users["trainer"], "post", f"{self.base(self.project)}/models/{model.id}/stage", {}), 200)
        labeler = self.users["labeler"]
        self.give(labeler, self.project, "approver")
        response = self.send(
            client_for(labeler), "post", f"{self.base(self.project)}/models/{model.id}/approve", {"note": "Checked with the line lead."}
        )
        self.assertEqual(response.status_code, 200, response.content)
        note = ModelEvent.objects.get(model_version=model, kind="approved").note
        self.assertIn(f"[Acted as Approver, user u{labeler.pk}.]", note)  # not Labeler, their console-wide role

    def test_a_viewer_on_a_project_cannot_approve_even_as_an_approver_elsewhere(self) -> None:
        approver = self.users["approver"]
        self.give(approver, self.project, "viewer")
        self.assertEqual(
            self.status(approver, "post", f"{self.base(self.project)}/models/{self.model.id}/approve", {"note": "x"}), 403
        )

    def test_moving_check_screens_with_an_approval_follows_the_role_on_that_project(self) -> None:
        from .models import InferenceApp

        model = self.train_candidate_after_approved()
        self.assertEqual(self.status(self.users["trainer"], "post", f"{self.base(self.project)}/models/{model.id}/stage", {}), 200)
        url = f"{self.base(self.project)}/models/{model.id}/approve"
        body = {"note": "Checked.", "repoint_apps": True}
        approver = self.users["approver"]
        self.give(approver, self.project, "viewer")  # an Approver elsewhere, a Viewer here
        self.assertEqual(self.status(approver, "post", url, body), 403)
        labeler = self.users["labeler"]
        self.give(labeler, self.other, "approver")  # an Approver on the OTHER project only
        self.assertEqual(self.status(labeler, "post", url, body), 403)
        self.assertEqual(InferenceApp.objects.get(pk=self.app["id"]).model_version_id, self.model.id)
        self.assertFalse(ModelEvent.objects.filter(model_version=model, kind__in=("approved", "apps_repointed")).exists())
        self.give(labeler, self.project, "approver")  # a Labeler console-wide, an Approver on this project
        response = self.send(client_for(labeler), "post", url, body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["repointed"][0]["app_id"], self.app["id"])
        self.assertEqual(InferenceApp.objects.get(pk=self.app["id"]).model_version_id, model.id)
        self.assertIn(f"[Acted as Approver, user u{labeler.pk}.]", ModelEvent.objects.get(kind="apps_repointed").note)

    def test_a_screen_of_another_project_cannot_be_moved_by_an_approval_here(self) -> None:
        from .models import InferenceApp

        foreign = InferenceApp.objects.create(
            project=self.other, name="Elsewhere", slug="elsewhere", model_version=self.model, config={}
        )
        model = self.train_candidate_after_approved()
        self.send(self.as_user("trainer"), "post", f"{self.base(self.project)}/models/{model.id}/stage", {})
        response = self.send(
            self.as_user("approver"), "post", f"{self.base(self.project)}/models/{model.id}/approve",
            {"note": "Checked.", "repoint_app_ids": [str(foreign.id)]},
        )
        self.assertEqual(response.status_code, 400, response.content)
        foreign.refresh_from_db()
        self.assertEqual(foreign.model_version_id, self.model.id)
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")

    def test_a_denial_names_the_role_on_the_project(self) -> None:
        user = self.users["approver"]
        self.give(user, self.project, "viewer")
        body = self.send(client_for(user), "post", f"{self.base(self.project)}/annotations/review", {}).json()
        self.assertEqual((body["your_role"], body["required_role"]), ("viewer", "labeler"))


# ---------------------------------------------------------------------------------------
class WhatThePageAndMeSayTests(ProjectRoleBase):
    def body_tag(self, user, url: str) -> str:
        html = client_for(user).get(url).content.decode()
        return re.search(r"<body[^>]*>", html).group(0)

    def test_me_lists_the_roles_given_for_single_projects(self) -> None:
        user = self.users["viewer"]
        self.give(user, self.project, "trainer")
        body = client_for(user).get("/api/me").json()
        self.assertEqual(body["project_roles"], {str(self.project.id): "trainer"})
        self.assertEqual(body["role"], "viewer")  # /api/me is the console-wide answer
        self.assertFalse(body["can_administer"])
        self.assertTrue(client_for(self.admin).get("/api/me").json()["can_administer"])
        self.assertNotIn("@example.com", json.dumps(body))

    def test_check_screen_pages_use_the_role_for_their_own_project(self) -> None:
        user = self.users["viewer"]
        self.give(user, self.project, "trainer")
        for url in (f"/projects/{self.project.id}/check-screen/setup", f"/check/{self.app['slug']}"):
            with self.subTest(url=url):
                tag = self.body_tag(user, url)
                self.assertIn('data-role="trainer"', tag)
                self.assertIn('data-can-train="1"', tag)

    def test_the_console_carries_the_per_project_roles_for_the_script(self) -> None:
        user = self.users["trainer"]
        self.give(user, self.project, "viewer")
        tag = self.body_tag(user, "/")
        self.assertIn('data-role="trainer"', tag)  # console-wide
        match = re.search(r'data-project-roles="([^"]*)"', tag)
        self.assertIsNotNone(match, tag)
        import html as _html

        roles = json.loads(_html.unescape(match.group(1)))
        self.assertEqual(roles[str(self.project.id)], {"role": "viewer", "title": "Viewer", "label": False, "train": False, "approve": False})

    def test_no_marker_when_there_are_no_project_roles(self) -> None:
        self.assertNotIn("data-project-roles", self.body_tag(self.users["trainer"], "/"))
        self.give(self.admin, self.project, "viewer")  # a superuser is never lowered, so nothing to tell the page
        self.assertNotIn("data-project-roles", self.body_tag(self.admin, "/"))


# ---------------------------------------------------------------------------------------
class AccessApiTests(ProjectRoleBase):
    def put(self, client, url, payload):
        return self.send(client, "put", url, payload)

    def console_url(self, user) -> str:
        return f"/api/access/people/{user.pk}/console"

    def project_url(self, user) -> str:
        return f"/api/access/people/{user.pk}/projects"

    def test_only_a_superuser_can_use_any_of_it(self) -> None:
        target = self.users["viewer"]
        calls = [
            ("get", "/access", None), ("get", "/api/access", None), ("get", "/api/access/changes", None),
            ("put", self.console_url(target), {"role": "trainer"}),
            ("put", self.project_url(target), {"project_id": str(self.project.id), "role": "trainer"}),
            ("delete", f"{self.project_url(target)}?project={self.project.id}", None),
        ]
        for who in ("viewer", "labeler", "trainer", "approver", "unassigned"):
            for method, url, payload in calls:
                with self.subTest(who=who, call=f"{method} {url}"):
                    response = self.send(self.as_user(who), method, url, payload)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()["required_role"], "administrator")
                    self.assertIn("Only an administrator", response.json()["error"])
        self.assertFalse(RoleChange.objects.exclude(via="other").exists())
        self.assertEqual(permissions.assigned_roles(target), ["viewer"])
        self.assertFalse(ProjectRole.objects.exists())

    def test_anonymous_callers_are_turned_away(self) -> None:
        from django.test import Client

        self.assertEqual(Client().get("/api/access").status_code, 401)
        self.assertEqual(Client().get("/access").status_code, 302)

    def test_overview_lists_people_roles_and_projects_without_emails(self) -> None:
        self.give(self.users["viewer"], self.project, "trainer")
        response = self.as_user("superuser").get("/api/access")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual([r["key"] for r in body["roles"]], list(permissions.ROLES))
        self.assertNotIn("@example.com", response.content.decode())
        people = {p["username"]: p for p in body["people"]}
        self.assertEqual(people["viewer"]["console_role"], "viewer")
        self.assertEqual(people["viewer"]["project_roles"], [{"project_id": str(self.project.id), "project_name": self.project.name, "role": "trainer"}])
        self.assertIsNone(people["newcomer"]["console_role"])
        self.assertEqual(people["newcomer"]["effective_console_role"], "labeler")
        self.assertTrue(people["root"]["superuser"])
        self.assertIn(str(self.other.id), [p["id"] for p in body["projects"]])

    def test_setting_a_console_role_changes_it_and_records_who_did_it(self) -> None:
        target = self.users["viewer"]
        response = self.put(self.as_user("superuser"), self.console_url(target), {"role": "trainer", "note": "Moved to the night shift"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["changed"])
        self.assertEqual(permissions.assigned_roles(target), ["trainer"])
        self.assertEqual(body["person"]["console_role"], "trainer")
        row = RoleChange.objects.get()
        self.assertEqual(
            (row.scope, row.via, row.before, row.after, row.note, row.user_ref, row.actor_ref, row.project_ref),
            ("console", "screen", "viewer", "trainer", "Moved to the night shift", f"u{target.pk}", f"u{self.admin.pk}", ""),
        )
        self.assertEqual((row.user_id, row.actor_id), (target.pk, self.admin.pk))
        self.assertEqual(body["change"]["who"]["ref"], f"u{target.pk}")

    def test_the_record_stores_no_names_or_emails(self) -> None:
        self.put(self.as_user("superuser"), self.console_url(self.users["labeler"]), {"role": "approver"})
        self.give(self.users["viewer"], self.project, "trainer")
        stored = " ".join(str(value) for row in RoleChange.objects.values() for value in row.values())
        self.assertNotIn("@example.com", stored)
        for username in ("root", "newcomer"):
            self.assertNotIn(username, stored)
        fields = {f.name for f in RoleChange._meta.get_fields()}
        self.assertFalse({"username", "email", "name"} & fields)

    def test_a_change_that_changes_nothing_writes_no_record(self) -> None:
        target = self.users["trainer"]
        response = self.put(self.as_user("superuser"), self.console_url(target), {"role": "trainer"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["changed"])
        self.assertIsNone(response.json()["change"])
        self.assertEqual(RoleChange.objects.exclude(via="other").count(), 0)

    def test_clearing_a_console_role_sends_the_person_back_to_the_default(self) -> None:
        target = self.users["approver"]
        response = self.put(self.as_user("superuser"), self.console_url(target), {"role": None})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(permissions.assigned_roles(target), [])
        row = RoleChange.objects.exclude(via="other").get()
        self.assertEqual((row.before, row.after), ("approver", ""))
        self.assertEqual(permissions.role_of(target), "labeler")

    def test_a_person_who_somehow_holds_two_groups_is_left_with_one(self) -> None:
        target = self.users["viewer"]
        grant_both = Group.objects.get(name=permissions.GROUP_NAMES["approver"])
        target.groups.add(grant_both)
        self.assertEqual(permissions.assigned_roles(target), ["viewer", "approver"])
        self.put(self.as_user("superuser"), self.console_url(target), {"role": "approver"})
        self.assertEqual(permissions.assigned_roles(target), ["approver"])

    def test_project_roles_can_be_given_changed_and_removed_with_a_record_each_time(self) -> None:
        target, admin = self.users["viewer"], self.as_user("superuser")
        payload = {"project_id": str(self.project.id), "role": "labeler"}
        first = self.put(admin, self.project_url(target), payload)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(permissions.role_of(target, self.project), "labeler")
        self.put(admin, self.project_url(target), {**payload, "role": "approver"})
        removed = self.send(admin, "delete", f"{self.project_url(target)}?project={self.project.id}")
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertEqual(permissions.role_of(target, self.project), "viewer")
        changes = list(RoleChange.objects.exclude(via="other").order_by("id"))
        self.assertEqual([(c.before, c.after) for c in changes], [("", "labeler"), ("labeler", "approver"), ("approver", "")])
        for change in changes:
            self.assertEqual((change.scope, change.project_ref, change.project_name), ("project", str(self.project.id), self.project.name))
        again = self.send(admin, "delete", f"{self.project_url(target)}?project={self.project.id}")
        self.assertFalse(again.json()["changed"])

    def test_mistakes_are_plain_errors_and_change_nothing(self) -> None:
        admin, target = self.as_user("superuser"), self.users["viewer"]
        cases = [
            (self.console_url(target), {"role": "boss"}, 400, "Unknown role"),
            (self.console_url(target), {}, 400, "Say which role"),
            (self.console_url(target), {"role": "viewer", "colour": "red"}, 400, "Unsupported field"),
            (self.console_url(target), {"role": 7}, 400, "Unknown role"),
            (self.console_url(target), {"role": "viewer", "note": "x" * 301}, 400, "too long"),
            (self.console_url(self.admin), {"role": "viewer"}, 409, "superuser"),
            ("/api/access/people/999999/console", {"role": "viewer"}, 404, None),
            (self.project_url(target), {"project_id": str(uuid.uuid4()), "role": "viewer"}, 404, None),
            (self.project_url(target), {"project_id": "nope", "role": "viewer"}, 400, "project"),
            (self.project_url(target), {"role": "viewer"}, 400, "project"),
            (self.project_url(target), {"project_id": str(self.project.id)}, 400, "Say which role"),
            (self.project_url(self.admin), {"project_id": str(self.project.id), "role": "viewer"}, 409, "superuser"),
        ]
        for url, payload, status, text in cases:
            with self.subTest(url=url, payload=payload):
                response = self.put(admin, url, payload)
                self.assertEqual(response.status_code, status, response.content)
                if text:
                    self.assertIn(text, response.json()["error"])
        self.assertEqual(permissions.assigned_roles(target), ["viewer"])
        self.assertFalse(ProjectRole.objects.exists())
        self.assertFalse(RoleChange.objects.exclude(via="other").exists())

    def test_an_inactive_account_cannot_be_given_a_role(self) -> None:
        gone = make_user("leaver", is_active=False)
        self.assertEqual(self.put(self.as_user("superuser"), self.console_url(gone), {"role": "viewer"}).status_code, 404)
        self.assertNotIn("leaver", [p["username"] for p in self.as_user("superuser").get("/api/access").json()["people"]])

    def test_wrong_methods_are_405_not_a_silent_success(self) -> None:
        admin, target = self.as_user("superuser"), self.users["viewer"]
        self.assertEqual(admin.post(self.console_url(target)).status_code, 405)
        self.assertEqual(admin.delete(self.console_url(target)).status_code, 405)
        self.assertEqual(admin.post("/api/access").status_code, 405)
        self.assertEqual(admin.post(self.project_url(target)).status_code, 405)

    def test_role_changes_take_effect_on_the_persons_next_request(self) -> None:
        target = self.users["viewer"]
        client = self.as_user("viewer")
        url = f"{self.base(self.project)}/annotations/review"
        self.assertEqual(self.send(client, "post", url, {}).status_code, 403)
        self.put(self.as_user("superuser"), self.console_url(target), {"role": "labeler"})
        self.assertNotEqual(self.send(client, "post", url, {}).status_code, 403)

    def test_the_changes_list_is_newest_first_filterable_and_paged(self) -> None:
        admin = self.as_user("superuser")
        a, b = self.users["viewer"], self.users["labeler"]
        self.put(admin, self.console_url(a), {"role": "trainer"})
        self.put(admin, self.project_url(a), {"project_id": str(self.project.id), "role": "approver"})
        self.put(admin, self.project_url(b), {"project_id": str(self.other.id), "role": "viewer"})
        body = admin.get("/api/access/changes").json()
        self.assertEqual(body["total"], 3)
        self.assertEqual([c["who"]["username"] for c in body["changes"]], ["labeler", "viewer", "viewer"])
        self.assertEqual(body["changes"][0]["project"], {"id": str(self.other.id), "name": self.other.name, "deleted": False})
        self.assertEqual(body["changes"][0]["by"]["ref"], f"u{self.admin.pk}")
        by_project = admin.get(f"/api/access/changes?project={self.project.id}").json()
        self.assertEqual(by_project["total"], 1)
        by_person = admin.get(f"/api/access/changes?person=u{a.pk}").json()
        self.assertEqual(by_person["total"], 2)
        page = admin.get("/api/access/changes?limit=1&offset=1").json()
        self.assertEqual((page["total"], len(page["changes"])), (3, 1))
        self.assertEqual(admin.get("/api/access/changes?limit=abc").status_code, 400)

    def test_the_record_outlives_the_project_and_the_person_and_keeps_their_ids(self) -> None:
        admin, target = self.as_user("superuser"), self.users["viewer"]
        self.put(admin, self.project_url(target), {"project_id": str(self.other.id), "role": "trainer"})
        ref, other_id, name = f"u{target.pk}", str(self.other.id), self.other.name
        self.other.delete()
        target.delete()
        row = RoleChange.objects.exclude(via="other").get()
        self.assertEqual((row.user_id, row.project_id), (None, None))
        self.assertEqual((row.user_ref, row.project_ref, row.project_name), (ref, other_id, name))
        shown = admin.get("/api/access/changes").json()["changes"][0]
        self.assertEqual(shown["who"], {"ref": ref, "username": None})
        self.assertEqual(shown["project"], {"id": other_id, "name": name, "deleted": True})
        self.assertFalse(ProjectRole.objects.exists())  # the assignment itself went with the project


# ---------------------------------------------------------------------------------------
class AccessScreenTests(ProjectRoleBase):
    def test_the_screen_renders_for_an_administrator_with_its_landmarks(self) -> None:
        response = self.as_user("superuser").get("/access")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        html = response.content.decode()
        for needle in (
            "<h1>Who can do what</h1>", 'id="people"', 'id="record-rows"', 'id="people-search"',
            'aria-current="page">Access</a>', "projects/access.js", "projects/access.css", 'name="csrf-token"',
        ):
            self.assertIn(needle, html)
        self.assertNotIn("<script>", html)  # the page's CSP allows scripts from the console only

    def test_other_roles_are_refused_the_screen(self) -> None:
        for who in ("viewer", "labeler", "trainer", "approver"):
            self.assertEqual(self.as_user(who).get("/access").status_code, 403, who)

    def test_a_person_who_types_the_address_gets_a_page_and_a_script_gets_json(self) -> None:
        response = self.as_user("trainer").get("/access", HTTP_ACCEPT="text/html,application/xhtml+xml")
        self.assertEqual(response.status_code, 403)
        self.assertIn("text/html", response["Content-Type"])
        self.assertIn("Only an administrator can change who holds which role.", response.content.decode())
        self.assertIn('href="/"', response.content.decode())
        self.assertEqual(response["Cache-Control"], "no-store")
        api = self.as_user("trainer").get("/api/access", HTTP_ACCEPT="text/html")
        self.assertEqual(api["Content-Type"], "application/json")  # the API never answers with a page

    def test_the_console_links_to_the_screen_for_administrators_only(self) -> None:
        link = 'href="/access"'
        self.assertIn(link, self.as_user("superuser").get("/").content.decode())
        for who in ("approver", "trainer", "viewer"):
            self.assertNotIn(link, self.as_user(who).get("/").content.decode(), who)

    def test_the_scripts_are_served_and_hold_no_secrets(self) -> None:
        from pathlib import Path

        static = Path(__file__).resolve().parent / "static" / "projects"
        script = (static / "access.js").read_text()
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "innerHTML", "eval(", "location."):
            self.assertNotIn(forbidden, script, f"access.js must not use {forbidden}")
        self.assertIn("/api/access", script)
        common = (static / "ui_common.js").read_text()
        self.assertIn("useProject", common)
        self.assertIn('data-needs-scope', (static / ".." / ".." / "templates" / "projects" / "console.html").resolve().read_text())
        self.assertIn("Athena.useProject(", (static / "console.js").read_text())

    def test_creating_a_project_stays_a_console_wide_action(self) -> None:
        html = self.as_user("viewer").get("/").content.decode()
        for identifier in ("open-project-dialog", "empty-create"):
            self.assertRegex(html, rf'id="{identifier}"[^>]*data-needs-scope="console"')


# ---------------------------------------------------------------------------------------
class RoleChangeRecordTests(TestCase):
    def test_a_record_cannot_be_edited_or_deleted(self) -> None:
        user = make_user("pat")
        change = access_admin.set_console_role(user, "viewer", actor=None, via="command")
        change.after = "approver"
        with self.assertRaises(ValueError):
            change.save()
        with self.assertRaises(ValueError):
            change.delete()
        with self.assertRaises(ValueError):
            RoleChange.objects.all().delete()
        with self.assertRaises(ValueError):
            RoleChange.objects.all().update(after="approver")

    def test_changes_made_by_another_tool_are_recorded_once_each(self) -> None:
        user = make_user("quinn")
        group, _ = Group.objects.get_or_create(name=permissions.GROUP_NAMES["trainer"])
        user.groups.add(group)
        user.groups.add(group)  # already a member: nothing changes
        rows = list(RoleChange.objects.filter(user_ref=f"u{user.pk}"))
        self.assertEqual([(r.via, r.before, r.after, r.actor_ref) for r in rows], [("other", "", "trainer", "")])
        user.groups.remove(group)
        group.user_set.add(user)  # the other side of the relation
        group.user_set.remove(user)
        user.groups.add(group)
        user.groups.clear()
        changes = [(r.before, r.after) for r in RoleChange.objects.filter(user_ref=f"u{user.pk}").order_by("id")]
        self.assertEqual(
            changes,
            [("", "trainer"), ("trainer", ""), ("", "trainer"), ("trainer", ""), ("", "trainer"), ("trainer", "")],
        )

    def test_unrelated_groups_are_not_recorded(self) -> None:
        user = make_user("robin")
        user.groups.add(Group.objects.create(name="Canteen"))
        self.assertFalse(RoleChange.objects.exists())

    def test_the_service_records_once_not_twice(self) -> None:
        user = make_user("sam")
        access_admin.set_console_role(user, "labeler", via="command")
        access_admin.set_console_role(user, "approver", via="command")
        self.assertEqual(
            [(r.via, r.before, r.after) for r in RoleChange.objects.order_by("id")],
            [("command", "", "labeler"), ("command", "labeler", "approver")],
        )

    def test_a_failed_change_leaves_no_record(self) -> None:
        user = make_user("tess")
        with self.assertRaises(access_admin.AccessError):
            access_admin.set_console_role(user, "boss")
        self.assertFalse(RoleChange.objects.exists())
        self.assertEqual(permissions.assigned_roles(user), [])


# ---------------------------------------------------------------------------------------
class RolesCommandProjectTests(TestCase):
    def run_command(self, *args: str) -> str:
        out = io.StringIO()
        call_command("athena_roles", *args, stdout=out)
        return out.getvalue()

    def setUp(self) -> None:
        self.project = Project.objects.create(name="Pallet check", modality="image", task="Detect", labels=["a"])
        self.user = make_user("uma", "labeler")

    def test_assign_and_revoke_on_a_project_by_name_or_id(self) -> None:
        self.assertIn("is now a Viewer on “Pallet check”", self.run_command("--project", "Pallet check", "--assign", "uma", "viewer"))
        self.assertEqual(permissions.role_of(self.user, self.project), "viewer")
        self.assertEqual(permissions.role_of(self.user), "labeler")
        self.assertIn("Pallet check: uma is Viewer", self.run_command())
        self.run_command("--project", str(self.project.id), "--assign", "uma", "trainer")
        self.assertEqual(permissions.role_of(self.user, self.project), "trainer")
        self.assertIn("no role of their own", self.run_command("--project", "Pallet check", "--revoke", "uma"))
        self.assertEqual(permissions.role_of(self.user, self.project), "labeler")

    def test_the_command_records_what_it_changed(self) -> None:
        self.run_command("--project", "Pallet check", "--assign", "uma", "viewer")
        self.run_command("--assign", "uma", "approver")
        self.run_command("--revoke", "uma")
        rows = [(r.scope, r.via, r.before, r.after) for r in RoleChange.objects.exclude(via="other").order_by("id")]
        self.assertEqual(
            rows,
            [("project", "command", "", "viewer"), ("console", "command", "labeler", "approver"), ("console", "command", "approver", "")],
        )
        self.assertTrue(all(r.actor_ref == "" for r in RoleChange.objects.all()))

    def test_plain_errors(self) -> None:
        with self.assertRaisesMessage(CommandError, "There is no project named"):
            self.run_command("--project", "Nowhere", "--assign", "uma", "viewer")
        with self.assertRaisesMessage(CommandError, "needs --assign or --revoke"):
            self.run_command("--project", "Pallet check")
        with self.assertRaisesMessage(CommandError, "has no role of their own"):
            self.run_command("--project", "Pallet check", "--revoke", "uma")
        with self.assertRaisesMessage(CommandError, "takes only a username"):
            self.run_command("--project", "Pallet check", "--revoke", "uma", "viewer")
        Project.objects.create(name="Pallet check", modality="image", task="Detect", labels=["a"])
        with self.assertRaisesMessage(CommandError, "More than one project"):
            self.run_command("--project", "Pallet check", "--assign", "uma", "viewer")
