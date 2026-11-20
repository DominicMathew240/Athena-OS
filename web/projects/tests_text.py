"""The Wave 2 text-classification slice (OS-NLP-001, classification only), end to end.

Covers the text modality plugin (CSV upload validation for the documented ``text,label``
shape, annotation validation), the recipe's availability, the generic snapshot flow on a
real text project, the text-sklearn training backend (availability, dataset layout, a real
small training run on deterministic synthetic texts, the evaluation shape, determinism) and
the run -> candidate -> staged lifecycle through the API. sklearn tests skip cleanly when
scikit-learn is not installed (the tabular tests' pattern).

The synthetic texts are deterministic filler built from fixed word pools. They say NOTHING
about model quality: they only exercise the pipeline. A word-statistics model scoring well
on them means the plumbing works, not that real text will be sorted well - see the design
note (docs/plans/2026-11-19-os-nlp-001-text-classification.md).
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .datasets import generic as generic_service
from .datasets import splits
from .modalities import get_plugin
from .modalities.text import TextModalityPlugin
from .models import Asset, AssetAnnotation, DatasetEntry, DatasetVersion, ModelVersion, Project, TrainingRun
from .tasks import get_recipe
from .tests import SignedInMixin
from .tests_datasets import DatasetBase

SKLEARN_OK = importlib.util.find_spec("sklearn") is not None
REASON = "scikit-learn is needed for text training"

#: Two disjoint word pools: a TF-IDF + logistic regression learner can separate rows built
#: from them, which is enough to check that training and honest evaluation both really run.
WORD_POOLS = {
    "request": ["invoice", "payment", "attached", "please", "find", "statement", "amount", "due", "kindly", "regards"],
    "incident": ["machine", "stopped", "fault", "alarm", "shift", "urgent", "maintenance", "check", "spill", "broken"],
}


def synthetic_texts_csv(rows_per_class: int = 100) -> bytes:
    """A deterministic CSV of synthetic {text, label} rows.

    Each row's text is 6-10 words drawn from its label's pool with a seeded pick pattern -
    deterministic (same bytes every run) but varied enough that TF-IDF sees real vocabulary.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["text", "label"])
    for label, pool in WORD_POOLS.items():
        for i in range(rows_per_class):
            words = [pool[(i * 7 + step * 3) % len(pool)] for step in range(6 + (i % 5))]
            writer.writerow([" ".join(words), label])
    return out.getvalue().encode()


