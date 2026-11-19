"""The Teach panel's six-step flow for every recipe, not just vision detection.

Covers the generalization the OCR fixer honestly left out: readiness wording that speaks the
recipe's own language (clips for audio, pictures for OCR), the one-click ``snapshot: "latest"``
start on the generic snapshot service (audio and OCR projects), and one-click config defaults
that each backend resolves itself (the OCR backend gets ``pretrained: "auto"`` and translates it;
the audio backend, which has no ready-made starts, is sent no settings it does not know).

Nothing here downloads anything: the OCR model is stubbed exactly as ``tests_ocr`` stubs it, and
the audio runs use the real tiny CNN on synthetic tones only when torch is installed (they skip
cleanly otherwise). The vision one-click flow has its own extensive suites (``tests_training_auto``,
``tests_training_ui``); these tests only pin what changed for the other recipes.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest import mock, skipUnless

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from .models import DatasetVersion, Project, TrainingRun
from .datasets import generic as generic_service
from .datasets import splits
from .tests import SignedInMixin
from .tests_datasets import DatasetBase
from .tests_audio import TONES, write_sine_wav
from .tests_ocr import handwriting_png

ML_INSTALLED = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers"))
NEEDS_ML = skipUnless(ML_INSTALLED, "torch and transformers are not installed here; run in the ML venv")
TORCH_ONLY = skipUnless(importlib.util.find_spec("torch") is not None, "torch is not installed here")


class TeachGeneralBase(DatasetBase):
    """A signed-in user, a media sandbox, an isolated data home and cache (the OCR tests' pattern)."""

    def setUp(self) -> None:
        super().setUp()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        override = override_settings(
            DATA_HOME=Path(self.home.name),
            CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        )
        override.enable()
        self.addCleanup(override.disable)

    def readiness(self, project, query: str = "") -> dict:
        response = self.client.get(f"/api/projects/{project.id}/training/readiness{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def post_runs(self, project, payload: dict):
        return self.client.post(
            f"/api/projects/{project.id}/training/runs",
            data=json.dumps(payload),
            content_type="application/json",
        )


# -- gap 1 + 4: readiness speaks the recipe's language -----------------------------------------------------
class AudioProject(TeachGeneralBase):
    def make_audio_project(self, labelled: int = 6) -> Project:
        from .models import Asset, AssetAnnotation

        project = Project.objects.create(
            name="Motor sounds", modality="audio", task="Classify equipment sounds",
            recipe="audio-classification", labels=list(TONES),
        )
        for index in range(labelled):
            for label_index, (label, frequency) in enumerate(TONES.items()):
                if index % len(TONES) != label_index:
                    continue
                content = write_sine_wav(frequency=frequency, seed=index * 10 + label_index)
                asset_id = uuid.uuid4()
                key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.wav", io.BytesIO(content))
                Asset.objects.create(
                    id=asset_id, project=project, filename=f"clip-{index}-{label_index}.wav", storage_key=key,
                    content_type="audio/wav", sha256=__import__("hashlib").sha256(content).hexdigest(),
                    size_bytes=len(content), width=0, height=0,
                )
                AssetAnnotation.objects.create(asset_id=asset_id, kind="clip_labels", data={"label": label})
        return project

    def test_readiness_words_use_the_recipes_noun_not_boxes_or_images(self) -> None:
        project = self.make_audio_project()
        body = self.readiness(project)
        self.assertFalse(body["ready"], body)  # no dataset version yet: a blocker in the recipe's words
        self.assertIn("sound clips", body["blockers"][0])
        self.assertNotIn("boxes", body["blockers"][0])
        self.assertNotIn("images", body["blockers"][0])
        # The one-click preview's warning for an empty test split says clips too.
        joined = " ".join(body["one_click"]["blockers"]) + " ".join(body["one_click"]["warnings"])
        self.assertNotRegex(joined, r"\b(boxes|images)\b")

    def test_a_vision_project_keeps_the_picture_wording(self) -> None:
        body = self.readiness(self.project)  # DatasetBase's image project, no recipe
        self.assertIn("pictures", body["blockers"][0])

    def test_the_empty_test_warning_says_clips_for_audio(self) -> None:
        from .training import lifecycle

        project = self.make_audio_project()
        nouns = lifecycle.recipe_nouns(project)
        self.assertEqual(nouns, {"noun": "sound clip", "plural": "sound clips"})
        self.assertIn("sound clips", lifecycle.no_held_out_message(nouns))
        self.assertIn("sound clips", lifecycle.no_planned_train_message(nouns))
        # The vision default is the historical sentence, unchanged.
        self.assertEqual(lifecycle.no_held_out_message(None), lifecycle.NO_HELD_OUT_MESSAGE)


class OcrReadinessWordsTests(TeachGeneralBase):
    """An image-modality recipe (handwriting OCR) gets the real readiness, not the "not available
    yet" state the non-image dispatch used to hand every recipe - and its words are pictures,
    because that is what it trains on."""

    def make_ocr_project(self, labelled: int = 0) -> Project:
        from .models import Asset, AssetAnnotation

        project = Project.objects.create(
            name="Handwriting", modality="image", task="Read handwriting",
            recipe="handwriting-ocr", labels=["transcript"],
        )
        for index in range(labelled):
            content = handwriting_png(f"teach-{index}")
            asset_id = uuid.uuid4()
            key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.png", io.BytesIO(content))
            asset = Asset.objects.create(
                id=asset_id, project=project, filename=f"line-{index}.png", storage_key=key,
                content_type="image/png", sha256=__import__("hashlib").sha256(content).hexdigest(),
                size_bytes=len(content), width=64, height=32,
            )
            AssetAnnotation.objects.create(
                asset=asset, kind="transcripts", data={"label": "transcript", "text": f"baris {index}"}
            )
        return project
    def test_an_ocr_project_gets_the_real_readiness_with_picture_words(self) -> None:
        project = self.make_ocr_project()
        body = self.readiness(project)
        self.assertNotIn("task_unavailable", body)
        self.assertFalse(body["ready"])
        self.assertIn("pictures", body["blockers"][0])  # no dataset version yet
        # The generic service's own refusal (its words, no box language) comes through the one-click preview.
        self.assertTrue(any("no accepted labels" in line for line in body["one_click"]["blockers"]))
        self.assertFalse(any("boxes" in line for line in body["one_click"]["blockers"]))

    def test_the_not_available_state_is_still_served_to_a_recipe_without_tools(self) -> None:
        from .tasks import register_recipe, reload_recipes, unregister_recipe
        from .tasks.base import TaskRecipe

        register_recipe(TaskRecipe(
            key="teach-test-future", title="Future task", modality="video", summary="s",
            annotation_kind="frames", labels_help="h", available=False,
            unavailable_reason="Video projects are coming soon.",
        ), replace=True)
        self.addCleanup(unregister_recipe, "teach-test-future")
        self.addCleanup(reload_recipes)
        project = Project.objects.create(
            name="Future", modality="video", task="t", recipe="teach-test-future", labels=["a"]
        )
        body = self.readiness(project)
        self.assertTrue(body.get("task_unavailable"))
        self.assertIn("coming soon", body["blockers"][0])


# -- gap 2: the one-click snapshot path on the generic service ----------------------------------------------
@TORCH_ONLY
class OneClickSnapshotAudioTests(TeachGeneralBase):
    """``snapshot: "latest"`` on an audio project: analyse -> freeze (or reuse) -> queue, through
    the generic snapshot service. The model is never trained here (no claim step); the run must
    be queued and runnable with the audio backend's own defaults."""

    def setUp(self) -> None:
        super().setUp()
        from .training import lifecycle

        # A helper that says the audio backend can run (the lean-venv pattern from tests_training_auto).
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {"audio-wav-cnn": True})

    def make_project_with_clips(self, per_class: int = 4) -> Project:
        from .models import Asset, AssetAnnotation

        project = Project.objects.create(
            name="Motor sounds", modality="audio", task="Classify equipment sounds",
            recipe="audio-classification", labels=list(TONES),
        )
        for label, frequency in TONES.items():
            for index in range(per_class):
                content = write_sine_wav(frequency=frequency, seed=hash(label) % 1000 + index)
                asset_id = uuid.uuid4()
                key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.wav", io.BytesIO(content))
                Asset.objects.create(
                    id=asset_id, project=project, filename=f"{label}-{index}.wav", storage_key=key,
                    content_type="audio/wav", sha256=__import__("hashlib").sha256(content).hexdigest(),
                    size_bytes=len(content), width=0, height=0,
                )
                AssetAnnotation.objects.create(asset_id=asset_id, kind="clip_labels", data={"label": label})
        return project

    def test_one_click_start_freezes_a_generic_snapshot_and_queues_the_run(self) -> None:
        project = self.make_project_with_clips(per_class=4)
        response = self.post_runs(project, {"snapshot": "latest", "backend": "audio-wav-cnn"})
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        version = DatasetVersion.objects.get()
        self.assertTrue(body["snapshot"]["newly_created"])
        self.assertEqual(body["snapshot"]["id"], str(version.id))
        self.assertGreater(body["snapshot"]["entry_count"], 0)
        run = TrainingRun.objects.get()
        self.assertEqual(run.status, TrainingRun.QUEUED)
        self.assertEqual(run.backend, "audio-wav-cnn")
        self.assertEqual((run.task, run.annotation_kind), ("audio-classification", "clip_labels"))
        self.assertEqual(
            run.config, {"epochs": 5, "batch_size": 4, "learning_rate": 0.001, "seed": 0}
        )  # the audio backend's own defaults: no pretrained/quality keys it does not know

    def test_an_unchanged_project_reuses_the_snapshot(self) -> None:
        from .training import lifecycle

        project = self.make_project_with_clips(per_class=4)
        first = self.post_runs(project, {"snapshot": "latest", "backend": "audio-wav-cnn"}).json()
        lifecycle.request_cancel(TrainingRun.objects.get(), self.user)
        again = self.post_runs(project, {"snapshot": "latest", "backend": "audio-wav-cnn"})
        self.assertEqual(again.status_code, 202, again.content)
        self.assertFalse(again.json()["snapshot"]["newly_created"])
        self.assertEqual(again.json()["snapshot"]["id"], first["snapshot"]["id"])
        self.assertEqual(DatasetVersion.objects.count(), 1)

    def test_a_label_change_after_the_snapshot_makes_a_new_one(self) -> None:
        from .training import lifecycle

        project = self.make_project_with_clips(per_class=4)
        self.post_runs(project, {"snapshot": "latest", "backend": "audio-wav-cnn"})
        lifecycle.request_cancel(TrainingRun.objects.get(), self.user)
        project.labels = [*project.labels, "new_fault"]
        project.save()
        response = self.post_runs(project, {"snapshot": "latest", "backend": "audio-wav-cnn"})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertTrue(response.json()["snapshot"]["newly_created"])
        self.assertEqual(DatasetVersion.objects.count(), 2)

    def test_no_accepted_labels_is_the_plain_refusal_and_creates_nothing(self) -> None:
        project = Project.objects.create(
            name="Empty", modality="audio", task="t", recipe="audio-classification", labels=["a"]
        )
        response = self.post_runs(project, {"snapshot": "latest", "backend": "audio-wav-cnn"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("no accepted labels", response.json()["error"])
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_the_readiness_preview_agrees_with_what_the_start_would_freeze(self) -> None:
        project = self.make_project_with_clips(per_class=4)
        body = self.readiness(project, "?backend=audio-wav-cnn")
        self.assertFalse(body["one_click"]["ready"], body["one_click"])  # audio has no ready-made start
        self.assertTrue(any("cannot pick its own starting model" in line for line in body["one_click"]["blockers"]))
        self.assertTrue(body["one_click"]["snapshot"]["would_create_new"])
        self.assertIsNone(body["start_from"])
        # The preview writes nothing.
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_one_click_start_without_a_backend_name_uses_the_audio_recipe_backend(self) -> None:
        # The Teach panel's one-click start never names a backend; an audio project must land on
        # audio-wav-cnn, not the console's box default.
        project = self.make_project_with_clips(per_class=4)
        response = self.post_runs(project, {"snapshot": "latest"})
        self.assertEqual(response.status_code, 202, response.content)
        run = TrainingRun.objects.get()
        self.assertEqual(run.backend, "audio-wav-cnn")

    def test_a_box_backend_name_on_an_audio_project_is_refused_in_plain_words(self) -> None:
        project = self.make_project_with_clips(per_class=4)
        response = self.post_runs(project, {"snapshot": "latest", "backend": "torchvision-fasterrcnn"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("its runs use the audio-wav-cnn backend", response.json()["error"])
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))


# -- gap 3: the one-click defaults resolve per backend ------------------------------------------------------
class OneClickConfigResolutionTests(TeachGeneralBase):
    """``snapshot_default_config`` sends only what the backend understands, and each backend's
    ``prepare_config`` resolves what it is sent. No downloads: the OCR start is resolved through
    the cached-weights / download-switch rule with the cache stubbed, exactly as tests_ocr does."""

    def test_the_audio_backend_is_sent_no_settings_it_does_not_know(self) -> None:
        from .training import lifecycle

        self.assertEqual(
            lifecycle.snapshot_default_config("audio-wav-cnn", {}), {}
        )  # resolve_start is None: no pretrained, no quality

    def test_the_vision_backend_still_gets_both_defaults(self) -> None:
        from .training import lifecycle

        self.assertEqual(
            lifecycle.snapshot_default_config("torchvision-fasterrcnn", {}),
            {"pretrained": "auto", "quality": "quick"},
        )

    def test_the_ocr_backend_gets_the_start_default_but_not_quality(self) -> None:
        from .training import lifecycle

        self.assertEqual(lifecycle.snapshot_default_config("ocr-trocr", {}), {"pretrained": "auto"})

    def test_the_callers_own_choice_always_wins(self) -> None:
        from .training import lifecycle

        self.assertEqual(
            lifecycle.snapshot_default_config("torchvision-fasterrcnn", {"quality": "better"}),
            {"pretrained": "auto", "quality": "better"},
        )


@NEEDS_ML
class OcrOneClickConfigTests(TeachGeneralBase):
    """The OCR backend's own resolution of the one-click defaults, with the model stubbed
    (nothing is downloaded; the network is refused outright)."""

    def setUp(self) -> None:
        super().setUp()
        from .training import ocr_trocr

        for patch in (
            mock.patch.object(ocr_trocr, "cached_base_model_ok", return_value=True),
            mock.patch.dict(os.environ, {"ATHENA_TRAINING_ALLOW_DOWNLOAD": "1"}),
            mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network used")),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.backend = ocr_trocr.OcrTrOCRBackend()

    def test_prepare_config_translates_the_shared_start_default(self) -> None:
        prepared = self.backend.prepare_config(
            {"pretrained": "auto"}, train_pictures=6, worker={"online": True, "downloads_allowed": True}
        )
        self.assertEqual(prepared["base_model"], "microsoft/trocr-small-handwritten")
        self.assertNotIn("pretrained", prepared)  # the foreign key never reaches validate_config

    def test_the_translated_config_validates(self) -> None:
        prepared = self.backend.prepare_config(
            {"pretrained": "auto"}, train_pictures=6, worker={"online": True, "downloads_allowed": True}
        )
        normalized = self.backend.validate_config(prepared)
        self.assertEqual(normalized["base_model"], "microsoft/trocr-small-handwritten")
        self.assertEqual(sorted(normalized), ["base_model", "batch_size", "epochs", "learning_rate", "seed", "threads"])

    def test_a_download_that_is_switched_off_blocks_in_plain_words(self) -> None:
        from .training.base import StartBlocked
        from .training import ocr_trocr

        # This class patches cached_base_model_ok to True for the happy paths; the blocked path
        # needs the real answer (nothing cached) and a worker that says downloads are off.
        with mock.patch.object(ocr_trocr, "cached_base_model_ok", return_value=False), \
                mock.patch.dict(os.environ, {"ATHENA_TRAINING_ALLOW_DOWNLOAD": "0"}):
            with self.assertRaises(StartBlocked) as raised:
                self.backend.prepare_config(
                    {"pretrained": "auto"}, train_pictures=6, worker={"online": True, "downloads_allowed": False}
                )
        self.assertIn("downloading is switched off", str(raised.exception).lower())


@NEEDS_ML
class OcrOneClickSnapshotTests(TeachGeneralBase):
    """The full one-click start on an OCR project: readiness preview, snapshot freeze, queued run
    with the recipe's backend chosen by the server, and the run actually trainable through the
    runner with the tiny stubbed model (the tests_ocr RunnerIntegration pattern)."""

    def setUp(self) -> None:
        super().setUp()
        from .tests_ocr import tiny_model_and_processor
        from .training import lifecycle, ocr_trocr

        for patch in (
            mock.patch.object(ocr_trocr, "ensure_base_model", return_value={
                "repo_id": "microsoft/trocr-small-handwritten", "what": "tiny test model",
                "revision": ocr_trocr.REVISION, "source_url": "https://example.invalid/pytorch_model.bin",
                "sha256": "0" * 64, "bytes": 1, "files": {}, "licence_declared": "mit (model card)",
                "from": "cache",
            }),
            mock.patch.object(ocr_trocr.OcrTrOCRBackend, "_load_base_model", staticmethod(tiny_model_and_processor)),
            mock.patch.object(ocr_trocr.OcrTrOCRBackend, "_rebuild", staticmethod(lambda checkpoint: tiny_model_and_processor())),
            mock.patch.dict(os.environ, {"ATHENA_TRAINING_ALLOW_DOWNLOAD": "1"}),
            mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network used")),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {"ocr-trocr": True}, {"downloads_allowed": True})
        self.ocr_project = self.make_ocr_project()

    def make_ocr_project(self, count: int = 6) -> Project:
        from .models import Asset, AssetAnnotation

        project = Project.objects.create(
            name="Handwriting", modality="image", task="Read handwriting",
            recipe="handwriting-ocr", labels=["transcript"],
        )
        for index in range(count):
            content = handwriting_png(f"one-click-{index}")
            asset_id = uuid.uuid4()
            key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.png", io.BytesIO(content))
            asset = Asset.objects.create(
                id=asset_id, project=project, filename=f"line-{index}.png", storage_key=key,
                content_type="image/png",
                sha256=__import__("hashlib").sha256(content).hexdigest(),
                size_bytes=len(content), width=64, height=32,
            )
            AssetAnnotation.objects.create(
                asset=asset, kind="transcripts", data={"label": "transcript", "text": f"baris {index}"}
            )
        return project

    def test_one_click_readiness_then_start_and_run(self) -> None:
        from .training import runner

        body = self.readiness(self.ocr_project, "?backend=ocr-trocr")
        self.assertEqual(body["start_from"]["resolved"], "trocr")
        self.assertTrue(body["one_click"]["ready"], body["one_click"])
        self.assertEqual(body["one_click"]["snapshot"]["would_create_new"], True)

        # The one-click POST: no backend named (the recipe's own is chosen), the tick sent.
        response = self.post_runs(
            self.ocr_project, {"snapshot": "latest", "acknowledge_pretrained_terms": True}
        )
        self.assertEqual(response.status_code, 202, response.content)
        reply = response.json()
        self.assertTrue(reply["snapshot"]["newly_created"])
        self.assertGreater(reply["snapshot"]["entry_count"], 0)
        run = TrainingRun.objects.get()
        self.assertEqual(run.backend, "ocr-trocr")  # the recipe's backend, not the console default
        self.assertEqual(run.config["base_model"], "microsoft/trocr-small-handwritten")
        self.assertNotIn("pretrained", run.config)
        self.assertNotIn("quality", run.config)

        # And the queued run is runnable: the stubbed model takes it to a candidate.
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)

    def test_a_box_backend_is_refused_for_the_one_click_start_too(self) -> None:
        response = self.post_runs(
            self.ocr_project,
            {"snapshot": "latest", "backend": "torchvision-fasterrcnn", "acknowledge_pretrained_terms": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("handwriting", response.json()["error"])
