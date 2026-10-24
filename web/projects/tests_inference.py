"""Inference apps: strict config, approved-model pinning, queued predictions, decisions and feedback."""

from __future__ import annotations

import io
import json
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from PIL import Image

from .inference import runner as inference_runner, schema, service as inference_service
from .models import (
    BoxAnnotation,
    DatasetVersion,
    FeedbackRecord,
    InferenceApp,
    ModelEvent,
    ModelVersion,
    ObservationRecord,
    Project,
    ReviewDecision,
    TrainingRun,
)
from .tests_training import FakeBackend, TrainingBase
from .training import lifecycle
from .training.base import BoxPrediction


def image_upload(name="part.png", fmt="PNG", color=(200, 30, 30), size=(40, 30)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


class InferenceBase(TrainingBase):
    def setUp(self) -> None:
        super().setUp()
        self.model = self.approved_model()

    def approved_model(self) -> ModelVersion:
        candidate = self.train_candidate()
        lifecycle.stage_model(candidate, self.user)
        return lifecycle.approve_model(candidate, self.user, "Reviewed the evaluation.")[0]

    def train_candidate_after_approved(self, solid: bool = True) -> ModelVersion:
        from .training import runner

        self.assertEqual(self.start_run().status_code, 202)
        model = runner.run_training(runner.claim_next_run()).model_versions.get()
        # The approval gate asks for an acknowledgement when the tiny fabricated test set reads "cannot tell";
        # most tests are about roles and inference, not that gate, so give the model a solid evaluation.
        # Pass solid=False to keep the real (tiny, weak) evaluation.
        return self.set_solid_evaluation(model) if solid else model

    def apps_url(self, suffix: str = "", project=None) -> str:
        return self.api(f"/inference/apps{suffix}", project)

    def create_app(self, project=None, **overrides):
        payload = {"name": "Line 4 check", "model_version_id": str(self.model.id), **overrides}
        return self.post(self.apps_url(project=project), payload)

    def make_app(self, **overrides) -> dict:
        response = self.create_app(**overrides)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["app"]

    def run_image(self, app_id: str, upload=None, *, process: bool = True, client=None):
        response = (client or self.client).post(
            self.apps_url(f"/{app_id}/run"), {"image": upload or image_upload()}
        )
        if process and response.status_code == 202:
            observation = inference_runner.claim_next_observation()
            self.assertIsNotNone(observation)
            inference_runner.process_observation(observation)
        return response

    def observation_url(self, app_id, observation_id, suffix="") -> str:
        return self.apps_url(f"/{app_id}/observations/{observation_id}{suffix}")

    def result(self, app_id, observation_id) -> dict:
        return self.client.get(self.observation_url(app_id, observation_id)).json()["render"]

    def finished_observation(self, **app_overrides) -> tuple[dict, str]:
        app = self.make_app(**app_overrides)
        response = self.run_image(app["id"])
        self.assertEqual(response.status_code, 202, response.content)
        return app, response.json()["observation_id"]


class AccessTests(InferenceBase):
    def test_every_route_requires_sign_in(self) -> None:
        anonymous = Client()
        aid, oid = uuid.uuid4(), uuid.uuid4()
        routes = [
            ("get", ""), ("post", ""), ("get", f"/{aid}"), ("patch", f"/{aid}"), ("delete", f"/{aid}"),
            ("get", f"/{aid}/preview"), ("post", f"/{aid}/run"), ("get", f"/{aid}/observations"),
            ("get", f"/{aid}/observations/{oid}"), ("get", f"/{aid}/observations/{oid}/image"),
            ("post", f"/{aid}/observations/{oid}/feedback"), ("post", f"/{aid}/observations/{oid}/decision"),
            ("get", f"/{aid}/feedback/export"),
        ]
        for method, suffix in routes:
            with self.subTest(method=method, route=suffix):
                response = getattr(anonymous, method)(self.apps_url(suffix))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Sign in to continue."})

    def test_nothing_is_reachable_across_projects(self) -> None:
        app, observation_id = self.finished_observation()
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged"])
        text = Project.objects.create(name="Docs", modality="text", task="Tag", labels=["a"])
        aid = app["id"]
        for project in (other, text):
            for suffix in ("", f"/{aid}", f"/{aid}/preview", f"/{aid}/observations",
                           f"/{aid}/observations/{observation_id}", f"/{aid}/observations/{observation_id}/image",
                           f"/{aid}/feedback/export"):
                expected = 200 if (suffix == "" and project is other) else 404  # an empty list for a real project
                self.assertEqual(self.client.get(self.apps_url(suffix, project)).status_code, expected, suffix)
            self.assertEqual(self.post(self.apps_url(f"/{aid}/run", project)).status_code, 404)
            self.assertEqual(self.post(self.apps_url(f"/{aid}/observations/{observation_id}/feedback", project), {}).status_code, 404)
        self.assertEqual(self.client.get(self.apps_url(project=other)).json()["apps"], [])
        # a model from another project cannot back an app here
        self.assertEqual(self.create_app(project=other).status_code, 404)  # self.model belongs to the first project
        self.assertEqual(self.client.get(self.apps_url(f"/{uuid.uuid4()}")).status_code, 404)

    def test_csrf_is_enforced(self) -> None:
        response = self.csrf_client().post(self.apps_url(), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_methods(self) -> None:
        app = self.make_app()
        self.assertEqual(self.client.put(self.apps_url(f"/{app['id']}")).status_code, 405)
        self.assertEqual(self.client.get(self.apps_url(f"/{app['id']}/run")).status_code, 405)
        self.assertEqual(self.client.delete(self.apps_url()).status_code, 405)


class AppConfigTests(InferenceBase):
    def test_create_fills_safe_defaults_and_returns_the_preview(self) -> None:
        response = self.create_app()
        self.assertEqual(response.status_code, 201)
        app, preview = response.json()["app"], response.json()["preview"]
        config = app["config"]
        self.assertTrue(config["human_review_required"])
        self.assertEqual(config["task_title"], "Line 4 check")
        self.assertEqual(config["accepted_inputs"], ["image/jpeg", "image/png", "image/webp"])
        self.assertEqual(config["displayed_labels"], ["damaged", "normal"])
        self.assertEqual(app["slug"], "line-4-check")
        self.assertEqual(app["model_version"]["id"], str(self.model.id))
        self.assertEqual(preview["app"]["id"], app["id"])

    def test_config_can_be_customised_and_is_normalised(self) -> None:
        app = self.make_app(
            slug="belt-check",
            config={
                "task_title": "Belt inspection",
                "accepted_inputs": ["image/png"],
                "displayed_labels": ["damaged"],
                "confidence_threshold": 0.7,
                "status_texts": {"clear": "No damage seen", "flagged": "Possible damage"},
                "decision_controls": {"options": ["confirm", "reject"], "required_note_on_override": False},
                "human_review_required": False,
            },
        )
        config = app["config"]
        self.assertEqual((app["slug"], config["task_title"], config["confidence_threshold"]), ("belt-check", "Belt inspection", 0.7))
        self.assertEqual(config["status_texts"]["clear"], "No damage seen")
        self.assertEqual(config["status_texts"]["needs_review"], schema.DEFAULT_STATUS_TEXTS["needs_review"])
        self.assertFalse(config["human_review_required"])

    def test_strict_validation_rejects_each_bad_field_with_a_plain_message(self) -> None:
        cases = [
            ({"surprise": 1}, "Unsupported config field"),
            ({"task_title": ""}, "task_title"),
            ({"task_title": "x" * 200}, "task_title"),
            ({"accepted_inputs": ["image/gif"]}, "accepted_inputs"),
            ({"accepted_inputs": []}, "accepted_inputs"),
            ({"accepted_inputs": ["image/png", "image/png"]}, "accepted_inputs"),
            ({"displayed_labels": ["ghost"]}, "not recognised: ghost"),
            ({"displayed_labels": []}, "displayed_labels"),
            ({"confidence_threshold": 1.5}, "confidence_threshold"),
            ({"confidence_threshold": -0.1}, "confidence_threshold"),
            ({"confidence_threshold": True}, "confidence_threshold"),
            ({"confidence_threshold": "0.5"}, "confidence_threshold"),
            ({"status_texts": {"clear": ""}}, "status_texts.clear"),
            ({"status_texts": {"unknown": "x"}}, "status_texts"),
            ({"status_texts": "x"}, "status_texts must be an object"),
            ({"decision_controls": {"options": []}}, "decision_controls.options"),
            ({"decision_controls": {"options": ["approve"]}}, "decision_controls.options"),
            ({"decision_controls": {"required_note_on_override": "yes"}}, "required_note_on_override"),
            ({"decision_controls": {"extra": 1}}, "decision_controls"),
            ({"human_review_required": "yes"}, "human_review_required"),
            ({"human_review_required": True, "decision_controls": {"options": ["needs_second_look"]}}, "record a decision"),
        ]
        for config, text in cases:
            with self.subTest(config=config):
                response = self.create_app(config=config)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(text, response.json()["error"])
        self.assertEqual(InferenceApp.objects.count(), 0)

    def test_payload_level_errors(self) -> None:
        self.assertEqual(self.post(self.apps_url(), {"name": "x"}).status_code, 400)
        self.assertEqual(self.post(self.apps_url(), {"model_version_id": "nope", "name": "x"}).status_code, 400)
        self.assertEqual(self.create_app(name="").status_code, 400)
        self.assertEqual(self.create_app(name=5).status_code, 400)
        self.assertEqual(self.create_app(slug="Bad Slug!").status_code, 400)
        self.assertEqual(self.create_app(bonus=1).status_code, 400)
        self.assertEqual(self.create_app(config="x").status_code, 400)
        self.assertEqual(self.post(self.apps_url(), {"name": "x", "model_version_id": str(uuid.uuid4())}).status_code, 404)

    def test_slug_is_unique_and_generated_names_do_not_collide(self) -> None:
        self.make_app(slug="same")
        clash = self.create_app(slug="same")
        self.assertEqual(clash.status_code, 409)
        self.assertIn("already used", clash.json()["error"])
        first = self.make_app(name="Repeat")
        second = self.make_app(name="Repeat")
        self.assertEqual((first["slug"], second["slug"]), ("repeat", "repeat-2"))

    def test_an_app_can_only_point_at_an_approved_model(self) -> None:
        candidate = self.train_candidate_after_approved()
        for stage in ("candidate", "staged"):
            ModelVersion.objects.filter(pk=candidate.pk).update(stage=stage)
            response = self.create_app(model_version_id=str(candidate.id))
            self.assertEqual(response.status_code, 409)
            self.assertIn("only use an approved model", response.json()["error"])
        self.assertEqual(InferenceApp.objects.count(), 0)

    def test_patch_updates_config_and_repoints_only_to_an_approved_model(self) -> None:
        app = self.make_app()
        url = self.apps_url(f"/{app['id']}")
        patched = self.client.patch(url, data=json.dumps({"config": {"confidence_threshold": 0.9}, "name": "Renamed"}),
                                    content_type="application/json")
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["app"]["config"]["confidence_threshold"], 0.9)
        self.assertEqual(patched.json()["app"]["config"]["displayed_labels"], ["damaged", "normal"])  # untouched
        self.assertEqual(patched.json()["app"]["name"], "Renamed")
        bad = self.client.patch(url, data=json.dumps({"config": {"confidence_threshold": 7}}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        candidate = self.train_candidate_after_approved()
        refused = self.client.patch(url, data=json.dumps({"model_version_id": str(candidate.id)}), content_type="application/json")
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(InferenceApp.objects.get().model_version_id, self.model.id)

    def test_delete_is_refused_once_evidence_exists(self) -> None:
        app = self.make_app()
        self.assertEqual(self.client.delete(self.apps_url(f"/{app['id']}")).json(), {"deleted": True})
        app, _ = self.finished_observation(name="Used")
        refused = self.client.delete(self.apps_url(f"/{app['id']}"))
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Archive it instead", refused.json()["error"])
        archived = self.client.patch(self.apps_url(f"/{app['id']}"), data=json.dumps({"status": "archived"}),
                                     content_type="application/json")
        self.assertEqual(archived.json()["app"]["status"], "archived")
        self.assertEqual(self.run_image(app["id"]).status_code, 409)

    def test_listing(self) -> None:
        self.make_app(name="B app")
        self.make_app(name="A app")
        self.assertEqual([a["name"] for a in self.client.get(self.apps_url()).json()["apps"]], ["A app", "B app"])


class PreviewTests(InferenceBase):
    def test_preview_is_the_render_model_the_ui_uses(self) -> None:
        app = self.make_app(config={"status_texts": {"clear": "All good"}})
        preview = self.client.get(self.apps_url(f"/{app['id']}/preview")).json()["preview"]
        self.assertEqual(preview["title"], "Line 4 check")
        self.assertIn("not a final determination", preview["disclaimer"])
        self.assertEqual(preview["model"]["id"], str(self.model.id))
        self.assertEqual(preview["model"]["artifact_sha256"], self.model.artifact_sha256)
        self.assertTrue(preview["model"]["evaluation"]["available"])
        self.assertTrue(preview["human_review_required"])
        self.assertIsNone(preview["result"])
        states = {e["state"]: e for e in preview["examples"]}
        self.assertEqual(states["clear"]["status_text"], "All good")
        self.assertTrue(all(e["awaiting_human_decision"] and e["example_only"] for e in preview["examples"]))
        # a rendered result uses the same keys as the preview
        _, observation_id = self.finished_observation()
        rendered = self.result(*self.first_app_and(observation_id))
        self.assertLessEqual(set(preview) - {"examples"}, set(rendered))

    def first_app_and(self, observation_id):
        return str(InferenceApp.objects.order_by("-created_at").first().id), observation_id

    def test_disclaimer_is_present_even_when_review_is_not_required(self) -> None:
        app = self.make_app(config={"human_review_required": False})
        preview = self.client.get(self.apps_url(f"/{app['id']}/preview")).json()["preview"]
        self.assertIn("not a final determination", preview["disclaimer"])
        self.assertFalse(any(e["awaiting_human_decision"] for e in preview["examples"]))


class RunTests(InferenceBase):
    def test_run_is_queued_then_processed_by_the_worker_and_pins_the_exact_model(self) -> None:
        app = self.make_app()
        response = self.run_image(app["id"], process=False)
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertIn(body["observation_id"], body["poll_url"])
        queued = self.result(app["id"], body["observation_id"])
        self.assertEqual(queued["result"]["state"], "processing")
        self.assertNotEqual(queued["result"]["status_text"], queued["status_texts"]["clear"])

        observation = inference_runner.claim_next_observation()
        done = inference_runner.process_observation(observation)
        self.assertEqual(done.status, "succeeded")
        self.assertEqual(done.model_version_id, self.model.id)
        self.assertEqual(len(done.predictions), 1)
        self.assertEqual(done.predictions[0]["label"], "damaged")
        self.assertEqual(done.predictions[0]["index"], 0)
        self.assertEqual(done.config_snapshot, app["config"])
        self.assertEqual(len(done.image_sha256), 64)
        stored = Path(self.media.name) / done.image_key
        self.assertTrue(stored.is_file())

    def test_high_impact_guard_every_result_needs_review_until_a_person_decides(self) -> None:
        for boxes, machine in (([BoxPrediction("damaged", 0.1, 0.1, 0.2, 0.2, 0.9)], "flagged"), ([], "clear")):
            FakeBackend.boxes = boxes
            app, observation_id = self.finished_observation(name=f"Guard {machine}")
            result = self.result(app["id"], observation_id)["result"]
            self.assertEqual(result["machine_state"], machine)
            self.assertEqual(result["state"], "needs_review", "nothing may be shown as clear/flagged before a decision")
            self.assertEqual(result["status_text"], "Needs review by a person")
            self.assertTrue(result["awaiting_human_decision"])
            self.assertIsNone(result["decision"])

    def test_confirming_reveals_the_machine_state_and_second_look_keeps_review(self) -> None:
        app, observation_id = self.finished_observation()
        url = self.observation_url(app["id"], observation_id, "/decision")
        look = self.post(url, {"decision": "needs_second_look"})
        self.assertEqual(look.status_code, 201)
        self.assertEqual(look.json()["render"]["result"]["state"], "needs_review")
        confirmed = self.post(url, {"decision": "confirm"})
        result = confirmed.json()["render"]["result"]
        self.assertEqual(result["state"], "flagged")
        self.assertFalse(result["awaiting_human_decision"])
        self.assertEqual(result["decision"]["by"], f"u{self.user.pk}")
        self.assertEqual(ReviewDecision.objects.count(), 2)  # history is kept

    def test_without_required_review_the_machine_state_is_shown_directly(self) -> None:
        app, observation_id = self.finished_observation(config={"human_review_required": False})
        result = self.result(app["id"], observation_id)["result"]
        self.assertEqual(result["state"], "flagged")
        self.assertFalse(result["awaiting_human_decision"])

    def test_reject_needs_a_note_when_configured_and_leaves_the_result_in_review(self) -> None:
        app, observation_id = self.finished_observation()
        url = self.observation_url(app["id"], observation_id, "/decision")
        missing = self.post(url, {"decision": "reject"})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("note", missing.json()["error"])
        ok = self.post(url, {"decision": "reject", "note": "That is a shadow, not damage."})
        result = ok.json()["render"]["result"]
        self.assertEqual(result["state"], "needs_review")
        self.assertTrue(result["result_rejected"])
        self.assertEqual(result["decision"]["note"], "That is a shadow, not damage.")

    def test_reject_without_note_is_allowed_when_not_required(self) -> None:
        app, observation_id = self.finished_observation(
            config={"decision_controls": {"required_note_on_override": False}})
        url = self.observation_url(app["id"], observation_id, "/decision")
        self.assertEqual(self.post(url, {"decision": "reject"}).status_code, 201)

    def test_decisions_are_limited_to_the_enabled_controls(self) -> None:
        app, observation_id = self.finished_observation(
            config={"decision_controls": {"options": ["confirm", "reject"]}})
        url = self.observation_url(app["id"], observation_id, "/decision")
        self.assertEqual(self.post(url, {"decision": "needs_second_look"}).status_code, 400)
        self.assertEqual(self.post(url, {"decision": "approve"}).status_code, 400)
        self.assertEqual(self.post(url, {"extra": 1}).status_code, 400)

    def test_changing_the_app_later_does_not_change_old_results(self) -> None:
        app, observation_id = self.finished_observation()
        self.client.patch(self.apps_url(f"/{app['id']}"), data=json.dumps({"config": {"confidence_threshold": 0.99,
                          "status_texts": {"flagged": "Changed wording"}}}), content_type="application/json")
        result = self.result(app["id"], observation_id)
        self.assertEqual(result["confidence_threshold"], 0.5)
        self.assertNotEqual(result["status_texts"]["flagged"], "Changed wording")
        self.assertEqual(result["result"]["detections"][0]["label"], "damaged")

    def test_threshold_and_displayed_labels_filter_detections_but_keep_hidden_counts(self) -> None:
        FakeBackend.boxes = [
            BoxPrediction("damaged", 0.1, 0.1, 0.2, 0.2, 0.9),
            BoxPrediction("damaged", 0.5, 0.5, 0.2, 0.2, 0.3),
            BoxPrediction("normal", 0.3, 0.3, 0.2, 0.2, 0.8),
        ]
        app, observation_id = self.finished_observation(config={"displayed_labels": ["damaged"]})
        result = self.result(app["id"], observation_id)["result"]
        self.assertEqual([d["score"] for d in result["detections"]], [0.9])
        self.assertEqual((result["hidden_below_threshold"], result["hidden_other_labels"]), (1, 1))

    def test_pinned_model_stops_running_when_it_is_no_longer_approved_and_can_be_repointed(self) -> None:
        app = self.make_app()
        _, first_observation = app, self.run_image(app["id"]).json()["observation_id"]
        newer = self.train_candidate_after_approved()
        lifecycle.stage_model(newer, self.user)
        response = self.post(self.api(f"/models/{newer.id}/approve"), {"note": "Better recall."})
        self.assertEqual([a["id"] for a in response.json()["apps_still_on_retired_model"]], [app["id"]])

        refused = self.run_image(app["id"])
        self.assertEqual(refused.status_code, 409)
        self.assertIn("no longer approved", refused.json()["error"])
        self.assertIn("model_version_id", refused.json()["error"])
        # old result still shows the exact version that produced it, and says it is retired
        old = self.result(app["id"], first_observation)
        self.assertEqual(old["model"]["id"], str(self.model.id))
        self.assertEqual(old["model"]["stage_now"], "retired")
        self.assertFalse(old["model_in_use"])

        repoint = self.client.patch(self.apps_url(f"/{app['id']}"), data=json.dumps({"model_version_id": str(newer.id)}),
                                    content_type="application/json")
        self.assertEqual(repoint.status_code, 200)
        fresh = self.run_image(app["id"])
        self.assertEqual(fresh.status_code, 202)
        self.assertEqual(self.result(app["id"], fresh.json()["observation_id"])["model"]["id"], str(newer.id))

    def test_backend_unavailable_returns_503_with_how_to_enable_and_stores_nothing(self) -> None:
        app = self.make_app()
        FakeBackend.available = False
        response = self.run_image(app["id"])
        self.assertEqual(response.status_code, 503)
        error = response.json()["error"]
        self.assertIn("no prediction was made", error)
        self.assertIn("Install the fake framework", error)
        self.assertIn("inference_worker", error)
        self.assertEqual(ObservationRecord.objects.count(), 0)
        # a live inference worker that has the framework makes it available
        lifecycle.record_worker_heartbeat(lifecycle.INFERENCE_WORKER_KEY, "ready", {"fake-backend": True})
        self.assertEqual(self.run_image(app["id"], process=False).status_code, 202)

    def test_a_model_whose_backend_is_not_installed_gets_a_plain_503_not_a_500(self) -> None:
        # Found by the browser smoke run: the stand-in backend that made a seeded model was not installed in the web process.
        from . import training

        app = self.make_app()
        training.unregister_backend(FakeBackend.name)
        training._loaded = True  # keep the registry as it is: no re-discovery
        response = self.run_image(app["id"], process=False)
        self.assertEqual(response.status_code, 503, response.content[:200])
        self.assertIn("no prediction was made", response.json()["error"])
        self.assertIn("not installed", response.json()["error"])
        self.assertEqual(ObservationRecord.objects.count(), 0)

    def test_upload_validation(self) -> None:
        app = self.make_app(config={"accepted_inputs": ["image/png"]})
        url = self.apps_url(f"/{app['id']}/run")
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertEqual(self.client.post(url, {"image": SimpleUploadedFile("x.png", b"", content_type="image/png")}).status_code, 400)
        bad = self.client.post(url, {"image": SimpleUploadedFile("x.png", b"not an image at all", content_type="image/png")})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not a valid supported image", bad.json()["error"])
        jpeg = self.client.post(url, {"image": image_upload("a.jpg", "JPEG")})
        self.assertEqual(jpeg.status_code, 415)
        self.assertIn("accepts image/png", jpeg.json()["error"])
        two = self.client.post(url, {"image": [image_upload("a.png"), image_upload("b.png")]})
        self.assertEqual(two.status_code, 400)
        other_field = self.client.post(url, {"file": image_upload()})
        self.assertEqual(other_field.status_code, 400)
        self.assertEqual(ObservationRecord.objects.count(), 0)
        self.assertEqual(list((Path(self.media.name) / "inference").rglob("*.*")) if (Path(self.media.name) / "inference").exists() else [], [])

    def test_webp_and_jpeg_are_accepted_when_enabled(self) -> None:
        app = self.make_app()
        for name, fmt in (("a.jpg", "JPEG"), ("b.webp", "WEBP")):
            self.assertEqual(self.run_image(app["id"], image_upload(name, fmt, color=(10, 20, 30 if fmt == "JPEG" else 40))).status_code, 202)

    def test_queue_limit(self) -> None:
        app = self.make_app()
        with override_settings(TRAINING={"INFERENCE_QUEUE_LIMIT": 1}):
            self.assertEqual(self.run_image(app["id"], process=False).status_code, 202)
            second = self.run_image(app["id"], process=False)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Too many images", second.json()["error"])

    def test_prediction_failure_is_stored_plainly_and_is_never_shown_as_clear(self) -> None:
        app = self.make_app()

        class Boom(FakeBackend):
            def predict(self, artifact, image, thresholds):
                raise RuntimeError(f"weights unreadable at {artifact}")

        observation_id = self.run_image(app["id"], process=False).json()["observation_id"]
        row = inference_runner.claim_next_observation()
        inference_runner.process_observation(row, backend=Boom())
        result = self.result(app["id"], observation_id)["result"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertIn("could not be analysed", result["status_text"])
        self.assertNotIn(self.home.name, result["error"])
        decision = self.post(self.observation_url(app["id"], observation_id, "/decision"), {"decision": "confirm"})
        self.assertEqual(decision.status_code, 409)

    def test_tampered_model_file_fails_the_prediction(self) -> None:
        app = self.make_app()
        (Path(self.home.name) / self.model.artifact_path).write_bytes(b"swapped")
        observation_id = self.run_image(app["id"]).json()["observation_id"]
        result = self.result(app["id"], observation_id)["result"]
        self.assertEqual(result["state"], "failed")
        self.assertIn("no longer matches the checksum", result["error"])

    def test_inference_worker_command_once_and_orphan_recovery(self) -> None:
        app = self.make_app()
        observation_id = self.run_image(app["id"], process=False).json()["observation_id"]
        out = StringIO()
        call_command("inference_worker", "--once", stdout=out)
        self.assertIn("succeeded", out.getvalue())
        self.assertEqual(self.result(app["id"], observation_id)["result"]["status"], "succeeded")
        ObservationRecord.objects.filter(pk=observation_id).update(status="running")
        self.assertEqual(inference_runner.fail_orphaned_observations(), 1)

    def test_image_endpoint_serves_the_stored_image_and_no_response_leaks_paths(self) -> None:
        app, observation_id = self.finished_observation()
        image = self.client.get(self.observation_url(app["id"], observation_id, "/image"))
        self.assertEqual((image.status_code, image["Content-Type"]), (200, "image/png"))
        self.assertEqual(image["X-Content-Type-Options"], "nosniff")
        for suffix in ("", f"/{app['id']}", f"/{app['id']}/preview", f"/{app['id']}/observations",
                       f"/{app['id']}/observations/{observation_id}", f"/{app['id']}/feedback/export"):
            text = self.client.get(self.apps_url(suffix)).content.decode()
            for secret in (self.home.name, self.media.name, "model.bin", "image_key", "inference/"):
                self.assertNotIn(secret, text, f"{suffix}: {secret}")


class FeedbackTests(InferenceBase):
    def setUp(self) -> None:
        super().setUp()
        self.app, self.observation_id = self.finished_observation()

    def feedback(self, payload, observation_id=None):
        return self.post(self.observation_url(self.app["id"], observation_id or self.observation_id, "/feedback"), payload)

    def test_true_positive_on_a_shown_detection(self) -> None:
        response = self.feedback({"prediction_index": 0, "verdict": "true_positive"})
        self.assertEqual(response.status_code, 201, response.content)
        record = FeedbackRecord.objects.get()
        self.assertEqual((record.verdict, record.prediction_index, record.label), ("true_positive", 0, "damaged"))
        self.assertEqual(record.decided_by_id, self.user.pk)
        self.assertIn("does not change the model", response.json()["message"])
        shown = self.result(self.app["id"], self.observation_id)["result"]["feedback"]
        self.assertEqual(shown[0]["verdict"], "true_positive")

    def test_false_positive_needs_a_note_when_configured(self) -> None:
        missing = self.feedback({"prediction_index": 0, "verdict": "false_positive"})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("note", missing.json()["error"])
        self.assertEqual(self.feedback({"prediction_index": 0, "verdict": "false_positive", "note": "Reflection."}).status_code, 201)

    def test_missed_object_with_label_and_optional_box(self) -> None:
        with_box = self.feedback({"prediction_index": "missed", "label": "normal", "note": "Second item was missed.",
                                  "box": {"x": 0.5, "y": 0.5, "width": 0.2, "height": 0.2}})
        self.assertEqual(with_box.status_code, 201, with_box.content)
        record = FeedbackRecord.objects.get(verdict="false_negative")
        self.assertIsNone(record.prediction_index)
        self.assertEqual((record.label, record.box["width"]), ("normal", 0.2))
        no_box = self.feedback({"prediction_index": "missed", "label": "damaged", "note": "No box needed."})
        self.assertEqual(no_box.status_code, 201)

    def test_invalid_feedback_is_refused(self) -> None:
        cases = [
            {"prediction_index": 5, "verdict": "true_positive"},
            {"prediction_index": -1, "verdict": "true_positive"},
            {"prediction_index": True, "verdict": "true_positive"},
            {"prediction_index": "0", "verdict": "true_positive"},
            {"prediction_index": 0, "verdict": "false_negative"},
            {"prediction_index": 0, "verdict": "bogus"},
            {"prediction_index": 0, "verdict": "true_positive", "box": {"x": 0, "y": 0, "width": 1, "height": 1}},
            {"prediction_index": "missed", "verdict": "true_positive", "label": "damaged", "note": "x"},
            {"prediction_index": "missed", "note": "x"},
            {"prediction_index": "missed", "label": "ghost", "note": "x"},
            {"prediction_index": "missed", "label": "damaged", "note": "x", "box": {"x": 0.9, "y": 0, "width": 0.5, "height": 0.1}},
            {"prediction_index": "missed", "label": "damaged", "note": "x", "box": {"x": 0, "y": 0, "width": 0, "height": 0.1}},
            {"prediction_index": "missed", "label": "damaged", "note": "x", "box": {"x": 0}},
            {"prediction_index": 0, "verdict": "true_positive", "surprise": 1},
            {"prediction_index": 0, "verdict": "true_positive", "note": "x" * 3000},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(self.feedback(payload).status_code, 400, payload)
        self.assertEqual(FeedbackRecord.objects.count(), 0)

    def test_hidden_detections_cannot_receive_feedback(self) -> None:
        FakeBackend.boxes = [BoxPrediction("damaged", 0.1, 0.1, 0.2, 0.2, 0.9), BoxPrediction("damaged", 0.5, 0.5, 0.2, 0.2, 0.2)]
        app, observation_id = self.finished_observation(name="Two boxes")
        url = self.observation_url(app["id"], observation_id, "/feedback")
        self.assertEqual(self.post(url, {"prediction_index": 1, "verdict": "true_positive"}).status_code, 400)
        self.assertEqual(self.post(url, {"prediction_index": 0, "verdict": "true_positive"}).status_code, 201)

    def test_feedback_is_immutable(self) -> None:
        self.feedback({"prediction_index": 0, "verdict": "true_positive"})
        record = FeedbackRecord.objects.get()
        record.note = "rewritten"
        with self.assertRaises(ValueError):
            record.save()
        with self.assertRaises(ValueError):
            record.delete()
        with self.assertRaises(ValueError):
            FeedbackRecord.objects.update(verdict="false_positive")
        with self.assertRaises(ValueError):
            ReviewDecision.objects.all().delete()

    def test_feedback_on_unfinished_observation_is_refused(self) -> None:
        queued = self.run_image(self.app["id"], process=False).json()["observation_id"]
        response = self.feedback({"prediction_index": 0, "verdict": "true_positive"}, queued)
        self.assertEqual(response.status_code, 409)
        self.assertIn("no finished result", response.json()["error"])

    def test_export_carries_exact_observation_hash_and_model_lineage(self) -> None:
        self.feedback({"prediction_index": 0, "verdict": "false_positive", "note": "A shadow."})
        self.feedback({"prediction_index": "missed", "label": "normal", "note": "Missed a second part."})
        self.post(self.observation_url(self.app["id"], self.observation_id, "/decision"), {"decision": "confirm"})
        observation = ObservationRecord.objects.get(pk=self.observation_id)
        body = self.client.get(self.apps_url(f"/{self.app['id']}/feedback/export")).json()
        self.assertEqual(body["count"], 2)
        self.assertIn("NOT part of any dataset", body["notice"])
        row = body["candidates"][0]
        self.assertEqual(row["schema"], "athena.feedback-candidate/1")
        self.assertEqual(row["observation"]["image_sha256"], observation.image_sha256)
        self.assertEqual(row["observation"]["id"], self.observation_id)
        self.assertEqual(row["observation"]["review_decision"]["decision"], "confirm")
        model = row["model_version"]
        run = self.model.training_run
        self.assertEqual(model["id"], str(self.model.id))
        self.assertEqual(model["artifact_sha256"], self.model.artifact_sha256)
        self.assertEqual(model["dataset_manifest_sha256"], run.dataset_manifest_sha256)
        self.assertEqual(model["training_run_id"], str(run.id))
        self.assertEqual(model["config_sha256"], run.config_sha256)
        self.assertEqual((row["verdict"], row["prediction"]["label"], row["decided_by"]), ("false_positive", "damaged", f"u{self.user.pk}"))
        self.assertEqual(row["status"], "candidate_not_in_any_dataset")
        self.assertEqual(body["candidates"][1]["verdict"], "false_negative")

    def test_export_jsonl_and_format_validation(self) -> None:
        self.feedback({"prediction_index": 0, "verdict": "true_positive"})
        response = self.client.get(self.apps_url(f"/{self.app['id']}/feedback/export?format=jsonl"))
        self.assertEqual(response["Content-Type"], "application/x-ndjson; charset=utf-8")
        lines = response.content.decode().strip().split("\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["verdict"], "true_positive")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(self.client.get(self.apps_url(f"/{self.app['id']}/feedback/export?format=csv")).status_code, 400)

    def test_a_later_verdict_supersedes_an_earlier_one_in_the_default_export(self) -> None:
        self.feedback({"prediction_index": 0, "verdict": "false_positive", "note": "Thought it was a shadow."})
        self.feedback({"prediction_index": 0, "verdict": "true_positive"})
        default = self.client.get(self.apps_url(f"/{self.app['id']}/feedback/export")).json()
        self.assertEqual([r["verdict"] for r in default["candidates"]], ["true_positive"])
        everything = self.client.get(self.apps_url(f"/{self.app['id']}/feedback/export?include_superseded=1")).json()
        self.assertEqual([(r["verdict"], r["effective"]) for r in everything["candidates"]],
                         [("false_positive", False), ("true_positive", True)])

    def test_feedback_never_changes_the_model_or_adds_data_anywhere(self) -> None:
        before = ModelVersion.objects.get(pk=self.model.pk)
        counts = (DatasetVersion.objects.count(), TrainingRun.objects.count(), BoxAnnotation.objects.count(),
                  ModelVersion.objects.count(), ModelEvent.objects.count())
        self.feedback({"prediction_index": 0, "verdict": "false_positive", "note": "Not damage."})
        self.feedback({"prediction_index": "missed", "label": "normal", "note": "Missed."})
        self.post(self.observation_url(self.app["id"], self.observation_id, "/decision"),
                  {"decision": "reject", "note": "Overriding."})
        self.client.get(self.apps_url(f"/{self.app['id']}/feedback/export"))
        after = ModelVersion.objects.get(pk=self.model.pk)
        self.assertEqual((after.artifact_sha256, after.stage, after.evaluation), (before.artifact_sha256, "approved", before.evaluation))
        self.assertEqual(after.approved_at, before.approved_at)
        self.assertEqual(lifecycle.sha256_file(lifecycle.artifact_file(after)), before.artifact_sha256)
        self.assertEqual(counts, (DatasetVersion.objects.count(), TrainingRun.objects.count(), BoxAnnotation.objects.count(),
                                  ModelVersion.objects.count(), ModelEvent.objects.count()))
        self.assertEqual(self.app["model_version"]["id"], str(after.id))

    def test_deleting_a_project_with_apps_and_feedback_works(self) -> None:
        self.feedback({"prediction_index": 0, "verdict": "true_positive"})
        self.project.delete()
        self.assertEqual((InferenceApp.objects.count(), ObservationRecord.objects.count(), FeedbackRecord.objects.count()), (0, 0, 0))


class MeasuredWordingTests(InferenceBase):
    """W4-T: ``confidence_wording`` and ``suggested_threshold`` in the render model, from the pinned model's stored
    ``operating_points``. SYNTHETIC numbers only. The JS does not use them yet; this pins the contract."""

    NOTE = "Scores are the computer's own numbers; they have not been checked against pictures it has not seen."

    def preview(self, app_id: str) -> dict:
        return self.client.get(self.apps_url(f"/{app_id}/preview")).json()["preview"]

    def give_model_points(self, doc=None, *, drop: bool = False) -> None:
        from .tests_operating_points import synthetic_operating_points

        evaluation = {k: v for k, v in self.model.evaluation.items() if k != "operating_points"}
        if not drop:
            evaluation["operating_points"] = doc or synthetic_operating_points()
        self.model = self.set_evaluation(self.model, evaluation)

    def test_a_tiny_trained_model_is_not_measured_and_nothing_is_suggested(self) -> None:
        self.assertIn("operating_points", self.model.evaluation)  # trained through the real runner, 14 pictures
        preview = self.preview(self.make_app()["id"])
        self.assertEqual(preview["confidence_wording"], {"measured": False, "bands": [], "note": self.NOTE})
        suggested = preview["suggested_threshold"]
        self.assertEqual(set(suggested), {"value", "target_recall", "basis", "enough_data", "plain"})
        self.assertIsNone(suggested["value"])
        self.assertEqual((suggested["target_recall"], suggested["basis"], suggested["enough_data"]), (0.8, "validation split", False))
        self.assertIn("Too few checking boxes to choose a threshold you can trust", suggested["plain"])

    def test_an_old_model_without_the_key_says_not_measured(self) -> None:
        self.give_model_points(drop=True)
        preview = self.preview(self.make_app()["id"])
        self.assertEqual(preview["confidence_wording"], {"measured": False, "bands": [], "note": self.NOTE})
        self.assertEqual(
            preview["suggested_threshold"]["value"], None
        )
        self.assertEqual(preview["suggested_threshold"]["basis"], "not measured")
        self.assertIsNone(preview["suggested_threshold"]["target_recall"])
        self.assertIn("not measured", preview["suggested_threshold"]["plain"])
        self.assertIn("evaluation", preview["model"])  # the earlier keys are all still there
        self.assertTrue(preview["model"]["evaluation"]["available"])

    def test_a_block_made_without_final_test_pictures_suggests_no_value(self) -> None:
        from .tests_operating_points import synthetic_split
        from .training import operating_points as op

        gt, pred = synthetic_split(1, 260)
        doc = op.build_operating_points(gt, pred, None, None, ["a", "b"])
        self.assertGreaterEqual(doc["ground_truth_boxes"], op.MIN_VALIDATION_BOXES)  # enough boxes, but nothing to check on
        self.give_model_points(doc)
        suggested = self.preview(self.make_app()["id"])["suggested_threshold"]
        self.assertEqual(set(suggested), schema.SUGGESTED_THRESHOLD_KEYS)
        self.assertIsNone(suggested["value"])
        self.assertFalse(suggested["enough_data"])
        self.assertIn("not been checked on pictures the model did not see", suggested["plain"])

    def test_a_stored_block_with_a_cutoff_outside_zero_to_one_is_not_served_and_suggests_nothing(self) -> None:
        from .tests_operating_points import synthetic_operating_points

        doc = synthetic_operating_points()  # (the database itself refuses NaN, so a valid-JSON bad number stands in)
        doc["suggestions"][0]["threshold"] = 1.5
        self.give_model_points(doc)
        preview = self.preview(self.make_app()["id"])
        self.assertFalse(preview["confidence_wording"]["measured"])
        self.assertIsNone(preview["suggested_threshold"]["value"])
        self.assertIn("could not be read", preview["suggested_threshold"]["plain"])

    def test_a_model_with_enough_measurements_gets_quantitative_bands_and_a_suggested_cutoff(self) -> None:
        from .tests_operating_points import synthetic_operating_points

        doc = synthetic_operating_points()
        self.give_model_points(doc)
        app = self.make_app()
        preview = self.preview(app["id"])
        wording = preview["confidence_wording"]
        self.assertTrue(wording["measured"])
        self.assertEqual(set(wording), {"measured", "bands", "note"})
        self.assertEqual(len(wording["bands"]), len(doc["bucket_precision"]))
        previous_high = 0.0
        for band, stored in zip(wording["bands"], doc["bucket_precision"]):
            self.assertEqual(set(band), {"low", "high", "label", "precision", "n", "enough"})
            self.assertEqual((band["low"], band["high"], band["n"]), (stored["low"], stored["high"], stored["predictions"]))
            self.assertEqual(band["low"], previous_high)  # bands tile 0..1 with no gap
            previous_high = band["high"]
            self.assertEqual(band["enough"], stored["predictions"] >= 20)
            if band["enough"]:
                self.assertRegex(band["label"], r"^right .* in our check$")
            else:
                self.assertEqual(band["label"], "too few examples to say")
        self.assertEqual(previous_high, 1.0)
        self.assertTrue(any(b["enough"] for b in wording["bands"]))
        text = json.dumps(wording).lower()
        for old in ("very sure", "fairly sure", "somewhat sure", "not very sure", "accurate", "certified", "safe"):
            self.assertNotIn(old, text)
        suggested = preview["suggested_threshold"]
        self.assertEqual(suggested["value"], doc["suggestions"][0]["threshold"])
        self.assertEqual((suggested["target_recall"], suggested["enough_data"], suggested["basis"]), (0.8, True, "validation split"))
        self.assertEqual(suggested["plain"], doc["suggestions"][0]["plain"])

    def test_the_suggestion_is_never_applied_and_the_validator_still_takes_the_same_range(self) -> None:
        self.give_model_points()
        app = self.make_app()
        self.assertEqual(app["config"]["confidence_threshold"], 0.5)  # the person's (default) value, not the suggestion
        self.assertNotEqual(self.preview(app["id"])["suggested_threshold"]["value"], 0.5)
        self.assertEqual(self.preview(app["id"])["confidence_threshold"], 0.5)
        for value, ok in ((0, True), (1, True), (0.37, True), (1.01, False), (-0.01, False), (True, False), (float("nan"), False)):
            config = schema.default_config("x", ["damaged"])
            config["confidence_threshold"] = value
            if ok:
                self.assertEqual(schema.validate_config(config, ["damaged"])["confidence_threshold"], float(value))
            else:
                with self.assertRaises(schema.ConfigError):
                    schema.validate_config(config, ["damaged"])
        self.assertEqual(set(schema.CONFIG_KEYS), {"task_title", "accepted_inputs", "displayed_labels", "confidence_threshold", "status_texts", "decision_controls", "human_review_required"})

    def test_a_result_uses_the_wording_of_the_model_it_was_made_with(self) -> None:
        self.give_model_points()
        app, observation_id = self.finished_observation()
        shown = self.result(app["id"], observation_id)
        self.assertTrue(shown["confidence_wording"]["measured"])
        self.assertEqual(shown["confidence_wording"], self.preview(app["id"])["confidence_wording"])
        self.assertEqual(shown["suggested_threshold"], self.preview(app["id"])["suggested_threshold"])
        self.assertEqual(shown["confidence_threshold"], 0.5)

    def test_a_damaged_block_is_not_measured_and_does_not_break_the_page(self) -> None:
        for junk in ({"schema": "athena.operating_points/1", "bucket_precision": "x", "suggestions": 4}, 5, [], "x"):
            evaluation = {**self.model.evaluation, "operating_points": junk}
            self.model = self.set_evaluation(self.model, evaluation)
            app = self.make_app(name=f"Damaged {json.dumps(junk)[:20]}")
            preview = self.preview(app["id"])
            self.assertFalse(preview["confidence_wording"]["measured"])
            self.assertIsNone(preview["suggested_threshold"]["value"])


# ---------------------------------------------------------------------------------------
class RepointOnApprovalTests(InferenceBase):
    """W12-G2a: approving (or rolling back) can EXPLICITLY re-point check screens; nothing is ever silent."""

    def staged_next(self) -> ModelVersion:
        candidate = self.train_candidate_after_approved()
        lifecycle.stage_model(candidate, self.user)
        return candidate

    def approve_with(self, model, **extra):
        return self.post(self.api(f"/models/{model.id}/approve"), {"note": "Better recall.", **extra})

    def rollback_with(self, model, **extra):
        return self.post(self.api(f"/models/{model.id}/rollback"), {"note": "Went back.", **extra})

    def pinned(self, app) -> uuid.UUID:
        return InferenceApp.objects.get(pk=app["id"]).model_version_id

    def set_labels(self, model, labels) -> None:
        with connection.cursor() as cursor:  # a model's labels are write-once for the ORM; simulate another label list
            cursor.execute(
                f"UPDATE {ModelVersion._meta.db_table} SET label_names = %s WHERE id = %s",
                [json.dumps(labels), model.pk.hex if hasattr(model.pk, "hex") else model.pk],
            )
        model.refresh_from_db()

    def untouched_snapshot(self):
        return (
            list(ObservationRecord.objects.order_by("id").values()),
            list(FeedbackRecord.objects.order_by("id").values()),
            [(m.id, m.artifact_sha256, lifecycle.sha256_file(lifecycle.artifact_file(m))) for m in ModelVersion.objects.order_by("id")],
        )

    # -- without the flag nothing changes ------------------------------------------------------------------------
    def test_without_the_flag_the_screens_stay_pinned_exactly_as_before(self) -> None:
        app = self.make_app()
        newer = self.staged_next()
        response = self.approve_with(newer)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["repointed"], body["repoint_skipped"]), ([], []))
        self.assertEqual([a["id"] for a in body["apps_still_on_retired_model"]], [app["id"]])
        self.assertIn("keep using the exact model version they were set up with", body["message"])
        self.assertEqual(self.pinned(app), self.model.id)
        self.assertFalse(ModelEvent.objects.filter(kind="apps_repointed").exists())
        self.assertNotIn("re-pointed", ModelEvent.objects.get(model_version=newer, kind="approved").note)
        self.assertEqual(self.run_image(app["id"]).status_code, 409)  # as before: the screen refuses
        for falsy in ({"repoint_apps": False}, {"repoint_app_ids": []}):
            again = self.staged_next()
            self.assertEqual(self.approve_with(again, **falsy).json()["repointed"], [])
            self.assertEqual(self.pinned(app), self.model.id)

    # -- one screen, with every side condition -------------------------------------------------------------------
    def test_one_screen_is_repointed_in_the_same_approval_and_everything_else_is_untouched(self) -> None:
        app = self.make_app(name="Night shift")
        first = self.run_image(app["id"]).json()["observation_id"]
        self.assertEqual(
            self.post(self.observation_url(app["id"], first, "/feedback"), {"prediction_index": 0, "verdict": "true_positive"}).status_code, 201
        )
        before = self.untouched_snapshot()
        config_before = InferenceApp.objects.get(pk=app["id"]).config
        newer = self.staged_next()
        response = self.approve_with(newer, repoint_apps=True)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["repointed"], [{"app_id": app["id"], "name": "Night shift"}])
        self.assertEqual(body["repoint_skipped"], [])
        self.assertEqual(body["apps_still_on_retired_model"], [])
        self.assertEqual([a["id"] for a in body["model"]["in_use_by_apps"]], [app["id"]])
        self.assertEqual(self.pinned(app), newer.id)
        self.assertEqual(InferenceApp.objects.get(pk=app["id"]).config, config_before)  # same strictly validated config
        # audit: one append-only line on the NEW model, after the approval line
        approved = ModelEvent.objects.get(model_version=newer, kind="approved")
        event = ModelEvent.objects.get(kind="apps_repointed")
        self.assertGreater(event.id, approved.id)
        self.assertEqual((event.model_version_id, event.actor_id_snapshot, event.project_id), (newer.id, self.user.pk, self.project.id))
        data = json.loads(next(line for line in event.note.splitlines() if line.startswith("{")))
        self.assertEqual(
            data,
            {"app_id": app["id"], "app_name": "Night shift", "from_model_id": str(self.model.id),
             "from_version": self.model.version, "to_model_id": str(newer.id), "to_version": newer.version},
        )
        self.assertIn("Better recall.", event.note)
        self.assertIn("Acted as Approver", event.note)
        self.assertIn("Check screens re-pointed to this model at approval: 1. Left alone: 0.", approved.note)
        # nothing else moved: old observations and feedback, both artifacts
        after = self.untouched_snapshot()
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1])
        self.assertEqual([row for row in after[2] if row[0] == self.model.id], [row for row in before[2] if row[0] == self.model.id])
        for model in ModelVersion.objects.all():
            self.assertEqual(lifecycle.sha256_file(lifecycle.artifact_file(model)), model.artifact_sha256)
        self.assertEqual(ObservationRecord.objects.get(pk=first).model_version_id, self.model.id)
        # the screen now runs on the new model, and says so
        fresh = self.run_image(app["id"])
        self.assertEqual(fresh.status_code, 202, fresh.content)
        shown = self.result(app["id"], fresh.json()["observation_id"])
        self.assertEqual((shown["model"]["id"], shown["result"]["state"]), (str(newer.id), "needs_review"))
        self.assertEqual(ObservationRecord.objects.get(pk=fresh.json()["observation_id"]).model_version_id, newer.id)
        self.assertEqual(self.result(app["id"], first)["model"]["id"], str(self.model.id))  # the old answer keeps its model

    def test_several_screens_are_repointed_together_and_an_archived_one_is_left_alone(self) -> None:
        apps = [self.make_app(name=name) for name in ("Bay A", "Bay B", "Bay C")]
        archived = self.make_app(name="Old bay")
        self.assertEqual(
            self.client.patch(self.apps_url(f"/{archived['id']}"), data=json.dumps({"status": "archived"}), content_type="application/json").status_code, 200
        )
        newer = self.staged_next()
        body = self.approve_with(newer, repoint_apps=True).json()
        self.assertEqual([r["name"] for r in body["repointed"]], ["Bay A", "Bay B", "Bay C"])
        self.assertEqual({r["app_id"] for r in body["repointed"]}, {a["id"] for a in apps})
        self.assertEqual(body["repoint_skipped"], [])
        for app in apps:
            self.assertEqual(self.pinned(app), newer.id)
            self.assertEqual(self.run_image(app["id"]).status_code, 202)
        self.assertEqual(self.pinned(archived), self.model.id)
        self.assertEqual(ModelEvent.objects.filter(kind="apps_repointed", model_version=newer).count(), 3)
        self.assertIn("re-pointed to this model at approval: 3. Left alone: 0.", ModelEvent.objects.get(model_version=newer, kind="approved").note)

    def test_a_screen_whose_labels_do_not_fit_is_skipped_with_a_reason_and_the_approval_still_succeeds(self) -> None:
        self.set_labels(self.model, ["damaged", "dent"])  # the model in use knows a label the next model will not
        fits = self.make_app(name="Fits", config={"displayed_labels": ["damaged"]})
        stuck = self.make_app(name="Shows dents")  # shows every label of the model in use, "dent" included
        newer = self.staged_next()
        self.assertEqual(newer.label_names, ["damaged", "normal"])
        # BEFORE approval the detail already tells which screens would move and which could not
        detail = self.client.get(self.api(f"/models/{newer.id}")).json()["model"]
        would = {row["name"]: row for row in detail["would_repoint"]}
        self.assertEqual(set(would), {"Fits", "Shows dents"})
        self.assertEqual((would["Fits"]["can_repoint"], would["Fits"]["reason"]), (True, None))
        self.assertFalse(would["Shows dents"]["can_repoint"])
        self.assertIn("dent", would["Shows dents"]["reason"])
        response = self.approve_with(newer, repoint_apps=True)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["repointed"], [{"app_id": fits["id"], "name": "Fits"}])
        self.assertEqual([(r["app_id"], r["name"]) for r in body["repoint_skipped"]], [(stuck["id"], "Shows dents")])
        self.assertEqual(body["repoint_skipped"][0]["reason"], would["Shows dents"]["reason"])  # the preview told the truth
        self.assertEqual(set(body["repoint_skipped"][0]), {"app_id", "name", "reason"})
        self.assertEqual((self.pinned(fits), self.pinned(stuck)), (newer.id, self.model.id))
        self.assertEqual([a["id"] for a in body["apps_still_on_retired_model"]], [stuck["id"]])
        self.assertEqual(ModelVersion.objects.get(pk=newer.id).stage, "approved")
        self.assertEqual(ModelEvent.objects.filter(kind="apps_repointed").count(), 1)  # only the one that moved
        self.assertIn("re-pointed to this model at approval: 1. Left alone: 1.", ModelEvent.objects.get(model_version=newer, kind="approved").note)
        # the skipped screen was not half-changed
        self.assertEqual(InferenceApp.objects.get(pk=stuck["id"]).config["displayed_labels"], ["damaged", "dent"])

    def test_would_repoint_is_read_only_lists_only_active_screens_on_the_model_in_use_and_only_for_a_staged_model(self) -> None:
        live = self.make_app(name="Live")
        archived = self.make_app(name="Archived")
        self.client.patch(self.apps_url(f"/{archived['id']}"), data=json.dumps({"status": "archived"}), content_type="application/json")
        candidate = self.train_candidate_after_approved()
        self.assertEqual(self.client.get(self.api(f"/models/{candidate.id}")).json()["model"]["would_repoint"], [])  # not staged
        lifecycle.stage_model(candidate, self.user)
        detail = self.client.get(self.api(f"/models/{candidate.id}")).json()["model"]
        self.assertEqual(detail["would_repoint"], [{"app_id": live["id"], "name": "Live", "can_repoint": True, "reason": None}])
        self.assertEqual(self.pinned(live), self.model.id)  # looking changed nothing
        self.assertEqual(self.client.get(self.api(f"/models/{self.model.id}")).json()["model"]["would_repoint"], [])
        self.approve_with(candidate, repoint_apps=True)
        self.assertEqual(self.client.get(self.api(f"/models/{candidate.id}")).json()["model"]["would_repoint"], [])

    # -- which screens, exactly ----------------------------------------------------------------------------------
    def test_a_screen_on_an_older_retired_model_moves_only_when_it_is_listed_explicitly(self) -> None:
        old = self.make_app(name="Left on v1")
        second = self.staged_next()
        self.assertEqual(self.approve_with(second).status_code, 200)  # no flag: "Left on v1" stays on the retired v1
        current = self.make_app(name="On v2", model_version_id=str(second.id))
        third = self.staged_next()
        body = self.approve_with(third, repoint_apps=True).json()
        self.assertEqual([r["name"] for r in body["repointed"]], ["On v2"])
        self.assertEqual((self.pinned(current), self.pinned(old)), (third.id, self.model.id))  # v1 is older than the retired v2
        self.assertEqual(self.client.get(self.api(f"/models/{self.model.id}")).json()["model"]["stage"], "retired")
        # now list it explicitly: allowed, it is pinned to a model that can no longer run
        fourth = self.staged_next()
        body = self.approve_with(fourth, repoint_app_ids=[old["id"]]).json()
        self.assertEqual(body["repointed"], [{"app_id": old["id"], "name": "Left on v1"}])
        self.assertEqual((self.pinned(old), self.pinned(current)), (fourth.id, third.id))  # "On v2" was not listed
        self.assertEqual([a["id"] for a in body["apps_still_on_retired_model"]], [current["id"]])

    def test_a_listed_subset_moves_only_those_screens(self) -> None:
        a, b, c = (self.make_app(name=f"Bay {n}") for n in "ABC")
        newer = self.staged_next()
        body = self.approve_with(newer, repoint_app_ids=[c["id"], a["id"], c["id"]]).json()  # order and repeats do not matter
        self.assertEqual(sorted(r["name"] for r in body["repointed"]), ["Bay A", "Bay C"])
        self.assertEqual((self.pinned(a), self.pinned(b), self.pinned(c)), (newer.id, self.model.id, newer.id))
        self.assertEqual([x["id"] for x in body["apps_still_on_retired_model"]], [b["id"]])

    def test_a_bad_request_is_refused_before_anything_is_written(self) -> None:
        app = self.make_app()
        other = Project.objects.create(name="Spare line", modality="image", task="Detect", labels=["damaged"])
        foreign = InferenceApp.objects.create(project=other, name="Elsewhere", slug="elsewhere", model_version=self.model, config={})
        newer = self.staged_next()
        bad = [
            {"repoint_apps": "yes"}, {"repoint_apps": 1}, {"repoint_apps": "true"}, {"repoint_apps": None},
            {"repoint_app_ids": app["id"]}, {"repoint_app_ids": [1]}, {"repoint_app_ids": ["nope"]},
            {"repoint_app_ids": [str(uuid.uuid4())]}, {"repoint_app_ids": [str(foreign.id)]},
            {"repoint_app_ids": [str(uuid.uuid4()) for _ in range(101)]},
            {"repoint_apps": True, "repoint_app_ids": [app["id"]]},
        ]
        for extra in bad:
            with self.subTest(extra=str(extra)[:60]):
                response = self.approve_with(newer, **extra)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertTrue(response.json()["error"])
        newer.refresh_from_db()
        self.assertEqual(newer.stage, "staged")
        self.assertEqual((self.pinned(app), InferenceApp.objects.get(pk=foreign.id).model_version_id), (self.model.id, self.model.id))
        self.assertFalse(ModelEvent.objects.filter(model_version=newer, kind__in=("approved", "apps_repointed")).exists())

    def test_the_weak_results_gate_still_comes_first_and_changes_nothing(self) -> None:
        app = self.make_app()
        FakeBackend.boxes = []
        weak = self.train_candidate_after_approved(solid=False)
        lifecycle.stage_model(weak, self.user)
        response = self.approve_with(weak, repoint_apps=True)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["needs_acknowledgement"])
        self.assertEqual(self.pinned(app), self.model.id)
        ok = self.approve_with(weak, repoint_apps=True, acknowledge_weak_results=True)
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(self.pinned(app), weak.id)
        note = ModelEvent.objects.get(model_version=weak, kind="approved").note
        self.assertIn("Weak results acknowledged", note)
        self.assertIn("re-pointed to this model at approval: 1.", note)

    # -- all or nothing ------------------------------------------------------------------------------------------
    def test_an_error_while_repointing_rolls_the_whole_approval_back(self) -> None:
        apps = [self.make_app(name=name) for name in ("Bay A", "Bay B")]
        newer = self.staged_next()
        real, calls = inference_service.update_app, []

        def flaky(app, **kwargs):
            calls.append(app.name)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(app, **kwargs)

        with mock.patch.object(inference_service, "update_app", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                lifecycle.approve_model(newer, self.user, "Better.", repoint_apps=True)
        self.assertEqual(len(calls), 2)  # the first one really was re-pointed before the failure
        newer.refresh_from_db()
        self.model.refresh_from_db()
        self.assertEqual((newer.stage, newer.approved_by_id, newer.previous_approved_id), ("staged", None, None))
        self.assertEqual((self.model.stage, self.model.retired_at), ("approved", None))
        self.assertEqual([self.pinned(a) for a in apps], [self.model.id, self.model.id])
        self.assertFalse(ModelEvent.objects.filter(kind__in=("approved", "retired", "apps_repointed"), model_version=newer).exists())
        self.assertFalse(ModelEvent.objects.filter(kind="retired").exists())
        # and the very same approval works afterwards
        self.assertEqual(self.approve_with(newer, repoint_apps=True).status_code, 200)
        self.assertEqual([self.pinned(a) for a in apps], [newer.id, newer.id])

    # -- rollback goes the other way -----------------------------------------------------------------------------
    def test_rollback_without_the_flag_leaves_the_screens_on_the_retired_model_as_before(self) -> None:
        app = self.make_app()
        newer = self.staged_next()
        self.approve_with(newer, repoint_apps=True)
        body = self.rollback_with(newer).json()
        self.assertEqual((body["repointed"], body["repoint_skipped"]), ([], []))
        self.assertEqual([a["id"] for a in body["apps_still_on_retired_model"]], [app["id"]])
        self.assertIn("will not run until re-pointed", body["message"])
        self.assertEqual(self.pinned(app), newer.id)
        self.assertEqual(ModelEvent.objects.filter(kind="apps_repointed").count(), 1)  # only the approval's

    def test_rollback_can_repoint_the_screens_back_to_the_restored_model(self) -> None:
        app = self.make_app(name="Night shift")
        other = self.make_app(name="Day shift")
        newer = self.staged_next()
        self.approve_with(newer, repoint_apps=True)
        response = self.rollback_with(newer, repoint_apps=True)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([(r["app_id"], r["name"]) for r in body["repointed"]], [(other["id"], "Day shift"), (app["id"], "Night shift")])
        self.assertEqual((body["repoint_skipped"], body["apps_still_on_retired_model"]), ([], []))
        self.assertEqual(body["model"]["id"], str(self.model.id))
        self.assertEqual((self.pinned(app), self.pinned(other)), (self.model.id, self.model.id))
        events = ModelEvent.objects.filter(kind="apps_repointed", model_version=self.model)
        self.assertEqual(events.count(), 2)
        data = json.loads(next(line for line in events.first().note.splitlines() if line.startswith("{")))
        self.assertEqual((data["from_model_id"], data["to_model_id"]), (str(newer.id), str(self.model.id)))
        restored = ModelEvent.objects.get(kind="rollback_restored")
        self.assertLess(restored.id, events.first().id)
        self.assertIn("re-pointed to this model by the rollback: 2. Left alone: 0.", restored.note)
        self.assertEqual(self.run_image(app["id"]).status_code, 202)
        self.assertEqual(
            self.result(app["id"], ObservationRecord.objects.latest("created_at").id)["model"]["id"], str(self.model.id)
        )

    def test_rollback_skips_a_screen_that_does_not_fit_the_restored_model(self) -> None:
        newer = self.staged_next()
        self.approve_with(newer)
        self.set_labels(newer, ["damaged", "normal", "extra"])  # the model being replaced knows a label the old one does not
        wide = self.make_app(name="Shows extra", model_version_id=str(newer.id))
        narrow = self.make_app(name="Narrow", model_version_id=str(newer.id), config={"displayed_labels": ["damaged"]})
        response = self.rollback_with(newer, repoint_apps=True)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["repointed"], [{"app_id": narrow["id"], "name": "Narrow"}])
        self.assertEqual([(r["app_id"], r["name"]) for r in body["repoint_skipped"]], [(wide["id"], "Shows extra")])
        self.assertIn("extra", body["repoint_skipped"][0]["reason"])
        self.assertEqual((self.pinned(narrow), self.pinned(wide)), (self.model.id, newer.id))
        self.assertEqual(ModelVersion.objects.get(pk=self.model.id).stage, "approved")  # the rollback itself still happened

    def test_an_error_while_repointing_during_a_rollback_undoes_the_rollback(self) -> None:
        newer = self.staged_next()
        self.approve_with(newer)
        mover = self.make_app(name="Mover", model_version_id=str(newer.id))
        with mock.patch.object(inference_service, "update_app", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                lifecycle.rollback_model(newer, self.user, "Back.", repoint_apps=True)
        newer.refresh_from_db()
        self.model.refresh_from_db()
        self.assertEqual((newer.stage, self.model.stage), ("approved", "retired"))
        self.assertEqual(self.pinned(mover), newer.id)
        self.assertFalse(ModelEvent.objects.filter(kind__in=("rollback_retired", "rollback_restored", "apps_repointed")).exists())

    def test_rollback_with_a_bad_request_is_refused_first(self) -> None:
        app = self.make_app()
        newer = self.staged_next()
        self.approve_with(newer)
        for extra in ({"repoint_apps": "yes"}, {"repoint_app_ids": ["nope"]}, {"repoint_apps": True, "repoint_app_ids": [app["id"]]}):
            self.assertEqual(self.rollback_with(newer, **extra).status_code, 400, extra)
        self.assertEqual(ModelVersion.objects.get(pk=newer.id).stage, "approved")
