"""Training backend interface, evaluation metric, run/model lifecycle and staged approval."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

import numpy as np
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from . import training
from .datasets import service as dataset_service, splits
from .models import (
    DatasetVersion,
    InferenceApp,
    ModelEvent,
    ModelVersion,
    Project,
    TrainingRun,
)
from .tests_datasets import DatasetBase, png_bytes
from .training import evaluation as ev, lifecycle, report, runner, torchvision_frcnn as tv
from .training.base import Availability, BoxPrediction, TrainingBackend, TrainingCancelled, TrainResult

TORCH_INSTALLED = (
    importlib.util.find_spec("torch") is not None and importlib.util.find_spec("torchvision") is not None
)


class FakeBackend(TrainingBackend):
    """Deterministic stand-in: 'trains' instantly and predicts exactly the default test box."""

    name = "fake-backend"
    description = "Test double."
    license_note = "Test only; no third-party code."
    available = True
    boxes = [BoxPrediction("damaged", 0.1, 0.2, 0.3, 0.4, 0.9)]
    fail_with: str | None = None
    cancel_after_start = False

    def check_available(self) -> Availability:
        if type(self).available:
            return Availability(True, "ready")
        return Availability(False, "The fake framework is not installed.", "Install the fake framework.")

    def default_config(self):
        return {"epochs": 1}

    def validate_config(self, config):
        unknown = set(config) - {"epochs"}
        if unknown:
            raise ValueError("Unsupported training setting: " + ", ".join(sorted(unknown)) + ".")
        epochs = config.get("epochs", 1)
        if isinstance(epochs, bool) or not isinstance(epochs, int) or not 1 <= epochs <= 10:
            raise ValueError("epochs must be a whole number between 1 and 10.")
        return {"epochs": epochs}

    def runtime_info(self):
        return {"fake_framework": "1.0", "device": "cpu"}

    def train(self, dataset_dir, config, progress_cb, cancel_cb):
        assert (dataset_dir / "classes.txt").is_file() and (dataset_dir / "data.yaml").is_file()
        if type(self).fail_with:
            raise RuntimeError(type(self).fail_with.replace("{dir}", str(dataset_dir)))
        for step in range(1, 4):
            if cancel_cb():
                raise TrainingCancelled("stop")
            progress_cb(step / 3, f"step {step}")
        artifact = dataset_dir.parent / "model.bin"
        artifact.write_bytes(b"fake-model-weights")
        return TrainResult(artifact, {"loss": 0.1}, [line for line in (dataset_dir / "classes.txt").read_text().splitlines()])

    def predict(self, artifact, image, thresholds):
        return [b for b in type(self).boxes if b.score >= thresholds.get("confidence", 0)]


class TrainingBase(DatasetBase):
    """Project with reviewed boxes, an isolated data home / cache, and the fake backend registered."""

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
        cache.clear()
        FakeBackend.available = True
        FakeBackend.fail_with = None
        FakeBackend.boxes = [BoxPrediction("damaged", 0.1, 0.2, 0.3, 0.4, 0.9)]
        training.register_backend(FakeBackend, replace=True)
        self.addCleanup(training.unregister_backend, FakeBackend.name)

    # -- fixtures -------------------------------------------------------------------
    def make_dataset(self, *, with_test: bool = True, project=None, count: int = 14) -> DatasetVersion:
        project = project or self.project
        for _ in range(count):
            image = Image.new("RGB", (40, 30), (30 + self._counter * 9 % 200, 60, 90))
            ImageDraw.Draw(image).rectangle([4, 6, 13, 17], fill=(250, 250, 40))  # box x.1 y.2 w.3 h.4 of 40x30
            import io

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            asset = self.make_asset(project=project, data=buffer.getvalue())
            self.make_box(asset)
        shares = (0.6, 0.2, 0.2) if with_test else (0.7, 0.3, 0.0)
        for seed in range(200):
            config = splits.SplitConfig(seed, *shares)
            analysis = dataset_service.analyse(project, config)
            counts = dataset_service.preview(analysis)["split_counts"]
            if counts["train"]["images"] > 0 and (counts["test"]["images"] > 0) == with_test:
                return dataset_service.create_version(analysis, self.user)
        raise AssertionError("could not find a split with the wanted shape")

    def post(self, path: str, payload: dict | None = None, client=None):
        return (client or self.client).post(path, data=json.dumps(payload or {}), content_type="application/json")

    def api(self, suffix: str, project=None) -> str:
        return f"/api/projects/{(project or self.project).id}{suffix}"

    def start_run(self, dataset=None, **payload):
        dataset = dataset or self.make_dataset()
        body = {"backend": "fake-backend", "dataset_id": str(dataset.id), **payload}
        return self.post(self.api("/training/runs"), body)

    def train_candidate(self, dataset=None, *, solid: bool | None = None) -> ModelVersion:
        """Queue + run a training run to completion; returns the resulting candidate.

        The approval gate asks for an acknowledgement when the plain verdict is ``not_ready`` or ``cannot_tell``, and
        a fabricated 14-picture run is always ``cannot_tell``. So a candidate asked for with no dataset (the "I just
        need a model I can approve" call) is made *solid*: its tiny evaluation is swapped for one with enough test
        boxes and a high share found (``good_enough_to_try``). Pass a dataset (the test is about the real numbers) or
        ``solid=False`` to keep the real evaluation, ``solid=True`` to force it."""
        if solid is None:
            solid = dataset is None
        response = self.start_run(dataset)
        self.assertEqual(response.status_code, 202, response.content)
        run = runner.claim_next_run()
        finished = runner.run_training(run)
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        return self.set_solid_evaluation(model) if solid else model

    def set_evaluation(self, model: ModelVersion, evaluation: dict) -> ModelVersion:
        """Overwrite a stored evaluation (the ORM refuses to; a damaged or hand-made row is simulated in SQL)."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {ModelVersion._meta.db_table} SET evaluation = %s WHERE id = %s",
                [json.dumps(evaluation), model.pk.hex if hasattr(model.pk, "hex") else model.pk],
            )
        model.refresh_from_db()
        return model

    def set_solid_evaluation(self, model: ModelVersion, boxes_per_label: int = 100, found: int = 95) -> ModelVersion:
        from .tests_results_plain import counts_evaluation

        counts = counts_evaluation({label: (boxes_per_label, found, 2) for label in model.evaluation["per_label"]})
        evaluation = {**model.evaluation, **{key: counts[key] for key in ("per_label", "overall", "data")}}
        model = self.set_evaluation(model, evaluation)
        self.assertEqual(report.plain_results(model.evaluation)["overall"]["verdict"], "good_enough_to_try")
        return model


