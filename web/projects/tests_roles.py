"""Access roles: Viewer / Labeler / Trainer / Approver, enforced on the server for every write.

* a table of representative endpoints is run as every role (allowed and denied);
* the URL configuration is walked, so a new write route with no declared role fails here by name;
* the unassigned default, the second-person rule, superusers, anonymous callers, the ``athena_roles`` command,
  and the markers the console reads to switch controls off (taken from the server, never from the browser).
"""

from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import JsonResponse
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import path

from . import permissions
from .models import AutoLabelJob, DatasetVersion, ModelEvent, ModelVersion, Project
from .permissions import ADMIN, APPROVE, LABEL, TRAIN, VIEW
from .tests import grant
from .tests_hardening import METHODS, SAMPLE, render, walk
from .tests_inference import InferenceBase, image_upload

User = get_user_model()
STATIC = Path(__file__).resolve().parent / "static" / "projects"
ROLE_CASES = ["viewer", "labeler", "trainer", "approver"]
VIEWER_MESSAGE = "Your account can look but not change things. Ask an administrator for the Labeler role."


def client_for(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def make_user(name: str, role: str | None = None, **extra):
    user = User.objects.create_user(name, email=f"{name}@example.com", **extra)
    if role:
        grant(user, role)
    return user


class RoleBase(InferenceBase):
    """Approved model, a check screen, one picture with a box, and one user per role."""

    def setUp(self) -> None:
        super().setUp()
        self.app = self.make_app()
        self.asset = self.project.assets.first()
        self.box = self.asset.annotations.first()
        self.dataset = DatasetVersion.objects.filter(project=self.project).first()
        self.users = {role: make_user(role, role) for role in ROLE_CASES}
        self.users["superuser"] = make_user("root", is_superuser=True, is_staff=True)
        self.users["unassigned"] = make_user("newcomer")

    def as_user(self, name: str) -> Client:
        return client_for(self.users[name])

    def send(self, client: Client, method: str, url: str, payload=None, **extra):
        if payload is None:
            return getattr(client, method)(url, **extra)
        return getattr(client, method)(url, data=json.dumps(payload), content_type="application/json", **extra)


# ---------------------------------------------------------------------------------------
class RoleMatrixTests(RoleBase):
    """Every role against one representative route per kind of change."""

    def endpoints(self, other: Project):
        """(label, method, url, payload, needed action). Payloads are deliberately junk or empty: a
        call that passes the role check may answer 400/404/409, but never 403."""
        pid, aid = self.project.id, self.asset.id
        api = f"/api/projects/{pid}"
        model_id, run_id = self.model.id, self.model.training_run_id
        observation = f"{api}/inference/apps/{self.app['id']}/observations/{uuid.uuid4()}"
        return [
            ("read dashboard", "get", "/", None, VIEW),
            ("read projects", "get", "/api/projects", None, VIEW),
            ("read models", "get", f"{api}/models", None, VIEW),
            ("read run", "get", f"{api}/training/runs/{run_id}", None, VIEW),
            ("read audit log", "get", f"{api}/datasets/{self.dataset.id}/exports", None, VIEW),
            ("read feedback export", "get", f"{api}/inference/apps/{self.app['id']}/feedback/export", None, VIEW),
            ("create project", "post", "/api/projects", {}, LABEL),
            ("edit labels", "patch", f"/api/projects/{other.id}", {}, LABEL),
            ("add pictures", "post", f"{api}/assets", None, LABEL),
            ("draw a box", "post", f"{api}/assets/{aid}/annotations", {}, LABEL),
            ("edit a box", "patch", f"{api}/assets/{aid}/annotations/{uuid.uuid4()}", {}, LABEL),
            ("delete a box", "delete", f"{api}/assets/{aid}/annotations/{uuid.uuid4()}", None, LABEL),
            ("delete a picture", "delete", f"{api}/assets/{uuid.uuid4()}", None, LABEL),
            ("suggest boxes", "post", f"{api}/autolabel", {}, LABEL),
            ("stop suggesting", "post", f"{api}/autolabel/{uuid.uuid4()}/cancel", None, LABEL),
            ("review suggestions", "post", f"{api}/annotations/review", {}, LABEL),
            ("save a snapshot", "post", f"{api}/datasets", {}, LABEL),
            ("export a snapshot", "get", f"{api}/datasets/{self.dataset.id}/export?format=coco", None, LABEL),
            ("run a check screen", "post", f"{api}/inference/apps/{self.app['id']}/run", None, LABEL),
            ("record feedback", "post", f"{observation}/feedback", {}, LABEL),
            ("record a decision", "post", f"{observation}/decision", {}, LABEL),
            ("start teaching", "post", f"{api}/training/runs", {}, TRAIN),
            ("stop teaching", "post", f"{api}/training/runs/{run_id}/cancel", None, TRAIN),
            ("move to trial", "post", f"{api}/models/{model_id}/stage", {}, TRAIN),
            ("create check screen", "post", f"{api}/inference/apps", {}, TRAIN),
            ("edit check screen", "patch", f"{api}/inference/apps/{self.app['id']}", {}, TRAIN),
            ("delete check screen", "delete", f"{api}/inference/apps/{uuid.uuid4()}", None, TRAIN),
            ("delete project", "delete", f"/api/projects/{other.id}", None, TRAIN),
            ("approve", "post", f"{api}/models/{model_id}/approve", {}, APPROVE),
            ("roll back", "post", f"{api}/models/{model_id}/rollback", {}, APPROVE),
        ]

    ALLOWED = {
        "viewer": {VIEW},
        "labeler": {VIEW, LABEL},
        "trainer": {VIEW, LABEL, TRAIN},
        "approver": {VIEW, LABEL, TRAIN, APPROVE},
        "superuser": {VIEW, LABEL, TRAIN, APPROVE},
        "unassigned": {VIEW, LABEL},  # ATHENA_UNASSIGNED_ROLE defaults to labeler
    }

    def run_matrix(self, who: str, allowed: set[str]) -> int:
        other = Project.objects.create(name="Spare", modality="image", task="Detect", labels=["damaged"])
        client = self.as_user(who)
        checked = 0
        for label, method, url, payload, action in self.endpoints(other):
            with self.subTest(role=who, step=label):
                response = self.send(client, method, url, payload)
                if action in allowed:
                    self.assertNotIn(response.status_code, (401, 403), f"{who} should be allowed to {label}")
                else:
                    self.assertEqual(response.status_code, 403, f"{who} should be refused: {label}")
                    body = response.json()
                    self.assertEqual(body["required_role"], permissions.ACTION_ROLE[action])
                    self.assertEqual(body["your_role"], permissions.role_of(self.users[who]))
                    self.assertEqual(response["Cache-Control"], "no-store")
                checked += 1
        return checked

    def test_each_role_can_do_exactly_its_own_things(self) -> None:
        for who, allowed in self.ALLOWED.items():
            self.assertGreater(self.run_matrix(who, allowed), 25)

    def test_the_viewer_message_is_plain_and_names_the_role_to_ask_for(self) -> None:
        response = self.send(self.as_user("viewer"), "post", "/api/projects", {})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], VIEWER_MESSAGE)
        for action, role in ((TRAIN, "Trainer"), (APPROVE, "Approver")):
            self.assertIn(f"Ask an administrator for the {role} role.", permissions.denial_message("viewer", action))
        self.assertIn("Trainer role", permissions.denial_message("labeler", TRAIN))
        self.assertIn("Approver role", permissions.denial_message("trainer", APPROVE))

    def test_a_refused_write_changes_nothing(self) -> None:
        before = (Project.objects.count(), ModelEvent.objects.count(), self.project.assets.count())
        viewer = self.as_user("viewer")
        for method, url, payload in (
            ("post", "/api/projects", {"name": "x", "modality": "image", "task": "y"}),
            ("delete", f"/api/projects/{self.project.id}/assets/{self.asset.id}", None),
            ("delete", f"/api/projects/{self.project.id}/assets/{self.asset.id}/annotations/{self.box.id}", None),
            ("post", f"/api/projects/{self.project.id}/models/{self.model.id}/rollback", {"note": "n"}),
        ):
            self.assertEqual(self.send(viewer, method, url, payload).status_code, 403)
        self.assertEqual(before, (Project.objects.count(), ModelEvent.objects.count(), self.project.assets.count()))
        self.assertTrue(self.asset.annotations.filter(id=self.box.id).exists())

    def test_a_viewer_can_read_everything_that_a_labeler_can_read(self) -> None:
        viewer, labeler = self.as_user("viewer"), self.as_user("labeler")
        pid = self.project.id
        for url in (
            "/", "/api/projects", f"/api/projects/{pid}/assets", f"/api/projects/{pid}/assets/{self.asset.id}/annotations",
            f"/api/projects/{pid}/assets/{self.asset.id}/content", f"/api/projects/{pid}/datasets",
            f"/api/projects/{pid}/datasets/readiness", f"/api/projects/{pid}/training/runs", f"/api/projects/{pid}/models",
            f"/api/projects/{pid}/inference/apps", f"/projects/{pid}/check-screen/setup", f"/check/{self.app['slug']}",
            "/api/autolabel", f"/api/projects/{pid}/autolabel", "/api/me",
        ):
            with self.subTest(url=url):
                self.assertEqual(viewer.get(url).status_code, labeler.get(url).status_code)
                self.assertEqual(viewer.get(url).status_code, 200)


