"""The Wave 1 handwriting-OCR slice: recipe, transcript annotations, snapshots, training.

Covers the machinery, not model quality (synthetic "handwriting" is not learnable): the recipe
and project creation, the transcript annotation CRUD and its validation rules, the
recipe-based endpoint dispatch (an OCR project gets generic annotations; a vision project
keeps boxes exactly as before), the generic snapshot flow freezing ``data["text"]``, the
runner's image+transcript export, and the TrOCR backend - config, download gating, and a full
tiny run with a STUBbed model (nothing is ever downloaded in tests; the network is refused
outright). Machine-learning tests skip cleanly when torch or transformers is missing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest import mock, skipUnless

from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from .models import (
    Asset,
    AssetAnnotation,
    DatasetEntry,
    DatasetVersion,
    ModelVersion,
    Project,
    TrainingRun,
)
from .modalities.ocr import OcrImagePlugin
from .tasks import get_recipe, recipes, reload_recipes
from .tests import SignedInMixin
from .tests_datasets import DatasetBase, png_bytes

ML_INSTALLED = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers"))
NEEDS_ML = skipUnless(ML_INSTALLED, "torch and transformers are not installed here; run in the ML venv")
WORKER_OFF = {"online": True, "downloads_allowed": False}
WORKER_ON = {"online": True, "downloads_allowed": True}

TRANSCRIPT = "selamat pagi"


def handwriting_png(text_hint: str = "line", size=(64, 32)) -> bytes:
    """A small picture that stands for one line of handwriting (colored noise, deterministic)."""
    import random

    rng = random.Random(text_hint)
    image = Image.new("RGB", size, (250, 248, 240))
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if rng.random() < 0.25:
                pixels[x, y] = (20 + rng.randrange(60), 20 + rng.randrange(40), 60 + rng.randrange(80))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class OcrBase(DatasetBase):
    """A signed-in user, a media sandbox, and the OCR recipe's project convention set up."""

    def setUp(self) -> None:
        super().setUp()
        # The recipe/plugin module registers itself on import; make sure that happened.
        import projects.modalities.ocr  # noqa: F401
        reload_recipes()
        self.ocr_project = self.make_ocr_project()

    def make_ocr_project(self) -> Project:
        response = self.client.post(
            "/api/projects",
            data=json.dumps(
                {
                    "name": f"Handwriting {uuid.uuid4().hex[:6]}",
                    "modality": "image",
                    "task": "Read handwriting",
                    "labels": ["transcript"],
                    "recipe": "handwriting-ocr",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return Project.objects.get(id=response.json()["project"]["id"])

    def make_handwriting_asset(self, project=None, text_hint: str | None = None) -> Asset:
        project = project or self.ocr_project
        self._counter += 1
        hint = text_hint or f"line-{self._counter}"
        data = handwriting_png(hint)
        asset_id = uuid.uuid4()
        key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.png", io.BytesIO(data))
        return Asset.objects.create(
            id=asset_id,
            project=project,
            filename=f"{hint}.png",
            storage_key=key,
            content_type="image/png",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            width=64,
            height=32,
        )

    def make_transcript(self, asset, text: str = TRANSCRIPT, **extra) -> AssetAnnotation:
        return AssetAnnotation.objects.create(
            asset=asset, kind="transcripts", data={"label": "transcript", "text": text}, **extra
        )

    def labelled_handwriting(self, text_hint: str | None = None, text: str = TRANSCRIPT) -> Asset:
        asset = self.make_handwriting_asset(text_hint=text_hint)
        self.make_transcript(asset, text=text)
        return asset

    # -- URLs ------------------------------------------------------------------
    def annotations_url(self, asset, project=None) -> str:
        return f"/api/projects/{(project or self.ocr_project).id}/assets/{asset.id}/annotations"

    def post_annotation(self, asset, payload, project=None):
        return self.client.post(
            self.annotations_url(asset, project=project),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def make_ocr_version(self, count: int = 6):
        """Create a generic dataset version with train+test entries (searching split seeds)."""
        from .datasets import generic as generic_service
        from .datasets import splits

        for index in range(count):
            self.labelled_handwriting(text_hint=f"v{index}")
        for seed in range(80):
            config = splits.SplitConfig(seed, 0.6, 0.0, 0.4)
            analysis = generic_service.analyse(self.ocr_project, config)
            if analysis.accepted and analysis.ready:
                counts = generic_service.preview(analysis)["split_counts"]
                if counts["train"]["entries"] > 0 and counts["test"]["entries"] > 0:
                    return generic_service.create_version(analysis, self.user)
        raise AssertionError("no trainable split found")


# -- the recipe ------------------------------------------------------------------------------------------
class RecipeTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def test_the_recipe_is_available_and_names_the_transcript_convention(self) -> None:
        recipe = get_recipe("handwriting-ocr")
        self.assertTrue(recipe.available)
        self.assertEqual(recipe.modality, "image")
        self.assertEqual(recipe.annotation_kind, "transcripts")
        self.assertIn("transcript", recipe.labels_help)
        body = self.client.get("/api/recipes").json()["recipes"]
        ocr = next(item for item in body if item["key"] == "handwriting-ocr")
        self.assertTrue(ocr["available"])
        self.assertEqual(ocr["annotation_kind"], "transcripts")

    def test_the_remaining_placeholder_stays_unavailable(self) -> None:
        found = recipes()
        # All four Wave 0/1 recipes have landed (vision, audio, OCR, tabular): no builtin
        # recipe is a placeholder any more.
        for key in ("audio-classification", "tabular-prediction", "vision-detection"):
            self.assertTrue(found[key].available, key)


# -- transcript annotations ----------------------------------------------------------------------------
class TranscriptAnnotationTests(OcrBase):
    def test_create_list_patch_delete(self) -> None:
        asset = self.make_handwriting_asset()
        created = self.post_annotation(asset, {"label": "transcript", "text": f" {TRANSCRIPT} "})
        self.assertEqual(created.status_code, 201, created.content)
        annotation = created.json()["annotation"]
        self.assertEqual(annotation["kind"], "transcripts")
        self.assertEqual(annotation["data"], {"label": "transcript", "text": TRANSCRIPT})  # stripped
        self.assertEqual(annotation["status"], "accepted")

        listed = self.client.get(self.annotations_url(asset)).json()["annotations"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["data"]["text"], TRANSCRIPT)

        patched = self.client.patch(
            f"/api/projects/{self.ocr_project.id}/asset-annotations/{annotation['id']}",
            data=json.dumps({"label": "transcript", "text": "terima kasih"}),
            content_type="application/json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["annotation"]["data"]["text"], "terima kasih")

        deleted = self.client.delete(
            f"/api/projects/{self.ocr_project.id}/asset-annotations/{annotation['id']}"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(AssetAnnotation.objects.count(), 0)

    def test_validation_rules(self) -> None:
        asset = self.make_handwriting_asset()
        bad_payloads = (
            {"label": "transcript", "text": ""},                      # empty text
            {"label": "transcript", "text": "   "},                   # whitespace only
            {"label": "transcript", "text": 12},                      # not text
            {"label": "other", "text": TRANSCRIPT},                   # wrong label
            {"label": "transcript"},                                  # missing text
            {"text": TRANSCRIPT},                                     # missing label
            {"label": "transcript", "text": "x" * 1001},              # too long
            {"label": "transcript", "text": "ok", "extra": 1},        # unknown key
        )
        for payload in bad_payloads:
            response = self.post_annotation(asset, payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertTrue(response.json()["error"], payload)
        self.assertEqual(AssetAnnotation.objects.count(), 0)

    def test_a_project_without_the_transcript_label_is_refused(self) -> None:
        project = Project.objects.create(
            name="No label", modality="image", task="t", labels=["other"], recipe="handwriting-ocr"
        )
        asset = self.make_handwriting_asset(project=project)
        response = self.post_annotation(asset, {"label": "transcript", "text": TRANSCRIPT}, project=project)
        self.assertEqual(response.status_code, 400)
        self.assertIn("transcript", response.json()["error"])

    def test_control_characters_are_refused(self) -> None:
        asset = self.make_handwriting_asset()
        response = self.post_annotation(asset, {"label": "transcript", "text": "bad\x00text"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("control characters", response.json()["error"])

    def test_the_plugin_validates_directly(self) -> None:
        class FakeProject:
            labels = ["transcript"]

        plugin = OcrImagePlugin()
        self.assertEqual(
            plugin.validate_annotation({"label": "transcript", "text": " apa khabar "}, FakeProject()),
            {"label": "transcript", "text": "apa khabar"},
        )
        self.assertEqual(plugin.annotation_kind, "transcripts")


# -- dispatch safety -----------------------------------------------------------------------------------
class DispatchTests(OcrBase):
    """An OCR project gets generic transcript endpoints; a vision project keeps boxes."""

    def test_a_vision_project_still_gets_box_endpoints(self) -> None:
        asset = self.make_asset()  # DatasetBase's image project (no recipe -> vision default)
        self.make_box(asset)
        response = self.client.get(self.annotations_url(asset, project=self.project))
        self.assertEqual(response.status_code, 200)
        body = response.json()["annotations"]
        self.assertEqual(body[0]["label"], "damaged")
        self.assertIn("x", body[0])  # the box shape, not the generic one
        # The generic detail endpoint is not the box project's:
        box = asset.annotations.get()
        generic = self.client.patch(
            f"/api/projects/{self.project.id}/asset-annotations/{box.id}",
            data=json.dumps({"label": "damaged"}),
            content_type="application/json",
        )
        self.assertEqual(generic.status_code, 404)

    def test_an_ocr_project_does_not_get_box_endpoints(self) -> None:
        asset = self.make_handwriting_asset()
        annotation = self.make_transcript(asset)
        # The box detail URL is not the OCR project's:
        response = self.client.delete(
            f"/api/projects/{self.ocr_project.id}/assets/{asset.id}/annotations/{annotation.id}"
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(AssetAnnotation.objects.filter(id=annotation.id).exists())

    def test_the_plugin_lookup_finds_transcripts_and_the_image_builtin_stays(self) -> None:
        from .modalities import get_annotation_plugin, get_plugin
        from .modalities.image import ImageModalityPlugin

        self.assertIs(type(get_plugin("image")), ImageModalityPlugin)  # the builtin is untouched
        plugin = get_annotation_plugin("transcripts")
        self.assertIs(type(plugin), OcrImagePlugin)
        self.assertIs(type(get_annotation_plugin("boxes")), ImageModalityPlugin)
        self.assertIsNone(get_annotation_plugin("no-such-kind"))


# -- snapshots -------------------------------------------------------------------------------------------
class SnapshotTests(OcrBase):
    def test_the_version_freezes_the_transcript_text(self) -> None:
        first = self.labelled_handwriting(text_hint="one", text="nama saya Ali")
        second = self.labelled_handwriting(text_hint="two", text="sila tulis di sini")
        response = self.client.post(
            f"/api/projects/{self.ocr_project.id}/datasets",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["dataset"]
        self.assertEqual(body["counts"]["entry_count"], 2)
        entries = {str(row.asset_uuid): row for row in DatasetEntry.objects.all()}
        self.assertEqual(entries[str(first.id)].annotation, {"label": "transcript", "text": "nama saya Ali"})
        self.assertEqual(entries[str(first.id)].label, "transcript")  # the snapshot convention holds
        self.assertEqual(entries[str(second.id)].annotation["text"], "sila tulis di sini")
        # Editing the live annotation afterwards cannot change the frozen row.
        annotation = AssetAnnotation.objects.get(asset=first)
        annotation.data = {"label": "transcript", "text": "changed later"}
        annotation.save(update_fields=["data"])
        self.assertEqual(
            DatasetEntry.objects.get(asset=first).annotation["text"], "nama saya Ali"
        )

    def test_readiness_speaks_of_labels_not_boxes(self) -> None:
        self.make_handwriting_asset()  # no transcript yet
        body = self.client.get(f"/api/projects/{self.ocr_project.id}/datasets/readiness").json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["counts"]["assets_total"], 1)
        self.assertEqual(body["problems"][0]["code"], "asset_without_accepted")

    def test_a_vision_project_still_gets_the_image_snapshot_path(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        created = self.create_version()
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["dataset"]["counts"], {"images": 1, "boxes": 1})
        self.assertEqual(DatasetEntry.objects.count(), 0)

    def test_the_export_streams_pictures_and_the_manifest(self) -> None:
        import zipfile

        self.labelled_handwriting(text_hint="exp")
        created = self.client.post(
            f"/api/projects/{self.ocr_project.id}/datasets",
            data=json.dumps({}),
            content_type="application/json",
        ).json()["dataset"]
        response = self.client.get(f"/api/projects/{self.ocr_project.id}/datasets/{created['id']}/export")
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        names = archive.namelist()
        self.assertIn("classes.txt", names)
        wrapper = json.loads(archive.read("manifest.json"))
        self.assertEqual(wrapper["dataset"]["entries"][0]["annotation"]["text"], TRANSCRIPT)


# -- the runner's image+transcript export --------------------------------------------------------------
class ExportLayoutTests(OcrBase):
    def test_build_generic_dataset_layout(self) -> None:
        from .training import runner

        version = self.make_ocr_version(count=6)
        run = TrainingRun.objects.create(
            project=self.ocr_project,
            dataset_version=version,
            dataset_manifest_sha256=version.manifest_sha256,
            backend="ocr-trocr",
            config={},
            config_sha256="0" * 64,
        )
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "dataset"
            test_items, labels = runner.build_generic_dataset(run, dest)
            self.assertEqual(labels, ["transcript"])
            entries = json.loads((dest / "transcripts.json").read_text())
            self.assertEqual(len(entries), version.entries.count())
            self.assertEqual({entry["split"] for entry in entries}, {"train", "test"})
            for entry in entries:
                self.assertTrue((dest / entry["path"]).is_file())
                self.assertEqual(entry["label"] if "label" in entry else "transcript", "transcript")
            self.assertEqual(len(test_items), sum(1 for entry in entries if entry["split"] == "test"))
            for item in test_items:
                self.assertTrue(Path(item.path).is_file())
                self.assertEqual(item.boxes, [])  # no boxes in this convention

    def test_an_image_version_is_refused(self) -> None:
        from .datasets import service as dataset_service, splits
        from .training import runner

        asset = self.make_asset()
        self.make_box(asset)
        analysis = dataset_service.analyse(self.project, splits.SplitConfig.default())
        version = dataset_service.create_version(analysis, self.user)
        run = TrainingRun.objects.create(
            project=self.project, dataset_version=version,
            dataset_manifest_sha256=version.manifest_sha256, backend="ocr-trocr",
            config={}, config_sha256="0" * 64,
        )
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(ValueError) as raised:
                runner.build_generic_dataset(run, Path(scratch))
        self.assertIn("no frozen entries", str(raised.exception))


# -- compute_cer (ported expected values) ---------------------------------------------------------------
class ComputeCerTests(SimpleTestCase):
    def test_identical_is_zero(self) -> None:
        from .training.ocr_trocr import compute_cer

        self.assertEqual(compute_cer("selamat pagi", "selamat pagi"), 0.0)

    def test_one_substitution_of_n_characters(self) -> None:
        from .training.ocr_trocr import compute_cer

        self.assertAlmostEqual(compute_cer("hello", "hallo"), 1 / 5)
        self.assertAlmostEqual(compute_cer("abc", "abd"), 1 / 3)

    def test_insertions_deletions_and_empty(self) -> None:
        from .training.ocr_trocr import compute_cer

        self.assertAlmostEqual(compute_cer("abc", "abcde"), 2 / 3)  # two insertions
        self.assertAlmostEqual(compute_cer("abcde", "abc"), 2 / 5)  # two deletions
        self.assertEqual(compute_cer("", ""), 0.0)
        self.assertEqual(compute_cer("", "something"), 1.0)
        self.assertEqual(compute_cer("something", ""), 1.0)

    def test_word_accuracy(self) -> None:
        from .training.ocr_trocr import word_accuracy

        self.assertEqual(word_accuracy("nama saya Ali", "nama saya Ali"), 1.0)
        self.assertAlmostEqual(word_accuracy("nama saya Ali", "nama saya Budi"), 2 / 3)
        self.assertEqual(word_accuracy("one two", "two one"), 1.0)  # position-insensitive
        self.assertEqual(word_accuracy("", "anything"), 0.0)


# -- the backend: availability, config, download gating --------------------------------------------------
class BackendRulesTests(OcrBase):
    def setUp(self) -> None:
        super().setUp()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        override = override_settings(DATA_HOME=Path(self.home.name))
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        from .training import torchvision_frcnn as tv

        os.environ.pop(tv.DOWNLOAD_ENV, None)
        from .training import ocr_trocr

        ocr_trocr._VERIFIED.clear()
        self.backend = ocr_trocr.OcrTrOCRBackend()

    def test_availability_and_registration(self) -> None:
        from . import training
        from .training.ocr_trocr import OcrTrOCRBackend

        self.assertIn("ocr-trocr", training.backend_classes())
        self.assertIs(training.backend_classes()["ocr-trocr"], OcrTrOCRBackend)
        self.assertIn(OcrTrOCRBackend.name, ("ocr-trocr",))
        if ML_INSTALLED:
            self.assertTrue(self.backend.check_available().ok)
        else:
            availability = self.backend.check_available()
            self.assertFalse(availability.ok)
            self.assertIn("not installed", availability.message)

    def test_config_validation(self) -> None:
        defaults = self.backend.default_config()
        self.assertEqual(self.backend.validate_config({}), defaults)
        self.assertEqual(defaults["base_model"], "microsoft/trocr-small-handwritten")
        self.assertEqual(defaults["epochs"], 3)
        for key, bad in (("epochs", 0), ("epochs", 51), ("epochs", True), ("batch_size", 9), ("seed", -1)):
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                self.backend.validate_config({key: bad})
        for bad in (0, -1, True, "x", None, 10**400):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.backend.validate_config({"learning_rate": bad})
        with self.assertRaisesMessage(ValueError, "Unsupported training setting"):
            self.backend.validate_config({"image_size": 320})
        # The checkpoint cannot be chosen by name.
        for key in ("checkpoint", "repo_id", "model", "revision", "weights"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.backend.validate_config({key: "evil/model"})

    def test_downloads_off_blocks_in_plain_words_with_no_network(self) -> None:
        from .training import ocr_trocr

        with mock.patch.object(ocr_trocr, "_fetch_file", side_effect=AssertionError("network")), \
                mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")):
            with self.assertRaises(ValueError) as caught:
                ocr_trocr.ensure_base_model()
        message = str(caught.exception)
        self.assertIn("downloading is switched off", message)
        self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", message)
        self.assertIn("licence", message)

    def test_start_options_follow_the_console_switch(self) -> None:
        from .console_settings import OFF, ON, TRAINING_DOWNLOADS
        from .models import ConsoleSetting
        from .training import torchvision_frcnn as tv

        [off] = self.backend.start_options(WORKER_OFF)
        self.assertFalse(off["available"])
        self.assertTrue(off["needs_acknowledgement"])
        self.assertIn("switched off", off["unavailable_reason"])
        self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", off["how_to_enable"])
        self.assertTrue(self.backend.start_check({}, WORKER_OFF)["needs_acknowledgement"])
        no_report = {"online": False, "downloads_allowed": None}
        os.environ[tv.DOWNLOAD_ENV] = "1"
        # A running helper's own report wins over the environment, so only the report-less
        # worker (the console's environment) can see the variable - the same rule as D-FINE's.
        self.assertFalse(self.backend.start_options(WORKER_OFF)[0]["available"])
        self.assertTrue(self.backend.start_options(no_report)[0]["available"])  # the environment
        ConsoleSetting.objects.create(key=TRAINING_DOWNLOADS, value=OFF)  # the console wins
        self.assertFalse(self.backend.start_options(WORKER_ON)[0]["available"])
        ConsoleSetting.objects.filter(pk=TRAINING_DOWNLOADS).update(value=ON)
        os.environ.pop(tv.DOWNLOAD_ENV, None)
        self.assertTrue(self.backend.start_options(WORKER_OFF)[0]["available"])

    def _fake_pins(self, content: bytes = b"fake-bytes"):
        """Patch the pinned table so that ``content`` + the file's name is each file's 'published' content."""
        from .training import ocr_trocr

        files = {
            name: {"sha256": hashlib.sha256(content + name.encode()).hexdigest(), "bytes": len(content) + len(name)}
            for name in ocr_trocr.BASE_FILES
        }
        payload = lambda name: content + name.encode()  # noqa: E731 - what the "publisher" serves
        return mock.patch.object(ocr_trocr, "BASE_FILES", files), payload

    def test_a_cached_model_is_usable_with_downloads_off(self) -> None:
        from .training import ocr_trocr

        patch, payload = self._fake_pins()
        with patch:
            folder = ocr_trocr.base_model_dir()
            folder.mkdir(parents=True)
            for name in ocr_trocr.BASE_FILES:
                (folder / name).write_bytes(payload(name))
            self.assertTrue(ocr_trocr.cached_base_model_ok())
            with mock.patch.object(ocr_trocr, "_fetch_file", side_effect=AssertionError("network")):
                provenance = ocr_trocr.ensure_base_model()  # no download, no refusal
            self.assertEqual(provenance["from"], "cache")
            self.assertEqual(provenance["revision"], ocr_trocr.REVISION)
            [option] = self.backend.start_options(WORKER_OFF)
            self.assertTrue(option["available"] and option["ready"])

    def test_urls_are_pinned_to_the_commit_not_a_moving_branch(self) -> None:
        from .training import ocr_trocr

        self.assertEqual(set(ocr_trocr.BASE_FILES), {"config.json", "preprocessor_config.json", "pytorch_model.bin"})
        for name in ocr_trocr.BASE_FILES:
            with self.subTest(name=name):
                self.assertEqual(
                    ocr_trocr.file_url(name),
                    f"https://huggingface.co/microsoft/trocr-small-handwritten/resolve/{ocr_trocr.REVISION}/{name}",
                )
        self.assertNotIn("/main/", ocr_trocr.file_url("config.json"))

    def test_a_corrupt_or_wrong_size_cached_file_is_not_trusted(self) -> None:
        from .training import ocr_trocr

        patch, payload = self._fake_pins()
        with patch:
            folder = ocr_trocr.base_model_dir()
            folder.mkdir(parents=True)
            for name in ocr_trocr.BASE_FILES:
                (folder / name).write_bytes(payload(name))
            self.assertTrue(ocr_trocr.cached_base_model_ok())
            weights = "pytorch_model.bin"
            (folder / weights).write_bytes(b"y" * len(payload(weights)))
            self.assertFalse(ocr_trocr.cached_base_model_ok())  # same size, other bytes: the SHA-256 catches it
            (folder / "config.json").write_bytes(b"short")
            self.assertFalse(ocr_trocr.cached_base_model_ok())  # wrong size is caught before any hashing

    def test_downloads_on_fetch_each_pinned_file_once_then_use_the_cache(self) -> None:
        from .training import ocr_trocr

        patch, payload = self._fake_pins()

        def fake_fetch(name, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload(name))

        with patch, mock.patch.object(ocr_trocr, "_fetch_file", side_effect=fake_fetch) as fetch:
            os.environ["ATHENA_TRAINING_ALLOW_DOWNLOAD"] = "1"
            first = ocr_trocr.ensure_base_model()
            second = ocr_trocr.ensure_base_model()
        self.assertEqual(fetch.call_count, 3)  # one per pinned file, only on the first call
        self.assertEqual([call.args[0] for call in fetch.call_args_list], list(ocr_trocr.BASE_FILES))
        self.assertEqual((first["from"], second["from"]), ("download", "cache"))
        self.assertEqual(first["revision"], ocr_trocr.REVISION)
        self.assertEqual(first["sha256"], hashlib.sha256(payload("pytorch_model.bin")).hexdigest())
        self.assertEqual(first["bytes"], len(payload("pytorch_model.bin")))
        self.assertEqual(set(first["files"]), set(ocr_trocr.BASE_FILES))

    def test_a_file_that_does_not_match_its_pinned_checksum_is_discarded(self) -> None:
        from .training import ocr_trocr

        class Response:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                chunk, self.data = self.data[:max(0, size)], self.data[max(0, size):]
                return chunk

        patch, payload = self._fake_pins()
        # The server serves the right number of bytes but the wrong ones for every file.
        with patch, mock.patch("urllib.request.OpenerDirector.open", return_value=Response(b"z" * len(payload("config.json")))):
            os.environ["ATHENA_TRAINING_ALLOW_DOWNLOAD"] = "1"
            with self.assertRaises(ValueError) as caught:
                ocr_trocr.ensure_base_model()
        self.assertIn("did not match its pinned checksum", str(caught.exception))
        folder = ocr_trocr.base_model_dir()
        self.assertEqual(list(folder.iterdir()), [])  # nothing survived

    def test_a_download_larger_than_the_pinned_size_is_cut_off(self) -> None:
        from .training import ocr_trocr

        class Endless:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                return b"x" * 1024  # keeps sending forever

        with mock.patch("urllib.request.OpenerDirector.open", return_value=Endless()):
            os.environ["ATHENA_TRAINING_ALLOW_DOWNLOAD"] = "1"
            with self.assertRaises(ValueError) as caught:
                ocr_trocr._fetch_file("config.json", ocr_trocr.base_model_dir() / "config.json")
        self.assertIn("larger than expected", str(caught.exception))


# -- a tiny run with a stubbed model (nothing is downloaded; the network is refused) --------------------
def tiny_model_and_processor():
    """A minimal VisionEncoderDecoder-style pair with the interface the backend touches.

    The processor returns fixed pixel values; the model's loss is a constant tensor, its
    ``generate`` returns token ids the stub tokenizer decodes back to the training text (so
    CER is 0 on a seen picture - machinery, not quality)."""
    import torch

    class TinyProcessor:
        class tokenizer:
            pad_token_id = 1
            cls_token_id = 0

        def __call__(self, images=None, return_tensors=None, **kwargs):
            if not isinstance(images, list):
                images = [images]
            import numpy as np

            arrays = [
                torch.from_numpy(
                    (np.asarray(image.convert("RGB").resize((8, 8)), dtype=np.float32) / 255.0).transpose(2, 0, 1)
                )
                for image in images
            ]
            from types import SimpleNamespace

            return SimpleNamespace(pixel_values=torch.stack(arrays))

    class TinyDecoderConfig:
        vocab = {0: "", 1: "<pad>", 2: "a", 3: "b", 4: "c", 5: " ", 6: "d", 7: "e", 8: "f", 9: "g"}
        vocab_size = 10

        def to_json_string(self):
            return json.dumps({"vocab_size": self.vocab_size, "vocab": {str(k): v for k, v in self.vocab.items()}})

    class TinyConfig:
        decoder_start_token_id = 0
        pad_token_id = 1
        vocab_size = 10

        def to_json_string(self):
            return json.dumps({
                "decoder_start_token_id": self.decoder_start_token_id,
                "pad_token_id": self.pad_token_id,
                "vocab_size": self.vocab_size,
            })

    class TinyModel:
        def __init__(self) -> None:
            self.encoder = type("E", (), {"config": TinyDecoderConfig()})()
            self.decoder = type("D", (), {"config": TinyDecoderConfig()})()
            self.config = TinyConfig()
            self.parameter = torch.nn.Parameter(torch.zeros(1))  # one trainable thing

        def parameters(self):
            return iter([self.parameter])

        def __call__(self, pixel_values=None, labels=None):
            from types import SimpleNamespace

            loss = (self.parameter * 2).sum() + 1.0
            return SimpleNamespace(loss=loss)

        def state_dict(self):
            return {"parameter": self.parameter.detach().cpu()}

        def generate(self, pixel_values, max_length=64):
            return torch.tensor([[2, 5, 3, 5, 6, 5, 7, 5, 8, 5, 9]])  # "a b c d e f g" by the vocab below

        def eval(self):
            return self

        def load_state_dict(self, state):
            self.parameter.data = state["parameter"].clone()

    vocab_text = {0: "", 1: "<pad>", 2: "a", 3: "b", 4: "c", 5: " ", 6: "d", 7: "e", 8: "f", 9: "g"}

    class TinyTokenizer:
        def __init__(self) -> None:
            self.pad_token_id = 1
            self.cls_token_id = 0

        def __call__(self, text, **kwargs):
            import torch

            length = kwargs.get("max_length", 64)
            ids = [vocab_text.get(character, 2) for character in text][:length]
            ids += [1] * (length - len(ids))
            return SimpleNamespaceIds(ids)

        def batch_decode(self, sequences, skip_special_tokens=True):
            inverted = {value: key for key, value in vocab_text.items()}
            return [
                "".join(inverted.get(int(token), "") for token in sequence.tolist()).replace("<pad>", "").strip()
                for sequence in sequences
            ]

    class SimpleNamespaceIds:
        def __init__(self, ids) -> None:
            import torch

            self.input_ids = torch.tensor([ids])

    class WithTokenizer:
        def __init__(self) -> None:
            self.tokenizer = TinyTokenizer()

        def __call__(self, images=None, return_tensors=None, **kwargs):
            return TinyProcessor()(images=images, return_tensors=return_tensors, **kwargs)

        def batch_decode(self, sequences, skip_special_tokens=True):
            return self.tokenizer.batch_decode(sequences, skip_special_tokens=skip_special_tokens)

    processor = WithTokenizer()
    processor.tokenizer = TinyTokenizer()
    return processor, TinyModel()


@NEEDS_ML
class TinyRunTests(OcrBase):
    """The full runner path with the model stubbed: export, train, evaluate, register."""

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
        from .training import ocr_trocr

        self.patches = [
            mock.patch.object(ocr_trocr, "ensure_base_model", return_value={
                "repo_id": "microsoft/trocr-small-handwritten", "what": "tiny test model",
                "revision": ocr_trocr.REVISION, "source_url": "https://example.invalid/pytorch_model.bin",
                "sha256": "0" * 64, "bytes": 1, "files": {}, "licence_declared": "mit (model card)",
                "from": "cache",
            }),
            mock.patch.object(ocr_trocr.OcrTrOCRBackend, "_load_base_model", staticmethod(tiny_model_and_processor)),
            mock.patch.object(ocr_trocr.OcrTrOCRBackend, "_rebuild", staticmethod(lambda checkpoint: tiny_model_and_processor())),
            mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network used")),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_train_writes_an_artifact_and_evaluation(self) -> None:
        from .training import ocr_trocr

        version = self.make_ocr_version(count=4)
        # The runner's work-directory layout: work/dataset/ with the artifact saved next to it,
        # which is where ``evaluate`` looks for the sibling transcripts.json.
        work = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(work, ignore_errors=True))
        dataset = work / "dataset"
        backend = ocr_trocr.OcrTrOCRBackend()
        items, labels = backend.build_dataset(
            TrainingRun.objects.create(
                project=self.ocr_project, dataset_version=version,
                dataset_manifest_sha256=version.manifest_sha256, backend="ocr-trocr",
                config={}, config_sha256="0" * 64,
            ),
            dataset,
        )
        calls = []
        result = backend.train(
            dataset, {"epochs": 1, "batch_size": 2}, lambda f, m: calls.append((f, m)), lambda: False
        )
        self.assertTrue(result.artifact_path.is_file())
        self.assertEqual(result.label_names, ["transcript"])
        self.assertEqual(result.metrics["train_pictures"], sum(
            1 for entry in json.loads((dataset / "transcripts.json").read_text()) if entry["split"] == "train"
        ))
        self.assertTrue(calls)
        evaluation = backend.evaluate(result.artifact_path, items, labels, {})
        self.assertEqual(evaluation["schema"], "athena.ocr-evaluation/1")
        self.assertTrue(evaluation["available"])
        self.assertIn("cer", evaluation)
        self.assertIn("word_accuracy", evaluation)
        self.assertIn("per_picture", evaluation)
        self.assertIn("out of every 100 characters", evaluation["plain_language"].lower())
        for picture in evaluation["per_picture"]:
            self.assertIn("reference", picture)
            self.assertIn("hypothesis", picture)

    def test_cancel_stops_training(self) -> None:
        from .training import ocr_trocr
        from .training.base import TrainingCancelled

        version = self.make_ocr_version(count=4)
        dataset = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(dataset, ignore_errors=True))
        backend = ocr_trocr.OcrTrOCRBackend()
        backend.build_dataset(
            TrainingRun.objects.create(
                project=self.ocr_project, dataset_version=version,
                dataset_manifest_sha256=version.manifest_sha256, backend="ocr-trocr",
                config={}, config_sha256="0" * 64,
            ),
            dataset,
        )
        with self.assertRaises(TrainingCancelled):
            backend.train(dataset, {"epochs": 1}, lambda f, m: None, lambda: True)

    def test_predict_refuses_boxes_honestly(self) -> None:
        from .training import ocr_trocr

        backend = ocr_trocr.OcrTrOCRBackend()
        with Image.new("RGB", (8, 8)) as image, self.assertRaises(NotImplementedError):
            backend.predict(Path("unused"), image, {})


@NEEDS_ML
class RunnerIntegrationTests(OcrBase):
    """A queued run goes through the product's own runner and becomes a candidate."""

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
        from .training import ocr_trocr

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

    def test_a_run_becomes_a_candidate_with_cer_and_a_verifiable_hash(self) -> None:
        from .training import lifecycle, runner
        from .training.ocr_trocr import OcrTrOCRBackend

        version = self.make_ocr_version(count=6)
        response = self.client.post(
            f"/api/projects/{self.ocr_project.id}/training/runs",
            data=json.dumps({"dataset_id": str(version.id), "acknowledge_pretrained_terms": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202, response.content)
        run = TrainingRun.objects.get(id=response.json()["run"]["id"])
        self.assertEqual(run.backend, "ocr-trocr")  # defaulted, not sent
        self.assertEqual(run.task, "handwriting-ocr")
        self.assertEqual(run.annotation_kind, "transcripts")
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded", finished.error)
        model = finished.model_versions.get()
        self.assertEqual((model.stage, model.backend), (ModelVersion.CANDIDATE, "ocr-trocr"))
        self.assertEqual(model.task, "handwriting-ocr")
        evaluation = model.evaluation
        self.assertEqual(evaluation["schema"], "athena.ocr-evaluation/1")
        self.assertTrue(evaluation["available"])
        self.assertIn("cer", evaluation)
        artifact = lifecycle.verify_artifact(model)  # the stored file still matches its hash
        self.assertTrue(Path(artifact).is_file())

    def test_a_box_backend_is_refused_for_an_ocr_project(self) -> None:
        version = self.make_ocr_version(count=4)
        response = self.client.post(
            f"/api/projects/{self.ocr_project.id}/training/runs",
            data=json.dumps(
                {"dataset_id": str(version.id), "backend": "torchvision-fasterrcnn",
                 "acknowledge_pretrained_terms": True}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("handwriting", response.json()["error"])

    def test_the_licence_tick_is_required(self) -> None:
        version = self.make_ocr_version(count=4)
        response = self.client.post(
            f"/api/projects/{self.ocr_project.id}/training/runs",
            data=json.dumps({"dataset_id": str(version.id)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json().get("needs_acknowledgement"))

    def test_a_candidate_can_be_staged(self) -> None:
        from .training import lifecycle, runner

        version = self.make_ocr_version(count=6)
        run = TrainingRun.objects.create(
            project=self.ocr_project, dataset_version=version,
            dataset_manifest_sha256=version.manifest_sha256, backend="ocr-trocr",
            task="handwriting-ocr", annotation_kind="transcripts",
            config={}, config_sha256="0" * 64,
        )
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded", finished.error)
        model = finished.model_versions.get()
        staged = lifecycle.stage_model(model, self.user)
        self.assertEqual(staged.stage, ModelVersion.STAGED)
        # The plain-results endpoint says honestly where a text model's results live.
        response = self.client.get(
            f"/api/projects/{self.ocr_project.id}/models/{model.id}/results/plain"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body.get("non_vision"))


# -- evaluation shape -------------------------------------------------------------------------------------
class EvaluationShapeTests(SimpleTestCase):
    def test_the_evaluation_shape_is_stable(self) -> None:
        from .training.ocr_trocr import EVALUATION_SCHEMA, plain_language_from_cer

        self.assertEqual(EVALUATION_SCHEMA, "athena.ocr-evaluation/1")
        self.assertIn("out of every 100 characters", plain_language_from_cer(0.08).lower())
        self.assertIn("every character", plain_language_from_cer(0.0).lower())
        self.assertIn("words", plain_language_from_cer(0.1, 0.9).lower())
