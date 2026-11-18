"""The audio classification slice (Wave 1, OS-AUD-001), end to end.

Covers the audio modality plugin (WAV upload validation and clip-label validation), the
recipe's availability, the generic snapshot flow on a real audio project, the audio-wav-cnn
training backend (availability, dataset layout, a real tiny training run on synthetic tones,
the evaluation shape) and the run -> candidate -> staged lifecycle through the API. Torch
tests skip cleanly when torch is not installed (the torchvision tests' pattern).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import random
import tempfile
import uuid
import wave
from array import array
from pathlib import Path
from unittest import skipUnless

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .datasets import generic as generic_service
from .datasets import splits
from .models import Asset, AssetAnnotation, DatasetEntry, ModelVersion, Project, TrainingRun
from .modalities.audio import AudioModalityPlugin
from .tasks import get_recipe
from .tests import SignedInMixin
from .tests_datasets import DatasetBase

TORCH_INSTALLED = importlib.util.find_spec("torch") is not None
NUMPY_INSTALLED = importlib.util.find_spec("numpy") is not None
ML_INSTALLED = TORCH_INSTALLED and NUMPY_INSTALLED

SAMPLE_RATE = 16_000
#: Two frequencies a tiny CNN can separate reliably in a few epochs: low hum vs high whine.
TONES = {"healthy_motor": 440.0, "bearing_fault": 1760.0}


def write_sine_wav(*, frequency: float, seconds: float = 0.5, channels: int = 1, sample_rate: int = SAMPLE_RATE,
                   bits: int = 16, amplitude: float = 0.6, seed: int = 0) -> bytes:
    """A deterministic mono/stereo sine-tone WAV, pure stdlib (the examples' generator pattern)."""
    rng = random.Random(seed)
    count = int(sample_rate * seconds)
    if bits == 8:
        type_code, scale, offset = "B", 127, 128
    elif bits == 16:
        type_code, scale, offset = "h", 32767, 0
    elif bits == 24:
        type_code, scale, offset = None, 8388607, 0  # packed below
    elif bits == 32:
        type_code, scale, offset = "i", 2147483647, 0
    else:
        raise ValueError(bits)
    samples = array(type_code) if type_code else []
    packed24 = bytearray()
    for i in range(count):
        value = amplitude * math.sin(2 * math.pi * frequency * i / sample_rate) + 0.02 * (rng.random() * 2 - 1)
        value = max(-1.0, min(1.0, value))
        if bits == 24:
            packed24 += int(value * scale).to_bytes(3, "little", signed=True)
        else:
            samples.append(int(value * scale) + offset)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(bits // 8)
        clip.setframerate(sample_rate)
        if bits == 24:
            clip.writeframes(bytes(packed24))
        else:
            clip.writeframes(samples.tobytes())
    return buffer.getvalue()


class AudioTestBase(DatasetBase):
    """A signed-in user, a media sandbox and an isolated data home, with an audio project."""

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
        self.audio_project = Project.objects.create(
            name="Motor sounds", modality="audio", task="Classify equipment sounds",
            recipe="audio-classification", labels=list(TONES),
        )

    # -- fixtures -----------------------------------------------------------------
    def upload_clip(self, *, filename="clip.wav", label=None, content: bytes | None = None,
                    frequency: float | None = None, seconds: float = 2.5, **wav_kwargs) -> dict:
        """Upload one WAV through the API and return the created asset payload.

        Clips default to 2.5 s: the backend pads or crops every clip to about 2 s of features,
        so shorter clips are mostly zero-padding and the two tones stop being trivially
        separable (a real clip carries the sound for its whole length)."""
        content = content if content is not None else write_sine_wav(
            frequency=frequency if frequency is not None else TONES[label or "healthy_motor"],
            seconds=seconds,
            seed=self._counter,
            **wav_kwargs,
        )
        upload = SimpleUploadedFile(filename, content, content_type="audio/wav")
        response = self.client.post(f"/api/projects/{self.audio_project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 201, response.content)
        asset = response.json()["assets"][0]
        self._counter += 1
        if label is not None:
            self.label_clip(asset["id"], label)
        return asset

    def label_clip(self, asset_id, label) -> dict:
        response = self.client.post(
            f"/api/projects/{self.audio_project.id}/assets/{asset_id}/annotations",
            data=json.dumps({"label": label}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["annotation"]

    def clip_count(self) -> int:
        return Asset.objects.filter(project=self.audio_project).count()

    def make_labelled_version(self, per_class: int = 8):
        """Upload per_class clips per label, then freeze a generic dataset version with a test split."""
        for label, frequency in TONES.items():
            for _ in range(per_class):
                self.upload_clip(label=label, frequency=frequency)
        for seed in range(200):
            config = splits.SplitConfig(seed, 0.7, 0.0, 0.3)
            analysis = generic_service.analyse(self.audio_project, config)
            if not (analysis.accepted and analysis.ready):
                continue
            counts = generic_service.preview(analysis)["split_counts"]
            if counts["train"]["entries"] > 0 and counts["test"]["entries"] > 0:
                return generic_service.create_version(analysis, self.user)
        raise AssertionError("could not find a split with train and test clips")


class RecipeAndPluginTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def test_the_audio_recipe_is_available(self) -> None:
        recipe = get_recipe("audio-classification")
        self.assertIsNotNone(recipe)
        self.assertTrue(recipe.available)
        self.assertEqual(recipe.modality, "audio")
        self.assertEqual(recipe.annotation_kind, "clip_labels")

    def test_the_audio_plugin_is_registered_as_a_builtin(self) -> None:
        from .modalities import available_modalities, get_plugin

        self.assertIn("audio", available_modalities())
        self.assertIsInstance(get_plugin("audio"), AudioModalityPlugin)
        self.assertEqual(get_plugin("audio").annotation_kind, "clip_labels")


class UploadValidationTests(AudioTestBase):
    def post_upload(self, filename: str, content: bytes):
        upload = SimpleUploadedFile(filename, content, content_type="audio/wav")
        return self.client.post(f"/api/projects/{self.audio_project.id}/assets", {"images": upload})

    def test_a_good_clip_is_stored_with_its_facts(self) -> None:
        asset = self.upload_clip()
        self.assertEqual(asset["filename"], "clip.wav")
        self.assertEqual(asset["metadata"]["sample_rate"], SAMPLE_RATE)
        self.assertEqual(asset["metadata"]["channels"], 1)
        self.assertEqual(asset["metadata"]["bits_per_sample"], 16)
        self.assertAlmostEqual(asset["metadata"]["duration_seconds"], 2.5, places=2)
        row = Asset.objects.get(id=asset["id"])
        self.assertEqual(row.metadata["duration_seconds"], asset["metadata"]["duration_seconds"])

    def test_the_wave_spelling_is_accepted(self) -> None:
        response = self.post_upload("clip.wave", write_sine_wav(frequency=440.0))
        self.assertEqual(response.status_code, 201, response.content)

    def test_stereo_and_other_bit_depths_are_accepted(self) -> None:
        for channels, bits in ((2, 16), (1, 8), (1, 32)):
            with self.subTest(channels=channels, bits=bits):
                response = self.post_upload(f"c-{channels}-{bits}.wav",
                                            write_sine_wav(frequency=440.0, channels=channels, bits=bits))
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.json()["assets"][0]["metadata"]["channels"], channels)
                self.assertEqual(response.json()["assets"][0]["metadata"]["bits_per_sample"], bits)

    def test_non_wav_files_are_refused_plainly(self) -> None:
        response = self.post_upload("song.mp3", b"ID3 not really audio")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only WAV sound clips", response.json()["error"])
        self.assertEqual(self.clip_count(), 0)

    def test_corrupt_files_are_refused_plainly(self) -> None:
        response = self.post_upload("broken.wav", b"RIFF....WAVEjunk")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a valid WAV", response.json()["error"])
        self.assertEqual(self.clip_count(), 0)

    def test_too_short_and_too_long_clips_are_refused_with_the_numbers(self) -> None:
        short = self.post_upload("short.wav", write_sine_wav(frequency=440.0, seconds=0.1))
        self.assertEqual(short.status_code, 400)
        self.assertIn("between 0.25 and 60 seconds", short.json()["error"])
        long = self.post_upload("long.wav", write_sine_wav(frequency=440.0, seconds=61.0))
        self.assertEqual(long.status_code, 400)
        self.assertIn("between 0.25 and 60 seconds", long.json()["error"])
        self.assertEqual(self.clip_count(), 0)

    def test_bad_sample_rates_are_refused(self) -> None:
        response = self.post_upload("slow.wav", write_sine_wav(frequency=440.0, sample_rate=4_000))
        self.assertEqual(response.status_code, 400)
        self.assertIn("8,000 and 48,000 Hz", response.json()["error"])
        self.assertEqual(self.clip_count(), 0)


class AnnotationTests(AudioTestBase):
    def test_a_clip_label_round_trip(self) -> None:
        asset = self.upload_clip()
        annotation = self.label_clip(asset["id"], "bearing_fault")
        self.assertEqual(annotation["kind"], "clip_labels")
        self.assertEqual(annotation["data"], {"label": "bearing_fault"})
        self.assertEqual(annotation["status"], "accepted")
        self.assertEqual(annotation["origin"], "manual")

    def test_unknown_or_missing_labels_are_refused(self) -> None:
        asset = self.upload_clip()
        for bad in ({"label": "nope"}, {"label": ""}, {}, {"label": "healthy_motor", "note": "x"}):
            response = self.client.post(
                f"/api/projects/{self.audio_project.id}/assets/{asset['id']}/annotations",
                data=json.dumps(bad),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400, bad)
            self.assertTrue(response.json()["error"])
        self.assertEqual(AssetAnnotation.objects.count(), 0)

    def test_clip_labels_can_be_listed_edited_and_deleted(self) -> None:
        asset = self.upload_clip()
        annotation = self.label_clip(asset["id"], "healthy_motor")
        listed = self.client.get(
            f"/api/projects/{self.audio_project.id}/assets/{asset['id']}/annotations"
        ).json()["annotations"]
        self.assertEqual(len(listed), 1)
        patched = self.client.patch(
            f"/api/projects/{self.audio_project.id}/asset-annotations/{annotation['id']}",
            data=json.dumps({"label": "bearing_fault"}),
            content_type="application/json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["annotation"]["data"], {"label": "bearing_fault"})
        deleted = self.client.delete(
            f"/api/projects/{self.audio_project.id}/asset-annotations/{annotation['id']}"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(AssetAnnotation.objects.count(), 0)


class SnapshotTests(AudioTestBase):
    def test_upload_label_and_freeze_a_version(self) -> None:
        version = self.make_labelled_version(per_class=4)
        self.assertEqual(version.label_names, list(TONES))
        self.assertEqual(version.entries.count(), 8)
        self.assertEqual(DatasetEntry.objects.filter(label="healthy_motor").count(), 4)
        row = DatasetEntry.objects.first()
        self.assertEqual(row.annotation, {"label": row.label})
        self.assertIn(row.split, splits.SPLIT_NAMES)

    def test_unlabelled_clips_block_the_version(self) -> None:
        self.upload_clip()
        self.upload_clip(label="healthy_motor")
        response = self.client.post(f"/api/projects/{self.audio_project.id}/datasets", data="{}",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(DatasetEntry.objects.count(), 0)


class BackendAvailabilityTests(AudioTestBase):
    def test_the_backend_is_listed_and_reports_availability(self) -> None:
        from .training import describe_backend, get_backend

        backend = get_backend("audio-wav-cnn")
        self.assertEqual(backend.name, "audio-wav-cnn")
        described = describe_backend(backend)
        self.assertEqual(described["name"], "audio-wav-cnn")
        self.assertTrue(described["license_note"])
        self.assertEqual(described["available_in_web_process"], ML_INSTALLED)
        self.assertEqual(backend.check_available().ok, ML_INSTALLED)

    def test_config_validation_is_plain(self) -> None:
        from .training import get_backend

        backend = get_backend("audio-wav-cnn")
        self.assertEqual(backend.default_config(), {"epochs": 5, "batch_size": 4, "learning_rate": 0.001, "seed": 0})
        for bad in ({"epochs": 0}, {"batch_size": True}, {"learning_rate": -1}, {"nope": 1}):
            with self.assertRaises(ValueError):
                backend.validate_config(bad)
        self.assertEqual(backend.validate_config({"epochs": 2}), {
            "epochs": 2, "batch_size": 4, "learning_rate": 0.001, "seed": 0,
        })


@skipUnless(ML_INSTALLED, "torch not installed")
class TrainingTests(AudioTestBase):
    """The real ML path: export, train, evaluate, register a candidate."""

    def make_run(self, per_class: int = 10, epochs: int = 12) -> TrainingRun:
        from .training import lifecycle

        version = self.make_labelled_version(per_class=per_class)
        return lifecycle.create_run(
            self.audio_project, self.user, dataset=version, backend_name="audio-wav-cnn",
            config={"epochs": epochs, "batch_size": 4, "learning_rate": 0.01},
        )

    def test_build_dataset_writes_the_export_and_returns_test_items(self) -> None:
        from .training import get_backend, runner

        run = self.make_run()
        backend = get_backend("audio-wav-cnn")
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "dataset"
            items, labels = backend.build_dataset(run, dest)
            self.assertEqual(set(labels), set(TONES))
            self.assertTrue(items)
            for item in items:
                self.assertTrue(item.path.is_file())
                self.assertTrue(str(item.path).startswith(str(dest / "entries" / "test")))
                self.assertIn(item.label, TONES)
                self.assertTrue(item.asset_id)
            self.assertEqual(
                len(list((dest / "entries" / "train").iterdir())),
                run.dataset_version.entries.filter(split="train").count(),
            )
            self.assertEqual(set((dest / "classes.txt").read_text().splitlines()), set(TONES))
            self.assertIn("filename,label", (dest / "labels.csv").read_text())

    def test_a_real_training_run_learns_the_tones(self) -> None:
        from .training import runner

        run = self.make_run(per_class=8)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        self.assertEqual(model.task, "audio-classification")
        self.assertEqual(model.stage, ModelVersion.CANDIDATE)
        self.assertEqual(set(model.label_names), set(TONES))
        self.assertTrue(model.artifact_sha256)

        evaluation = model.evaluation
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["eval_kind"], "clip_classification")
        self.assertGreaterEqual(evaluation["accuracy"], 0.5)
        self.assertEqual(set(evaluation["per_label"]), set(TONES))
        for row in evaluation["per_label"].values():
            for key in ("precision", "recall", "f1"):
                self.assertIn(key, row)
        self.assertEqual(
            sum(sum(row.values()) for row in evaluation["confusion"].values()), evaluation["data"]["clips"]
        )

        # The loss went down over the epochs.
        losses = [entry["mean_loss"] for entry in finished.metrics["epochs"]]
        self.assertGreater(len(losses), 1)
        self.assertLess(losses[-1], losses[0])

    def test_the_model_can_classify_a_fresh_clip(self) -> None:
        from .training import get_backend, lifecycle, runner

        run = self.make_run(per_class=8)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        artifact = lifecycle.artifact_file(model)  # the runner-stored model.bin (ONNX or torch)
        backend = get_backend("audio-wav-cnn")
        with tempfile.TemporaryDirectory() as scratch:
            fresh = Path(scratch) / "fresh.wav"
            fresh.write_bytes(write_sine_wav(frequency=1760.0, seconds=0.5, seed=99))
            label, confidence, probabilities = backend.classify(artifact, fresh)
            self.assertEqual(label, "bearing_fault")
            self.assertGreater(confidence, 0.5)
            self.assertEqual(set(probabilities), set(TONES))

    def test_a_run_on_a_version_with_no_train_split_is_refused(self) -> None:
        from .training import lifecycle

        self.make_labelled_version(per_class=8)
        # Freeze a second version whose train share is zero: starting a run on it must refuse.
        for seed in range(200):
            config = splits.SplitConfig(seed, 0.0, 0.0, 1.0)
            analysis = generic_service.analyse(self.audio_project, config)
            if analysis.accepted and analysis.ready:
                empty_train = generic_service.create_version(analysis, self.user)
                with self.assertRaises(Exception) as raised:
                    lifecycle.create_run(
                        self.audio_project, self.user, dataset=empty_train, backend_name="audio-wav-cnn", config={},
                    )
                self.assertIn("train", str(raised.exception))
                return
        self.skipTest("could not build an empty-train version")

    def test_the_training_api_works_for_audio_projects(self) -> None:
        from .training import runner

        version = self.make_labelled_version(per_class=8)
        response = self.client.post(
            f"/api/projects/{self.audio_project.id}/training/runs",
            data=json.dumps({"backend": "audio-wav-cnn", "dataset_id": str(version.id),
                             "config": {"epochs": 3, "learning_rate": 0.01}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202, response.content)
        run_id = response.json()["run"]["id"]
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        listed = self.client.get(f"/api/projects/{self.audio_project.id}/training/runs").json()["runs"]
        self.assertEqual(listed[0]["id"], run_id)
        self.assertEqual(listed[0]["task"], "audio-classification")

    def test_model_stage_transition_through_the_api(self) -> None:
        from .training import runner

        run = self.make_run(per_class=8)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        # The evaluation is available, so staging is allowed.
        staged = self.client.post(f"/api/projects/{self.audio_project.id}/models/{model.id}/stage",
                                  data=json.dumps({"note": "sounds good"}),
                                  content_type="application/json")
        self.assertEqual(staged.status_code, 200, staged.content)
        self.assertEqual(staged.json()["model"]["stage"], "staged")
        evaluation = self.client.get(
            f"/api/projects/{self.audio_project.id}/models/{model.id}/evaluation"
        ).json()
        # The report endpoint reads the box-detection shape, so it answers "cannot read this"
        # for the clip-classification shape without crashing; the numbers themselves are the
        # model's stored evaluation (asserted by the training test) and the plain results
        # card points there.
        self.assertIn("available", evaluation["report"])
        results = self.client.get(
            f"/api/projects/{self.audio_project.id}/models/{model.id}/results/plain"
        ).json()
        self.assertTrue(results["non_vision"])
        self.assertIn("evaluation report", results["overall"]["headline"])

    def test_model_list_and_detail_do_not_crash_on_the_clip_shape(self) -> None:
        from .training import runner

        run = self.make_run(per_class=8)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        model = finished.model_versions.get()
        listed = self.client.get(f"/api/projects/{self.audio_project.id}/models").json()["models"]
        self.assertEqual(listed[0]["task"], "audio-classification")
        self.assertIn("available", listed[0]["evaluation"])  # the summary degrades, never crashes
        detail = self.client.get(f"/api/projects/{self.audio_project.id}/models/{model.id}").json()["model"]
        self.assertEqual(set(detail["label_names"]), set(TONES))


class ConsolePanelTests(AudioTestBase):
    def test_the_console_page_carries_the_audio_panel(self) -> None:
        html = self.client.get("/detectors").content.decode()
        for element_id in ("audio-assets-panel", "audio-upload-form", "audio-upload", "clip-list"):
            self.assertIn(f'id="{element_id}"', html)
        js = (Path(__file__).parent / "static" / "projects" / "console.js").read_text()
        self.assertIn("loadClips", js)
        self.assertIn('project.modality !== "audio"', js)