# ---------------------------------------------------------------------------------------
class UnassignedUserTests(RoleBase):
    def test_default_is_labeler_level_but_never_approves(self) -> None:
        user = self.as_user("unassigned")
        pid = self.project.id
        self.assertEqual(permissions.role_of(self.users["unassigned"]), "labeler")
        self.assertNotEqual(self.send(user, "post", f"/api/projects/{pid}/assets/{self.asset.id}/annotations", {}).status_code, 403)
        approve = self.send(user, "post", f"/api/projects/{pid}/models/{self.model.id}/approve", {"note": "ok"})
        rollback = self.send(user, "post", f"/api/projects/{pid}/models/{self.model.id}/rollback", {"note": "ok"})
        self.assertEqual((approve.status_code, rollback.status_code), (403, 403))
        self.assertIn("Approver role", approve.json()["error"])

    @override_settings(ATHENA_UNASSIGNED_ROLE="viewer")
    def test_viewer_mode_makes_unassigned_users_read_only(self) -> None:
        user = self.as_user("unassigned")
        response = self.send(user, "post", "/api/projects", {})
        self.assertEqual((response.status_code, response.json()["error"]), (403, VIEWER_MESSAGE))
        self.assertEqual(user.get("/api/projects").status_code, 200)
        self.assertEqual(user.get("/api/me").json()["role"], "viewer")

    @override_settings(ATHENA_UNASSIGNED_ROLE="admin")
    def test_an_unreadable_setting_fails_closed(self) -> None:
        self.assertEqual(permissions.unassigned_role(), "viewer")

    def test_an_assigned_role_replaces_the_default_in_both_directions(self) -> None:
        with override_settings(ATHENA_UNASSIGNED_ROLE="labeler"):
            grant(self.users["unassigned"], "viewer")  # an explicit Viewer is not widened by the default
            self.assertEqual(permissions.role_of(self.users["unassigned"]), "viewer")
        grant(self.users["unassigned"], "trainer")
        self.assertEqual(permissions.role_of(self.users["unassigned"]), "trainer")

    def test_the_highest_group_wins_when_someone_is_in_several(self) -> None:
        user = self.users["unassigned"]
        for role in ("viewer", "approver", "labeler"):
            user.groups.add(Group.objects.get_or_create(name=permissions.GROUP_NAMES[role])[0])
        self.assertEqual(permissions.role_of(user), "approver")
        self.assertEqual(permissions.assigned_roles(user), ["viewer", "labeler", "approver"])

    def test_unrelated_groups_grant_nothing(self) -> None:
        user = self.users["unassigned"]
        user.groups.add(Group.objects.create(name="Visitors"))
        self.assertFalse(permissions.assigned_roles(user))
        self.assertEqual(permissions.role_of(user), "labeler")


