"""Release bundles: append-only, digest-verified release candidates built from draft pins.

The bundle endpoint (T05) assembles one bundle from a draft revision's verified pins plus
its recording. These tests cover the full happy path, per-operation numbering, every
missing-pin refusal, the unbound-draft refusal, the append-only and PROTECT deletion
guarantees, the snapshot's independence from later draft edits, owner scoping, and the
approver-only role gate on POST.
"""

from __future__ import annotations

import json
import re

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import Client, TestCase

from .models import (
    DatasetVersion,
    ModelVersion,
    OperationRecording,
    OperationReleaseBundle,
    Project,
    TrainingRun,
)
from .tests import grant

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseBundleBase(TestCase):
    """A draft with every component pin set and a recording, one POST away from a bundle."""

    def setUp(self):
        from .models import Operation, OperationSource, Site

        # The owner is a superuser so the approver-only POST is permitted; the role gate
        # itself (labeler/viewer refusals, approver success) has its own test.
        self.user = get_user_model().objects.create_user("releaser", is_superuser=True)
        self.client.force_login(self.user)
        self.site = Site.objects.create(created_by=self.user, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(created_by=self.user, site=self.site, source=self.source, name="Door")
        self.line = {"start": [0.1, 0.5], "end": [0.9, 0.5], "positive_label": "Entering", "negative_label": "Leaving"}
        self.source_view = self._post(
            f"/api/operations/{self.operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 0,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
        )["source_view"]
        self.geometry = self._post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": self.source_view["id"], "geometry": {"line": self.line}},
        )["geometry_version"]
        self.recipe = self._post(
            f"/api/operations/{self.operation.id}/recipe-versions",
            {
                "recipe_key": "people_counting",
                "configuration": {
                    "dead_band": 0.01,
                    "confirmations": 2,
                    "max_gap_seconds": 1.0,
                    "comparison_tolerance_seconds": 0.5,
                },
            },
        )["recipe_version"]
        self.policy = self._post(
            f"/api/operations/{self.operation.id}/policy-versions",
            {"configuration": {"evidence_retention_days": 90, "max_coverage_gap_seconds": 5.0}},
        )["policy_version"]
        project = Project.objects.create(name="Counting", modality="image", task="Detect", labels=["person"])
        # The binding view looks the model up through the training run's creator (not the
        # project), so the run and its dataset version must exist and carry created_by.
        dataset_version = DatasetVersion.objects.create(
            project=project, version=1, manifest={}, manifest_sha256="c" * 64, created_by=self.user
        )
        training_run = TrainingRun.objects.create(
            project=project,
            dataset_version=dataset_version,
            backend="fake",
            config={},
            config_sha256="d" * 64,
            dataset_manifest_sha256="e" * 64,
            created_by=self.user,
        )
        self.model_version = ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=training_run,
            backend="fake",
            artifact_path=f"models/{project.id}/v1.onnx",
            artifact_sha256="a" * 64,
            artifact_size_bytes=100,
            label_names=["person"],
            stage=ModelVersion.APPROVED,
        )
        self.binding = self._post(
            f"/api/operations/{self.operation.id}/model-bindings",
            {"model_version_id": str(self.model_version.id)},
        )["model_binding"]
        self.screen = self._post(
            f"/api/operations/{self.operation.id}/screen-versions",
            {"configuration": {"title": "Shop door board", "show_entries": True, "show_exits": True}},
        )["screen_version"]

    def _post(self, path, payload, client=None, status=201):
        response = (client or self.client).post(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def _create_draft(self, pins=None, operation_id=None):
        """A draft bound to the operation; every component pin set unless overridden."""
        payload = {
            "operation_id": str(self.operation.id if operation_id is None else operation_id),
            "name": "Shop door",
            "line": self.line,
            "geometry_version_id": self.geometry["id"],
            "recipe_version_id": self.recipe["id"],
            "policy_version_id": self.policy["id"],
            "model_binding_id": self.binding["id"],
            "screen_version_id": self.screen["id"],
        }
        payload.update(pins or {})
        for key, value in list(payload.items()):
            if value is _OMIT:
                del payload[key]
        return self._post("/api/operation-drafts", payload)["draft"]

    def _attach_recording(self, draft_id):
        """A direct recording row (the upload path needs no ffmpeg here)."""
        return OperationRecording.objects.create(
            draft_id=draft_id,
            storage_key=f"operation-recordings/{draft_id}.mp4",
            filename="clip.mp4",
            sha256="b" * 64,
            size_bytes=100,
            width=640,
            height=480,
            duration_seconds=12.5,
        )

    def _create_bundle(self, draft):
        """Create the release-ready draft and POST its first bundle."""
        self._attach_recording(draft["id"])
        url = f"/api/operation-drafts/{draft['id']}/release-bundles"
        return self._post(url, {}), url


class _Omit:
    """Marker for ``_create_draft``: leave the pin out of the payload entirely."""


_OMIT = _Omit()


class ReleaseBundleHappyPathTests(ReleaseBundleBase):
    def test_first_bundle_contains_every_pin_the_recording_and_a_verifiable_digest(self):
        draft = self._create_draft()
        recording = self._attach_recording(draft["id"])
        url = f"/api/operation-drafts/{draft['id']}/release-bundles"
        created = self._post(url, {})
        bundle = created["release_bundle"]

        self.assertEqual(bundle["number"], 1)
        self.assertEqual(bundle["operation_id"], str(self.operation.id))
        self.assertEqual(bundle["draft_id"], draft["id"])
        self.assertEqual(bundle["draft_revision"], draft["revision"])
        self.assertRegex(bundle["sha256"], _SHA256)
        self.assertEqual(bundle["sha256"], OperationReleaseBundle.objects.get(pk=bundle["id"]).snapshot_sha256)

        snapshot = bundle["snapshot"]
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["kind"], "operation_release_bundle")
        self.assertTrue(snapshot["release_ready"])
        self.assertEqual(snapshot["operation_id"], str(self.operation.id))
        self.assertEqual(snapshot["draft_id"], draft["id"])
        self.assertEqual(snapshot["draft_revision"], draft["revision"])
        pins = snapshot["component_pins"]
        self.assertEqual(pins["source_view"]["id"], self.source_view["id"])
        self.assertEqual(pins["geometry"]["id"], self.geometry["id"])
        self.assertEqual(pins["geometry"]["source_view_version_id"], self.source_view["id"])
        self.assertEqual(pins["recipe"]["id"], self.recipe["id"])
        self.assertEqual(pins["model"]["id"], self.binding["id"])
        self.assertEqual(pins["model"]["model_version_id"], str(self.model_version.id))
        self.assertEqual(pins["screen"]["id"], self.screen["id"])
        self.assertEqual(pins["policy"]["id"], self.policy["id"])
        self.assertEqual(
            snapshot["recording"],
            {
                "sha256": recording.sha256,
                "size_bytes": recording.size_bytes,
                "width": recording.width,
                "height": recording.height,
                "duration_seconds": recording.duration_seconds,
            },
        )
        self.assertEqual(snapshot["reference_events"], [])
        self.assertNotIn("draft", snapshot, "a bundle records pins, not the draft body")
        self.assertNotIn("geometry_state", snapshot)

        # GET lists the same bundle, newest first, with the full row.
        listed = self.client.get(url)
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()["release_bundles"], [bundle])
        row = OperationReleaseBundle.objects.get(pk=bundle["id"])
        self.assertEqual(row.number, 1)
        self.assertEqual(row.draft_revision, draft["revision"])
        self.assertEqual(str(row.operation_id), str(self.operation.id))
        self.assertEqual(str(row.created_by_id), str(self.user.id))

    def test_second_bundle_on_a_new_revision_is_number_2_for_the_same_operation(self):
        draft = self._create_draft()
        first, url = self._create_bundle(draft)
        # A new recipe version pinned on a new draft revision: bundle 2 builds on it.
        second_recipe = self._post(
            f"/api/operations/{self.operation.id}/recipe-versions",
            {
                "recipe_key": "people_counting",
                "configuration": {
                    "dead_band": 0.02,
                    "confirmations": 3,
                    "max_gap_seconds": 1.0,
                    "comparison_tolerance_seconds": 0.5,
                },
            },
        )["recipe_version"]
        updated = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps(
                {
                    "name": "Shop door",
                    "line": self.line,
                    "revision": draft["revision"],
                    "recipe_version_id": second_recipe["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        revision = updated.json()["draft"]["revision"]
        second = self._post(url, {})["release_bundle"]

        self.assertEqual(second["number"], 2)
        self.assertEqual(second["draft_revision"], revision)
        self.assertEqual(second["draft_id"], draft["id"])
        self.assertEqual(second["snapshot"]["component_pins"]["recipe"]["id"], second_recipe["id"])
        self.assertNotEqual(second["sha256"], first["release_bundle"]["sha256"])
        listed = self.client.get(url).json()["release_bundles"]
        self.assertEqual([item["number"] for item in listed], [2, 1])
        self.assertEqual(set(OperationReleaseBundle.objects.values_list("number", flat=True)), {1, 2})


class ReleaseBundleRefusalTests(ReleaseBundleBase):
    def test_each_missing_pin_is_named_and_nothing_is_stored(self):
        cases = [
            ("geometry", "geometry_version_id", "geometry"),
            ("recipe", "recipe_version_id", "recipe"),
            ("model", "model_binding_id", "model"),
            ("screen", "screen_version_id", "screen"),
            ("policy", "policy_version_id", "policy"),
        ]
        for label, key, expected in cases:
            with self.subTest(pin=label):
                draft = self._create_draft({key: _OMIT})
                self._attach_recording(draft["id"])
                url = f"/api/operation-drafts/{draft['id']}/release-bundles"
                response = self.client.post(url, json.dumps({}), content_type="application/json")
                self.assertEqual(response.status_code, 409, response.content)
                self.assertIn(expected, response.json()["error"])
                self.assertIn("Pin every component", response.json()["error"])
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)

    def test_missing_recording_is_its_own_refusal(self):
        draft = self._create_draft()
        url = f"/api/operation-drafts/{draft['id']}/release-bundles"
        response = self.client.post(url, json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("Attach a recording", response.json()["error"])
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)

    def test_all_missing_pins_are_listed_together(self):
        draft = self._create_draft(
            {
                "geometry_version_id": _OMIT,
                "recipe_version_id": _OMIT,
                "policy_version_id": _OMIT,
                "model_binding_id": _OMIT,
                "screen_version_id": _OMIT,
            }
        )
        self._attach_recording(draft["id"])
        response = self.client.post(
            f"/api/operation-drafts/{draft['id']}/release-bundles", json.dumps({}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 409, response.content)
        error = response.json()["error"]
        for name in ("geometry", "model", "policy", "recipe", "screen", "source_view"):
            self.assertIn(name, error)
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)

    def test_a_draft_without_an_operation_is_refused(self):
        # An unbound draft (no operation_id in the payload, no pins: they would need one).
        response = self.client.post(
            "/api/operation-drafts", json.dumps({"name": "Free", "line": None}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        draft = response.json()["draft"]
        self._attach_recording(draft["id"])
        response = self.client.post(
            f"/api/operation-drafts/{draft['id']}/release-bundles", json.dumps({}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Bind the draft to an operation", response.json()["error"])
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)


class ReleaseBundleImmutabilityTests(ReleaseBundleBase):
    def test_bundles_are_append_only_at_every_orm_door(self):
        draft = self._create_draft()
        bundle, _url = self._create_bundle(draft)
        row = OperationReleaseBundle.objects.get(pk=bundle["release_bundle"]["id"])

        with self.assertRaises(ValueError):
            OperationReleaseBundle.objects.filter(pk=row.pk).update(number=99)
        with self.assertRaises(ValueError):
            OperationReleaseBundle.objects.filter(pk=row.pk).update(snapshot={"tampered": True})
        with self.assertRaises(ValueError):
            OperationReleaseBundle.objects.bulk_update([row], ["snapshot"])
        with self.assertRaises(ValueError):
            row.snapshot = {"tampered": True}
            row.save()
        with self.assertRaises(ValueError):
            row.delete()
        with self.assertRaises(ValueError):
            OperationReleaseBundle.objects.filter(pk=row.pk).delete()

        fresh = OperationReleaseBundle.objects.get(pk=row.pk)
        self.assertEqual(fresh.number, 1)
        self.assertEqual(fresh.snapshot["kind"], "operation_release_bundle")
        self.assertEqual(OperationReleaseBundle.objects.count(), 1)

    def test_deleting_a_bundled_draft_is_refused(self):
        """The PROTECT foreign key makes the draft undeletable while a bundle exists.

        The endpoint does not catch the ``ProtectedError``, so Django's handler turns it
        into a plain 500 page (logged, no JSON body) and nothing is deleted. The same
        protection is observable at ORM level.
        """
        draft = self._create_draft()
        bundle, _url = self._create_bundle(draft)
        quiet = Client(raise_request_exception=False)
        quiet.force_login(self.user)
        response = quiet.delete(f"/api/operation-drafts/{draft['id']}")
        self.assertEqual(response.status_code, 500, response.content)
        from projects.models import OperationDraft

        self.assertTrue(OperationDraft.objects.filter(pk=draft["id"]).exists())
        self.assertEqual(OperationReleaseBundle.objects.filter(draft_id=draft["id"]).count(), 1)

        with self.assertRaises(ProtectedError):
            OperationDraft.objects.get(pk=draft["id"]).delete()
        self.assertEqual(
            OperationReleaseBundle.objects.filter(draft_id=draft["id"]).count(),
            1,
            "the bundle must survive the refused delete",
        )
        self.assertTrue(OperationDraft.objects.filter(pk=draft["id"]).exists())

    def test_bundle_snapshot_survives_later_draft_edits(self):
        draft = self._create_draft()
        bundle, url = self._create_bundle(draft)
        stored = OperationReleaseBundle.objects.get(pk=bundle["release_bundle"]["id"])

        renamed = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps({"name": "Back door", "line": self.line, "revision": draft["revision"]}),
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)

        fresh = OperationReleaseBundle.objects.get(pk=stored.pk)
        self.assertEqual(fresh.snapshot, stored.snapshot)
        self.assertEqual(fresh.snapshot_sha256, stored.snapshot_sha256)
        self.assertEqual(fresh.draft_revision, stored.draft_revision)
        self.assertEqual(
            self.client.get(url).json()["release_bundles"][0]["sha256"], bundle["release_bundle"]["sha256"]
        )

        # The draft's own latest version snapshot did move on.
        versions = self.client.get(f"/api/operation-drafts/{draft['id']}/versions").json()["versions"]
        self.assertEqual([v["revision"] for v in versions], [2, 1])
        self.assertEqual(versions[0]["snapshot"]["draft"]["name"], "Back door")
        self.assertEqual(versions[1]["snapshot"]["draft"]["name"], "Shop door")
        self.assertEqual(fresh.snapshot["component_pins"]["recipe"]["id"], self.recipe["id"])


class ReleaseBundleAccessTests(ReleaseBundleBase):
    def setUp(self):
        super().setUp()
        # Demote the fixture owner to a plain account so the role gate decides POSTs.
        self.user.is_superuser = False
        self.user.save()

    def test_strangers_cannot_read_or_assemble_someone_elses_bundles(self):
        draft = self._create_draft()
        self._attach_recording(draft["id"])
        url = f"/api/operation-drafts/{draft['id']}/release-bundles"
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("bundle-stranger"))
        self.assertEqual(stranger.get(url).status_code, 404)
        # On POST the role gate answers first: an unassigned stranger is a labeler by
        # default, so the approver requirement refuses them (403) before the owner
        # scoping would (404). Either way, nothing is assembled.
        self.assertEqual(stranger.post(url, json.dumps({}), content_type="application/json").status_code, 403)
        # A foreign approver passes the role gate and is then scoped out: the Approver
        # role does not grant access to another person's private draft.
        approver = get_user_model().objects.create_user("foreign-approver")
        grant(approver, "approver")
        stranger.force_login(approver)
        self.assertEqual(stranger.get(url).status_code, 404)
        self.assertEqual(stranger.post(url, json.dumps({}), content_type="application/json").status_code, 404)
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)
        self.assertEqual(self.client.get(url).json(), {"release_bundles": []})

    def test_post_is_approver_only_while_get_stays_open_to_the_owner(self):
        draft = self._create_draft()
        self._attach_recording(draft["id"])
        url = f"/api/operation-drafts/{draft['id']}/release-bundles"

        # Unassigned (labeler) owner: GET works, POST is refused.
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)

        grant(self.user, "viewer")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, json.dumps({}), content_type="application/json").status_code, 403)
        self.assertEqual(OperationReleaseBundle.objects.count(), 0)

        grant(self.user, "approver")
        self.assertEqual(self.client.get(url).status_code, 200)
        assembled = self._post(url, {})
        self.assertEqual(assembled["release_bundle"]["number"], 1)
        self.assertEqual(assembled["release_bundle"]["draft_revision"], draft["revision"])
        self.assertEqual(OperationReleaseBundle.objects.count(), 1)