class TextTestBase(DatasetBase):
    """A signed-in user, a media sandbox, an isolated data home and a text project."""

    def setUp(self) -> None:
        super().setUp()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        for override in (
            override_settings(
                DATA_HOME=Path(self.home.name),
                CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
            ),
        ):
            override.enable()
            self.addCleanup(override.disable)
        self.labels = list(WORD_POOLS)
        self.text_project = Project.objects.create(
            name="Sort reports", modality="text", task="Sort text by topic",
            recipe="text-classification", labels=list(self.labels),
        )

    # -- fixtures -----------------------------------------------------------------
    def upload_dataset(self, content: bytes | None = None, filename="texts.csv") -> dict:
        """Upload the text CSV through the API and return the created asset payload."""
        content = content if content is not None else synthetic_texts_csv()
        upload = SimpleUploadedFile(filename, content, content_type="text/csv")
        response = self.client.post(f"/api/projects/{self.text_project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["assets"][0]

    def confirm_labels(self, asset_id=None, label=None) -> dict:
        """The recipe's whole-dataset confirmation annotation (one accepted annotation freezes the file)."""
        asset_id = asset_id or self.asset_id
        response = self.client.post(
            f"/api/projects/{self.text_project.id}/assets/{asset_id}/annotations",
            data=json.dumps({"label": label or self.labels[0]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["annotation"]

    def make_labelled_version(self, rows_per_class: int = 100) -> DatasetVersion:
        """Upload the CSV, confirm its labels, then freeze a generic dataset version."""
        asset = self.upload_dataset(synthetic_texts_csv(rows_per_class))
        self.asset_id = asset["id"]
        self.confirm_labels(asset["id"])
        for seed in range(200):
            analysis = generic_service.analyse(self.text_project, splits.SplitConfig.default())
            if analysis.ready:
                return generic_service.create_version(analysis, self.user)
        raise AssertionError("the text project never became ready")
    def make_run(self, dataset=None) -> TrainingRun:
        from .training import lifecycle

        dataset = dataset or self.make_labelled_version()
        return lifecycle.create_run(
            self.text_project, self.user, dataset=dataset, backend_name="text-sklearn", config={},
        )

    def api(self, suffix: str) -> str:
        return f"/api/projects/{self.text_project.id}{suffix}"

    def post(self, path: str, payload: dict | None = None):
        return self.client.post(path, data=json.dumps(payload or {}), content_type="application/json")


class RecipeAndPluginTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def test_the_text_recipe_is_available(self) -> None:
        recipe = get_recipe("text-classification")
        self.assertIsNotNone(recipe)
        self.assertTrue(recipe.available)
        self.assertEqual(recipe.modality, "text")
        self.assertEqual(recipe.annotation_kind, "text_dataset")

    def test_the_text_plugin_is_registered_as_a_builtin(self) -> None:
        from .modalities import available_modalities

        self.assertIn("text", available_modalities())
        plugin = get_plugin("text")
        self.assertIsInstance(plugin, TextModalityPlugin)
        self.assertEqual(plugin.annotation_kind, "text_dataset")
        body = self.client.get("/api/recipes").json()["recipes"]
        entry = next(item for item in body if item["key"] == "text-classification")
        self.assertTrue(entry["available"])
        self.assertEqual(entry["unavailable_reason"], "")

    def test_the_backend_is_registered_and_described(self) -> None:
        from . import training

        backend = training.get_backend("text-sklearn")
        self.assertEqual(backend.name, "text-sklearn")
        availability = backend.check_available()
        self.assertEqual(availability.ok, SKLEARN_OK, availability.message)
        described = training.describe_backend(backend)
        self.assertIn("scikit-learn", described["license_note"])
        self.assertIn("Classification only", " ".join(described["constraints"]))


class UploadValidationTests(TextTestBase):
    def post_upload(self, filename: str, content: bytes):
        upload = SimpleUploadedFile(filename, content, content_type="application/octet-stream")
        return self.client.post(f"/api/projects/{self.text_project.id}/assets", {"images": upload})

    def test_a_good_upload_stores_the_row_and_label_summary(self) -> None:
        asset = self.upload_dataset(synthetic_texts_csv(rows_per_class=10))
        self.assertEqual(asset["filename"], "texts.csv")
        self.assertEqual(asset["metadata"]["columns"], ["text", "label"])
        self.assertEqual(asset["metadata"]["row_count"], 20)
        self.assertEqual(sorted(asset["metadata"]["label_values"]), sorted(self.labels))
        row = Asset.objects.get(id=asset["id"])
        self.assertEqual(row.content_type, "text/csv")
        self.assertEqual(sorted(row.metadata["label_values"]), sorted(self.labels))

    def test_a_wrong_header_is_refused(self) -> None:
        response = self.post_upload("bad.csv", b"content,category\nsome text, work\n" * 11)
        self.assertEqual(response.status_code, 400)
        self.assertIn("text,label", response.json()["error"])

    def test_a_swapped_column_order_is_refused(self) -> None:
        rows = ["label,text"] + [f"work, some text {i}" for i in range(12)]
        response = self.post_upload("swapped.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("text,label", response.json()["error"])

    def test_a_wrong_extension_is_refused(self) -> None:
        response = self.post_upload("texts.json", b'[{"text": "a", "label": "b"}]')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only CSV text datasets", response.json()["error"])

    def test_a_tab_separated_file_is_refused(self) -> None:
        rows = ["text\tlabel"] + ["some text\twork" for _ in range(12)]
        response = self.post_upload("tabs.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("two columns", response.json()["error"])

    def test_extra_columns_are_refused(self) -> None:
        rows = ["text,label,extra"] + [f"some text,work,{i}" for i in range(12)]
        response = self.post_upload("wide.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("two columns", response.json()["error"])

    def test_a_ragged_body_is_refused(self) -> None:
        rows = ["text,label"] + ["some text,work" for _ in range(11)] + ["one cell"]
        response = self.post_upload("ragged.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("two columns", response.json()["error"])

    def test_a_short_dataset_is_refused(self) -> None:
        rows = ["text,label"] + [f"some text {i},work" for i in range(5)]
        response = self.post_upload("short.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 10 data rows", response.json()["error"])

    def test_empty_text_or_label_cells_are_refused(self) -> None:
        rows = ["text,label"] + ["some text,work"] * 10 + [",work"]
        response = self.post_upload("blank.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("text column", response.json()["error"])
        rows = ["text,label"] + ["some text,work"] * 10 + ["some text,"]
        response = self.post_upload("blank2.csv", ("\n".join(rows) + "\n").encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("label column", response.json()["error"])

    def test_binary_garbage_is_refused_plainly(self) -> None:
        response = self.post_upload("bad.csv", bytes(range(256)))
        self.assertEqual(response.status_code, 400)
        self.assertIn("not readable text", response.json()["error"])

    def test_only_one_text_file_per_upload_request(self) -> None:
        plugin = TextModalityPlugin()
        self.assertEqual(plugin.max_uploads(), 1)


class AnnotationTests(TextTestBase):
    def annotate(self, payload, asset_id=None):
        asset_id = asset_id or self.upload_dataset(synthetic_texts_csv(rows_per_class=10))["id"]
        return self.client.post(
            f"/api/projects/{self.text_project.id}/assets/{asset_id}/annotations",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_the_label_confirmation_is_stored_as_the_annotation(self) -> None:
        response = self.annotate({"label": "incident"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["annotation"]
        self.assertEqual(body["kind"], "text_dataset")
        self.assertEqual(body["data"], {"label": "incident"})
        self.assertEqual(body["status"], "accepted")

    def test_a_label_outside_the_project_list_is_refused(self) -> None:
        response = self.annotate({"label": "no_such_label"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not one of this project's labels", response.json()["error"])

    def test_extra_fields_are_refused(self) -> None:
        response = self.annotate({"label": "request", "target": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirm one label name", response.json()["error"])


class SnapshotTests(TextTestBase):
    def test_one_file_plus_confirmation_freezes_one_entry(self) -> None:
        content = synthetic_texts_csv(rows_per_class=10)
        asset = self.upload_dataset(content)
        self.confirm_labels(asset["id"], label="request")
        response = self.post(self.api("/datasets"))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["dataset"]
        self.assertEqual(body["counts"]["entry_count"], 1)
        self.assertEqual(body["label_names"], self.labels)
        row = DatasetEntry.objects.get()
        self.assertEqual(row.label, "request")  # the confirmed label, via primary_label
        self.assertEqual(row.annotation, {"label": "request"})
        self.assertEqual(row.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(DatasetVersion.objects.get().image_count, 0)

    def test_a_file_without_a_confirmation_is_not_ready(self) -> None:
        self.upload_dataset(synthetic_texts_csv(rows_per_class=10))
        body = self.client.get(self.api("/datasets/readiness")).json()
        self.assertFalse(body["ready"])
        self.assertIn("no accepted labels", " ".join(body["blockers"]))

    def test_the_annotation_kind_must_be_the_text_one(self) -> None:
        asset = self.upload_dataset(synthetic_texts_csv(rows_per_class=10))
        AssetAnnotation.objects.create(asset=Asset.objects.get(id=asset["id"]), kind="transcripts",
                                       data={"label": "request"})
        body = self.client.get(self.api("/datasets/readiness")).json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problems"][0]["code"], "annotation_kind_mismatch")

    def test_the_export_streams_the_text_file(self) -> None:
        content = synthetic_texts_csv(rows_per_class=10)
        asset = self.upload_dataset(content)
        self.confirm_labels(asset["id"], label="request")
        created = self.post(self.api("/datasets")).json()["dataset"]
        response = self.client.get(self.api(f"/datasets/{created['id']}/export"))
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        entry_names = [name for name in archive.namelist() if name.startswith("entries/")]
        self.assertEqual(len(entry_names), 1)
        self.assertEqual(archive.read(entry_names[0]), content)
        self.assertEqual(archive.read("classes.txt").decode().splitlines(), self.labels)


class TrainingRunTests(TextTestBase):
    """The real ML path: export, train, evaluate, register a candidate. Skipped without sklearn."""

    def run_one(self, rows_per_class: int = 100) -> TrainingRun:
        from .training import runner

        run = self.make_run()
        claimed = runner.claim_next_run()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, run.id)
        return runner.run_training(claimed)

    def train_to_candidate(self) -> tuple[TrainingRun, ModelVersion]:
        finished = self.run_one()
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        return finished, finished.model_versions.get()

    def test_build_dataset_writes_the_csv_and_target_json(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        from .training.text_sklearn import TextSklearnBackend

        content = synthetic_texts_csv(rows_per_class=100)
        asset = self.upload_dataset(content)
        self.confirm_labels(asset["id"], label="request")
        analysis = generic_service.analyse(self.text_project, splits.SplitConfig.default())
        self.assertTrue(analysis.ready, analysis.blockers)
        run = self.make_run(dataset=generic_service.create_version(analysis, self.user))
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "dataset"
            items, labels = TextSklearnBackend().build_dataset(run, dest)
            self.assertEqual(labels, self.labels)
            self.assertEqual(len(items), 1)
            texts = dest / "texts.csv"
            self.assertTrue(texts.is_file())
            self.assertEqual(texts.read_bytes(), content)
            target = json.loads((dest / "target.json").read_text())
            self.assertEqual(target, {"label": "request"})

    def test_a_real_training_run_learns_the_synthetic_texts(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        finished, model = self.train_to_candidate()
        self.assertEqual(model.backend, "text-sklearn")
        self.assertEqual(model.task, "text-classification")
        self.assertEqual(model.stage, ModelVersion.CANDIDATE)
        self.assertEqual(sorted(model.label_names), sorted(self.labels))
        self.assertTrue(model.artifact_sha256)

        evaluation = model.evaluation
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["eval_kind"], "text_classification")
        self.assertGreaterEqual(evaluation["accuracy"], 0.5, evaluation)
        self.assertEqual(set(evaluation["per_label"]), set(self.labels))
        for row in evaluation["per_label"].values():
            for key in ("precision", "recall", "f1"):
                self.assertIn(key, row)
        self.assertEqual(evaluation["data"]["texts"], evaluation["texts_correct"] + sum(
            row["wrong"] for row in evaluation["per_label"].values()
        ))
        self.assertIn("never seen", evaluation["summary"])
        self.assertTrue(evaluation["limits"])
        # The run's own metrics carry the training-side picture.
        self.assertEqual(finished.metrics["task_type"], "classification")
        self.assertEqual(finished.metrics["model"], "tfidf-logreg")
        self.assertEqual(finished.metrics["train_rows"], 160)  # 80% of the 200 rows
        # The artifact is a real joblib file under the data home.
        artifact = Path(self.home.name) / model.artifact_path
        self.assertTrue(artifact.is_file())
        self.assertEqual(model.artifact_sha256, hashlib.sha256(artifact.read_bytes()).hexdigest())

    def test_the_same_csv_trains_deterministically(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        _, first_model = self.train_to_candidate()
        second = self.run_one()
        self.assertEqual(second.status, TrainingRun.SUCCEEDED, second.error)
        second_model = second.model_versions.get()
        self.assertEqual(
            first_model.evaluation["accuracy"],
            second_model.evaluation["accuracy"],
        )

    def test_training_reports_how_far_it_got(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        finished, _ = self.train_to_candidate()
        self.assertEqual(finished.progress, 1.0)
        self.assertIn("Reading the text rows", finished.logs)
        self.assertIn("Teaching the computer", finished.logs)

    def test_a_run_with_a_single_class_fails_plainly(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        # One label only: the learner has nothing to tell apart, so the run fails plainly.
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["text", "label"])
        for i in range(20):
            writer.writerow([f"identical filler text number {i}", "request"])
        single = Project.objects.create(
            name="One class", modality="text", task="t", recipe="text-classification", labels=["request"],
        )
        asset_id = uuid.uuid4()
        key = default_storage.save(f"projects/{single.id}/assets/{asset_id}.csv", io.BytesIO(out.getvalue().encode()))
        asset = Asset.objects.create(
            id=asset_id, project=single, filename="one.csv", storage_key=key, content_type="text/csv",
            sha256=hashlib.sha256(out.getvalue().encode()).hexdigest(), size_bytes=len(out.getvalue()),
            width=0, height=0, metadata={"columns": ["text", "label"], "row_count": 20, "label_values": ["request"]},
        )
        AssetAnnotation.objects.create(asset=asset, kind="text_dataset", data={"label": "request"})
        for seed in range(50):
            analysis = generic_service.analyse(single, splits.SplitConfig.default())
            if analysis.ready:
                version = generic_service.create_version(analysis, self.user)
                break
        else:
            raise AssertionError("never ready")
        from .training import lifecycle, runner

        lifecycle.create_run(single, self.user, dataset=version, backend_name="text-sklearn", config={})
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.FAILED)
        self.assertIn("only one distinct value", finished.error)


class LifecycleTests(TextTestBase):
    """The approval gate for a non-vision model, and the training API for text projects."""

    def train_candidate(self) -> ModelVersion:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        from .training import runner

        self.make_run()
        claimed = runner.claim_next_run()
        self.assertIsNotNone(claimed)
        finished = runner.run_training(claimed)
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        return finished.model_versions.get()

    def test_training_api_and_staged_lifecycle(self) -> None:
        model = self.train_candidate()
        listed = self.client.get(self.api("/training/runs")).json()["runs"]
        self.assertEqual(listed[0]["task"], "text-classification")
        self.assertEqual(listed[0]["backend"], "text-sklearn")
        # The evaluation is available, so staging is allowed.
        staged = self.post(self.api(f"/models/{model.id}/stage"), {"note": "Numbers look plausible."})
        self.assertEqual(staged.status_code, 200, staged.content)
        self.assertEqual(staged.json()["model"]["stage"], "staged")
        approved = self.post(self.api(f"/models/{model.id}/approve"), {"note": "Read the report; good enough."})
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["model"]["stage"], "approved")
        model.refresh_from_db()
        self.assertEqual(model.stage, ModelVersion.APPROVED)

    def test_a_candidate_cannot_be_approved_directly(self) -> None:
        model = self.train_candidate()
        response = self.post(self.api(f"/models/{model.id}/approve"), {"note": "skip the gate"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Only a staged model can be approved", response.json()["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, ModelVersion.CANDIDATE)

    def test_models_are_scoped_to_the_project_and_runs_route_only_with_a_plugin(self) -> None:
        model = self.train_candidate()
        # A modality whose plugin and recipe are both absent keeps the old 404s (speech has
        # neither, unlike text which this slice gave a real plugin).
        pluginless = Project.objects.create(name="Raw", modality="speech", task="x", labels=["a"])
        self.assertEqual(self.client.get(f"/api/projects/{pluginless.id}/models").status_code, 404)
        self.assertEqual(self.client.get(f"/api/projects/{pluginless.id}/training/runs").status_code, 404)
        # A text project's models are reachable and scoped: only its own are listed.
        other_text = Project.objects.create(
            name="Other text", modality="text", task="t", recipe="text-classification", labels=["a", "b"]
        )
        self.assertEqual(self.client.get(f"/api/projects/{other_text.id}/models").json()["models"], [])
        listed = self.client.get(self.api("/models")).json()["models"]
        self.assertEqual([item["id"] for item in listed], [str(model.id)])

    def test_model_list_and_results_do_not_crash_on_the_text_shape(self) -> None:
        model = self.train_candidate()
        listed = self.client.get(self.api("/models")).json()["models"]
        self.assertEqual(listed[0]["task"], "text-classification")
        self.assertIn("available", listed[0]["evaluation"])  # the summary degrades, never crashes
        results = self.client.get(self.api(f"/models/{model.id}/results/plain")).json()
        self.assertTrue(results["non_vision"])
        self.assertIn("evaluation report", results["overall"]["headline"])

    def test_config_validation_is_plain(self) -> None:
        from .training import get_backend

        backend = get_backend("text-sklearn")
        self.assertEqual(backend.default_config(), {})
        self.assertEqual(backend.validate_config({}), {})
        with self.assertRaises(ValueError):
            backend.validate_config({"epochs": 5})
