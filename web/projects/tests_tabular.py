"""Wave 1 tabular slice: uploads, target choice, snapshots, training, evaluation.

End-to-end coverage of the spreadsheet recipe (OS-TAB-001) against the Wave 0 foundations:
the table modality plugin (CSV via the standard library, XLSX via a zipfile structure check),
the ``table_target`` annotation (the labels of a table project are its columns), the generic
snapshot path (one asset + one accepted annotation freezes the whole table), the
scikit-learn backend (ported from Forge's ``TabularTrainer``) with real training runs on
deterministic synthetic tables, regression as well as classification, the approval gate for
a non-vision model, and the lean-environment refusals (everything heavy is skipped with a
named reason when scikit-learn/pandas are missing).
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
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone

from .datasets import generic as generic_service
from .datasets import splits
from .models import Asset, AssetAnnotation, DatasetEntry, DatasetVersion, ModelVersion, Project, TrainingRun
from .modalities import get_plugin
from .modalities.table import TableModalityPlugin
from .tasks import get_recipe
from .tests import SignedInMixin
from .tests_datasets import DatasetBase

SKLEARN_OK = importlib.util.find_spec("sklearn") is not None and importlib.util.find_spec("pandas") is not None
OPENPYXL_OK = importlib.util.find_spec("openpyxl") is not None
REASON = "scikit-learn and pandas are needed for table training"


def classification_csv(rows: int = 200) -> bytes:
    """A deterministic, easily learnable table: target = high when x > 0.5."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["x", "noise", "category", "outcome"])
    for i in range(rows):
        x = (i * 37 % 100) / 100.0  # 0.00..0.99 in a shuffled-looking but fixed order
        noise = (i * 13 % 7) / 7.0
        category = "alpha" if i % 3 else "beta"
        writer.writerow([f"{x:.2f}", f"{noise:.3f}", category, "high" if x > 0.5 else "low"])
    return out.getvalue().encode()


def regression_csv(rows: int = 200) -> bytes:
    """A deterministic numeric target: y = 2*a + 1 (plus a small fixed wobble)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["a", "b", "y"])
    for i in range(rows):
        a = (i * 41 % 50) / 10.0
        b = (i * 17 % 5) / 5.0
        writer.writerow([f"{a:.2f}", f"{b:.2f}", f"{2 * a + 1 + (i % 3) * 0.01:.3f}"])
    return out.getvalue().encode()


def minimal_xlsx(rows: int = 40) -> bytes:
    """A real (minimal) XLSX workbook built with the standard library only.

    Enough structure for Excel and pandas to read: [Content_Types].xml, a workbook, one
    sheet with inline-string cells, and the relationships that tie them together."""
    def cell(value: str, is_number: bool) -> str:
        if is_number:
            return f'<c><v>{value}</v></c>'
        return f'<c t="inlineStr"><is><t>{value}</t></is></c>'

    body = io.StringIO()
    body.write('<row><c t="inlineStr"><is><t>x</t></is></c>'
               '<c t="inlineStr"><is><t>category</t></is></c>'
               '<c t="inlineStr"><is><t>outcome</t></is></c></row>')
    for i in range(rows):
        x = (i * 29 % 100) / 100.0
        outcome = "high" if x > 0.5 else "low"
        body.write(f"<row>{cell(f'{x:.2f}', True)}{cell('alpha' if i % 2 else 'beta', False)}"
                   f"{cell(outcome, False)}</row>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{body.getvalue()}</sheetData></worksheet>',
        )
    return buffer.getvalue()


class TabularBase(DatasetBase):
    """A signed-in user, a media sandbox, and a table project whose labels are its columns."""

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
        content = classification_csv()
        self.columns = ["x", "noise", "category", "outcome"]
        self.table_project = Project.objects.create(
            name="Bearing life",
            modality="table",
            task="Predict failures",
            recipe="tabular-prediction",
            labels=list(self.columns),
        )
        self.asset = self.make_table_asset(self.table_project, content=content, filename="bearing.csv")

    def make_table_asset(self, project, content: bytes, filename="table.csv", extension="csv",
                         content_type="text/csv", columns=None) -> Asset:
        asset_id = uuid.uuid4()
        key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.{extension}", io.BytesIO(content))
        metadata = {}
        if columns is not None:
            metadata["columns"] = columns
        return Asset.objects.create(
            id=asset_id,
            project=project,
            filename=filename,
            storage_key=key,
            content_type=content_type,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            width=0,
            height=0,
            metadata=metadata,
        )

    def choose_target(self, asset=None, column="outcome", kind="table_target") -> AssetAnnotation:
        asset = asset if asset is not None else self.asset
        return AssetAnnotation.objects.create(asset=asset, kind=kind, data={"label": column})

    def make_run(self, project=None, dataset=None, config=None) -> TrainingRun:
        project = project or self.table_project
        if dataset is None:
            dataset = self.frozen_version(project)
        return TrainingRun.objects.create(
            project=project,
            dataset_version=dataset,
            dataset_manifest_sha256=dataset.manifest_sha256,
            backend="tabular-sklearn",
            task="tabular-prediction",
            annotation_kind="table_target",
            config=config or {},
            config_sha256="0" * 64,
        )

    def frozen_version(self, project=None) -> DatasetVersion:
        """Freeze the project's table + target choice as the next dataset version."""
        project = project or self.table_project
        for seed in range(50):
            analysis = generic_service.analyse(project, splits.SplitConfig.default())
            if analysis.ready:
                return generic_service.create_version(analysis, self.user)
        raise AssertionError("the table project never became ready")

    def api(self, suffix: str, project=None) -> str:
        return f"/api/projects/{(project or self.table_project).id}{suffix}"

    def post(self, path: str, payload: dict | None = None):
        return self.client.post(path, data=json.dumps(payload or {}), content_type="application/json")


