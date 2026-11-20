"""Operation model bindings: immutable, server-verified pins of one exact model version.

The binding endpoint is T05's "model version reference": it records which model version an
operation builds on, which compatibility checks the server ran, and their results. The tests
below cover the happy path (approved, own-project model), the refusal paths (stage, foreign
data, wrong bodies), the frozen-row guarantees, and draft pinning/undo.
"""

from __future__ import annotations

import json
import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import (
    DatasetVersion,
    ModelVersion,
    Operation,
    OperationModelBinding,
    OperationSource,
    Project,
    Site,
    TrainingRun,
)
from .operation_views import _component_sha256, _model_compatibility
from .tests import grant

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _make_model_version(project, *, version=1, stage=ModelVersion.APPROVED, training_run=None, labels=None):
    """The minimal ModelVersion row the binding endpoint needs (its full lifecycle lives in tests_training).

    The binding route scopes by the training run's creator, so every model meant to be
    bindable is created with a run owned by the binder. The one-run-per-project DB
    constraint means same-project models share one run (see ModelBindingBase.setUp);
    a foreign owner needs their own project (and therefore their own run).
    """
    return ModelVersion.objects.create(
        project=project,
        version=version,
        training_run=training_run,
        backend="fake",
        artifact_path=f"models/{project.id}/v{version}.onnx",
        artifact_sha256=f"{version:064d}"[-64:],
        artifact_size_bytes=100,
        label_names=labels if labels is not None else ["person"],
        stage=stage,
    )


def _make_binding(operation, model_version, created_by):
    """Create the binding row the endpoint would create: the same checks dict and digest.

    Used where the endpoint itself is blocked (see the module docstring) so the frozen-row,
    listing, and draft-pin behavior can still be tested.
    """
    compatibility = _model_compatibility(operation, model_version)
    return OperationModelBinding.objects.create(
        operation=operation,
        version=OperationModelBinding.objects.filter(operation=operation).count() + 1,
        created_by=created_by,
        model_version=model_version,
        compatibility=compatibility,
        compatibility_sha256=_component_sha256(compatibility),
    )


class ModelBindingBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("binder")
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Counting", modality="image", task="Detect", labels=["person"])
        # One training run per project (DB constraint): every same-project model below shares it.
        self.dataset = DatasetVersion.objects.create(
            project=self.project, version=1, created_by=self.user, manifest_sha256="d" * 64, label_names=["person"]
        )
        self.run = TrainingRun.objects.create(
            project=self.project,
            dataset_version=self.dataset,
            dataset_manifest_sha256="d" * 64,
            backend="fake",
            config={},
            config_sha256="e" * 64,
            created_by=self.user,
        )
        self.model_version = _make_model_version(self.project, training_run=self.run)
        self.site = Site.objects.create(created_by=self.user, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(created_by=self.user, site=self.site, source=self.source, name="Door")
        self.line = {"start": [0.1, 0.5], "end": [0.9, 0.5], "name": "Front door line", "positive_label": "Entering", "negative_label": "Leaving"}

    def _url(self, operation=None):
        return f"/api/operations/{(operation or self.operation).id}/model-bindings"

    def _post_binding(self, model_version=None, client=None, operation=None):
        body = json.dumps({"model_version_id": str((model_version or self.model_version).id)})
        return (client or self.client).post(self._url(operation), body, content_type="application/json")

    def _post_draft(self, pins=None, name="Shop door"):
        payload = {"operation_id": str(self.operation.id), "name": name, "line": self.line}
        payload.update(pins or {})
        response = self.client.post("/api/operation-drafts", json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["draft"]

    def _put_draft(self, draft, pins=None):
        payload = {"name": draft["name"], "line": self.line, "revision": draft["revision"]}
        payload.update(pins or {})
        response = self.client.put(
            f"/api/operation-drafts/{draft['id']}", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["draft"]


class OperationModelBindingTests(ModelBindingBase):
    def test_binding_an_approved_model_creates_a_numbered_immutable_record(self):
        response = self._post_binding()
        self.assertEqual(response.status_code, 201, response.content)
        binding = response.json()["model_binding"]
        self.assertEqual(binding["version"], 1)
        self.assertEqual(binding["operation_id"], str(self.operation.id))
        self.assertEqual(binding["model_version_id"], str(self.model_version.id))
        self.assertTrue(_SHA256.match(binding["sha256"]))
        compatibility = binding["compatibility"]
        self.assertIs(compatibility["compatible"], True)
        self.assertIsNone(compatibility["reason"])
        self.assertEqual(compatibility["model_version_number"], 1)
        self.assertEqual(compatibility["artifact_sha256"], self.model_version.artifact_sha256)
        self.assertEqual(compatibility["stage"], ModelVersion.APPROVED)
        self.assertEqual(compatibility["labels"], ["person"])

        second_project = Project.objects.create(name="Counting 2", modality="image", task="Detect", labels=["person"])
        second_dataset = DatasetVersion.objects.create(
            project=second_project, version=1, created_by=self.user, manifest_sha256="f" * 64, label_names=["person"]
        )
        second_run = TrainingRun.objects.create(
            project=second_project,
            dataset_version=second_dataset,
            dataset_manifest_sha256="f" * 64,
            backend="fake",
            config={},
            config_sha256="9" * 64,
            created_by=self.user,
        )
        second = _make_model_version(second_project, version=1, labels=["person", "dog"], training_run=second_run)
        response = self._post_binding(second)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["model_binding"]["version"], 2)

        listed = self.client.get(self._url()).json()["model_bindings"]
        self.assertEqual([item["version"] for item in listed], [2, 1])  # newest first
        self.assertEqual(listed[1]["id"], binding["id"])

    def test_binding_a_model_that_is_not_approved_is_refused(self):
        for number, stage in enumerate((ModelVersion.CANDIDATE, ModelVersion.STAGED, ModelVersion.RETIRED), start=2):
            with self.subTest(stage=stage):
                model = _make_model_version(self.project, version=number, stage=stage, training_run=self.run)
                response = self._post_binding(model)
                self.assertEqual(response.status_code, 409, response.content)
                self.assertIn("Only an approved model version", response.json()["error"])
        self.assertEqual(OperationModelBinding.objects.count(), 0)

    def test_binding_requires_exactly_one_uuid_body_key(self):
        for body in (
            {},
            {"model_version_id": None},
            {"model_version_id": 42},
            {"model_version_id": "not-a-uuid"},
            {"model_version_id": str(self.model_version.id), "extra": 1},
            {"model_version": str(self.model_version.id)},
        ):
            with self.subTest(body=body):
                response = self.client.post(self._url(), json.dumps(body), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(OperationModelBinding.objects.count(), 0)

    def test_binding_a_foreign_model_version_is_not_found(self):
        foreign_user = get_user_model().objects.create_user("model-stranger")
        foreign_project = Project.objects.create(name="Theirs", modality="image", task="Detect", labels=["person"])
        foreign_dataset = DatasetVersion.objects.create(
            project=foreign_project,
            version=1,
            created_by=foreign_user,
            manifest_sha256="a" * 64,
            label_names=["person"],
        )
        foreign_run = TrainingRun.objects.create(
            project=foreign_project,
            dataset_version=foreign_dataset,
            dataset_manifest_sha256="a" * 64,
            backend="fake",
            config={},
            config_sha256="b" * 64,
            created_by=foreign_user,
        )
        foreign_model = _make_model_version(foreign_project, training_run=foreign_run)
        response = self._post_binding(foreign_model)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(OperationModelBinding.objects.count(), 0)
        self.assertFalse(
            OperationModelBinding.objects.filter(model_version=foreign_model).exists(),
            "the stranger's model version must not become an operation's pin",
        )

    def test_the_endpoint_is_owner_scoped(self):
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("route-stranger"))
        self.assertEqual(stranger.get(self._url()).status_code, 404)
        self.assertEqual(self._post_binding(client=stranger).status_code, 404)
        self.assertEqual(OperationModelBinding.objects.count(), 0)
        self.assertEqual(self.client.get(self._url()).json(), {"model_bindings": []})

    def test_viewer_role_cannot_bind(self):
        viewer = get_user_model().objects.create_user("watcher")
        grant(viewer, "viewer")
        watched = Client()
        watched.force_login(viewer)
        response = watched.post(
            self._url(), json.dumps({"model_version_id": str(self.model_version.id)}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(OperationModelBinding.objects.count(), 0)
        # The owner (an unassigned user defaults to labeler) succeeds in the happy-path test above.

    def test_binding_rows_are_frozen(self):
        binding = _make_binding(self.operation, self.model_version, self.user)
        with self.assertRaises(ValueError):
            OperationModelBinding.objects.filter(id=binding.id).update(compatibility={"forged": True})
        binding.compatibility = {"forged": True}
        with self.assertRaises(ValueError):
            binding.save()
        fresh = OperationModelBinding.objects.get(id=binding.id)
        self.assertTrue(fresh.compatibility["compatible"])
        self.assertEqual(fresh.version, 1)

    def test_the_stage_rule_is_enforced_by_the_compatibility_checker(self):
        for number, stage in enumerate((ModelVersion.CANDIDATE, ModelVersion.STAGED, ModelVersion.RETIRED), start=2):
            with self.subTest(stage=stage):
                model = _make_model_version(self.project, version=number, stage=stage)
                checks = _model_compatibility(self.operation, model)
                self.assertFalse(checks["compatible"])
                self.assertIn("Only an approved model version", checks["reason"])
        # An approved model without a training run carries no dataset-owner question: it passes.
        checks = _model_compatibility(self.operation, self.model_version)
        self.assertIs(checks["compatible"], True)
        self.assertIsNone(checks["reason"])
        self.assertEqual(checks["model_version_id"], str(self.model_version.id))
        self.assertEqual(checks["model_version_number"], 1)
        self.assertEqual(checks["artifact_sha256"], self.model_version.artifact_sha256)
        self.assertEqual(checks["stage"], ModelVersion.APPROVED)
        self.assertEqual(checks["labels"], ["person"])

    def test_a_model_taught_from_someone_elses_dataset_is_refused(self):
        # A run the binder started, but on a dataset version someone else created: the
        # operation owner must not be able to bind the resulting model.
        other_user = get_user_model().objects.create_user("data-owner")
        other_project = Project.objects.create(name="Borrowed", modality="image", task="Detect", labels=["person"])
        dataset = DatasetVersion.objects.create(
            project=other_project, version=1, created_by=other_user, manifest_sha256="c" * 64, label_names=["person"]
        )
        run = TrainingRun.objects.create(
            project=other_project,
            dataset_version=dataset,
            dataset_manifest_sha256="c" * 64,
            backend="fake",
            config={},
            config_sha256="6" * 64,
            created_by=self.user,
        )
        model = _make_model_version(other_project, version=1, training_run=run)
        response = self._post_binding(model)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("your own project data", response.json()["error"])
        self.assertEqual(OperationModelBinding.objects.count(), 0)
        # The same rule, seen through the pure checker: only the dataset's owner matters.
        checks = _model_compatibility(self.operation, model)
        self.assertFalse(checks["compatible"])
        self.assertIn("your own project data", checks["reason"])

    def test_tampering_with_a_stored_compatibility_check_breaks_the_listing(self):
        binding = _make_binding(self.operation, self.model_version, self.user)
        self.assertEqual(self.client.get(self._url()).status_code, 200)
        # The manager refuses every write, so a damaged row is simulated in raw SQL.
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {OperationModelBinding._meta.db_table} SET compatibility = %s WHERE id = %s",
                [json.dumps({"forged": True}), binding.pk.hex],
            )
        self.assertEqual(self.client.get(self._url()).status_code, 500)

    def test_listings_are_scoped_to_their_own_operation(self):
        other_site = Site.objects.create(created_by=self.user, name="Back")
        other_source = OperationSource.objects.create(site=other_site, name="Rear", kind="recorded_video")
        other_operation = Operation.objects.create(
            created_by=self.user, site=other_site, source=other_source, name="Back door"
        )
        first = _make_binding(self.operation, self.model_version, self.user)
        second = _make_binding(other_operation, self.model_version, self.user)
        self.assertEqual((first.version, second.version), (1, 1))  # numbering is per operation
        self.assertEqual(
            [item["id"] for item in self.client.get(self._url()).json()["model_bindings"]], [str(first.id)]
        )
        self.assertEqual(
            [item["id"] for item in self.client.get(self._url(other_operation)).json()["model_bindings"]],
            [str(second.id)],
        )


class DraftModelPinTests(ModelBindingBase):
    def test_a_binding_can_be_pinned_and_unpinned_on_a_draft(self):
        binding = _make_binding(self.operation, self.model_version, self.user)
        draft = self._post_draft({"model_binding_id": str(binding.id)})
        self.assertEqual(draft["pins"]["model_binding_id"], str(binding.id))

        snapshot = self.client.get(f"/api/operation-drafts/{draft['id']}/versions").json()["versions"][0]["snapshot"]
        pinned_model = snapshot["component_pins"]["model"]
        self.assertEqual(pinned_model["id"], str(binding.id))
        self.assertEqual(pinned_model["version"], 1)
        self.assertEqual(pinned_model["model_version_id"], str(self.model_version.id))
        self.assertEqual(pinned_model["sha256"], binding.compatibility_sha256)

        cleared = self._put_draft(draft, {"model_binding_id": None})
        self.assertIsNone(cleared["pins"]["model_binding_id"])
        # The old snapshot keeps the pin; only the draft moved on.
        versions = self.client.get(f"/api/operation-drafts/{draft['id']}/versions").json()["versions"]
        self.assertIsNone(versions[0]["snapshot"]["component_pins"]["model"])
        self.assertEqual(versions[1]["snapshot"]["component_pins"]["model"]["id"], str(binding.id))

    def test_a_binding_from_a_foreign_operation_cannot_be_pinned(self):
        other_user = get_user_model().objects.create_user("other-binder")
        other_site = Site.objects.create(created_by=other_user, name="Other")
        other_source = OperationSource.objects.create(site=other_site, name="Back", kind="recorded_video")
        other_operation = Operation.objects.create(
            created_by=other_user, site=other_site, source=other_source, name="Other door"
        )
        other_project = Project.objects.create(name="Theirs", modality="image", task="Detect", labels=["person"])
        other_model = _make_model_version(other_project)
        foreign_binding = _make_binding(other_operation, other_model, other_user)

        payload = json.dumps(
            {
                "operation_id": str(self.operation.id),
                "name": "Wrong pin",
                "line": self.line,
                "model_binding_id": str(foreign_binding.id),
            }
        )
        response = self.client.post("/api/operation-drafts", payload, content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("belongs to this draft's operation", response.json()["error"])

    def test_pinning_a_made_up_binding_id_is_refused(self):
        for bad in ("not-a-uuid", "00000000-0000-0000-0000-000000000000", 42):
            with self.subTest(bad=bad):
                payload = json.dumps(
                    {
                        "operation_id": str(self.operation.id),
                        "name": "Bad pin",
                        "line": self.line,
                        "model_binding_id": bad,
                    }
                )
                response = self.client.post("/api/operation-drafts", payload, content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
