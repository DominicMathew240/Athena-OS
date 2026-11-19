"""OCR models on check screens: the text-output serving path (OS-OCR-002).

A handwriting-OCR model answers with the text it read, not boxes. These tests pin the whole
serving slice against a STAND-IN text backend (``FakeOcrBackend``) with the real
``OcrTrOCRBackend``'s serving contract - ``predict_transcript`` returning ``{"text", "confidence"}``
and ``predict`` refusing boxes - so nothing is downloaded and no real transformers runs here.
The stubbed-model contract on the real backend itself is covered by ``tests_ocr``; the two
torch-gated tests at the bottom pin its shape for the ML venv.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import skipUnless

from django.db import models
from django.test import SimpleTestCase
from PIL import Image

from .inference import schema
from .models import InferenceApp, ModelVersion, ObservationRecord, Project
from .tests_inference import InferenceBase, image_upload
from .training import lifecycle, register_backend, unregister_backend
from .training.base import Availability, TrainResult, TrainingCancelled

TORCH_INSTALLED = importlib.util.find_spec("torch") is not None

READS = "selamat pagi"


class FakeOcrBackend:
    """The OCR backend's serving contract only: reads a fixed transcript, refuses boxes."""

    name = "fake-ocr-backend"
    description = "Test double for the handwriting reader."
    license_note = "Test only; no third-party code."
    dataset_format = "generic"

    def check_available(self) -> Availability:
        return Availability(True, "ready")

    def default_config(self):
        return {}

    def validate_config(self, config):
        if config:
            raise ValueError("This backend takes no settings.")
        return {}

    def runtime_info(self):
        return {"fake_ocr": "1.0"}

    def train(self, dataset_dir, config, progress_cb, cancel_cb):
        for step in range(1, 4):
            if cancel_cb():
                raise TrainingCancelled("stop")
            progress_cb(step / 3, f"step {step}")
        artifact = dataset_dir.parent / "model.pt"
        artifact.write_bytes(b"fake-ocr-weights")
        return TrainResult(artifact, {"loss": 0.1}, ["transcript"])

    def predict_transcript(self, artifact, image):
        # The real backend generates token by token and has no score to give: an honest None.
        return {"text": READS, "confidence": None}

    def predict(self, artifact, image, thresholds):
        raise NotImplementedError("This model reads text; it does not find boxes. Use predict_transcript.")