class RecipeAndPluginTests(TabularBase):
    def test_the_recipe_is_available_and_registered_with_the_plugin(self) -> None:
        recipe = get_recipe("tabular-prediction")
        self.assertIsNotNone(recipe)
        self.assertTrue(recipe.available)
        self.assertEqual(recipe.modality, "table")
        self.assertEqual(recipe.annotation_kind, "table_target")
        plugin = get_plugin("table")
        self.assertIsInstance(plugin, TableModalityPlugin)
        self.assertEqual(plugin.annotation_kind, "table_target")
        body = self.client.get("/api/recipes").json()["recipes"]
        entry = next(item for item in body if item["key"] == "tabular-prediction")
        self.assertTrue(entry["available"])
        self.assertEqual(entry["unavailable_reason"], "")

    def test_backend_is_registered_and_described(self) -> None:
        from . import training

        backend = training.get_backend("tabular-sklearn")
        self.assertEqual(backend.name, "tabular-sklearn")
        availability = backend.check_available()
        self.assertEqual(availability.ok, SKLEARN_OK, availability.message)
        described = training.describe_backend(backend)
        self.assertIn("scikit-learn", described["license_note"])


class UploadValidationTests(TabularBase):
    def upload(self, name: str, content: bytes):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile(name, content, content_type="application/octet-stream")
        return self.client.post(self.api("/assets"), {"images": upload})

    def test_a_good_csv_upload_stores_the_column_summary(self) -> None:
        response = self.upload("logs.csv", classification_csv(rows=10))
        self.assertEqual(response.status_code, 201, response.content)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["filename"], "logs.csv")
        self.assertEqual(asset["metadata"]["columns"], ["x", "noise", "category", "outcome"])
        self.assertEqual(asset["metadata"]["row_count"], 10)
        row = Asset.objects.get(id=asset["id"])
        self.assertEqual(row.content_type, "text/csv")
        self.assertEqual(row.metadata["columns"][0], "x")

    def test_a_single_column_csv_is_refused(self) -> None:
        response = self.upload("one.csv", b"only\n1\n2\n3\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 2 columns", response.json()["error"])

    def test_a_short_table_is_refused(self) -> None:
        response = self.upload("short.csv", b"a,b\n1,2\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 3 data rows", response.json()["error"])

    def test_a_ragged_csv_is_refused(self) -> None:
        response = self.upload("ragged.csv", b"a,b,c\n1,2,3\n4,5\n6,7,8\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("same number of columns", response.json()["error"])

    def test_a_wrong_extension_is_refused(self) -> None:
        response = self.upload("notes.txt", b"a,b\n1,2\n2,3\n3,4\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only CSV and Excel", response.json()["error"])

    def test_binary_garbage_is_refused_plainly(self) -> None:
        response = self.upload("bad.csv", bytes(range(256)))
        self.assertEqual(response.status_code, 400)
        self.assertIn("not readable text", response.json()["error"])

    def test_an_xlsx_upload_is_checked_as_a_workbook(self) -> None:
        response = self.upload("sheet.xlsx", minimal_xlsx())
        self.assertEqual(response.status_code, 201, response.content)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["content_type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("read when teaching starts", asset["metadata"]["summary_note"])

    def test_a_zip_that_is_not_a_workbook_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:  # a real zip, but no xl/ structure
            archive.writestr("hello.txt", "not a workbook")
        response = self.upload("renamed.xlsx", buffer.getvalue())
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a readable Excel workbook", response.json()["error"])

    def test_a_non_zip_xlsx_is_refused(self) -> None:
        response = self.upload("fake.xlsx", b"this is not a zip")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a readable Excel workbook", response.json()["error"])

    def test_only_one_table_per_upload_request(self) -> None:
        plugin = TableModalityPlugin()
        self.assertEqual(plugin.max_uploads(), 1)


class AnnotationTests(TabularBase):
    def annotate(self, payload, asset=None):
        asset = asset or self.asset
        return self.post(self.api(f"/assets/{asset.id}/annotations"), payload)

    def test_the_target_choice_is_stored_as_the_annotation(self) -> None:
        response = self.annotate({"label": "outcome"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["annotation"]
        self.assertEqual(body["kind"], "table_target")
        self.assertEqual(body["data"], {"label": "outcome"})
        self.assertEqual(body["status"], "accepted")

    def test_an_unknown_column_is_refused(self) -> None:
        response = self.annotate({"label": "no_such_column"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a column of this project's table", response.json()["error"])

    def test_extra_fields_are_refused(self) -> None:
        response = self.annotate({"label": "outcome", "row": 3})
        self.assertEqual(response.status_code, 400)
        self.assertIn("one target column", response.json()["error"])

    def test_the_annotation_lists_and_is_versioned_by_the_generic_flow(self) -> None:
        self.annotate({"label": "x"})
        listed = self.client.get(self.api(f"/assets/{self.asset.id}/annotations")).json()["annotations"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["data"], {"label": "x"})


class SnapshotTests(TabularBase):
    def test_one_asset_plus_target_choice_freezes_one_entry(self) -> None:
        self.choose_target(column="outcome")
        response = self.post(self.api("/datasets"))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["dataset"]
        self.assertEqual(body["counts"]["entry_count"], 1)
        self.assertEqual(body["label_names"], self.columns)
        row = DatasetEntry.objects.get()
        self.assertEqual(row.label, "outcome")  # the chosen target column, via primary_label
        self.assertEqual(row.annotation, {"label": "outcome"})
        self.assertEqual(row.sha256, self.asset.sha256)
        self.assertEqual(DatasetVersion.objects.get().image_count, 0)

    def test_a_table_without_a_target_choice_is_not_ready(self) -> None:
        body = self.client.get(self.api("/datasets/readiness")).json()
        self.assertFalse(body["ready"])
        self.assertIn("no accepted labels", " ".join(body["blockers"]))

    def test_the_annotation_kind_must_be_the_table_one(self) -> None:
        self.choose_target(column="outcome", kind="transcripts")
        body = self.client.get(self.api("/datasets/readiness")).json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problems"][0]["code"], "annotation_kind_mismatch")

    def test_the_frozen_rows_are_immutable(self) -> None:
        self.choose_target()
        self.frozen_version()
        row = DatasetEntry.objects.get()
        with self.assertRaises(ValueError):
            row.label = "x"
            row.save()

    def test_the_export_streams_the_table_file(self) -> None:
        content = classification_csv()
        self.choose_target()
        created = self.post(self.api("/datasets")).json()["dataset"]
        response = self.client.get(self.api(f"/datasets/{created['id']}/export"))
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        entry_names = [name for name in archive.namelist() if name.startswith("entries/")]
        self.assertEqual(len(entry_names), 1)
        self.assertEqual(archive.read(entry_names[0]), content)
        self.assertEqual(archive.read("classes.txt").decode().splitlines(), self.columns)


@override_settings()
class BackendDatasetTests(TabularBase):
    """build_dataset: the table + target land in the scratch dir in the documented layout."""

    def test_build_dataset_writes_the_table_and_target_json(self) -> None:
        from .training import runner
        from .training.tabular_sklearn import TabularSklearnBackend

        self.choose_target(column="outcome")
        run = self.make_run()
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "dataset"
            items, labels = TabularSklearnBackend().build_dataset(run, dest)
            self.assertEqual(labels, ["outcome"])
            self.assertEqual(len(items), 1)
            table = dest / "table.csv"
            self.assertTrue(table.is_file())
            self.assertEqual(table.read_bytes(), classification_csv())
            target = json.loads((dest / "target.json").read_text())
            self.assertEqual(target, {"target_column": "outcome"})

    def test_build_dataset_refuses_a_version_without_a_target_choice(self) -> None:
        from django.db import connection

        from .training.tabular_sklearn import TabularSklearnBackend

        # Freeze needs a target; blank the frozen annotation afterwards to simulate a version
        # whose recorded choice is missing (a damaged row, not a state the API can produce).
        # The ORM refuses to touch frozen rows, so the damaged row is written in SQL, the same
        # way tests_training simulates a damaged evaluation.
        self.choose_target(column="outcome")
        run = self.make_run()
        with connection.cursor() as cursor:
            # The UUID primary key is stored without hyphens; uuid.hex matches both layouts.
            cursor.execute(
                f"UPDATE {DatasetEntry._meta.db_table} SET annotation = %s WHERE version_id = %s",
                [json.dumps({}), run.dataset_version_id.hex],
            )
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(ValueError) as raised:
                TabularSklearnBackend().build_dataset(run, Path(scratch) / "dataset")
        self.assertIn("no chosen target column", str(raised.exception))

    def test_config_validation(self) -> None:
        from .training.tabular_sklearn import TabularSklearnBackend

        backend = TabularSklearnBackend()
        self.assertEqual(backend.default_config(), {})
        self.assertEqual(backend.validate_config({}), {})
        self.assertEqual(backend.validate_config({"target_column": "outcome", "model": "forest"}),
                         {"target_column": "outcome", "model": "forest"})
        with self.assertRaises(ValueError):
            backend.validate_config({"model": "neural"})
        with self.assertRaises(ValueError):
            backend.validate_config({"epochs": 5})


@override_settings()
class TrainingRunTests(TabularBase):
    """Real training runs: classification, regression, determinism, metrics, cancellation."""

    def run_one(self, config=None, *, project=None) -> TrainingRun:
        """Freeze, queue, claim and run one tabular run; the caller asserts on the outcome."""
        from .training import runner

        run = self.make_run(project=project, config=config)
        claimed = runner.claim_next_run()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, run.id)
        return runner.run_training(claimed)

    def train_to_candidate(self, *, config=None) -> tuple[TrainingRun, ModelVersion]:
        self.choose_target(column="outcome")
        finished = self.run_one(config=config)
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        return finished, finished.model_versions.get()

    def test_classification_training_and_evaluation(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        finished, model = self.train_to_candidate()
        self.assertEqual(model.backend, "tabular-sklearn")
        self.assertEqual(model.task, "tabular-prediction")
        self.assertEqual(model.label_names, ["outcome"])
        self.assertEqual(model.stage, ModelVersion.CANDIDATE)
        evaluation = model.evaluation
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["kind"], "tabular-classification")
        metrics = evaluation["metrics"]
        self.assertEqual(metrics["target_column"], "outcome")
        self.assertEqual(metrics["test_rows"], 20)  # 10% of the 200 held-back rows
        self.assertGreaterEqual(metrics["accuracy"], 0.8, evaluation)
        self.assertIn("high", metrics["per_class"])
        self.assertIn("low", metrics["per_class"])
        self.assertIn("never seen", evaluation["summary"])
        self.assertTrue(evaluation["limits"])
        # The run's own metrics carry the training-side picture.
        self.assertEqual(finished.metrics["task_type"], "classification")
        self.assertEqual(finished.metrics["train_rows"], 160)
        # The artifact is a real joblib file under the data home.
        artifact = Path(self.home.name) / model.artifact_path
        self.assertTrue(artifact.is_file())
        self.assertEqual(model.artifact_sha256, hashlib.sha256(artifact.read_bytes()).hexdigest())

    def test_regression_training_reports_error_distances(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        # A second table (numeric target y = 2a + 1) replaces the default classification one:
        # a tabular project trains on ONE table, so the old asset and its choice go first.
        self.asset.delete()
        self.asset = self.make_table_asset(self.table_project, content=regression_csv(), filename="table.csv")
        self.choose_target(column="y")
        finished = self.run_one()
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        evaluation = model.evaluation
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["kind"], "tabular-regression")
        metrics = evaluation["metrics"]
        self.assertEqual(metrics["target_column"], "y")
        self.assertLess(metrics["mean_absolute_error"], 0.5, evaluation)
        self.assertIsNotNone(metrics["root_mean_squared_error"])
        self.assertIsNotNone(metrics["r2"])
        self.assertGreater(metrics["r2"], 0.9, evaluation)
        self.assertEqual(finished.metrics["task_type"], "regression")

    def test_the_same_table_trains_deterministically(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        _, first_model = self.train_to_candidate()
        second = self.run_one()
        self.assertEqual(second.status, TrainingRun.SUCCEEDED, second.error)
        second_model = second.model_versions.get()
        self.assertEqual(
            first_model.evaluation["metrics"]["accuracy"],
            second_model.evaluation["metrics"]["accuracy"],
        )

    def test_config_target_column_wins_over_the_annotation(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        # Same table, but the run says to predict "category" instead of the frozen "outcome".
        _, model = self.train_to_candidate(config={"target_column": "category"})
        self.assertEqual(model.evaluation["metrics"]["target_column"], "category")

    def test_training_reports_how_far_it_got(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        finished, _ = self.train_to_candidate()
        self.assertEqual(finished.progress, 1.0)
        self.assertIn("Loading the table", finished.logs)
        self.assertIn("Teaching the computer", finished.logs)

    def test_a_missing_target_column_is_a_plain_failure(self) -> None:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        self.choose_target(column="outcome")
        finished = self.run_one(config={"target_column": "not_a_column"})
        self.assertEqual(finished.status, TrainingRun.FAILED)
        self.assertIn("not in this table", finished.error)

    def _xlsx_run(self) -> TrainingRun:
        self.asset.delete()
        self.asset = self.make_table_asset(
            self.table_project, content=minimal_xlsx(), filename="sheet.xlsx",
            extension="xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.choose_target(column="outcome")
        return self.run_one()

    def test_an_xlsx_table_trains_when_openpyxl_exists(self) -> None:
        if not (SKLEARN_OK and OPENPYXL_OK):
            self.skipTest("scikit-learn, pandas and openpyxl are needed for XLSX table training")
        finished = self._xlsx_run()
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        self.assertTrue(model.evaluation["available"], model.evaluation)
        self.assertGreaterEqual(model.evaluation["metrics"]["accuracy"], 0.8, model.evaluation)

    def test_an_xlsx_table_without_openpyxl_fails_with_the_install_message(self) -> None:
        if not (SKLEARN_OK and not OPENPYXL_OK):
            self.skipTest("needs scikit-learn and pandas without openpyxl")
        finished = self._xlsx_run()
        self.assertEqual(finished.status, TrainingRun.FAILED)
        self.assertIn("openpyxl", finished.error)


class LifecycleTests(TabularBase):
    """The approval gate for a non-vision model: stage, evaluation refusal, approve."""

    def train_candidate(self) -> ModelVersion:
        if not SKLEARN_OK:
            self.skipTest(REASON)
        from .training import runner

        self.choose_target(column="outcome")
        run = self.make_run()
        claimed = runner.claim_next_run()
        self.assertIsNotNone(claimed)
        finished = runner.run_training(claimed)
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        return finished.model_versions.get()

    def test_staging_requires_an_evaluation_then_approval_records_who_and_when(self) -> None:
        model = self.train_candidate()
        staged = self.post(self.api(f"/models/{model.id}/stage"), {"note": "Numbers look plausible."})
        self.assertEqual(staged.status_code, 200, staged.content)
        self.assertEqual(staged.json()["model"]["stage"], "staged")

        missing_note = self.post(self.api(f"/models/{model.id}/approve"), {"note": "   "})
        self.assertEqual(missing_note.status_code, 400)
        # The vision-shaped weak-results rule reads a table evaluation as "no boxes found", so
        # approving needs the same explicit acknowledgement a weak vision model needs.
        cautious = self.post(
            self.api(f"/models/{model.id}/approve"),
            {"note": "Read the report; good enough for the pilot."},
        )
        self.assertEqual(cautious.status_code, 409)
        self.assertTrue(cautious.json()["needs_acknowledgement"])
        approved = self.post(
            self.api(f"/models/{model.id}/approve"),
            {"note": "Read the report; good enough for the pilot.", "acknowledge_weak_results": True},
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        body = approved.json()["model"]
        self.assertEqual(body["stage"], "approved")
        self.assertEqual(body["approved_by"], f"u{self.user.pk}")
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
        # A project whose data type has no plugin and no available recipe keeps the old 404s
        # here (speech has neither, unlike audio/table which Wave 1 gave real plugins).
        pluginless = Project.objects.create(name="Raw", modality="speech", task="x", labels=["a"])
        self.assertEqual(self.client.get(self.api("/models", pluginless)).status_code, 404)
        self.assertEqual(self.client.get(self.api("/training/runs", pluginless)).status_code, 404)
        # A table project's models are reachable and scoped: only its own are listed.
        other_table = Project.objects.create(
            name="Other table", modality="table", task="t", recipe="tabular-prediction", labels=["a", "b"]
        )
        self.assertEqual(self.client.get(self.api("/models", other_table)).json()["models"], [])
        listed = self.client.get(self.api("/models")).json()["models"]
        self.assertEqual([item["id"] for item in listed], [str(model.id)])
