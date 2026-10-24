"""Opt-in pretrained weights for the reference torchvision backend.

Nothing here touches the network: the downloader (`_fetch`) and `urllib` are always patched, and the
"published weights" used by the torch-gated tests are random tensors written to a temp folder.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock, skipUnless

from django.test import TestCase, override_settings
from PIL import Image

from .training import torchvision_frcnn as tv
from .training.torchvision_frcnn import PRETRAINED_FILES, TorchvisionFasterRCNN

TORCH_INSTALLED = (
    importlib.util.find_spec("torch") is not None and importlib.util.find_spec("torchvision") is not None
)


class PretrainedBase(TestCase):  # a database test: the download switch is read from the console settings too
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        override = override_settings(DATA_HOME=Path(self.home.name))
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(tv.DOWNLOAD_ENV, None)
        self.backend = TorchvisionFasterRCNN()

    def fake_fetch(self, content: bytes = b"fake-weights"):
        def write(url, destination, hash_prefix):
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(content)

        return mock.patch.object(tv, "_fetch", side_effect=write)


class PretrainedConfigTests(PretrainedBase):
    def test_default_is_none_and_nothing_else_changed(self) -> None:
        self.assertEqual(self.backend.default_config()["pretrained"], "none")
        self.assertEqual(self.backend.validate_config({})["pretrained"], "none")
        self.assertEqual(self.backend.validate_config({})["epochs"], 5)

    def test_accepts_only_the_three_modes(self) -> None:
        for mode in ("none", "imagenet_backbone", "coco"):
            self.assertEqual(self.backend.validate_config({"pretrained": mode})["pretrained"], mode)
        for bad in ("imagenet", "COCO", "", None, True, 3, ["coco"], "https://example.com/w.pth"):
            with self.subTest(bad=bad), self.assertRaisesMessage(ValueError, "pretrained must be one of"):
                self.backend.validate_config({"pretrained": bad})

    def test_download_switch_defaults_off(self) -> None:
        self.assertFalse(tv.downloads_allowed())
        for value, expected in (("1", True), ("true", True), ("0", False), ("", False), ("no", False), ("2", False)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: value}):
                self.assertEqual(tv.downloads_allowed(), expected)

    def test_cache_lives_under_data_home_models(self) -> None:
        self.assertEqual(tv.pretrained_dir(), Path(self.home.name) / "models" / "pretrained")
        self.assertEqual(tv._weights_path("coco").parent, Path(self.home.name) / "models" / "pretrained" / "checkpoints")

    def test_license_note_is_honest_about_what_was_and_was_not_checked(self) -> None:
        note = self.backend.license_note
        for needle in ("BSD-3-Clause", "No pretrained weights", "ATHENA_TRAINING_ALLOW_DOWNLOAD=1",
                       "non-commercial research and educational purposes", "CC BY 4.0", "Flickr",
                       "unverified", "ask a lawyer", "no licence field"):
            self.assertIn(needle, note)
        self.assertNotIn("commercial use is allowed", note.lower())


class EnsureWeightsTests(PretrainedBase):
    def test_none_does_no_io_and_no_download(self) -> None:
        with mock.patch.object(tv, "_fetch", side_effect=AssertionError("network")):
            self.assertEqual(tv.ensure_pretrained_weights("none"), {"mode": "none"})
        self.assertFalse((Path(self.home.name) / "models").exists())

    def test_downloads_off_fails_in_plain_language_and_fetches_nothing(self) -> None:
        for mode in ("coco", "imagenet_backbone"):
            with self.subTest(mode=mode), mock.patch.object(tv, "_fetch", side_effect=AssertionError("network")):
                with self.assertRaises(ValueError) as caught:
                    tv.ensure_pretrained_weights(mode)
                message = str(caught.exception)
                self.assertIn("downloading is switched off", message)
                self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", message)
                self.assertIn("pretrained set to 'none'", message)
                self.assertIn("licence", message)
                self.assertNotIn(self.home.name, message)
        self.assertFalse((Path(self.home.name) / "models").exists())

    def test_downloads_on_fetches_once_then_uses_the_cache(self) -> None:
        os.environ[tv.DOWNLOAD_ENV] = "1"
        content = b"pretend-weights"
        with self.fake_fetch(content) as fetch:
            first = tv.ensure_pretrained_weights("coco")
            with mock.patch.object(tv, "_verify", return_value=True):
                second = tv.ensure_pretrained_weights("coco")
        self.assertEqual(fetch.call_count, 1)
        url, destination, prefix = fetch.call_args.args
        self.assertEqual((url, prefix), (PRETRAINED_FILES["coco"]["url"], PRETRAINED_FILES["coco"]["hash_prefix"]))
        self.assertEqual(Path(destination), tv._weights_path("coco"))
        self.assertEqual((first["from"], second["from"]), ("download", "cache"))
        self.assertEqual(first["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(first["source_url"], PRETRAINED_FILES["coco"]["url"])

    def test_a_cached_file_with_the_right_checksum_works_with_downloads_off(self) -> None:
        content = b"already-here"
        patched = {"coco": {**PRETRAINED_FILES["coco"], "hash_prefix": hashlib.sha256(content).hexdigest()[:8]}}
        path = tv._weights_path("coco")
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        with mock.patch.dict(tv.PRETRAINED_FILES, patched), mock.patch.object(tv, "_fetch", side_effect=AssertionError):
            self.assertEqual(tv.ensure_pretrained_weights("coco")["from"], "cache")

    def test_a_cached_file_with_the_wrong_checksum_is_not_trusted(self) -> None:
        path = tv._weights_path("coco")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"corrupt")
        with self.assertRaisesMessage(ValueError, "downloading is switched off"):
            tv.ensure_pretrained_weights("coco")
        os.environ[tv.DOWNLOAD_ENV] = "1"
        with self.fake_fetch() as fetch:
            tv.ensure_pretrained_weights("coco")
        self.assertEqual(fetch.call_count, 1)  # replaced, not reused


class FetchTests(PretrainedBase):
    class Response:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    def test_checksum_mismatch_discards_the_file(self) -> None:
        destination = Path(self.home.name) / "x" / "w.pth"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(b"tampered")):
            with self.assertRaisesMessage(ValueError, "did not match its published checksum"):
                tv._fetch("https://example.invalid/w.pth", destination, "00000000")
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_matching_checksum_is_kept(self) -> None:
        data = b"good-bytes"
        destination = Path(self.home.name) / "x" / "w.pth"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(data)):
            tv._fetch("https://example.invalid/w.pth", destination, hashlib.sha256(data).hexdigest()[:8])
        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual([p.name for p in destination.parent.iterdir()], ["w.pth"])

    def test_network_failure_is_a_plain_message_naming_the_way_out(self) -> None:
        destination = Path(self.home.name) / "x" / "w.pth"
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(ValueError) as caught:
                tv._fetch("https://example.invalid/w.pth", destination, "00000000")
        self.assertIn("Could not download the pretrained weights (no route)", str(caught.exception))
        self.assertIn("'none'", str(caught.exception))


# --------------------------------------------------------------------------------------
@skipUnless(TORCH_INSTALLED, "torch/torchvision are not installed here; run in the ML venv")
class PretrainedTrainingTests(PretrainedBase):
    def test_constants_match_the_installed_torchvision_enums(self) -> None:
        from torchvision.models import MobileNet_V3_Large_Weights
        from torchvision.models.detection import FasterRCNN_MobileNet_V3_Large_320_FPN_Weights

        self.assertEqual(PRETRAINED_FILES["imagenet_backbone"]["url"], MobileNet_V3_Large_Weights.IMAGENET1K_V1.url)
        self.assertEqual(PRETRAINED_FILES["coco"]["url"], FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1.url)
        for info in PRETRAINED_FILES.values():
            self.assertTrue(info["url"].rsplit("-", 1)[1].startswith(info["hash_prefix"]))

    def test_installed_weight_metadata_has_no_licence_field(self) -> None:
        """The licence note says so; if a newer torchvision adds one, update the note."""
        from torchvision.models import MobileNet_V3_Large_Weights
        from torchvision.models.detection import FasterRCNN_MobileNet_V3_Large_320_FPN_Weights

        for weights in (MobileNet_V3_Large_Weights.IMAGENET1K_V1, FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1):
            self.assertFalse([key for key in weights.meta if "licen" in key.lower()])

    def make_dataset(self) -> Path:
        root = Path(self.home.name) / "work" / "dataset"
        (root / "images" / "train").mkdir(parents=True)
        (root / "labels" / "train").mkdir(parents=True)
        (root / "classes.txt").write_text("damaged\nnormal\n", encoding="utf-8")
        for index in range(4):
            Image.new("RGB", (96, 96), (30 * index, 90, 160)).save(root / "images" / "train" / f"p{index}.png")
            (root / "labels" / "train" / f"p{index}.txt").write_text(
                "0 0.5 0.5 0.4 0.4\n1 0.2 0.2 0.2 0.2\n", encoding="ascii"
            )
        return root

    def fake_published_weights(self, mode: str):
        """Random weights laid out exactly like torchvision's published file for ``mode``."""
        import torch

        torch.manual_seed(123)
        if mode == "coco":
            state = TorchvisionFasterRCNN.build_structure(91, True).state_dict()
        else:
            from torchvision.models import mobilenet_v3_large

            state = mobilenet_v3_large(weights=None).state_dict()  # the published file is a plain BatchNorm one

        def write(url, destination, hash_prefix):
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            torch.save(state, destination)

        return state, mock.patch.object(tv, "_fetch", side_effect=write)

    def run_mode(self, mode: str):
        os.environ[tv.DOWNLOAD_ENV] = "1"
        state, fetch = self.fake_published_weights(mode)
        dataset = self.make_dataset()
        with fetch, mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")):
            result = self.backend.train(
                dataset, {"epochs": 1, "batch_size": 2, "seed": 1, "pretrained": mode}, lambda f, m: None, lambda: False
            )
        return state, result

    def test_coco_start_loads_the_published_weights_and_round_trips(self) -> None:
        import torch

        state, result = self.run_mode("coco")
        self.assertEqual(result.metrics["pretrained"]["mode"], "coco")
        self.assertEqual(result.metrics["pretrained"]["from"], "download")
        checkpoint = torch.load(result.artifact_path, map_location="cpu", weights_only=True)
        self.assertEqual((checkpoint["pretrained"], checkpoint["frozen_bn"]), ("coco", True))
        # The head was replaced for our 2 labels (+ background); the backbone came from the file.
        self.assertEqual(checkpoint["state_dict"]["roi_heads.box_predictor.cls_score.weight"].shape[0], 3)
        key = "backbone.body.0.0.weight"
        self.assertTrue(torch.equal(checkpoint["state_dict"][key], state[key]))  # early backbone layers are frozen
        found = self.backend.predict(result.artifact_path, Image.new("RGB", (96, 96)), {"confidence": 0.0, "max_boxes": 3})
        self.assertLessEqual(len(found), 3)

    def test_imagenet_backbone_start_loads_only_the_backbone(self) -> None:
        import torch

        state, result = self.run_mode("imagenet_backbone")
        self.assertEqual(result.metrics["pretrained"]["mode"], "imagenet_backbone")
        checkpoint = torch.load(result.artifact_path, map_location="cpu", weights_only=True)
        self.assertTrue(checkpoint["frozen_bn"])
        # backbone.body layer 0 is frozen (trainable layers = 3), so it must equal the file exactly.
        self.assertTrue(torch.equal(checkpoint["state_dict"]["backbone.body.0.0.weight"], state["features.0.0.weight"]))
        self.backend.predict(result.artifact_path, Image.new("RGB", (96, 96)), {"confidence": 0.0, "max_boxes": 3})

    def test_none_still_downloads_nothing_and_reloads_with_old_layout(self) -> None:
        import torch

        dataset = self.make_dataset()
        with mock.patch.object(tv, "_fetch", side_effect=AssertionError("network")), mock.patch(
            "urllib.request.OpenerDirector.open", side_effect=AssertionError("network")
        ), mock.patch("torch.hub.load_state_dict_from_url", side_effect=AssertionError("network")):
            result = self.backend.train(dataset, {"epochs": 1, "batch_size": 2}, lambda f, m: None, lambda: False)
            self.assertEqual(result.metrics["pretrained"], {"mode": "none"})
            checkpoint = torch.load(result.artifact_path, map_location="cpu", weights_only=True)
            self.assertFalse(checkpoint["frozen_bn"])
            # An artifact written before this setting existed has neither key and must still load.
            for key in ("pretrained", "frozen_bn"):
                checkpoint.pop(key)
            torch.save(checkpoint, result.artifact_path)
            self.backend.predict(result.artifact_path, Image.new("RGB", (96, 96)), {"confidence": 0.0, "max_boxes": 3})
        self.assertFalse((Path(self.home.name) / "models").exists())

    def test_downloads_off_stops_training_before_any_work(self) -> None:
        dataset = self.make_dataset()
        with mock.patch.object(tv.TorchvisionFasterRCNN, "build_model", side_effect=AssertionError("started")):
            with self.assertRaisesMessage(ValueError, "downloading is switched off"):
                self.backend.train(dataset, {"pretrained": "coco"}, lambda f, m: None, lambda: False)