class OcrInferenceBase(InferenceBase):
    """The vision fixtures of ``InferenceBase`` plus the fake OCR backend and an approved reader.

    The reader model lives in its OWN project (a handwriting project, as the recipe makes): one
    model per project may be approved, and keeping the reader out of the vision project leaves
    the vision fixtures (``self.model``) exactly as ``InferenceBase`` built them."""

    def setUp(self) -> None:
        super().setUp()
        register_backend(FakeOcrBackend, replace=True)
        self.addCleanup(unregister_backend, FakeOcrBackend.name)
        self.ocr_project = Project.objects.create(
            name="Letters", modality="image", task="Read handwriting", labels=["transcript"],
            recipe="handwriting-ocr",
        )
        self.ocr_model = self.approved_ocr_model(self.ocr_project)

    def approved_ocr_model(self, project: Project) -> ModelVersion:
        """An approved handwriting-OCR model: a real stored artifact plus the OCR evaluation shape."""
        from django.conf import settings

        artifact_dir = Path(settings.DATA_HOME) / "models" / str(uuid.uuid4())
        artifact_dir.mkdir(parents=True)
        artifact = artifact_dir / "model.bin"
        artifact.write_bytes(b"fake-ocr-weights")
        model = ModelVersion.objects.create(
            project=project,
            version=(project.model_versions.aggregate(top=models.Max("version"))["top"] or 0) + 1,
            backend=FakeOcrBackend.name,
            task="handwriting-ocr",
            artifact_path=str(artifact.relative_to(settings.DATA_HOME)),
            artifact_sha256=lifecycle.sha256_file(artifact),
            artifact_size_bytes=artifact.stat().st_size,
            label_names=["transcript"],
            evaluation={
                "schema": "athena.ocr-evaluation/1",
                "available": True,
                "reason": "",
                "split": "test",
                "task": "handwriting-ocr",
                "counts": {"test_pictures": 3},
                "cer": 0.08,
                "word_accuracy": 0.91,
                "plain_language": "Out of every 100 characters, 8 were wrong in our check.",
                "per_picture": [],
                "limits": [],
            },
        )
        return lifecycle.approve_model(
            lifecycle.stage_model(model, self.user), self.user, "Read the evaluation.", acknowledge_weak_results=True
        )[0]

    def approved_vision_model(self, project: Project) -> ModelVersion:
        """An approved box model in the OCR project, for cross-kind re-point refusals.

        Built like ``self.model`` (the vision project's) with a solid evaluation so the approval
        gate passes. Approving it retires the reader in that project; callers that still need
        the reader create it first."""
        model = ModelVersion.objects.create(
            project=project,
            version=(project.model_versions.aggregate(top=models.Max("version"))["top"] or 0) + 1,
            backend=self.model.backend,
            task="vision-detection",
            artifact_path=self.model.artifact_path,
            artifact_sha256=self.model.artifact_sha256,
            artifact_size_bytes=self.model.artifact_size_bytes,
            label_names=self.model.label_names,
            evaluation=self.model.evaluation,
        )
        return lifecycle.approve_model(
            lifecycle.stage_model(model, self.user), self.user, "Read the evaluation."
        )[0]

    def make_ocr_app(self, **overrides) -> dict:
        """A check screen on the OCR model, in its own project (an OCR model must serve text)."""
        response = self.create_app(project=self.ocr_project, model_version_id=str(self.ocr_model.id), **overrides)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["app"]

    def ocr_url(self, app_id: str, suffix: str = "") -> str:
        """The app's routes under its own (OCR) project: the API is project-scoped."""
        return self.apps_url(f"/{app_id}{suffix}", project=self.ocr_project)

    def run_reading(self, app_id: str, upload=None, *, process: bool = True) -> str:
        from .inference import runner as inference_runner

        response = self.client.post(self.ocr_url(app_id, "/run"), {"image": upload or image_upload(name="note.png")})
        self.assertEqual(response.status_code, 202, response.content)
        if process:
            observation = inference_runner.claim_next_observation()
            self.assertIsNotNone(observation)
            inference_runner.process_observation(observation)
        return response.json()["observation_id"]

    def reading_result(self, app_id: str, observation_id: str) -> dict:
        return self.client.get(self.ocr_url(app_id, f"/observations/{observation_id}")).json()["render"]