# ----------------------------------------------------------------------------------------
class EvaluationMetricTests(TrainingBase):
    def test_iou_of_identical_disjoint_and_half_overlapping_boxes(self) -> None:
        a = np.array([[0, 0, 0.5, 0.5]])
        b = np.array([[0, 0, 0.5, 0.5], [0.6, 0.6, 0.2, 0.2], [0.25, 0, 0.5, 0.5]])
        result = ev.iou_matrix(a, b)[0]
        self.assertAlmostEqual(result[0], 1.0)
        self.assertEqual(result[1], 0.0)
        self.assertAlmostEqual(result[2], 1 / 3)  # intersection 0.25*0.5, union 0.5-0.125

    def test_matching_is_greedy_by_score_and_a_ground_truth_box_matches_once(self) -> None:
        gt = [[0.1, 0.1, 0.4, 0.4]]
        flags = ev.match_image([[0.1, 0.1, 0.4, 0.4], [0.11, 0.1, 0.4, 0.4]], [0.6, 0.9], gt)
        self.assertEqual(flags, [(0.9, True), (0.6, False)])  # duplicate detection is a false positive

    def test_prediction_below_iou_threshold_is_a_false_positive(self) -> None:
        flags = ev.match_image([[0.3, 0.3, 0.4, 0.4]], [0.9], [[0.1, 0.1, 0.4, 0.4]])
        self.assertEqual(flags, [(0.9, False)])

    def test_average_precision(self) -> None:
        self.assertEqual(ev.average_precision([(0.9, True), (0.8, True)], 2), 1.0)
        self.assertEqual(ev.average_precision([], 3), 0.0)
        self.assertIsNone(ev.average_precision([(0.9, False)], 0))
        # A wrong top-ranked detection costs precision: hits at ranks 2 and 3 -> 0.5*0.5 + 0.5*(2/3)
        self.assertAlmostEqual(ev.average_precision([(0.9, False), (0.8, True), (0.7, True)], 2), 0.5 * 2 / 3 + 0.5 * 2 / 3)
        # half the ground truth is never found
        self.assertAlmostEqual(ev.average_precision([(0.9, True)], 2), 0.5)

    def test_evaluate_detections_counts_by_hand(self) -> None:
        box = (0.1, 0.1, 0.3, 0.3)
        ground_truth = [[("a", box), ("b", box)], [("a", box)], []]
        predictions = [
            [("a", box, 0.9), ("b", (0.6, 0.6, 0.2, 0.2), 0.8)],  # a hit; b is a miss + a false alarm
            [("a", box, 0.3)],  # below the 0.5 operating point: counts as a miss at that threshold
            [("a", box, 0.95)],  # a detection on an empty image
        ]
        result = ev.evaluate_detections(ground_truth, predictions, ["a", "b"], confidence=0.5)
        a, b = result["per_label"]["a"], result["per_label"]["b"]
        self.assertEqual((a["true_positives"], a["false_positives"], a["false_negatives"]), (1, 1, 1))
        self.assertEqual((a["precision"], a["recall"]), (0.5, 0.5))
        self.assertEqual((b["true_positives"], b["false_positives"], b["false_negatives"]), (0, 1, 1))
        self.assertEqual(b["precision"], 0.0)
        self.assertEqual(result["overall"]["true_positives"], 1)
        self.assertEqual(result["data"], {"images": 3, "boxes": 3, "boxes_per_label": {"a": 2, "b": 1}})
        self.assertTrue(result["available"])
        # AP50 uses every prediction regardless of threshold: a has hits at 0.9 and 0.3 (rank 1 and 3)
        self.assertGreater(a["ap50"], 0)

    def test_undefined_values_are_none_not_zero(self) -> None:
        result = ev.evaluate_detections([[("a", (0, 0, 0.5, 0.5))]], [[]], ["a", "b"])
        self.assertIsNone(result["per_label"]["a"]["precision"])  # no predictions
        self.assertEqual(result["per_label"]["a"]["recall"], 0.0)
        self.assertIsNone(result["per_label"]["b"]["recall"])  # no ground truth
        self.assertIsNone(result["per_label"]["b"]["ap50"])
        self.assertTrue(any("'b'" in line for line in result["limits"]))

    def test_small_samples_are_flagged_in_limits(self) -> None:
        result = ev.evaluate_detections([[("a", (0, 0, 0.5, 0.5))]], [[("a", (0, 0, 0.5, 0.5), 0.9)]], ["a"])
        text = " ".join(result["limits"])
        self.assertIn("Only 1 test image", text)
        self.assertIn("Only 1 test box", text)

    def test_empty_ground_truth_is_not_available(self) -> None:
        self.assertFalse(ev.evaluate_detections([[]], [[]], ["a"])["available"])

    def test_mismatched_inputs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            ev.evaluate_detections([[]], [], ["a"])

    def test_report_text_states_counts_and_limits_and_never_overclaims(self) -> None:
        result = ev.evaluate_detections([[("a", (0, 0, 0.5, 0.5))]], [[("a", (0, 0, 0.5, 0.5), 0.9)]], ["a"])
        built = report.build_report(result)
        self.assertTrue(built["available"])
        self.assertIn("1 held-out test image", built["text"])
        self.assertIn("not used to train", built["text"])
        self.assertIn("Do not read them as accuracy in the field", built["text"])
        self.assertTrue(built["limits"])
        missing = report.build_report(ev.not_available("The test split has no images."))
        self.assertFalse(missing["available"])
        self.assertIn("Evaluation not available", missing["text"])
        self.assertIn("cannot be staged", missing["text"])