class SuperuserAndAnonymousTests(RoleBase):
    def test_a_superuser_with_no_group_can_approve(self) -> None:
        self.assertEqual(permissions.role_of(self.users["superuser"]), "approver")
        candidate = self.train_candidate_after_approved()
        client = self.as_user("superuser")
        self.assertEqual(self.send(client, "post", f"/api/projects/{self.project.id}/models/{candidate.id}/stage", {}).status_code, 200)
        response = self.send(client, "post", f"/api/projects/{self.project.id}/models/{candidate.id}/approve", {"note": "Checked by admin."})
        self.assertEqual(response.status_code, 200, response.content)

    def test_anonymous_callers_still_get_401_or_a_login_redirect(self) -> None:
        anonymous = Client()
        pid = self.project.id
        for method, url in (
            ("get", "/api/me"), ("post", f"/api/projects/{pid}/models/{self.model.id}/approve"),
            ("post", "/api/projects"), ("get", f"/api/projects/{pid}/models"),
        ):
            self.assertEqual(getattr(anonymous, method)(url).status_code, 401, url)
        self.assertEqual(anonymous.get("/").status_code, 302)


# ---------------------------------------------------------------------------------------
class ApprovalTests(RoleBase):
    def staged(self) -> ModelVersion:
        candidate = self.train_candidate_after_approved()
        self.assertEqual(self.send(self.as_user("trainer"), "post", f"/api/projects/{self.project.id}/models/{candidate.id}/stage", {}).status_code, 200)
        return candidate

    def approve(self, who: str, model, note="Reviewed the results with the team."):
        return self.send(self.as_user(who), "post", f"/api/projects/{self.project.id}/models/{model.id}/approve", {"note": note})

    def test_only_an_approver_approves_and_the_model_is_untouched_when_refused(self) -> None:
        model = self.staged()
        for who in ("viewer", "labeler", "trainer", "unassigned"):
            self.assertEqual(self.approve(who, model).status_code, 403, who)
        model.refresh_from_db()
        self.assertEqual((model.stage, model.approved_by_id), ("staged", None))
        self.assertFalse(ModelEvent.objects.filter(model_version=model, kind="approved").exists())
        response = self.approve("approver", model)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["model"]["approved_by"], f"u{self.users['approver'].pk}")

    def test_the_audit_trail_records_role_and_opaque_user_id_but_no_names(self) -> None:
        model = self.staged()
        self.assertEqual(self.approve("approver", model).status_code, 200)
        event = ModelEvent.objects.get(model_version=model, kind="approved")
        self.assertIn(f"[Acted as Approver, user u{self.users['approver'].pk}.]", event.note)
        self.assertIn("Reviewed the results", event.note)
        self.assertEqual(event.actor_id_snapshot, self.users["approver"].pk)
        # roll back: both events carry the tag too
        rollback = self.send(
            self.as_user("superuser"), "post",
            f"/api/projects/{self.project.id}/models/{model.id}/rollback", {"note": "Went back."},
        )
        self.assertEqual(rollback.status_code, 200, rollback.content)
        for kind in ("rollback_retired", "rollback_restored"):
            note = ModelEvent.objects.filter(kind=kind).latest("id").note
            self.assertIn(f"[Acted as Approver, user u{self.users['superuser'].pk}.]", note)
        every_note = " ".join(ModelEvent.objects.values_list("note", flat=True))
        for name in ("approver", "root", "@example.com"):
            self.assertNotIn(name, every_note.replace("Approver", ""))
        detail = self.as_user("viewer").get(f"/api/projects/{self.project.id}/models/{model.id}").content.decode()
        self.assertNotIn("@example.com", detail)

    def test_the_weak_results_acknowledgement_still_keeps_its_audit_line(self) -> None:
        from .tests_training import FakeBackend

        FakeBackend.boxes = []
        model = self.staged()
        response = self.send(
            self.as_user("approver"), "post", f"/api/projects/{self.project.id}/models/{model.id}/approve",
            {"note": "Pilot only.", "acknowledge_weak_results": True},
        )
        self.assertEqual(response.status_code, 200, response.content)
        note = ModelEvent.objects.get(model_version=model, kind="approved").note
        self.assertIn("Weak results acknowledged", note)
        self.assertIn("Acted as Approver", note)

    @override_settings(ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=True)
    def test_second_person_rule_blocks_the_person_who_started_the_run(self) -> None:
        starter = self.users["trainer"]
        self.assertEqual(
            self.send(self.as_user("trainer"), "post", f"/api/projects/{self.project.id}/training/runs", {"backend": "fake-backend", "dataset_id": str(self.dataset.id)}).status_code,
            202,
        )
        from .training import runner

        model = runner.run_training(runner.claim_next_run()).model_versions.get()
        self.assertEqual(model.training_run.created_by_id, starter.pk)
        self.send(self.as_user("trainer"), "post", f"/api/projects/{self.project.id}/models/{model.id}/stage", {})
        # a starter who also holds the Approver role is still refused, and so is a superuser starter
        grant(starter, "approver")
        refused = self.approve("trainer", model)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("someone else has to approve it", refused.json()["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")
        self.assertFalse(ModelEvent.objects.filter(model_version=model, kind="approved").exists())
        self.assertEqual(self.approve("approver", model).status_code, 200)  # a different person

    @override_settings(ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=True)
    def test_second_person_rule_also_binds_a_superuser_who_started_the_run(self) -> None:
        root = self.users["superuser"]
        client = self.as_user("superuser")
        self.assertEqual(self.send(client, "post", f"/api/projects/{self.project.id}/training/runs", {"backend": "fake-backend", "dataset_id": str(self.dataset.id)}).status_code, 202)
        from .training import runner

        model = runner.run_training(runner.claim_next_run()).model_versions.get()
        self.send(client, "post", f"/api/projects/{self.project.id}/models/{model.id}/stage", {})
        self.assertEqual(model.training_run.created_by_id, root.pk)
        self.assertEqual(self.approve("superuser", model).status_code, 403)

    def test_second_person_rule_is_off_by_default(self) -> None:
        self.assertFalse(permissions.requires_second_person())
        client = self.as_user("approver")
        self.assertEqual(self.send(client, "post", f"/api/projects/{self.project.id}/training/runs", {"backend": "fake-backend", "dataset_id": str(self.dataset.id)}).status_code, 202)
        from .training import runner

        model = runner.run_training(runner.claim_next_run()).model_versions.get()
        self.send(client, "post", f"/api/projects/{self.project.id}/models/{model.id}/stage", {})
        self.assertEqual(self.approve("approver", model).status_code, 200)  # own run, rule off

    @override_settings(ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=True)
    def test_the_rule_does_not_block_a_rollback(self) -> None:
        model = self.staged()
        self.assertEqual(self.approve("approver", model).status_code, 200)
        # the run was started by the test's own user, the rollback is by someone else and by the starter alike
        response = self.send(
            self.as_user("approver"), "post", f"/api/projects/{self.project.id}/models/{model.id}/rollback", {"note": "Back."}
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_model_json_names_who_started_the_run_with_an_opaque_id(self) -> None:
        body = self.as_user("viewer").get(f"/api/projects/{self.project.id}/models/{self.model.id}").json()["model"]
        self.assertEqual(body["lineage"]["run_started_by"], f"u{self.user.pk}")


# ---------------------------------------------------------------------------------------
class WhoAmITests(RoleBase):
    def test_capabilities_per_role_without_any_username_or_email(self) -> None:
        expected = {
            "viewer": (False, False, False), "labeler": (True, False, False), "trainer": (True, True, False),
            "approver": (True, True, True), "superuser": (True, True, True), "unassigned": (True, False, False),
        }
        for who, (label, train, approve) in expected.items():
            with self.subTest(who=who):
                response = self.as_user(who).get("/api/me")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "no-store")
                body = response.json()
                self.assertEqual((body["can_view"], body["can_label"], body["can_train"], body["can_approve"]), (True, label, train, approve))
                self.assertEqual(body["id"], f"u{self.users[who].pk}")
                text = response.content.decode()
                self.assertNotIn("@example.com", text)
                self.assertFalse({"username", "email", "name"} & set(body))
        self.assertEqual(self.as_user("viewer").get("/api/me").json()["roles"], ["viewer"])
        self.assertFalse(self.as_user("unassigned").get("/api/me").json()["assigned"])
        self.assertTrue(self.as_user("superuser").get("/api/me").json()["assigned"])

    @override_settings(ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=True)
    def test_it_says_when_approval_needs_a_second_person(self) -> None:
        self.assertTrue(self.as_user("approver").get("/api/me").json()["approval_requires_second_person"])

    def test_me_accepts_only_get(self) -> None:
        self.assertEqual(self.as_user("approver").post("/api/me").status_code, 405)


# ---------------------------------------------------------------------------------------
class EveryRouteDeclaresItsRole(RoleBase):
    """Walk the URL configuration. A new write route that forgets its role fails here, by name."""

    # route -> {method: needed action}; GET is "view" unless listed. Update this table on purpose, in review.
    DECLARED = {
        "": {},
        "api/me": {},
        "access": {"GET": ADMIN},
        "api/access": {"GET": ADMIN},
        "api/access/changes": {"GET": ADMIN},
        "api/access/people/<int:user_id>/console": {"GET": ADMIN, "PUT": ADMIN},
        "api/access/people/<int:user_id>/projects": {"GET": ADMIN, "PUT": ADMIN, "DELETE": ADMIN},
        "api/projects": {"POST": LABEL},
        "api/projects/<uuid:project_id>": {"PATCH": LABEL, "DELETE": TRAIN},
        "api/projects/<uuid:project_id>/assets": {"POST": LABEL},
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>": {"DELETE": LABEL},
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>/content": {},
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>/annotations": {"POST": LABEL},
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>/annotations/<uuid:annotation_id>": {"PATCH": LABEL, "DELETE": LABEL},
        "api/autolabel": {},
        "api/projects/<uuid:project_id>/autolabel": {"POST": LABEL},
        "api/projects/<uuid:project_id>/autolabel/<uuid:job_id>/cancel": {"POST": LABEL},
        "api/projects/<uuid:project_id>/annotations/review": {"POST": LABEL},
        "api/projects/<uuid:project_id>/datasets": {"POST": LABEL},
        "api/projects/<uuid:project_id>/datasets/readiness": {},
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>": {},
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>/": {},
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>/export": {"GET": LABEL},
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>/exports": {},
        "api/projects/<uuid:project_id>/training/backends": {},
        "api/projects/<uuid:project_id>/training/readiness": {},
        "api/projects/<uuid:project_id>/training/runs": {"POST": TRAIN},
        "api/projects/<uuid:project_id>/training/runs/<uuid:run_id>": {},
        "api/projects/<uuid:project_id>/training/runs/<uuid:run_id>/cancel": {"POST": TRAIN},
        "api/projects/<uuid:project_id>/models": {},
        "api/projects/<uuid:project_id>/models/<uuid:model_id>": {},
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/evaluation": {},
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/stage": {"POST": TRAIN},
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/approve": {"POST": APPROVE},
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/rollback": {"POST": APPROVE},
        "api/projects/<uuid:project_id>/inference/apps": {"POST": TRAIN},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>": {"PATCH": TRAIN, "DELETE": TRAIN},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/preview": {},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/run": {"POST": LABEL},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations": {},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>": {},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>/image": {},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>/feedback": {"POST": LABEL},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>/decision": {"POST": LABEL},
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/feedback/export": {},
        "projects/<uuid:project_id>/check-screen/setup": {},
        "check/<slug:slug>": {},
    }
    UNGUARDED = {"healthz", "login/", "logout/"}  # about signing in and out; no role applies

    def callbacks(self):
        from django.urls import get_resolver

        found = {}

        def collect(patterns, prefix=""):
            for pattern in patterns:
                if hasattr(pattern, "url_patterns"):
                    collect(pattern.url_patterns, prefix + str(pattern.pattern))
                else:
                    found[prefix + str(pattern.pattern)] = pattern.callback

        collect(get_resolver().url_patterns)
        return found

    def test_every_route_is_in_the_table_and_declares_what_the_table_says(self) -> None:
        callbacks = self.callbacks()
        undeclared = [route for route, view in callbacks.items() if route not in self.UNGUARDED and not hasattr(view, "athena_access")]
        self.assertEqual(undeclared, [], f"These routes have no @access(...) declaration: {undeclared}")
        self.assertEqual(sorted(set(callbacks) - self.UNGUARDED), sorted(self.DECLARED),
                         "A route was added or removed: declare it in DECLARED on purpose.")
        for route, expected in self.DECLARED.items():
            declared = {m: a for m, a in callbacks[route].athena_access.items() if not (m == "GET" and a == VIEW)}
            self.assertEqual(declared, expected, route)

    def test_no_write_method_is_declared_as_view_only(self) -> None:
        for route, view in self.callbacks().items():
            for method, action in getattr(view, "athena_access", {}).items():
                if method not in ("GET", "HEAD", "OPTIONS"):
                    self.assertNotEqual(action, VIEW, f"{method} {route} must need at least the Labeler role")

    def test_a_viewer_is_refused_or_told_405_on_every_write_of_every_route(self) -> None:
        """The decorator is the only thing between a Viewer and a write: probe it live, with a junk id."""
        viewer = self.as_user("viewer")
        callbacks = self.callbacks()
        probed = refused = 0
        for route, converters in walk():
            if route in self.UNGUARDED:
                continue
            values = {name: SAMPLE.get(kind, SAMPLE["str"])() for name, kind in converters.items()}
            path_ = render(route, values)
            declared = callbacks[route].athena_access
            for method in METHODS:
                with self.subTest(route=route, method=method):
                    kwargs = {"data": "{}", "content_type": "application/json"} if method in ("post", "put", "patch") else {}
                    status = getattr(viewer, method)(path_, **kwargs).status_code
                    probed += 1
                    if method == "get":
                        self.assertEqual(status == 403, declared["GET"] != VIEW, f"GET {path_} -> {status}")
                        continue
                    self.assertIn(status, (403, 405), f"{method.upper()} {path_} answered {status} to a Viewer")
                    self.assertEqual(status == 403, method.upper() in declared, f"{method.upper()} {path_}: {status} vs {declared}")
                    refused += status == 403
        self.assertGreater(probed, 200)
        self.assertGreaterEqual(refused, 20)

    def test_a_view_with_no_declaration_is_closed_by_the_middleware(self) -> None:
        with override_settings(ROOT_URLCONF="projects.tests_roles"):
            client = client_for(self.users["approver"])
            for method in ("get", "post"):
                response = getattr(client, method)("/api/forgotten")
                self.assertEqual(response.status_code, 403, method)
                self.assertIn("no access rule", response.json()["error"])
            self.assertEqual(client.get("/api/declared").status_code, 200)

    def test_declaring_an_unknown_level_is_a_programming_error(self) -> None:
        with self.assertRaises(ValueError):
            permissions.access(POST="administer")


def _forgotten(request):
    return JsonResponse({"oops": True})


@permissions.access()
def _declared(request):
    return JsonResponse({"fine": True})


urlpatterns = [path("api/forgotten", _forgotten), path("api/declared", _declared)]


# ---------------------------------------------------------------------------------------
class ConsoleMarkerTests(RoleBase):
    """The pages carry what the server decided; the scripts only read it, never decide it."""

    def body_tag(self, who: str, url: str = "/") -> str:
        html = self.as_user(who).get(url).content.decode()
        return re.search(r"<body[^>]*>", html).group(0)

    def test_dashboard_markers_follow_the_role_of_the_signed_in_user(self) -> None:
        expect = {
            "viewer": ("viewer", "0", "0", "0"), "labeler": ("labeler", "1", "0", "0"),
            "trainer": ("trainer", "1", "1", "0"), "approver": ("approver", "1", "1", "1"),
            "superuser": ("approver", "1", "1", "1"), "unassigned": ("labeler", "1", "0", "0"),
        }
        for who, (role, label, train, approve) in expect.items():
            with self.subTest(who=who):
                tag = self.body_tag(who)
                for attribute, value in (("data-role", role), ("data-can-label", label), ("data-can-train", train), ("data-can-approve", approve)):
                    self.assertIn(f'{attribute}="{value}"', tag)

    def test_the_role_shows_in_words_and_viewers_get_a_reason_up_front(self) -> None:
        viewer = self.as_user("viewer").get("/").content.decode()
        self.assertIn("Your role: Viewer", viewer)
        self.assertIn("Your account can look but not change things.", viewer)
        approver = self.as_user("approver").get("/").content.decode()
        self.assertIn("Your role: Approver", approver)
        self.assertNotIn("Your account can look but not change things.", approver)
        self.assertNotIn("there are no separate roles yet", approver)

    @override_settings(ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=True)
    def test_the_second_person_rule_is_visible_to_the_script(self) -> None:
        self.assertIn('data-second-person="1"', self.body_tag("approver"))

    def test_the_second_person_marker_is_off_by_default(self) -> None:
        self.assertIn('data-second-person="0"', self.body_tag("approver"))

    def test_the_check_screen_pages_carry_the_same_markers(self) -> None:
        for url in (f"/projects/{self.project.id}/check-screen/setup", f"/check/{self.app['slug']}"):
            for who, label, train in (("viewer", "0", "0"), ("labeler", "1", "0"), ("trainer", "1", "1")):
                with self.subTest(url=url, who=who):
                    tag = self.body_tag(who, url)
                    self.assertIn(f'data-can-label="{label}"', tag)
                    self.assertIn(f'data-can-train="{train}"', tag)

    def test_scripts_read_capabilities_from_the_page_not_from_the_browser(self) -> None:
        common = (STATIC / "ui_common.js").read_text()
        self.assertIn("document.body.dataset", common)
        for forbidden in ("localStorage", "sessionStorage", "location.", "document.cookie"):
            self.assertNotIn(forbidden, common, "the role must come from the server-rendered page only")
        for name in ("console.js", "checkscreen_builder.js", "checkscreen.js"):
            self.assertIn("Athena.can(", (STATIC / name).read_text(), f"{name} never checks a capability")

    def test_the_console_names_the_buttons_it_gates(self) -> None:
        html = self.as_user("viewer").get("/").content.decode()
        for marker in (
            'id="open-project-dialog" type="button" data-needs="label"', 'id="image-upload-form" data-needs="label"',
            'id="dataset-form" data-needs="label"', 'id="tr-form" data-needs="train"', 'id="tr-cancel" type="button" data-needs="train"',
            'id="delete-selected-project" type="button" data-needs="train"', 'id="keyboard-box-form" data-needs="label"',
            'id="autolabel-form" data-needs="label"',
        ):
            self.assertIn(marker, html)
        source = (STATIC / "console.js").read_text()
        for needle in ('"Approve for use"', '"Go back to previous model"', '"approve"', 'dataset.needs = "label"'):
            self.assertIn(needle, source)


# ---------------------------------------------------------------------------------------
class RolesCommandTests(TestCase):
    def run_command(self, *args: str) -> str:
        out = io.StringIO()
        call_command("athena_roles", *args, stdout=out)
        return out.getvalue()

    def test_creates_the_four_groups_and_is_safe_to_repeat(self) -> None:
        first = self.run_command()
        for role in permissions.ROLES:
            self.assertIn(f"Created group “{permissions.GROUP_NAMES[role]}”", first)
        second = self.run_command()
        self.assertNotIn("Created group", second)
        self.assertEqual(Group.objects.filter(name__in=list(permissions.GROUP_NAMES.values())).count(), 4)

    def test_assign_replaces_the_previous_role_and_lists_members(self) -> None:
        make_user("alice")
        make_user("bob", "viewer")
        self.run_command("--assign", "alice", "trainer")
        self.assertEqual(permissions.assigned_roles(User.objects.get(username="alice")), ["trainer"])
        self.run_command("--assign", "alice", "Approver")
        self.assertEqual(permissions.assigned_roles(User.objects.get(username="alice")), ["approver"])
        listing = self.run_command()
        self.assertIn("Approver (1): alice", listing)
        self.assertIn("Viewer (1): bob", listing)
        self.assertIn("Users in no Athena group count as: Labeler", listing)

    def test_revoke_one_role_or_all(self) -> None:
        user = make_user("carol", "trainer")
        self.assertIn("no longer holds", self.run_command("--revoke", "carol", "trainer"))
        self.assertEqual(permissions.assigned_roles(user), [])
        with self.assertRaises(CommandError):
            self.run_command("--revoke", "carol", "trainer")  # not held any more
        grant(user, "labeler")
        self.assertIn("Removed every Athena role", self.run_command("--revoke", "carol"))
        self.assertEqual(permissions.assigned_roles(user), [])

    def test_unknown_names_and_conflicting_options_are_plain_errors(self) -> None:
        make_user("dave")
        with self.assertRaisesMessage(CommandError, "Unknown role"):
            self.run_command("--assign", "dave", "boss")
        with self.assertRaisesMessage(CommandError, "There is no user named"):
            self.run_command("--assign", "nobody", "viewer")
        with self.assertRaises(CommandError):
            self.run_command("--assign", "dave", "viewer", "--revoke", "dave")

    def test_the_listing_shows_superusers_and_unassigned_people(self) -> None:
        make_user("root", is_superuser=True)
        make_user("erin")
        listing = self.run_command()
        self.assertIn("Superusers, who can do everything (1): root", listing)
        self.assertIn("(1): erin", listing)

    @override_settings(ATHENA_UNASSIGNED_ROLE="viewer", ATHENA_APPROVAL_REQUIRES_SECOND_PERSON=True)
    def test_the_listing_reports_both_settings(self) -> None:
        listing = self.run_command()
        self.assertIn("count as: Viewer", listing)
        self.assertIn("second person (ATHENA_APPROVAL_REQUIRES_SECOND_PERSON): on", listing)


class SettingsParsingTests(SimpleTestCase):
    def test_a_bad_unassigned_role_stops_the_console_from_starting(self) -> None:
        import subprocess
        import sys

        root = Path(__file__).resolve().parent.parent
        code = "import django; django.setup()"
        env = {"PATH": "/usr/bin", "DJANGO_DEBUG": "1", "ATHENA_UNASSIGNED_ROLE": "admin", "DJANGO_SETTINGS_MODULE": "athena_web.settings"}
        result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ATHENA_UNASSIGNED_ROLE must be 'labeler' or 'viewer'", result.stderr)
