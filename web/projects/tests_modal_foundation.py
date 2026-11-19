"""Wave 0 multimodal foundation: task recipes, modality plugins, generic annotations.

Covers the recipe registry and its API, the plugin registry, image-plugin parity (an upload
through the plugin produces exactly what the old inline validation produced), the generic
AssetAnnotation CRUD against a small fake table plugin registered only in tests, the new
model fields, the training-run task stamping, and the recipe-driven create flow.
"""

from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from . import tasks
from .modalities import get_plugin, register_plugin, unregister_plugin
from .modalities.base import ModalityPlugin
from .models import Asset, AssetAnnotation, ModelVersion, Project, TrainingRun
from .tasks import DEFAULT_RECIPE_KEY, TaskRecipe, get_recipe, recipes, register_recipe, reload_recipes
from .tests import SignedInMixin
from .tests_datasets import DatasetBase


class FakeTablePlugin(ModalityPlugin):
    """A minimal table modality: CSV files, one label per row's target value.

    Registered only inside tests (see the mixin below); it exists to prove the generic
    annotation path works without touching the image flow.
    """

    modality = "table"
    asset_noun = "table"
    annotation_kind = "table_target"

    def validate_upload(self, upload):
        if upload.size <= 0:
            raise ValidationError("Table files cannot be empty.")
        name = (upload.name or "table.csv").rsplit("/", 1)[-1]
        if not name.lower().endswith(".csv"):
            raise ValidationError("Only CSV tables are supported.")
        content = upload.read()
        upload.seek(0)
        import hashlib

        header = content.decode("utf-8").splitlines()[0].split(",") if content else []
        return {
            "filename": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "extension": "csv",
            "content_type": "text/csv",
            "columns": header,
        }

    def validate_annotation(self, payload, project):
        if not isinstance(payload, dict) or set(payload) - {"label", "row"}:
            raise ValidationError("A table annotation needs a label and a row number.")
        label = payload.get("label")
        row = payload.get("row")
        if not isinstance(label, str) or label not in project.labels:
            raise ValidationError("Choose a label defined in this project.")
        if isinstance(row, bool) or not isinstance(row, int) or row < 0:
            raise ValidationError("The row number must be a whole number.")
        return {"label": label, "row": row}


class ModalFoundationBase(DatasetBase):
    """A signed-in user with a media sandbox, plus a fake table plugin registered per test."""

    def setUp(self) -> None:
        super().setUp()
        # Replace the real table plugin for this test, but restore it afterwards: unregistering
        # the modality outright would hide table support from every later test module.
        original = get_plugin("table")
        register_plugin(FakeTablePlugin, replace=True)
        if original is not None:
            self.addCleanup(register_plugin, type(original), replace=True)
        else:
            self.addCleanup(unregister_plugin, "table")

    def make_table_project(self) -> Project:
        return Project.objects.create(
            name="Bearing logs", modality="table", task="Predict failures", labels=["pass", "fail"]
        )

    def make_table_asset(self, project, content=b"temp,vibration,outcome\n31,0.2,pass\n") -> Asset:
        import hashlib
        import io
        import uuid

        from django.core.files.storage import default_storage

        asset_id = uuid.uuid4()
        key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.csv", io.BytesIO(content))
        return Asset.objects.create(
            id=asset_id,
            project=project,
            filename="log.csv",
            storage_key=key,
            content_type="text/csv",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            width=0,
            height=0,
            metadata={"columns": ["temp", "vibration", "outcome"]},
        )


class RecipeRegistryTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def tearDown(self) -> None:
        reload_recipes()  # a test-registered recipe must not leak into other tests

    def test_builtins_are_listed_with_their_availability(self) -> None:
        found = recipes()
        self.assertIn("vision-detection", found)
        self.assertTrue(found["vision-detection"].available)
        # All four Wave 0/1 recipes are available: vision (the original), audio (OS-AUD-001),
        # handwriting OCR (OS-OCR-001) and tabular prediction (OS-TAB-001).
        for key in ("audio-classification", "handwriting-ocr", "tabular-prediction"):
            self.assertIn(key, found)
            self.assertTrue(found[key].available, key)

    def test_get_recipe_returns_none_for_unknown_keys(self) -> None:
        self.assertIsNone(get_recipe("no-such-recipe"))

    def test_register_and_unregister_a_recipe(self) -> None:
        recipe = TaskRecipe(
            key="test-recipe", title="Test", modality="image", summary="s", annotation_kind="boxes",
            labels_help="h",
        )
        register_recipe(recipe)
        self.assertIs(get_recipe("test-recipe"), recipe)
        again = TaskRecipe(
            key="test-recipe", title="Other", modality="image", summary="s", annotation_kind="boxes",
            labels_help="h",
        )
        with self.assertRaises(ValueError):
            register_recipe(again)  # same key twice refused
        tasks.unregister_recipe("test-recipe")
        self.assertIsNone(get_recipe("test-recipe"))

    def test_bad_keys_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            register_recipe(TaskRecipe(key="Not A Key", title="t", modality="image", summary="s",
                                       annotation_kind="boxes", labels_help="h"))

    def test_recipe_dataclass_serializes_every_field(self) -> None:
        data = get_recipe("vision-detection").as_dict()
        self.assertEqual(
            set(data),
            {
                "key", "title", "modality", "summary", "annotation_kind", "labels_help",
                "available", "unavailable_reason",
            },
        )


class RecipesApiTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def test_any_signed_in_role_can_read_the_recipe_list(self) -> None:
        response = self.client.get("/api/recipes")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        keys = {recipe["key"] for recipe in body["recipes"]}
        self.assertIn("vision-detection", keys)
        self.assertIn("audio-classification", keys)
        vision = next(r for r in body["recipes"] if r["key"] == "vision-detection")
        self.assertTrue(vision["available"])
        audio = next(r for r in body["recipes"] if r["key"] == "audio-classification")
        self.assertTrue(audio["available"])  # Wave 1 (OS-AUD-001) landed the audio recipe

    def test_anonymous_callers_are_refused(self) -> None:
        from django.test import Client

        self.assertIn(Client().get("/api/recipes").status_code, (302, 401, 403))


class PluginRegistryTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def test_image_is_registered_as_a_builtin(self) -> None:
        from .modalities import available_modalities, get_plugin
        from .modalities.image import ImageModalityPlugin

        self.assertIn("image", available_modalities())
        self.assertIsInstance(get_plugin("image"), ImageModalityPlugin)

    def test_get_plugin_returns_none_for_unregistered_modalities(self) -> None:
        from .modalities import get_plugin

        self.assertIsNone(get_plugin("speech"))

    def test_register_and_unregister_a_plugin(self) -> None:
        from .modalities import get_plugin, register_plugin, unregister_plugin

        class TestPlugin(FakeTablePlugin):
            modality = "time_series"

        register_plugin(TestPlugin, replace=True)
        self.addCleanup(unregister_plugin, "time_series")
        self.assertIsInstance(get_plugin("time_series"), TestPlugin)

    def test_the_default_annotation_refusal_is_plain_language(self) -> None:
        # A plugin that accepts uploads but has not implemented annotations gets the base's
        # plain refusal (call it unbound, the way a subclass without an override would).
        with self.assertRaises(ValidationError) as raised:
            ModalityPlugin.validate_annotation(FakeTablePlugin(), {}, None)
        self.assertIn("does not have labeling tools yet", raised.exception.messages[0])