# -- the config: output follows the bound model ------------------------------------------------------
class OutputModeConfigTests(OcrInferenceBase):
    def test_an_ocr_model_is_served_in_text_mode_and_the_mode_is_stored(self) -> None:
        app = self.make_ocr_app()
        config = InferenceApp.objects.get(pk=app["id"]).config
        self.assertEqual(config["output"], "text")
        preview = self.client.get(self.ocr_url(app["id"], "/preview")).json()["preview"]
        self.assertEqual(preview["output"], "text")
        self.assertEqual(preview["model"]["id"], str(self.ocr_model.id))
        # the summary block is the box-shaped one and says honestly it cannot read a CER report;
        # the reading itself carries no score to summarize
        self.assertEqual(preview["model"]["evaluation"]["available"], False)

    def test_a_vision_model_is_served_in_boxes_mode(self) -> None:
        app = self.make_app()  # the vision model from InferenceBase
        self.assertEqual(InferenceApp.objects.get(pk=app["id"]).config["output"], "boxes")
        preview = self.client.get(self.apps_url(f"/{app['id']}/preview")).json()["preview"]
        self.assertEqual(preview["output"], "boxes")

    def test_a_vision_model_with_a_text_output_is_refused_in_plain_words(self) -> None:
        response = self.create_app(config={"output": "text"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finds things in pictures", response.json()["error"])
        self.assertEqual(InferenceApp.objects.count(), 0)

    def test_an_ocr_model_with_a_boxes_output_is_refused_in_plain_words(self) -> None:
        response = self.create_app(project=self.ocr_project, model_version_id=str(self.ocr_model.id), config={"output": "boxes"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("reads handwriting", response.json()["error"])
        self.assertEqual(InferenceApp.objects.filter(model_version=self.ocr_model).count(), 0)

    def test_a_bad_output_mode_is_refused_and_the_key_set_stays_strict(self) -> None:
        for bad in ("noise", "", None, 1, True):
            response = self.create_app(config={"output": bad})
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(InferenceApp.objects.count(), 0)
        self.assertEqual(self.create_app(config={"surprise": 1}).status_code, 400)
        # the shape alone (no model) still validates both modes: tests of the config use this
        for mode in ("boxes", "text"):
            base = {**schema.default_config("x", ["damaged"]), "output": mode}
            self.assertEqual(schema.validate_config(base, ["damaged"])["output"], mode)

    def test_a_patch_cannot_switch_the_output_mode_either(self) -> None:
        app = self.make_ocr_app()
        url = self.ocr_url(app["id"])
        switched = self.client.patch(url, data=json.dumps({"config": {"output": "boxes"}}),
                                     content_type="application/json")
        self.assertEqual(switched.status_code, 400)
        self.assertIn("reads handwriting", switched.json()["error"])
        self.assertEqual(InferenceApp.objects.get(pk=app["id"]).config["output"], "text")
        renamed = self.client.patch(url, data=json.dumps({"name": "Letters check"}), content_type="application/json")
        self.assertEqual(renamed.status_code, 200, renamed.content)
        self.assertEqual(renamed.json()["app"]["config"]["output"], "text")

    def test_re_pointing_a_screen_across_the_two_kinds_of_model_is_refused(self) -> None:
        # The text screen first (approving a vision model in the same project retires the
        # reader), then a vision model there, so the refusal is about the output mode, not
        # about projects (a cross-project model is a 404, already covered elsewhere).
        text_app = self.make_ocr_app(name="Reading")
        local_vision = self.approved_vision_model(self.ocr_project)
        to_vision = self.client.patch(self.ocr_url(text_app["id"]),
                                      data=json.dumps({"model_version_id": str(local_vision.id)}),
                                      content_type="application/json")
        self.assertEqual(to_vision.status_code, 400)
        self.assertIn("Create a new screen", to_vision.json()["error"])
        self.assertEqual(InferenceApp.objects.get(pk=text_app["id"]).model_version_id, self.ocr_model.id)

    def test_approval_re_pointing_skips_a_screen_of_the_other_kind_with_a_reason(self) -> None:
        # A text screen on the reader, offered a vision model by an approval with repoint_apps:
        # it must be skipped with the output-mode reason, not silently switched.
        project = self.ocr_project
        text_app = self.make_ocr_app(name="Reading")
        staged = ModelVersion.objects.create(
            project=project, version=2, backend=self.model.backend, task="vision-detection",
            artifact_path=self.model.artifact_path, artifact_sha256=self.model.artifact_sha256,
            artifact_size_bytes=self.model.artifact_size_bytes, label_names=self.model.label_names,
            evaluation=self.model.evaluation, stage=ModelVersion.CANDIDATE,
        )
        lifecycle.stage_model(staged, self.user)
        response = self.post(self.api(f"/models/{staged.id}/approve", project=project),
                             {"note": "Switching.", "repoint_apps": True, "acknowledge_weak_results": True})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["repoint_skipped"][0]["app_id"], str(text_app["id"]))
        self.assertIn("answers in text", body["repoint_skipped"][0]["reason"])
        self.assertEqual(InferenceApp.objects.get(pk=text_app["id"]).model_version_id, self.ocr_model.id)


# -- the worker: one text answer, stored with the same provenance ------------------------------------
class TextPredictionTests(OcrInferenceBase):
    def test_a_reading_is_stored_as_one_prediction_and_rendered_as_the_answer(self) -> None:
        app = self.make_ocr_app()
        observation_id = self.run_reading(app["id"])
        row = ObservationRecord.objects.get(pk=observation_id)
        self.assertEqual(row.status, "succeeded")
        self.assertEqual(row.predictions, [{"index": 0, "text": READS, "confidence": None}])
        self.assertEqual(row.model_version_id, self.ocr_model.id)  # the exact model, as boxes do
        self.assertEqual(row.config_snapshot["output"], "text")

        result = self.reading_result(app["id"], observation_id)
        self.assertEqual(result["output"], "text")
        self.assertEqual(result["result"]["text_answer"], {"text": READS, "confidence": None})
        self.assertEqual(result["result"]["detections"], [])  # a text screen has nothing to list
        self.assertIsNone(result["result"]["machine_state"])
        self.assertEqual((result["result"]["hidden_below_threshold"], result["result"]["hidden_other_labels"]), (0, 0))
        self.assertIn("not a final determination", result["disclaimer"])  # the banner travels with it
        self.assertEqual(result["model"]["artifact_sha256"], self.ocr_model.artifact_sha256)

    def test_a_reading_waits_for_review_until_a_person_decides(self) -> None:
        app = self.make_ocr_app()
        observation_id = self.run_reading(app["id"])
        result = self.reading_result(app["id"], observation_id)["result"]
        self.assertEqual(result["state"], "needs_review")
        self.assertTrue(result["awaiting_human_decision"])
        self.assertIsNone(result["decision"])
        decided = self.post(self.ocr_url(app["id"], f"/observations/{observation_id}/decision"), {"decision": "confirm"})
        self.assertEqual(decided.status_code, 201)
        self.assertEqual(decided.json()["render"]["result"]["state"], "flagged")  # a reading is the thing to check
        self.assertFalse(decided.json()["render"]["result"]["awaiting_human_decision"])

    def test_a_rejected_reading_needs_its_note_and_stays_in_review(self) -> None:
        app = self.make_ocr_app()
        observation_id = self.run_reading(app["id"])
        url = self.ocr_url(app["id"], f"/observations/{observation_id}/decision")
        missing = self.post(url, {"decision": "reject"})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("note", missing.json()["error"])
        ok = self.post(url, {"decision": "reject", "note": "It misread the third word."})
        result = ok.json()["render"]["result"]
        self.assertEqual(result["state"], "needs_review")
        self.assertTrue(result["result_rejected"])
        self.assertEqual(result["decision"]["note"], "It misread the third word.")

    def test_a_backend_that_raises_is_stored_as_a_plain_failure(self) -> None:
        from .inference import runner as inference_runner

        class Boom(FakeOcrBackend):
            def predict_transcript(self, artifact, image):
                raise RuntimeError("decoder broke")

        app = self.make_ocr_app()
        observation_id = self.run_reading(app["id"], process=False)
        row = inference_runner.claim_next_observation()
        inference_runner.process_observation(row, backend=Boom())
        result = self.reading_result(app["id"], observation_id)["result"]
        self.assertEqual((result["status"], result["state"]), ("failed", "failed"))
        self.assertIn("could not be analysed", result["status_text"])
        self.assertIsNone(result["text_answer"])
        decided = self.post(self.ocr_url(app["id"], f"/observations/{observation_id}/decision"), {"decision": "confirm"})
        self.assertEqual(decided.status_code, 409)

    def test_per_finding_feedback_is_refused_on_a_text_screen(self) -> None:
        app = self.make_ocr_app()
        observation_id = self.run_reading(app["id"])
        url = self.ocr_url(app["id"], f"/observations/{observation_id}/feedback")
        for payload in (
            {"prediction_index": 0, "verdict": "true_positive"},
            {"prediction_index": 0, "verdict": "false_positive", "note": "x"},
            {"prediction_index": "missed", "label": "transcript", "note": "x"},
        ):
            response = self.post(url, payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("one whole reading", response.json()["error"])
        self.assertEqual(ObservationRecord.objects.get(pk=observation_id).feedback.count(), 0)

    def test_a_text_screen_survives_a_worker_restart_like_a_box_screen(self) -> None:
        from .inference import runner as inference_runner

        app = self.make_ocr_app()
        observation_id = self.run_reading(app["id"], process=False)
        ObservationRecord.objects.filter(pk=observation_id).update(status="running")
        self.assertEqual(inference_runner.fail_orphaned_observations(), 1)
        result = self.reading_result(app["id"], observation_id)["result"]
        self.assertEqual(result["state"], "failed")


# -- the operator page --------------------------------------------------------------------------------
class OperatorPageTests(OcrInferenceBase):
    def test_the_operator_page_renders_the_text_shell_for_an_ocr_screen(self) -> None:
        app = self.make_ocr_app(slug="letters-check", name="Letters check")
        html = self.client.get("/check/letters-check").content.decode()
        self.assertIn("Letters check", html)
        self.assertIn("What the computer read", html)  # the text-answer block
        self.assertIn("Not a final decision.", html)  # the banner stays
        self.assertIn("do <strong>not</strong> change the computer model", html)
        # the box-shaped panels the script hides on a text screen are still in the page (no inline style)
        self.assertIn("The computer missed something", html)

    def test_the_scripts_and_templates_carry_the_text_vocabulary(self) -> None:
        static = Path(__file__).parent / "static" / "projects"
        operator = (static / "checkscreen.js").read_text()
        builder = (static / "checkscreen_builder.js").read_text()
        core = (static / "checkscreen_core.js").read_text()
        template = (Path(__file__).parent / "templates" / "projects" / "checkscreen.html").read_text()
        for needle in ('render.output === "text"', "result.text_answer", "ui.missed.hidden = true"):
            self.assertIn(needle, operator)
        for needle in ("What the computer read", "cs-text-answer", "cs-text-feedback-note"):
            self.assertIn(needle, template)
        self.assertIn('m.task === "handwriting-ocr"', builder)  # the model picker says what it reads
        self.assertIn("output: isTextModel", builder)
        self.assertIn("reads handwriting", builder)
        self.assertIn('render.output === "text"', core)  # the machine sentence knows a reading
        self.assertIn("read no writing", core)

    def test_the_builder_try_panel_has_a_place_for_the_reading(self) -> None:
        template = (Path(__file__).parent / "templates" / "projects" / "checkscreen_builder.html").read_text()
        self.assertIn('id="cs-try-text"', template)
        builder = (Path(__file__).parent / "static" / "projects" / "checkscreen_builder.js").read_text()
        self.assertIn("cs-try-text", builder)


# -- the real backend's serving contract (torch-gated) ------------------------------------------------
@skipUnless(TORCH_INSTALLED, "torch is not installed here; run in the ML venv")
class RealBackendContractTests(SimpleTestCase):
    """The serving contract on the real TrOCR backend. Running it would load a real model, so these
    pin its shape only; ``tests_ocr`` runs the full stubbed path."""

    def test_predict_refuses_boxes_and_names_the_text_path(self) -> None:
        from .training.ocr_trocr import OcrTrOCRBackend

        backend = OcrTrOCRBackend()
        with Image.new("RGB", (8, 8)) as image, self.assertRaises(NotImplementedError) as raised:
            backend.predict(Path("unused"), image, {})
        self.assertIn("predict_transcript", str(raised.exception))

    def test_predict_transcript_returns_text_and_an_honest_no_confidence(self) -> None:
        from .training.ocr_trocr import OcrTrOCRBackend

        # The method delegates to predict_text (which loads the model); pin the delegation without
        # loading anything by stubbing predict_text on one instance. predict_text strips, so the
        # stub returns already-clean text and the contract passes it through untouched.
        backend = OcrTrOCRBackend()
        backend.predict_text = lambda artifact, image: "apa khabar"
        self.assertEqual(backend.predict_transcript(Path("unused"), Image.new("RGB", (8, 8))),
                         {"text": "apa khabar", "confidence": None})

    def test_the_module_imports_without_torch_or_transformers(self) -> None:
        code = (
            "import sys; sys.modules['torch'] = None; sys.modules['transformers'] = None\n"
            "import projects.training.ocr_trocr\n"
            "print('ok')\n"
        )
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ok", done.stdout)


# The lean venv has no torch: the database-backed classes still run there because the stand-in
# backend needs no framework (that is the point of the fake); only RealBackendContractTests skips.