# ----------------------------------------------------------------------------------------
class RegistryTests(TrainingBase):
    def test_reference_backend_is_listed_without_importing_torch(self) -> None:
        code = (
            "import os,sys,django;os.environ['DJANGO_SETTINGS_MODULE']='athena_web.settings';django.setup();"
            "from projects import training;b=training.get_backend('torchvision-fasterrcnn');"
            "info=training.describe_backend(b);assert 'torch' not in sys.modules, 'torch imported';"
            "assert info['license_note'];print('ok')"
        )
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env={"DJANGO_DEBUG": "1", "PATH": "/usr/bin", "ATHENA_DATA_HOME": self.home.name},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.stdout.strip(), "ok", result.stderr[-500:])

    def test_register_validates_names_and_refuses_duplicates(self) -> None:
        class Nameless(TrainingBackend):
            name = "Bad Name!"

        with self.assertRaises(ValueError):
            training.register_backend(Nameless)

        class Clash(FakeBackend):
            pass

        with self.assertRaisesMessage(ValueError, "already registered"):
            training.register_backend(Clash)

    def test_entry_point_backends_are_discovered_without_editing_core(self) -> None:
        class Plugin(FakeBackend):
            name = "third-party-plugin"

        entry = mock.Mock()
        entry.name = "plugin"
        entry.load.return_value = Plugin
        with mock.patch("projects.training.metadata.entry_points", return_value=[entry]) as patched:
            training.reload_backends()
            self.addCleanup(training.unregister_backend, "third-party-plugin")
            self.addCleanup(training.reload_backends)
            self.assertIn("third-party-plugin", training.backend_classes())
            patched.assert_called_with(group="athena.training_backends")

    def test_a_broken_plugin_does_not_hide_the_others(self) -> None:
        entry = mock.Mock()
        entry.name = "broken"
        entry.load.side_effect = ImportError("boom")
        with mock.patch("projects.training.metadata.entry_points", return_value=[entry]), self.assertLogs(
            "projects.training", level="ERROR"
        ):
            training.reload_backends()
            self.addCleanup(training.reload_backends)
            self.assertIn("torchvision-fasterrcnn", training.backend_classes())

    def test_unknown_backend_name(self) -> None:
        with self.assertRaisesMessage(ValueError, "No training backend named"):
            training.get_backend("nope")

    def test_reference_backend_config_validation(self) -> None:
        backend = training.get_backend("torchvision-fasterrcnn")
        self.assertEqual(backend.validate_config({})["epochs"], 5)
        for bad in ({"epochs": 0}, {"epochs": True}, {"epochs": 2.5}, {"batch_size": 99}, {"learning_rate": 0},
                    {"learning_rate": "x"}, {"seed": -1}, {"surprise": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                backend.validate_config(bad)
        self.assertIn("BSD", backend.license_note)
        self.assertIn("No pretrained weights", backend.license_note)

    @skipUnless(not TORCH_INSTALLED, "torch is installed here")
    def test_reference_backend_reports_how_to_enable_when_torch_is_missing(self) -> None:
        availability = training.get_backend("torchvision-fasterrcnn").check_available()
        self.assertFalse(availability.ok)
        self.assertIn("requirements-autolabel.txt", availability.how_to_enable)


# ----------------------------------------------------------------------------------------
class AccessTests(TrainingBase):
    def test_every_route_requires_sign_in(self) -> None:
        anonymous = Client()
        rid = uuid.uuid4()
        routes = [
            ("get", "/training/backends"), ("get", "/training/readiness"), ("get", "/training/runs"),
            ("post", "/training/runs"), ("get", f"/training/runs/{rid}"), ("post", f"/training/runs/{rid}/cancel"),
            ("get", "/models"), ("get", f"/models/{rid}"), ("get", f"/models/{rid}/evaluation"),
            ("post", f"/models/{rid}/stage"), ("post", f"/models/{rid}/approve"), ("post", f"/models/{rid}/rollback"),
        ]
        for method, suffix in routes:
            with self.subTest(route=suffix, method=method):
                response = getattr(anonymous, method)(self.api(suffix))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Sign in to continue."})

    def test_routes_404_for_unknown_non_image_and_other_projects(self) -> None:
        dataset = self.make_dataset()
        self.start_run(dataset)
        run = TrainingRun.objects.get()
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged"])
        text = Project.objects.create(name="Docs", modality="text", task="Tag", labels=["a"])
        for project_id in (uuid.uuid4(), text.id):
            for suffix in ("/training/runs", "/training/readiness", "/training/backends", "/models"):
                self.assertEqual(self.client.get(f"/api/projects/{project_id}{suffix}").status_code, 404, suffix)
        self.assertEqual(self.client.get(self.api(f"/training/runs/{run.id}", other)).status_code, 404)
        self.assertEqual(self.post(self.api(f"/training/runs/{run.id}/cancel", other)).status_code, 404)

    def test_other_projects_models_are_unreachable(self) -> None:
        model = self.train_candidate()
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged"])
        for suffix in ("", "/evaluation"):
            self.assertEqual(self.client.get(self.api(f"/models/{model.id}{suffix}", other)).status_code, 404)
        for action in ("stage", "approve", "rollback"):
            self.assertEqual(self.post(self.api(f"/models/{model.id}/{action}", other), {"note": "x"}).status_code, 404)
        self.assertEqual(self.client.get(self.api("/models", other)).json()["models"], [])

    def test_methods_are_restricted(self) -> None:
        model = self.train_candidate()
        self.assertEqual(self.client.delete(self.api("/training/runs")).status_code, 405)
        self.assertEqual(self.client.get(self.api(f"/models/{model.id}/approve")).status_code, 405)
        self.assertEqual(self.client.put(self.api(f"/models/{model.id}")).status_code, 405)

    def test_csrf_is_enforced_for_state_changes(self) -> None:
        model = self.train_candidate()
        response = self.csrf_client().post(
            self.api(f"/models/{model.id}/stage"), data="{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)


# ----------------------------------------------------------------------------------------
class ReadinessAndBackendsTests(TrainingBase):
    def test_backends_endpoint_lists_licence_and_availability(self) -> None:
        body = self.client.get(self.api("/training/backends")).json()
        names = {b["name"]: b for b in body["backends"]}
        self.assertIn("fake-backend", names)
        self.assertEqual(names["fake-backend"]["license_note"], "Test only; no third-party code.")
        self.assertTrue(names["fake-backend"]["available_in_web_process"])
        reference = names["torchvision-fasterrcnn"]
        self.assertIn("BSD-3-Clause", reference["license_note"])
        self.assertTrue(reference["constraints"])
        self.assertEqual(body["default"], "torchvision-fasterrcnn")
        self.assertFalse(body["worker"]["online"])
        FakeBackend.available = False
        unavailable = {b["name"]: b for b in self.client.get(self.api("/training/backends")).json()["backends"]}
        self.assertFalse(unavailable["fake-backend"]["available_in_web_process"])
        self.assertEqual(unavailable["fake-backend"]["availability"]["how_to_enable"], "Install the fake framework.")

    def test_readiness_explains_each_blocker(self) -> None:
        body = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertFalse(body["ready"])
        self.assertIn("no dataset version", body["blockers"][0])
        self.assertIsNone(body["dataset"])

        dataset = self.make_dataset(with_test=False)
        body = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertTrue(body["ready"], body)
        self.assertTrue(any("no held-out test images" in w for w in body["warnings"]))
        self.assertTrue(any("No training worker is running" in w for w in body["warnings"]))
        self.assertEqual(body["dataset"]["version"], dataset.version)

        FakeBackend.available = False
        body = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertFalse(body["ready"])
        self.assertTrue(any("not available" in b and "Install the fake framework" in b for b in body["blockers"]))

        FakeBackend.available = True
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {"fake-backend": True})
        body = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertTrue(body["worker"]["online"])
        self.assertFalse(any("No training worker" in w for w in body["warnings"]))

    def test_worker_reported_availability_counts_when_the_web_process_lacks_the_framework(self) -> None:
        self.make_dataset()
        FakeBackend.available = False
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {"fake-backend": True})
        self.assertEqual(self.start_run().status_code, 202)

    def test_readiness_reports_active_run(self) -> None:
        self.start_run()
        body = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertFalse(body["ready"])
        self.assertIsNotNone(body["active_run_id"])

    def test_unknown_backend_in_readiness_is_a_blocker_not_an_error(self) -> None:
        self.make_dataset()
        body = self.client.get(self.api("/training/readiness?backend=nope")).json()
        self.assertFalse(body["ready"])


# ----------------------------------------------------------------------------------------
class RunCreationTests(TrainingBase):
    def test_creates_a_queued_run_with_immutable_config_and_audit_event(self) -> None:
        dataset = self.make_dataset()
        response = self.start_run(dataset, config={"epochs": 3})
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()["run"]
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["config"], {"epochs": 3})
        self.assertEqual(body["dataset"]["manifest_sha256"], dataset.manifest_sha256)
        self.assertEqual(body["config_sha256"], lifecycle.config_digest({"epochs": 3}))
        event = ModelEvent.objects.get(kind="run_created")
        self.assertEqual((event.actor_id_snapshot, event.to_state), (self.user.pk, "queued"))
        self.assertEqual(str(event.training_run_id), body["id"])

        run = TrainingRun.objects.get()
        run.config = {"epochs": 99}
        with self.assertRaisesMessage(ValueError, "immutable"):
            run.save()
        with self.assertRaisesMessage(ValueError, "immutable"):
            TrainingRun.objects.filter(pk=run.pk).update(config={"epochs": 99})
        with self.assertRaises(ValueError):
            TrainingRun.objects.filter(pk=run.pk).update(dataset_version=None)
        run.refresh_from_db()
        run.logs = "more"  # mutable fields are fine
        run.save()

    def test_defaults_to_latest_dataset_and_default_backend_config(self) -> None:
        dataset = self.make_dataset()
        response = self.post(self.api("/training/runs"), {"backend": "fake-backend"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["run"]["dataset"]["id"], str(dataset.id))

    def test_requires_a_dataset_version(self) -> None:
        response = self.post(self.api("/training/runs"), {"backend": "fake-backend"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("no dataset version", response.json()["error"])

    def test_only_one_active_run_per_project(self) -> None:
        dataset = self.make_dataset()
        self.assertEqual(self.start_run(dataset).status_code, 202)
        second = self.start_run(dataset)
        self.assertEqual(second.status_code, 409)
        self.assertIn("already queued or running", second.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrainingRun.objects.create(
                project=self.project, dataset_version=dataset, backend="fake-backend", config={}, config_sha256="x"
            )

    def test_validation_errors_are_plain_language(self) -> None:
        dataset = self.make_dataset()
        cases = [
            ({"config": {"epochs": 0}}, 400, "epochs must be"),
            ({"config": {"nope": 1}}, 400, "Unsupported training setting"),
            ({"config": []}, 400, "config must be an object"),
            ({"backend": "ghost"}, 400, "No training backend named"),
            ({"backend": 7}, 400, "backend must be"),
            ({"dataset_id": "not-a-uuid"}, 400, "dataset_id is not valid"),
            ({"surprise": 1}, 400, "Unsupported field"),
        ]
        for payload, status, text in cases:
            with self.subTest(payload=payload):
                response = self.start_run(dataset, **payload) if "dataset_id" not in payload else self.post(
                    self.api("/training/runs"), {"backend": "fake-backend", **payload}
                )
                self.assertEqual(response.status_code, status)
                self.assertIn(text, response.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_dataset_from_another_project_is_404(self) -> None:
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged"])
        foreign = self.make_dataset(project=other)
        response = self.post(self.api("/training/runs"), {"backend": "fake-backend", "dataset_id": str(foreign.id)})
        self.assertEqual(response.status_code, 404)

    def test_dataset_without_train_images_is_refused(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        analysis = dataset_service.analyse(self.project, splits.SplitConfig(0, 0.0, 0.5, 0.5))
        dataset = dataset_service.create_version(analysis, self.user)
        response = self.start_run(dataset)
        self.assertEqual(response.status_code, 422)
        self.assertIn("no train images", response.json()["error"])

    def test_unavailable_backend_returns_503_with_how_to_enable(self) -> None:
        dataset = self.make_dataset()
        FakeBackend.available = False
        response = self.start_run(dataset)
        self.assertEqual(response.status_code, 503)
        error = response.json()["error"]
        self.assertIn("fake framework is not installed", error)
        self.assertIn("Install the fake framework", error)
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_json_content_type_and_size_are_enforced(self) -> None:
        self.make_dataset()
        self.assertEqual(self.client.post(self.api("/training/runs"), data="x", content_type="text/plain").status_code, 415)
        self.assertEqual(
            self.client.post(self.api("/training/runs"), data="{", content_type="application/json").status_code, 400
        )
        big = json.dumps({"config": {"x": "a" * 40_000}})
        self.assertEqual(self.client.post(self.api("/training/runs"), data=big, content_type="application/json").status_code, 413)

    def test_list_and_detail(self) -> None:
        self.start_run()
        run = TrainingRun.objects.get()
        listing = self.client.get(self.api("/training/runs")).json()["runs"]
        self.assertEqual([r["id"] for r in listing], [str(run.id)])
        self.assertNotIn("logs", listing[0])
        detail = self.client.get(self.api(f"/training/runs/{run.id}")).json()["run"]
        self.assertEqual(detail["events"][0]["kind"], "run_created")
        self.assertIn("logs", detail)

    def test_cancel_queued_running_and_finished(self) -> None:
        self.start_run()
        run = TrainingRun.objects.get()
        body = self.post(self.api(f"/training/runs/{run.id}/cancel")).json()["run"]
        self.assertEqual(body["status"], "cancelled")
        self.assertTrue(ModelEvent.objects.filter(kind="run_finished", to_state="cancelled").exists())
        again = self.post(self.api(f"/training/runs/{run.id}/cancel"))
        self.assertEqual(again.status_code, 409)
        self.assertIn("nothing to cancel", again.json()["error"])
        # now a new run that a worker has claimed: cancel only raises the flag
        self.start_run()
        running = runner.claim_next_run()
        flagged = self.post(self.api(f"/training/runs/{running.id}/cancel")).json()["run"]
        self.assertEqual((flagged["status"], flagged["cancel_requested"]), ("running", True))


# ----------------------------------------------------------------------------------------
class WorkerTests(TrainingBase):
    def test_successful_run_creates_only_a_candidate_with_evaluation_and_provenance(self) -> None:
        dataset = self.make_dataset()
        self.start_run(dataset)
        run = runner.claim_next_run()
        self.assertEqual(run.status, "running")
        finished = runner.run_training(run)

        self.assertEqual(finished.status, "succeeded", finished.error)
        self.assertEqual(finished.progress, 1.0)
        self.assertEqual(finished.runtime_info["fake_framework"], "1.0")
        self.assertIn("python", finished.runtime_info)
        self.assertNotIn(self.home.name, json.dumps(finished.runtime_info))
        self.assertIn("step 2", finished.logs)
        model = finished.model_versions.get()
        self.assertEqual(model.stage, ModelVersion.CANDIDATE)  # success never goes further
        self.assertEqual(model.version, 1)
        self.assertEqual(model.label_names, ["damaged", "normal"])
        file = Path(self.home.name) / model.artifact_path
        self.assertTrue(file.is_file())
        self.assertEqual(model.artifact_sha256, lifecycle.sha256_file(file))
        self.assertEqual(model.artifact_size_bytes, len(b"fake-model-weights"))
        self.assertTrue(model.evaluation["available"])
        self.assertEqual(model.evaluation["overall"]["recall"], 1.0)
        self.assertEqual(model.evaluation["overall"]["precision"], 1.0)
        self.assertEqual(model.evaluation["dataset"]["manifest_sha256"], dataset.manifest_sha256)
        self.assertEqual(
            model.evaluation["data"]["boxes"],
            model.training_run.dataset_version.manifest["split_counts"]["test"]["boxes"],
        )
        kinds = list(ModelEvent.objects.order_by("id").values_list("kind", flat=True))
        self.assertEqual(kinds, ["run_created", "candidate_created", "run_finished"])
        self.assertFalse((Path(self.home.name) / "training-scratch" / str(run.id)).exists())  # scratch cleaned up
        self.assertEqual(InferenceApp.objects.count(), 0)

    def test_dataset_is_exported_with_test_images_at_the_expected_paths(self) -> None:
        dataset = self.make_dataset()
        self.start_run(dataset)
        run = runner.claim_next_run()
        with tempfile.TemporaryDirectory() as scratch:
            items = runner.export_dataset(run, FakeBackend(), Path(scratch) / "dataset")
            self.assertEqual(len(items), dataset.manifest["split_counts"]["test"]["images"])
            for item in items:
                self.assertTrue(item.path.is_file(), item.path)
                self.assertEqual(item.boxes[0][0], "damaged")

    def test_no_test_split_means_evaluation_not_available_and_staging_refused(self) -> None:
        dataset = self.make_dataset(with_test=False)
        self.start_run(dataset)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded")
        model = finished.model_versions.get()
        self.assertFalse(model.evaluation["available"])
        self.assertIn("test split has no images", model.evaluation["reason"])
        refused = self.post(self.api(f"/models/{model.id}/stage"))
        self.assertEqual(refused.status_code, 422)
        self.assertIn("no held-out evaluation", refused.json()["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "candidate")
        report_body = self.client.get(self.api(f"/models/{model.id}/evaluation")).json()
        self.assertFalse(report_body["report"]["available"])
        self.assertFalse(report_body["can_stage"])
        self.assertIn("Evaluation not available", report_body["report"]["text"])

    def test_backend_failure_is_recorded_without_leaking_paths(self) -> None:
        FakeBackend.fail_with = f"CUDA exploded while reading {{dir}}/x.jpg and {Path.home()}/secret.key"
        self.start_run()
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "failed")
        self.assertIn("CUDA exploded", finished.error)
        self.assertNotIn(self.home.name, finished.error)
        self.assertNotIn(str(Path.home()), finished.error)
        self.assertNotIn(self.home.name, finished.logs)
        self.assertEqual(ModelVersion.objects.count(), 0)
        self.assertTrue(ModelEvent.objects.filter(kind="run_finished", to_state="failed").exists())
        api = self.client.get(self.api(f"/training/runs/{finished.id}")).content.decode()
        self.assertNotIn(self.home.name, api)
        # a failed run frees the project for the next one
        self.assertEqual(self.start_run().status_code, 202)

    def test_cancel_during_training(self) -> None:
        self.start_run()
        run = runner.claim_next_run()
        TrainingRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        finished = runner.run_training(run)
        self.assertEqual(finished.status, "cancelled")
        self.assertEqual(ModelVersion.objects.count(), 0)

    def test_time_limit_fails_the_run(self) -> None:
        self.start_run()
        run = runner.claim_next_run()
        with override_settings(TRAINING={"RUN_TIMEOUT_SECONDS": -1}):
            finished = runner.run_training(run)
        self.assertEqual(finished.status, "failed")
        self.assertIn("time limit", finished.error)

    def test_unavailable_backend_in_worker_fails_with_instructions(self) -> None:
        self.start_run()
        run = runner.claim_next_run()
        FakeBackend.available = False
        finished = runner.run_training(run)
        self.assertEqual(finished.status, "failed")
        self.assertIn("Install the fake framework", finished.error)

    def test_changed_image_on_disk_fails_the_run_not_the_model(self) -> None:
        dataset = self.make_dataset()
        self.start_run(dataset)
        row = dataset.images.first()
        from django.core.files.storage import default_storage

        with default_storage.open(row.storage_key, "wb") as handle:
            handle.write(png_bytes((1, 2, 3)))
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "failed")
        self.assertIn("missing or changed on disk", finished.error)

    def test_stale_and_orphaned_runs_are_failed(self) -> None:
        self.start_run()
        run = runner.claim_next_run()
        self.assertEqual(runner.fail_stale_runs(), 0)  # fresh heartbeat
        TrainingRun.objects.filter(pk=run.pk).update(heartbeat_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(runner.fail_stale_runs(), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("worker stopped", run.error)
        self.start_run()
        runner.claim_next_run()
        self.assertEqual(runner.fail_orphaned_runs(), 1)

    def test_training_logs_are_capped(self) -> None:
        self.start_run()
        run = runner.claim_next_run()
        log = runner.RunLog(run)
        log.limit = 500
        for index in range(200):
            log.add(f"line {index}")
        self.assertLessEqual(len("\n".join(log.lines)), 500)
        self.assertIn("line 199", log.lines[-1])

    def test_train_worker_command_once(self) -> None:
        self.start_run()
        out = StringIO()
        call_command("train_worker", "--once", stdout=out)
        self.assertEqual(TrainingRun.objects.get().status, "succeeded")
        self.assertIn("succeeded", out.getvalue())
        self.assertIn("fake-backend", out.getvalue())
        self.assertTrue(lifecycle.worker_status()["online"])


# ----------------------------------------------------------------------------------------
class StageAndApprovalTests(TrainingBase):
    def stage(self, model, **payload):
        return self.post(self.api(f"/models/{model.id}/stage"), payload)

    def approve(self, model, note="Read the report; numbers are acceptable for a pilot."):
        return self.post(self.api(f"/models/{model.id}/approve"), {"note": note} if note is not None else {})

    def new_candidate(self, *, solid: bool = True) -> ModelVersion:
        return self.train_candidate(solid=solid)

    def test_candidate_cannot_be_approved_directly(self) -> None:
        model = self.new_candidate()
        response = self.approve(model)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Only a staged model can be approved", response.json()["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "candidate")
        self.assertFalse(ModelEvent.objects.filter(kind="approved").exists())

    def test_staging_then_approval_with_note_records_who_and_when(self) -> None:
        model = self.new_candidate()
        staged = self.stage(model, note="Looks reasonable")
        self.assertEqual(staged.status_code, 200)
        self.assertEqual(staged.json()["model"]["stage"], "staged")
        self.assertEqual(self.stage(model).status_code, 409)  # not repeatable

        missing = self.approve(model, note=None)
        self.assertEqual(missing.status_code, 400)
        self.assertIn("approval note is required", missing.json()["error"])
        self.assertEqual(self.approve(model, note="   ").status_code, 400)
        self.assertEqual(self.approve(model, note="x" * 3000).status_code, 400)
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")

        response = self.approve(model)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["model"]
        self.assertEqual((body["stage"], body["approved_by"]), ("approved", f"u{self.user.pk}"))
        self.assertIn("pilot", body["approval_note"])
        self.assertIsNone(response.json()["retired_model_id"])
        events = [(e.kind, e.from_state, e.to_state, e.actor_id_snapshot) for e in ModelEvent.objects.filter(model_version=model)]
        self.assertIn(("staged", "candidate", "staged", self.user.pk), events)
        self.assertIn(("approved", "staged", "approved", self.user.pk), events)

    def weak_staged_model(self) -> ModelVersion:
        FakeBackend.boxes = []  # predicts nothing, so it finds none of the held-out boxes
        model = self.new_candidate(solid=False)
        self.assertEqual(model.evaluation["overall"]["recall"], 0.0)
        self.assertEqual(self.stage(model).status_code, 200)
        return model

    def test_a_model_that_found_none_of_the_test_boxes_needs_an_explicit_acknowledgement(self) -> None:
        model = self.weak_staged_model()
        response = self.approve(model)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertTrue(body["needs_acknowledgement"])
        self.assertIn("found none", body["error"])
        self.assertIn("acknowledge_weak_results", body["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")  # nothing changed
        self.assertFalse(ModelEvent.objects.filter(kind="approved").exists())
        # a falsy or non-boolean flag is not an acknowledgement
        self.assertEqual(self.post(self.api(f"/models/{model.id}/approve"), {"note": "ok", "acknowledge_weak_results": False}).status_code, 409)
        self.assertEqual(self.post(self.api(f"/models/{model.id}/approve"), {"note": "ok", "acknowledge_weak_results": "yes"}).status_code, 400)

    def test_acknowledged_weak_approval_works_and_is_audited_in_the_event_note(self) -> None:
        model = self.weak_staged_model()
        response = self.post(
            self.api(f"/models/{model.id}/approve"), {"note": "Only for a dry run.", "acknowledge_weak_results": True}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["model"]["stage"], "approved")
        event = ModelEvent.objects.get(model_version=model, kind="approved")
        self.assertIn("Only for a dry run.", event.note)
        self.assertIn("acknowledged", event.note.lower())
        self.assertIn("none of the", event.note)
        self.assertEqual(event.actor_id_snapshot, self.user.pk)

    def test_a_model_whose_evaluation_is_not_available_also_needs_the_acknowledgement(self) -> None:
        model = self.new_candidate()
        self.assertEqual(self.stage(model).status_code, 200)
        from django.db import connection

        with connection.cursor() as cursor:  # the ORM refuses to rewrite evaluations; simulate a damaged row
            cursor.execute(
                f"UPDATE {ModelVersion._meta.db_table} SET evaluation = %s WHERE id = %s",
                [json.dumps({"available": False, "reason": "No test images."}), model.pk.hex if hasattr(model.pk, "hex") else model.pk],
            )
        model.refresh_from_db()
        self.assertFalse(model.evaluation["available"])
        refused = self.approve(model)
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.json()["needs_acknowledgement"])
        self.assertIn("no results", refused.json()["error"])
        ok = self.post(self.api(f"/models/{model.id}/approve"), {"note": "Pilot.", "acknowledge_weak_results": True})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertIn("no results", ModelEvent.objects.get(model_version=model, kind="approved").note)

    # -- thin or weak verdicts need the acknowledgement too (final integration review I3) -----------------
    def staged_with(self, counts: dict[str, tuple[int, int, int]]) -> ModelVersion:
        from .tests_results_plain import counts_evaluation

        model = self.new_candidate(solid=False)
        self.assertEqual(self.stage(model).status_code, 200)
        fresh = counts_evaluation(counts)
        return self.set_evaluation(model, {**model.evaluation, **{k: fresh[k] for k in ("per_label", "overall", "data")}})

    def test_the_reason_helper_fires_for_not_ready_and_cannot_tell_but_not_for_good_or_needs_more_examples(self) -> None:
        from .tests_results_plain import counts_evaluation

        def reason(counts):
            evaluation = counts_evaluation(counts)
            return report.plain_results(evaluation)["overall"]["verdict"], lifecycle.weak_results_reason(evaluation)

        self.assertEqual(reason({"d": (100, 95, 2)}), ("good_enough_to_try", None))
        self.assertEqual(reason({"d": (100, 70, 2)}), ("needs_more_examples", None))  # decided, mediocre: note is enough
        verdict, thin = reason({"d": (10, 9, 0)})
        self.assertEqual(verdict, "cannot_tell")
        verdict, weak = reason({"d": (100, 40, 0)})
        self.assertEqual(verdict, "not_ready")
        for text in (thin, weak):
            self.assertIn("too thin or too weak", text)
            self.assertNotIn("Not a final decision", text)
            self.assertFalse(text.endswith("."))  # it is spliced into a longer sentence
        self.assertIn("Cannot tell yet", thin)
        self.assertIn("Not ready", weak)
        # recall 0 keeps its own, older wording
        self.assertIn("found none", reason({"d": (100, 0, 0)})[1])

    def test_a_thin_result_with_some_boxes_found_needs_the_acknowledgement(self) -> None:
        model = self.staged_with({"d": (10, 9, 0)})  # fewer than 30 test boxes: cannot tell
        self.assertGreater(model.evaluation["overall"]["recall"], 0)
        response = self.approve(model)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(set(body), {"error", "needs_acknowledgement", "weak_results"})
        self.assertTrue(body["needs_acknowledgement"])
        self.assertIn("too thin or too weak", body["weak_results"])
        self.assertIn("Cannot tell yet", body["weak_results"])
        self.assertIn(body["weak_results"], body["error"])
        self.assertIn("acknowledge_weak_results", body["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")
        self.assertEqual(
            self.post(self.api(f"/models/{model.id}/approve"), {"note": "ok", "acknowledge_weak_results": "yes"}).status_code, 400
        )
        self.assertFalse(ModelEvent.objects.filter(kind="approved").exists())

    def test_a_weak_result_with_some_boxes_found_needs_the_acknowledgement_and_it_is_audited(self) -> None:
        model = self.staged_with({"d": (100, 40, 3)})  # enough boxes, under half found: not ready
        self.assertEqual(self.approve(model).status_code, 409)
        ok = self.post(self.api(f"/models/{model.id}/approve"), {"note": "Pilot only.", "acknowledge_weak_results": True})
        self.assertEqual(ok.status_code, 200, ok.content)
        note = ModelEvent.objects.get(model_version=model, kind="approved").note
        self.assertIn("Pilot only.", note)
        self.assertIn("acknowledged", note.lower())
        self.assertIn("too thin or too weak", note)

    def test_needs_more_examples_is_approved_with_the_note_alone(self) -> None:
        model = self.staged_with({"d": (100, 70, 2)})
        self.assertEqual(report.plain_results(model.evaluation)["overall"]["verdict"], "needs_more_examples")
        self.assertEqual(self.approve(model).status_code, 200)
        self.assertNotIn("acknowledged", ModelEvent.objects.get(model_version=model, kind="approved").note.lower())

    # -- a fair "worse than the model in use" comparison needs the acknowledgement too (W12-G2b / RED7) -------------
    def staged_with_comparison(self, *, baseline_found: int = 190, b: int = 0, c: int = 20, fair: bool = True,
                               overlap: int = 0) -> ModelVersion:
        """A staged model whose own results read ``good_enough_to_try`` (170 of 200 found) and that carries a stored,
        hand-made comparison block: the model in use found ``baseline_found`` of the same 200 boxes."""
        from .tests_comparison import make_block
        from .tests_results_plain import counts_evaluation

        model = self.new_candidate(solid=False)
        self.assertEqual(self.stage(model).status_code, 200)
        fresh = counts_evaluation({"damaged": (200, 170, 2)})
        evaluation = {**model.evaluation, **{k: fresh[k] for k in ("per_label", "overall", "data")}}
        evaluation["comparison"] = make_block(
            fresh, baseline_found={"damaged": baseline_found}, baseline_wrong=2, b=b, c=c, fair=fair, overlap=overlap
        )
        model = self.set_evaluation(model, evaluation)
        self.assertEqual(report.plain_results(model.evaluation)["overall"]["verdict"], "good_enough_to_try")
        return model

    def test_a_fair_worse_comparison_needs_the_acknowledgement_even_when_the_results_read_good(self) -> None:
        model = self.staged_with_comparison()
        plain = report.plain_results(model.evaluation)
        self.assertEqual((plain["comparison"]["available"], plain["comparison"]["fair"], plain["comparison"]["verdict"]),
                         (True, True, "worse"))
        response = self.approve(model)
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(set(body), {"error", "needs_acknowledgement", "weak_results"})
        self.assertTrue(body["needs_acknowledgement"])
        self.assertTrue(
            body["weak_results"].startswith(
                "the new model did worse than the model in use on the same test pictures (Worse on these test pictures: "
            ),
            body["weak_results"],
        )
        self.assertIn("found 170 of 200 test boxes", body["weak_results"])
        self.assertNotIn("Not a final decision", body["weak_results"])
        self.assertTrue(body["weak_results"].endswith(")"))
        self.assertIn(body["weak_results"], body["error"])
        self.assertIn("acknowledge_weak_results", body["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")  # nothing changed
        self.assertFalse(ModelEvent.objects.filter(kind="approved").exists())
        url = self.api(f"/models/{model.id}/approve")
        self.assertEqual(self.post(url, {"note": "ok", "acknowledge_weak_results": False}).status_code, 409)
        self.assertEqual(self.post(url, {"note": "ok", "acknowledge_weak_results": "yes"}).status_code, 400)
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")

    def test_the_worse_acknowledgement_works_and_is_audited_like_the_other_weak_cases(self) -> None:
        model = self.staged_with_comparison()
        ok = self.post(self.api(f"/models/{model.id}/approve"), {"note": "Needed for a trial.", "acknowledge_weak_results": True})
        self.assertEqual(ok.status_code, 200, ok.content)
        note = ModelEvent.objects.get(model_version=model, kind="approved").note
        self.assertIn("Needed for a trial.", note)
        self.assertIn("[Weak results acknowledged by the approver: the new model did worse than the model in use", note)
        self.assertIn("Approved anyway.]", note)

    def test_other_comparison_verdicts_add_no_friction(self) -> None:
        for name, kwargs in (
            ("better", {"baseline_found": 150, "b": 20, "c": 0}),
            ("about_the_same", {"baseline_found": 168, "b": 2, "c": 0}),
            ("cannot_tell (unfair)", {"fair": False, "overlap": 5}),  # a worse-looking count, but not a fair test
        ):
            with self.subTest(name):
                model = self.staged_with_comparison(**kwargs)
                comparison = report.plain_results(model.evaluation)["comparison"]
                self.assertTrue(comparison["available"])
                self.assertNotEqual(comparison["verdict"], "worse")
                self.assertIsNone(lifecycle.weak_results_reason(model.evaluation))
                self.assertEqual(self.approve(model).status_code, 200)

    def test_an_unfair_comparison_with_fewer_boxes_found_is_not_a_worse_verdict_and_adds_no_gate(self) -> None:
        model = self.staged_with_comparison(fair=False, overlap=5, baseline_found=190, b=0, c=20)
        block = report.plain_results(model.evaluation)["comparison"]
        self.assertEqual((block["fair"], block["verdict"]), (False, "cannot_tell"))
        self.assertIsNone(lifecycle.weak_results_reason(model.evaluation))

    def test_a_damaged_or_lying_comparison_block_neither_triggers_nor_hides_the_rule(self) -> None:
        model = self.staged_with_comparison()
        worse = lifecycle.weak_results_reason(model.evaluation)
        self.assertIn("did worse", worse)
        # damaged: the counts no longer add up, so the reader refuses the block; the rule does not fire
        damaged = json.loads(json.dumps(model.evaluation))
        damaged["comparison"]["candidate_result"]["found"] += 1
        self.assertFalse(report.plain_results(damaged)["comparison"]["available"])
        self.assertIsNone(lifecycle.weak_results_reason(damaged))
        for junk in ("worse", 5, [], {"schema": "athena.comparison/1", "available": True}):
            self.assertIsNone(lifecycle.weak_results_reason({**model.evaluation, "comparison": junk}), junk)
        self.assertIsNone(lifecycle.weak_results_reason({k: v for k, v in model.evaluation.items() if k != "comparison"}))
        # a stored word is not trusted: a "better" block that claims "worse" does not trigger it
        better = self.staged_with_comparison(baseline_found=150, b=20, c=0)
        lying = json.loads(json.dumps(better.evaluation))
        lying["comparison"]["verdict"] = "worse"
        self.assertEqual(report.plain_results(lying)["comparison"]["verdict"], "better")
        self.assertIsNone(lifecycle.weak_results_reason(lying))
        # ... and a "worse" block that claims "better" does not hide it
        hiding = json.loads(json.dumps(model.evaluation))
        hiding["comparison"]["verdict"] = "better"
        self.assertEqual(lifecycle.weak_results_reason(hiding), worse)
        # the model itself, with the damaged block, is approved with the note alone
        self.set_evaluation(model, damaged)
        self.assertEqual(self.approve(model).status_code, 200)

    def test_a_good_model_needs_no_acknowledgement_and_gets_no_extra_audit_text(self) -> None:
        model = self.new_candidate()
        self.stage(model)
        self.assertEqual(self.approve(model).status_code, 200)
        self.assertNotIn("acknowledged", ModelEvent.objects.get(model_version=model, kind="approved").note.lower())

    def test_approving_a_second_model_retires_the_first_and_keeps_a_rollback_pointer(self) -> None:
        first = self.new_candidate()
        self.stage(first)
        self.approve(first)
        second = self.new_candidate()
        self.stage(second)
        response = self.approve(second, note="Better on the test set.")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["retired_model_id"], str(first.id))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.stage, second.stage), ("retired", "approved"))
        self.assertEqual(second.previous_approved_id, first.id)
        self.assertIsNotNone(first.retired_at)
        self.assertEqual(ModelVersion.objects.filter(stage="approved").count(), 1)
        retired = ModelEvent.objects.get(model_version=first, kind="retired")
        self.assertIn(f"v{second.version}", retired.note)

    def test_database_refuses_two_approved_models(self) -> None:
        first = self.new_candidate()
        self.stage(first)
        self.approve(first)
        second = self.new_candidate()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModelVersion.objects.filter(pk=second.pk).update(stage="approved")

    def test_rollback_restores_the_previous_model(self) -> None:
        first = self.new_candidate()
        self.stage(first)
        self.approve(first)
        second = self.new_candidate()
        self.stage(second)
        self.approve(second)

        no_note = self.post(self.api(f"/models/{second.id}/rollback"), {})
        self.assertEqual(no_note.status_code, 400)
        response = self.post(self.api(f"/models/{second.id}/rollback"), {"note": "False alarms on the night shift."})
        self.assertEqual(response.status_code, 200, response.content)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.stage, second.stage), ("approved", "retired"))
        self.assertEqual(response.json()["model"]["id"], str(first.id))
        kinds = set(ModelEvent.objects.values_list("kind", flat=True))
        self.assertTrue({"rollback_retired", "rollback_restored"} <= kinds)
        # rolled-back model cannot be rolled back again (it is retired)
        self.assertEqual(self.post(self.api(f"/models/{second.id}/rollback"), {"note": "again"}).status_code, 409)

    def test_rollback_without_a_previous_model_is_refused(self) -> None:
        only = self.new_candidate()
        self.stage(only)
        self.approve(only)
        response = self.post(self.api(f"/models/{only.id}/rollback"), {"note": "undo"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("no earlier approved model", response.json()["error"])
        only.refresh_from_db()
        self.assertEqual(only.stage, "approved")

    def test_rollback_of_a_non_approved_model_is_refused(self) -> None:
        model = self.new_candidate()
        self.assertEqual(self.post(self.api(f"/models/{model.id}/rollback"), {"note": "x"}).status_code, 409)

    def test_missing_or_tampered_artifact_blocks_approval(self) -> None:
        model = self.new_candidate()
        self.stage(model)
        path = Path(self.home.name) / model.artifact_path
        path.write_bytes(b"tampered")
        response = self.approve(model)
        self.assertEqual(response.status_code, 409)
        self.assertIn("no longer matches the checksum", response.json()["error"])
        path.unlink()
        self.assertIn("missing from storage", self.approve(model).json()["error"])
        model.refresh_from_db()
        self.assertEqual(model.stage, "staged")

    def test_model_defining_fields_are_immutable(self) -> None:
        model = self.new_candidate()
        for field, value in (("artifact_sha256", "0" * 64), ("evaluation", {}), ("label_names", ["x"]), ("artifact_path", "other")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    ModelVersion.objects.filter(pk=model.pk).update(**{field: value})
                setattr(model, field, value)
                with self.assertRaisesMessage(ValueError, "immutable"):
                    model.save()
                model.refresh_from_db()

    def test_audit_events_are_append_only(self) -> None:
        self.new_candidate()
        event = ModelEvent.objects.first()
        event.note = "edited"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        with self.assertRaises(ValueError):
            ModelEvent.objects.all().update(note="x")
        with self.assertRaises(ValueError):
            ModelEvent.objects.all().delete()
        with self.assertRaises(ValueError):
            ModelEvent.objects.all()._raw_delete(using="default")
        with self.assertRaises(ValueError):
            ModelEvent.objects.bulk_create([event], update_conflicts=True, update_fields=["note"], unique_fields=["id"])
        self.assertNotEqual(ModelEvent.objects.get(pk=event.pk).note, "edited")

    def test_model_list_detail_and_evaluation_report(self) -> None:
        model = self.new_candidate()
        listing = self.client.get(self.api("/models")).json()
        self.assertEqual(listing["models"][0]["id"], str(model.id))
        self.assertIsNone(listing["approved_model_id"])
        detail = self.client.get(self.api(f"/models/{model.id}")).json()["model"]
        self.assertEqual(detail["lineage"]["origin"], "training_run")
        self.assertEqual(detail["lineage"]["dataset_manifest_sha256"], model.training_run.dataset_manifest_sha256)
        self.assertEqual(detail["lineage"]["config_sha256"], model.training_run.config_sha256)
        self.assertIn("candidate_created", [e["kind"] for e in detail["events"]])
        self.assertEqual(detail["artifact_sha256"], model.artifact_sha256)
        self.assertNotIn("artifact_path", detail)

        body = self.client.get(self.api(f"/models/{model.id}/evaluation")).json()
        self.assertTrue(body["report"]["available"])
        self.assertTrue(body["can_stage"])
        text = body["report"]["text"]
        self.assertIn("held-out test image", text)
        self.assertIn("damaged:", text)
        self.assertEqual(body["report"]["numbers"]["data"]["images"], model.evaluation["data"]["images"])
        self.assertTrue(body["report"]["limits"])  # tiny test set -> limits are stated
        self.assertEqual(body["model"]["artifact_sha256"], model.artifact_sha256)

    def test_no_response_leaks_server_paths(self) -> None:
        model = self.new_candidate()
        self.stage(model)
        self.approve(model)
        for suffix in ("/models", f"/models/{model.id}", f"/models/{model.id}/evaluation", "/training/runs",
                       "/training/backends", "/training/readiness"):
            text = self.client.get(self.api(suffix)).content.decode()
            self.assertNotIn(self.home.name, text, suffix)
            self.assertNotIn(self.media.name, text, suffix)
            self.assertNotIn("model.bin", text, suffix)

    def test_deleting_a_project_removes_its_runs_and_models(self) -> None:
        self.new_candidate()
        self.project.delete()
        self.assertEqual((TrainingRun.objects.count(), ModelVersion.objects.count()), (0, 0))


# ----------------------------------------------------------------------------------------
@skipUnless(TORCH_INSTALLED, "torch/torchvision are not installed here; run in the ML venv (see DEPLOYMENT.md)")
class TorchvisionReferenceBackendTests(TrainingBase):
    def test_trains_evaluates_and_predicts_on_cpu_without_downloading_weights(self) -> None:
        import torch

        dataset = self.make_dataset(count=16)
        backend = training.get_backend("torchvision-fasterrcnn")
        with mock.patch("torch.hub.load_state_dict_from_url", side_effect=AssertionError("network used")):
            response = self.post(
                self.api("/training/runs"),
                {"backend": "torchvision-fasterrcnn", "dataset_id": str(dataset.id),
                 "config": {"epochs": 1, "batch_size": 4, "seed": 3}},
            )
            self.assertEqual(response.status_code, 202, response.content)
            finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded", finished.error)
        self.assertEqual(finished.runtime_info["device"], "cpu")
        self.assertEqual(finished.runtime_info["torch"], torch.__version__)
        model = finished.model_versions.get()
        self.assertEqual(model.stage, "candidate")
        self.assertEqual(model.backend, "torchvision-fasterrcnn")
        self.assertEqual(len(finished.metrics["epochs"]), 1)
        self.assertTrue(model.evaluation["available"])  # a real number, whatever it is, from held-out images
        self.assertIsNotNone(model.evaluation["overall"]["true_positives"])

        artifact = lifecycle.verify_artifact(model)
        with Image.open(self.make_asset_path()) as image:
            found = backend.predict(artifact, image, {"confidence": 0.0, "max_boxes": 5})
        self.assertLessEqual(len(found), 5)
        for box in found:
            self.assertIn(box.label, ["damaged", "normal"])
            self.assertTrue(0 <= box.x <= 1 and 0 < box.width <= 1 and 0 <= box.score <= 1)

    def make_asset_path(self):
        from django.core.files.storage import default_storage

        asset = self.project.assets.first()
        return default_storage.path(asset.storage_key)

    def test_cancel_stops_training(self) -> None:
        dataset = self.make_dataset(count=12)
        self.post(self.api("/training/runs"), {"backend": "torchvision-fasterrcnn", "dataset_id": str(dataset.id),
                                               "config": {"epochs": 2}})
        run = runner.claim_next_run()
        TrainingRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        self.assertEqual(runner.run_training(run).status, "cancelled")


class RedirectRestrictionTests(SimpleTestCase):
    """Downloads follow redirects only to the pinned hosts, over https (hygiene H5). No test uses the network."""

    ALLOWED = (
        "https://download.pytorch.org/models/other.pth",
        "https://DOWNLOAD.PYTORCH.ORG/models/other.pth",
        "https://download.pytorch.org:443/models/other.pth",
    )
    REFUSED = {
        "https://evil.example/x": "evil.example",
        "https://download.pytorch.org.evil.example/x": "download.pytorch.org.evil.example",
        "https://evildownload.pytorch.org/x": "evildownload.pytorch.org",
        "https://sub.download.pytorch.org/x": "sub.download.pytorch.org",
        "https://download.pytorch.org@evil.example/x": "evil.example",
        "https://download.pytorch.org./x": "download.pytorch.org.",
        "https://127.0.0.1/x": "127.0.0.1",
        "https://huggingface.co/x": "huggingface.co",
        "https://download.pytorch.org:8443/x": "download.pytorch.org:8443",
        "http://download.pytorch.org/x": "download.pytorch.org",
        "http://evil.example/x": "evil.example",
    }

    def test_the_decision_function_allows_only_the_pinned_host_over_https(self) -> None:
        for url in self.ALLOWED:
            with self.subTest(url=url):
                self.assertIsNone(tv.redirect_refusal(url, tv.DOWNLOAD_HOSTS))
        for url, named in self.REFUSED.items():
            with self.subTest(url=url):
                sentence = tv.redirect_refusal(url, tv.DOWNLOAD_HOSTS)
                self.assertIsNotNone(sentence)
                self.assertIn(named, sentence)
                self.assertIn("nothing was saved", sentence)
                if url.startswith("https"):
                    self.assertIn("download.pytorch.org", sentence)  # says where downloads may come from

    def test_the_exact_sentences(self) -> None:
        self.assertEqual(
            tv.redirect_refusal("https://evil.example/x", tv.DOWNLOAD_HOSTS),
            "The download was redirected to evil.example, which is not where Athena fetches ready-made models from "
            "(download.pytorch.org), so it was stopped and nothing was saved.",
        )
        self.assertEqual(
            tv.redirect_refusal("http://download.pytorch.org/x", tv.DOWNLOAD_HOSTS),
            "The download was redirected to download.pytorch.org over a plain, unencrypted connection (http), which is "
            "not allowed, so it was stopped and nothing was saved.",
        )

    def test_odd_inputs_are_a_refusal_not_a_crash(self) -> None:
        for url in ("", "not a url", "https://", "https:///x", "file:///etc/passwd", "ftp://download.pytorch.org/x",
                    "https://exa\x00mple.com/x", "https://" + "a" * 5000 + ".example/x", "https://[::1]/x", "https://[bad/x"):
            with self.subTest(url=url[:40]):
                sentence = tv.redirect_refusal(url, tv.DOWNLOAD_HOSTS)
                self.assertIsInstance(sentence, str)
                self.assertLess(len(sentence), 400)
                self.assertNotIn("\x00", sentence)

    def test_the_handler_refuses_through_the_standard_redirect_hook(self) -> None:
        import urllib.request

        handler = tv.PinnedRedirects(tv.DOWNLOAD_HOSTS)
        request = urllib.request.Request("https://download.pytorch.org/models/a.pth")
        ok = handler.redirect_request(request, None, 302, "Found", {}, "https://download.pytorch.org/models/b.pth")
        self.assertEqual(ok.full_url, "https://download.pytorch.org/models/b.pth")
        with self.assertRaisesMessage(ValueError, "evil.example"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/b.pth")

    def serve(self, hops):
        """Patch the https layer: each request pops the next ``(code, location_or_body)``; returns the urls asked for."""
        import email.message
        import io
        import urllib.request
        import urllib.response

        asked: list[str] = []

        def https_open(handler, request):
            asked.append(request.full_url)
            code, extra = hops.pop(0)
            headers = email.message.Message()
            if code == 302:
                headers["Location"] = extra
            response = urllib.response.addinfourl(io.BytesIO(b"" if code == 302 else extra), headers, request.full_url, code)
            response.msg = "Found" if code == 302 else "OK"
            return response

        return asked, mock.patch.object(urllib.request.HTTPSHandler, "https_open", https_open)

    def test_end_to_end_a_redirect_to_another_host_is_refused_and_never_followed(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "w" / "w.pth"
            asked, patched = self.serve([(302, "https://evil.example/w.pth"), (200, b"never read")])
            with patched, self.assertRaisesMessage(ValueError, "redirected to evil.example"):
                tv._fetch("https://download.pytorch.org/models/w.pth", destination, hashlib.sha256(b"x").hexdigest()[:8])
            self.assertEqual(asked, ["https://download.pytorch.org/models/w.pth"])  # the second hop was never requested
            self.assertEqual(list(destination.parent.iterdir()) if destination.parent.exists() else [], [])

    def test_end_to_end_a_redirect_within_the_pinned_host_still_works_and_the_checksum_still_decides(self) -> None:
        import hashlib

        data = b"weights"
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "w" / "w.pth"
            asked, patched = self.serve([(302, "https://download.pytorch.org/models/real.pth"), (200, data)])
            with patched:
                tv._fetch("https://download.pytorch.org/models/w.pth", destination, hashlib.sha256(data).hexdigest()[:8])
            self.assertEqual(asked, ["https://download.pytorch.org/models/w.pth", "https://download.pytorch.org/models/real.pth"])
            self.assertEqual(destination.read_bytes(), data)
            asked, patched = self.serve([(302, "https://download.pytorch.org/models/real.pth"), (200, b"tampered")])
            other = Path(folder) / "w" / "v.pth"
            with patched, self.assertRaisesMessage(ValueError, "did not match its published checksum"):
                tv._fetch("https://download.pytorch.org/models/w.pth", other, "00000000")
            self.assertFalse(other.exists())

    def test_the_fetch_uses_a_restricted_opener_not_the_global_default(self) -> None:
        source = Path(tv.__file__).read_text()
        self.assertFalse("urllib.request.urlopen(" in source, "a download must go through tv.open_pinned")


class StricterRedirectTests(SimpleTestCase):
    """W10-FX3 / B3: a web address with a user name or password in it, or a host with an empty part, is refused."""

    HOSTS = {
        "pytorch": tv.DOWNLOAD_HOSTS,
        "huggingface": tv.AllowedHosts(("huggingface.co",), (".hf.co",), "huggingface.co and hf.co"),
    }
    # (url, allowed set names that must ALLOW it)  - everything else must be refused by both
    ALLOW = {
        "https://download.pytorch.org/models/a.pth": {"pytorch"},
        "https://DOWNLOAD.PYTORCH.ORG:443/a": {"pytorch"},
        "https://huggingface.co/a": {"huggingface"},
        "https://HuggingFace.CO/a": {"huggingface"},
        "https://cdn-lfs.hf.co/a": {"huggingface"},
        "https://cas-bridge.xethub.hf.co/a": {"huggingface"},
        "https://a.b.hf.co:443/a": {"huggingface"},
        # an "@" after the "#" or "?" is not a user name: the connection still goes to huggingface.co
        "https://huggingface.co#@evil.com/a": {"huggingface"},
        "https://huggingface.co?@evil.com/a": {"huggingface"},
    }
    REFUSE = (
        # the reviewer's cases
        "https://evil.com@x.hf.co/a", "https://x..hf.co/a", "https://hf.co@evil.com/a", "https://x.hf.co./a",
        "https://.hf.co/a", "https://x.hf.co:8443/a", "https://x.hf.co:0/a", "https://x.hf.co:abc/a",
        "https://x.hf.co.evil.com/a", "https://evil-hf.co/a", "https://hf.co/a", "https://xn--hf-co-9ya.com/a",
        "https://x.HF.CO\t.evil.com/a", " https://x.hf.co/a", "ftp://x.hf.co/a", "file:///etc/passwd",
        "https:///x.hf.co/a", "https:x.hf.co/a", "//evil.com/x", "/relative/x", "relative/x", "",
        "https://[::1]/a", "https://[huggingface.co]/a", "http://huggingface.co/a", "http://download.pytorch.org/x",
        # new: user name / password in the address, whatever the host after it is
        "https://evil.com@download.pytorch.org/x", "https://user:pw@download.pytorch.org/x",
        "https://:pw@download.pytorch.org/x", "https://@download.pytorch.org/x", "https://u@huggingface.co/a",
        "https://u:p@cdn-lfs.hf.co/a", "https://@x.hf.co/a", "https://evil.com%40@x.hf.co/a",
        "https://download.pytorch.org:443@evil.com/x", "https://download.pytorch.org:pw@evil.com/x",
        # new: empty names between dots, leading or trailing dot
        "https://download..pytorch.org/x", "https://..download.pytorch.org/x", "https://download.pytorch.org../x",
        "https://download.pytorch.org./x", "https://.download.pytorch.org/x", "https://a..b.hf.co/a",
        "https://x.hf..co/a", "https://huggingface..co/a", "https://huggingface.co./a", "https://.huggingface.co/a",
        "https://cdn-lfs.hf.co../a", "https://%2e.hf.co/a", "https://x%2ehf.co/a",
    )

    def test_every_listed_address_is_decided_the_same_for_both_host_lists(self) -> None:
        for url in self.REFUSE:
            for name, hosts in self.HOSTS.items():
                with self.subTest(url=url, hosts=name):
                    sentence = tv.redirect_refusal(url, hosts)
                    self.assertIsInstance(sentence, str)
                    self.assertIn("nothing was saved", sentence)
                    self.assertLess(len(sentence), 400)
        for url, names in self.ALLOW.items():
            for name, hosts in self.HOSTS.items():
                with self.subTest(url=url, hosts=name):
                    sentence = tv.redirect_refusal(url, hosts)
                    if name in names:
                        self.assertIsNone(sentence)
                    else:
                        self.assertIsInstance(sentence, str)

    def test_dfines_real_host_list_decides_the_same_way(self) -> None:
        from .training import dfine

        for url in self.REFUSE:
            with self.subTest(url=url):
                self.assertIsNotNone(tv.redirect_refusal(url, dfine.PINNED_HOSTS))
        for url, names in self.ALLOW.items():
            with self.subTest(url=url):
                self.assertEqual(tv.redirect_refusal(url, dfine.PINNED_HOSTS) is None, "huggingface" in names)

    def test_the_user_name_sentence_names_the_host_never_the_password(self) -> None:
        sentence = tv.redirect_refusal("https://evil.com:s3cret@x.hf.co/a", self.HOSTS["huggingface"])
        self.assertEqual(
            sentence,
            "The download was redirected to x.hf.co using a web address with a user name or password in it, which is "
            "not allowed (Athena only fetches ready-made models from huggingface.co and hf.co), so it was stopped and "
            "nothing was saved.",
        )
        self.assertNotIn("s3cret", sentence)

    def test_an_address_that_hides_a_user_name_is_refused_by_the_handler_too(self) -> None:
        import urllib.request

        handler = tv.PinnedRedirects(tv.DOWNLOAD_HOSTS)
        request = urllib.request.Request("https://download.pytorch.org/models/a.pth")
        with self.assertRaisesMessage(ValueError, "user name or password"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.com@download.pytorch.org/b.pth")

    def test_a_scheme_less_target_does_not_print_empty_brackets(self) -> None:
        sentence = tv.redirect_refusal("//evil.com/x", tv.DOWNLOAD_HOSTS)
        self.assertNotIn("()", sentence)
        self.assertEqual(
            sentence,
            "The download was redirected to evil.com over a plain, unencrypted connection (an unknown scheme), which "
            "is not allowed, so it was stopped and nothing was saved.",
        )

    def test_a_named_scheme_is_still_printed(self) -> None:
        self.assertIn("(ftp)", tv.redirect_refusal("ftp://download.pytorch.org/x", tv.DOWNLOAD_HOSTS))
        self.assertIn("(http)", tv.redirect_refusal("http://download.pytorch.org/x", tv.DOWNLOAD_HOSTS))


class _Endless:
    """A response that never ends: ``read`` hands back as many bytes as asked for and counts them."""

    def __init__(self, limit_reads: int = 10_000) -> None:
        self.sent = 0
        self.reads = 0
        self.limit_reads = limit_reads

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        if self.reads > self.limit_reads or size is None or size < 0:
            raise AssertionError("the download kept reading past any sensible cap")
        self.sent += size
        return b"\0" * size

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Stream(_Endless):
    """A response with exactly these bytes."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.buffer = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self.buffer.read(size)


class DownloadSizeCapTests(SimpleTestCase):
    """W10-FX3 / B4: a torchvision download is cut off at a named size cap, before the checksum is looked at."""

    def fetch(self, response, destination: Path, url: str, **kw):
        with mock.patch.object(tv, "open_pinned", return_value=response):
            tv._fetch(url, destination, "00000000", **kw)

    def test_the_cap_is_a_named_constant_and_the_known_sizes_are_pinned(self) -> None:
        self.assertEqual(tv.MAX_DOWNLOAD_BYTES, 200 * 1024 * 1024)
        self.assertEqual(tv.PRETRAINED_FILES["imagenet_backbone"]["bytes"], 22_139_423)
        self.assertEqual(tv.PRETRAINED_FILES["coco"]["bytes"], 77_844_807)

    def test_a_known_file_is_cut_off_one_byte_past_its_size_and_the_partial_is_discarded(self) -> None:
        url = tv.PRETRAINED_FILES["imagenet_backbone"]["url"]
        endless = _Endless()
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "w" / "w.pth"
            with self.assertRaisesMessage(ValueError, "The download was larger than expected (22,139,423 bytes for this file)"):
                self.fetch(endless, destination, url)
            self.assertEqual(endless.sent, 22_139_423 + 1)
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.iterdir()), [])  # no .part left behind

    def test_the_exact_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as caught:
                self.fetch(_Endless(), Path(folder) / "w.pth", tv.PRETRAINED_FILES["coco"]["url"])
        self.assertEqual(
            str(caught.exception),
            "The download was larger than expected (77,844,807 bytes for this file), so it was stopped and discarded.",
        )

    def test_an_unknown_address_gets_the_general_cap(self) -> None:
        endless = _Endless()
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "w.pth"
            with self.assertRaisesMessage(ValueError, "larger than expected (209,715,200 bytes for this file)"):
                self.fetch(endless, destination, "https://download.pytorch.org/models/other.pth")
            self.assertEqual(endless.sent, tv.MAX_DOWNLOAD_BYTES + 1)
            self.assertFalse(destination.exists())

    def test_an_explicit_size_wins(self) -> None:
        endless = _Endless()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesMessage(ValueError, "larger than expected (16 bytes for this file)"):
                self.fetch(endless, Path(folder) / "w.pth", tv.PRETRAINED_FILES["coco"]["url"], expected_bytes=16)
        self.assertEqual(endless.sent, 17)

    def test_a_file_of_exactly_the_expected_size_is_still_checked_by_its_checksum_and_saved(self) -> None:
        import hashlib

        data = b"x" * 16
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "w.pth"
            with mock.patch.object(tv, "open_pinned", return_value=_Stream(data)):
                tv._fetch("https://download.pytorch.org/models/other.pth", destination, hashlib.sha256(data).hexdigest()[:8], expected_bytes=16)
            self.assertEqual(destination.read_bytes(), data)

    def test_a_small_wrong_file_still_fails_on_the_checksum_not_the_size(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "w.pth"
            with mock.patch.object(tv, "open_pinned", return_value=_Stream(b"tampered")):
                with self.assertRaisesMessage(ValueError, "did not match its published checksum"):
                    tv._fetch("https://download.pytorch.org/models/other.pth", destination, "00000000")
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_ensure_pretrained_weights_still_calls_fetch_with_three_arguments(self) -> None:
        with mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "1"}), tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(tv, "pretrained_dir", return_value=Path(folder)), \
                mock.patch.object(tv, "downloads_allowed", return_value=True), \
                mock.patch.object(tv, "_fetch") as fetch, mock.patch.object(tv, "_sha256", return_value="0" * 64):
            tv.ensure_pretrained_weights("coco")
        self.assertEqual(len(fetch.call_args.args), 3)