class ImagePluginParityTests(DatasetBase):
    """An upload through the plugin must produce exactly what the old inline validation did."""

    def test_validate_upload_returns_the_same_metadata_shape(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile
        from io import BytesIO
        import hashlib
        from PIL import Image

        from .modalities.image import ImageModalityPlugin

        buffer = BytesIO()
        Image.new("RGB", (7, 5), (1, 2, 3)).save(buffer, format="PNG")
        upload = SimpleUploadedFile("part.png", buffer.getvalue(), content_type="image/png")
        plugin = ImageModalityPlugin()
        metadata = plugin.validate_upload(upload)
        self.assertEqual(
            set(metadata),
            {"filename", "sha256", "size_bytes", "width", "height", "extension", "content_type"},
        )
        self.assertEqual(metadata["filename"], "part.png")
        self.assertEqual(metadata["sha256"], hashlib.sha256(buffer.getvalue()).hexdigest())
        self.assertEqual(metadata["size_bytes"], len(buffer.getvalue()))
        self.assertEqual((metadata["width"], metadata["height"]), (7, 5))
        self.assertEqual(metadata["extension"], "png")
        self.assertEqual(metadata["content_type"], "image/png")

    def test_bad_files_are_refused_with_the_same_words(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .modalities.image import ImageModalityPlugin

        plugin = ImageModalityPlugin()
        with self.assertRaises(ValidationError) as raised:
            plugin.validate_upload(SimpleUploadedFile("empty.png", b"", content_type="image/png"))
        self.assertEqual(raised.exception.messages[0], "Image files cannot be empty.")
        with self.assertRaises(ValidationError) as raised:
            plugin.validate_upload(SimpleUploadedFile("note.txt", b"not an image", content_type="text/plain"))
        self.assertEqual(raised.exception.messages[0], "The uploaded file is not a valid supported image.")

    def test_upload_endpoint_still_works_and_stores_no_extra_metadata(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile
        from io import BytesIO
        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (6, 4), (9, 9, 9)).save(buffer, format="PNG")
        upload = SimpleUploadedFile("thing.png", buffer.getvalue(), content_type="image/png")
        response = self.client.post(f"/api/projects/{self.project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 201, response.content)
        asset = response.json()["assets"][0]
        self.assertEqual((asset["width"], asset["height"]), (6, 4))
        self.assertEqual(asset["metadata"], {})
        row = Asset.objects.get(id=asset["id"])
        self.assertEqual(row.metadata, {})


class UnsupportedModalityRefusalsTests(DatasetBase):
    """A data kind with no plugin is refused everywhere, in plain words (the old behavior).

    Audio landed a plugin in Wave 1, so the subject here is an unsupported kind (speech)."""

    def setUp(self) -> None:
        super().setUp()
        self.speech = Project.objects.create(name="Speech", modality="speech", task="Transcribe", labels=["a"])

    def test_unsupported_project_uploads_are_refused_plainly(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("noise.wav", b"RIFF....", content_type="audio/wav")
        response = self.client.post(f"/api/projects/{self.speech.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 400)
        self.assertIn("isn't supported yet", response.json()["error"])

    def test_unsupported_project_assets_cannot_be_listed_or_annotated(self) -> None:
        self.assertGreaterEqual(self.client.get(f"/api/projects/{self.speech.id}/assets").status_code, 400)
        self.assertGreaterEqual(
            self.client.get(
                f"/api/projects/{self.speech.id}/assets/00000000-0000-0000-0000-000000000000/annotations"
            ).status_code,
            400,
        )

    def test_readiness_for_an_unsupported_recipe_is_a_clear_not_available_state(self) -> None:
        response = self.client.get(f"/api/projects/{self.speech.id}/training/readiness")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ready"])
        self.assertTrue(body["blockers"])
        self.assertTrue(body.get("task_unavailable"))

    def test_backend_list_stays_image_only(self) -> None:
        # The backend listing is the image one (unchanged); other modalities get the API's JSON 404,
        # not Django's HTML error page.
        response = self.client.get(f"/api/projects/{self.speech.id}/training/backends")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("error", response.json())


class AssetAnnotationCrudTests(ModalFoundationBase):
    """The generic annotation endpoints, driven by the fake table plugin."""

    def url(self, project, asset, suffix="") -> str:
        return f"/api/projects/{project.id}/assets/{asset.id}/annotations{suffix}"

    def test_upload_stores_plugin_metadata_and_lists_it(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        project = self.make_table_project()
        upload = SimpleUploadedFile("log.csv", b"temp,vibration\n31,0.2\n", content_type="text/csv")
        response = self.client.post(f"/api/projects/{project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 201, response.content)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["metadata"]["columns"], ["temp", "vibration"])
        row = Asset.objects.get(id=asset["id"])
        self.assertEqual(row.metadata["columns"], ["temp", "vibration"])

        listing = self.client.get(f"/api/projects/{project.id}/assets").json()["assets"]
        self.assertEqual(listing[0]["metadata"]["columns"], ["temp", "vibration"])

    def test_a_bad_upload_is_refused_with_the_plugin_message(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        project = self.make_table_project()
        upload = SimpleUploadedFile("log.xlsx", b"binary", content_type="application/vnd.ms-excel")
        response = self.client.post(f"/api/projects/{project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only CSV tables are supported", response.json()["error"])

    def test_annotation_lifecycle_create_list_patch_delete(self) -> None:
        project = self.make_table_project()
        asset = self.make_table_asset(project)

        created = self.client.post(
            self.url(project, asset),
            data=json.dumps({"label": "fail", "row": 3}),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        annotation = created.json()["annotation"]
        self.assertEqual(annotation["kind"], "table_target")
        self.assertEqual(annotation["data"], {"label": "fail", "row": 3})
        self.assertEqual(annotation["status"], "accepted")
        self.assertEqual(annotation["origin"], "manual")

        listed = self.client.get(self.url(project, asset)).json()["annotations"]
        self.assertEqual(len(listed), 1)

        patched = self.client.patch(
            f"/api/projects/{project.id}/asset-annotations/{annotation['id']}",
            data=json.dumps({"label": "pass", "row": 4}),
            content_type="application/json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["annotation"]["data"], {"label": "pass", "row": 4})

        deleted = self.client.delete(f"/api/projects/{project.id}/asset-annotations/{annotation['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(AssetAnnotation.objects.count(), 0)

    def test_annotation_validation_uses_the_plugin_rules(self) -> None:
        project = self.make_table_project()
        asset = self.make_table_asset(project)
        for bad in (
            {"label": "nope", "row": 1}, {"label": "pass", "row": -1},
            {"label": "pass", "row": True}, {"label": "pass"},
        ):
            response = self.client.post(self.url(project, asset), data=json.dumps(bad), content_type="application/json")
            self.assertEqual(response.status_code, 400, bad)
            self.assertTrue(response.json()["error"])
        self.assertEqual(AssetAnnotation.objects.count(), 0)

    def test_model_annotations_can_be_marked_needing_review_but_manual_ones_cannot(self) -> None:
        project = self.make_table_project()
        asset = self.make_table_asset(project)
        manual = AssetAnnotation.objects.create(
            asset=asset, kind="table_target", data={"label": "pass", "row": 1}, origin="manual", created_by=self.user
        )
        response = self.client.patch(
            f"/api/projects/{project.id}/asset-annotations/{manual.id}",
            data=json.dumps({"label": "pass", "row": 2, "status": "suggested"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only model proposals", response.json()["error"])

        model = AssetAnnotation.objects.create(
            asset=asset, kind="table_target", data={"label": "fail", "row": 5}, origin="model", status="suggested",
            confidence=0.7, model_name="table-1",
        )
        accepted = self.client.patch(
            f"/api/projects/{project.id}/asset-annotations/{model.id}",
            data=json.dumps({"label": "fail", "row": 5, "status": "accepted"}),
            content_type="application/json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        row = AssetAnnotation.objects.get(id=model.id)
        self.assertEqual(row.status, "accepted")
        self.assertEqual(row.reviewed_by, self.user)
        self.assertIsNotNone(row.reviewed_at)

    def test_editing_a_suggested_annotation_accepts_it_like_boxes_do(self) -> None:
        project = self.make_table_project()
        asset = self.make_table_asset(project)
        model = AssetAnnotation.objects.create(
            asset=asset, kind="table_target", data={"label": "fail", "row": 5}, origin="model", status="suggested"
        )
        response = self.client.patch(
            f"/api/projects/{project.id}/asset-annotations/{model.id}",
            data=json.dumps({"label": "fail", "row": 6}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(AssetAnnotation.objects.get(id=model.id).status, "accepted")

    def test_annotations_of_one_project_do_not_resolve_through_another(self) -> None:
        project = self.make_table_project()
        other = self.make_table_project()
        asset = self.make_table_asset(project)
        annotation = AssetAnnotation.objects.create(asset=asset, kind="table_target", data={"label": "pass", "row": 1})
        response = self.client.patch(
            f"/api/projects/{other.id}/asset-annotations/{annotation.id}",
            data=json.dumps({"label": "pass", "row": 2}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_image_projects_keep_box_annotations_on_the_same_url(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        response = self.client.get(self.url(self.project, asset))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["annotations"][0]["label"], "damaged")
        # ...and the generic detail endpoint is not theirs:
        box = asset.annotations.get()
        generic = self.client.patch(
            f"/api/projects/{self.project.id}/asset-annotations/{box.id}",
            data=json.dumps({"label": "damaged"}),
            content_type="application/json",
        )
        self.assertEqual(generic.status_code, 404)


class RecipeDrivenCreateFlowTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def post_project(self, payload):
        return self.client.post("/api/projects", data=json.dumps(payload), content_type="application/json")

    def test_creating_with_the_vision_recipe_stores_its_key(self) -> None:
        response = self.post_project(
            {"name": "Vision", "modality": "image", "task": "Spot defects", "labels": ["dent"],
             "recipe": "vision-detection"}
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["project"]
        self.assertEqual(body["recipe"], "vision-detection")
        self.assertEqual(Project.objects.get(id=body["id"]).recipe, "vision-detection")

    def test_image_projects_without_a_recipe_default_to_vision(self) -> None:
        response = self.post_project({"name": "Plain", "modality": "image", "task": "Detect", "labels": ["a"]})
        self.assertEqual(response.status_code, 201)
        body = response.json()["project"]
        self.assertEqual(body["recipe"], DEFAULT_RECIPE_KEY)

    def test_non_image_projects_get_no_recipe_by_default(self) -> None:
        response = self.post_project({"name": "Sound", "modality": "audio", "task": "Classify", "labels": ["a"]})
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["project"]["recipe"])

    def test_an_unknown_recipe_key_is_refused_plainly(self) -> None:
        response = self.post_project(
            {"name": "Odd", "modality": "image", "task": "Detect", "labels": ["a"], "recipe": "not-a-recipe"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("recipe", response.json()["error"])

    def test_a_recipe_for_the_wrong_modality_is_refused(self) -> None:
        response = self.post_project(
            {"name": "Odd", "modality": "audio", "task": "Classify", "labels": ["a"], "recipe": "vision-detection"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("different kind of files", response.json()["error"])

    def test_the_project_list_carries_the_recipe(self) -> None:
        self.post_project(
            {"name": "Vision", "modality": "image", "task": "Spot", "labels": ["a"], "recipe": "vision-detection"}
        )
        listing = self.client.get("/api/projects").json()["projects"]
        self.assertEqual(listing[0]["recipe"], "vision-detection")


class TrainingTaskFieldTests(ModalFoundationBase):
    """Runs carry their recipe's task; existing image runs default to vision."""

    def setUp(self) -> None:
        super().setUp()
        from . import training
        from .tests_training import FakeBackend

        training.register_backend(FakeBackend, replace=True)
        self.addCleanup(training.unregister_backend, FakeBackend.name)
        import tempfile
        from pathlib import Path

        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        override = override_settings(
            DATA_HOME=Path(self.home.name),
            CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        )
        override.enable()
        self.addCleanup(override.disable)

    def _make_run(self, project) -> TrainingRun:
        from .datasets import service as dataset_service, splits
        from .training import lifecycle

        for seed in range(50):
            config = splits.SplitConfig(seed, 0.7, 0.3, 0.0)
            analysis = dataset_service.analyse(project, config)
            if analysis.accepted and dataset_service.preview(analysis)["split_counts"]["train"]["images"] > 0:
                version = dataset_service.create_version(analysis, self.user)
                return lifecycle.create_run(project, self.user, dataset=version, backend_name="fake-backend",
                                            config={"epochs": 1})
        raise AssertionError("no trainable dataset")

    def test_image_runs_default_to_the_vision_recipe(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        run = self._make_run(self.project)
        self.assertEqual(run.task, "vision-detection")
        self.assertEqual(run.annotation_kind, "boxes")

    def test_a_run_from_a_recipe_project_carries_its_task(self) -> None:
        # The fake dataset machinery is image-only, so stamp a table project's recipe and read
        # the fields create_run derives from it (they come from the project, not the dataset).
        from .training.lifecycle import create_run, run_task_fields

        project = self.make_table_project()
        project.recipe = "tabular-prediction"
        project.save(update_fields=["recipe"])
        self.assertEqual(run_task_fields(project), {"task": "tabular-prediction", "annotation_kind": "table_target"})
        # And the same fields land on the TrainingRun when a dataset exists to run on:
        asset = self.make_asset()
        self.make_box(asset)
        run = self._make_run(self.project)
        self.assertEqual(run.task, "vision-detection")

    def test_run_task_fields_follow_the_recipe(self) -> None:
        from .training.lifecycle import run_task_fields

        image = Project.objects.create(name="I", modality="image", task="t", labels=["a"])
        self.assertEqual(run_task_fields(image), {"task": "vision-detection", "annotation_kind": "boxes"})

        with_recipe = Project.objects.create(
            name="R", modality="table", task="t", labels=["a"], recipe="tabular-prediction"
        )
        self.assertEqual(
            run_task_fields(with_recipe), {"task": "tabular-prediction", "annotation_kind": "table_target"}
        )

        unknown = Project.objects.create(name="U", modality="audio", task="t", labels=["a"], recipe="made-up")
        self.assertEqual(run_task_fields(unknown), {})

    def test_registered_model_carries_the_runs_task(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        run = self._make_run(self.project)
        finished = runner_run(run)
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        self.assertEqual(model.task, "vision-detection")


class MigrationFieldTests(ModalFoundationBase):
    def test_asset_metadata_defaults_to_an_empty_dict(self) -> None:
        project = self.make_table_project()
        asset = self.make_table_asset(project)
        self.assertEqual(asset.metadata, {"columns": ["temp", "vibration", "outcome"]})
        fresh = Asset.objects.create(
            project=project, filename="x.csv", storage_key=f"projects/{project.id}/assets/x.csv",
            content_type="text/csv", sha256="0" * 64, size_bytes=1, width=0, height=0,
        )
        self.assertEqual(fresh.metadata, {})

    def test_training_run_and_model_version_task_fields_exist_and_default_to_none(self) -> None:
        from .models import DatasetVersion

        version = DatasetVersion.objects.create(
            project=self.project, version=1, label_names=["damaged"], manifest_sha256="0" * 64
        )
        run = TrainingRun.objects.create(
            project=self.project, dataset_version=version, dataset_manifest_sha256="0" * 64,
            backend="fake", config={}, config_sha256="0" * 64,
        )
        self.assertIsNone(run.task)
        self.assertIsNone(run.annotation_kind)
        model = ModelVersion.objects.create(
            project=self.project, version=1, backend="fake", artifact_path="x", artifact_sha256="0" * 64,
            label_names=[], evaluation={},
        )
        self.assertIsNone(model.task)

    def test_project_recipe_column_exists(self) -> None:
        self.assertIsNone(self.project.recipe)


class ResultsPlainForNonVisionTests(TrainingTaskFieldTests):
    def test_results_plain_answers_generically_for_a_non_box_evaluation(self) -> None:
        # Build a non-vision model directly: ``evaluation`` is write-once on ModelVersion, so a
        # foreign-shaped evaluation is written at creation, the way a real tabular backend would.
        model = ModelVersion.objects.create(
            project=self.project, version=1, backend="fake-table-backend", task="tabular-prediction",
            artifact_path="models/x/model.bin", artifact_sha256="0" * 64, label_names=["pass", "fail"],
            evaluation={"available": True, "rows": 40, "accuracy": 0.9},
        )
        response = self.client.get(f"/api/projects/{self.project.id}/models/{model.id}/results/plain")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body.get("non_vision"))
        self.assertIn("evaluation report", body["overall"]["headline"])

    def test_a_vision_shaped_task_on_a_box_model_still_gets_the_box_answer(self) -> None:
        # The defensive shape check alone (per_label missing) must not hide a box model's results.
        model = ModelVersion.objects.create(
            project=self.project, version=1, backend="fake-backend",
            artifact_path="models/y/model.bin", artifact_sha256="0" * 64, label_names=["damaged"],
            evaluation={"available": False, "reason": "No held-out evaluation exists."},
        )
        response = self.client.get(f"/api/projects/{self.project.id}/models/{model.id}/results/plain")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("non_vision", response.json())

    def test_results_plain_is_unchanged_for_vision_models(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        run = self._make_run(self.project)
        finished = runner_run(run)
        model = finished.model_versions.get()
        response = self.client.get(f"/api/projects/{self.project.id}/models/{model.id}/results/plain")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("non_vision", response.json())


def runner_run(run) -> TrainingRun:
    from .training import runner

    return runner.run_training(runner.claim_next_run())