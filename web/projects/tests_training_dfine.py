"""The opt-in D-FINE training backend (``dfine-coco``): licence honesty, allow-list, download rules, hooks, and a
tiny CPU run.

Nothing here touches the network: ``_fetch`` / ``urllib`` are always patched, and the "pretrained" model used by the
run tests is a tiny randomly initialised D-FINE built from its config class. Tests that need torch + transformers +
scipy skip cleanly when they are missing (the lean web environment).
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock, skipUnless

from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image, ImageDraw

from . import training
from .tests_training import TrainingBase
from .training import dfine, runner, torchvision_frcnn as tv
from .training.base import BoxPrediction, StartBlocked, TrainingCancelled
from .training.dfine import DFineBackend

ML_INSTALLED = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "scipy"))
NEEDS_ML = skipUnless(ML_INSTALLED, "torch, transformers and scipy are not installed here; run in the dfine/ML venv")
ROOT = Path(__file__).resolve().parent.parent
WORKER_OFF = {"online": True, "downloads_allowed": False}
WORKER_ON = {"online": True, "downloads_allowed": True}


class DFineBase(TestCase):  # a database test: the download switch is read from the console settings too
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
        dfine._VERIFIED.clear()
        self.backend = DFineBackend()

    def fake_files(self, size: str = "nano", content: bytes = b"fake-bytes"):
        """Patch the pinned table for ``size`` so that ``content`` is the 'published' content of every file."""
        files = {
            name: {"sha256": hashlib.sha256(content + name.encode()).hexdigest(), "bytes": len(content) + len(name)}
            for name in dfine.CHECKPOINTS[size]["files"]
        }
        table = {**dfine.CHECKPOINTS, size: {**dfine.CHECKPOINTS[size], "files": files}}
        return mock.patch.object(dfine, "CHECKPOINTS", table), (lambda name: content + name.encode())

    def fake_fetch(self, payload):
        def write(url, destination, sha256, expected_bytes=None):
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(payload(Path(url).name))

        return mock.patch.object(dfine, "_fetch", side_effect=write)


# -- the allow-list ---------------------------------------------------------------------------------------
class AllowListTests(DFineBase):
    def test_exactly_the_two_coco_only_checkpoints_at_pinned_revisions(self) -> None:
        self.assertEqual(dfine.ALLOWED_REPO_IDS, {"ustc-community/dfine-nano-coco", "ustc-community/dfine-small-coco"})
        self.assertEqual(set(dfine.CHECKPOINTS), {"nano", "small"})
        for size, info in dfine.CHECKPOINTS.items():
            with self.subTest(size=size):
                self.assertRegex(info["revision"], r"^[0-9a-f]{40}$")
                self.assertTrue(info["repo_id"].endswith(f"dfine-{size}-coco"))
                self.assertEqual(set(info["files"]), {"config.json", "model.safetensors"})
                for name, expected in info["files"].items():
                    self.assertRegex(expected["sha256"], r"^[0-9a-f]{64}$", name)
                    self.assertGreater(expected["bytes"], 0)
                self.assertIs(dfine.checkpoint_for(size), info)

    def test_objects365_derived_checkpoints_are_refused(self) -> None:
        for repo in (
            "ustc-community/dfine-nano-obj2coco", "ustc-community/dfine-small-obj365", "ustc-community/dfine-medium-obj2coco",
            "PekingU/rtdetr_r18vd_coco_o365", "someone/dfine-nano-Objects365",
        ):
            with self.subTest(repo=repo), self.assertRaises(ValueError) as caught:
                dfine.refuse_checkpoint(repo)
            self.assertIn("Objects365", str(caught.exception))
            self.assertIn("commercially cleared", str(caught.exception))

    def test_any_other_checkpoint_is_refused_and_the_allowed_ones_pass(self) -> None:
        for repo in ("ustc-community/dfine-medium-coco", "evil/dfine-nano-coco", "", "../etc/passwd"):
            with self.subTest(repo=repo), self.assertRaisesMessage(ValueError, "not on Athena's list"):
                dfine.refuse_checkpoint(repo)
        for repo in dfine.ALLOWED_REPO_IDS:
            dfine.refuse_checkpoint(repo)

    def test_a_tampered_table_entry_is_caught_when_the_checkpoint_is_looked_up(self) -> None:
        bad = {**dfine.CHECKPOINTS, "nano": {**dfine.CHECKPOINTS["nano"], "repo_id": "ustc-community/dfine-nano-obj2coco"}}
        with mock.patch.object(dfine, "CHECKPOINTS", bad), self.assertRaisesMessage(ValueError, "Objects365"):
            dfine.checkpoint_for("nano")
        with self.assertRaisesMessage(ValueError, "size must be one of"):
            dfine.checkpoint_for("medium")

    def test_a_checkpoint_cannot_be_chosen_by_name_in_the_config(self) -> None:
        for key in ("checkpoint", "repo_id", "model", "revision", "weights"):
            with self.subTest(key=key), self.assertRaisesMessage(ValueError, "cannot be chosen by name"):
                self.backend.validate_config({key: "ustc-community/dfine-nano-obj2coco"})

    def test_urls_are_pinned_to_the_revision(self) -> None:
        url = dfine.file_url("nano", "model.safetensors")
        self.assertEqual(
            url,
            "https://huggingface.co/ustc-community/dfine-nano-coco/resolve/"
            "066438d3d8f0da137a37b38fdf3368fd4afceced/model.safetensors",
        )
        self.assertNotIn("/main/", url)


# -- the licence note --------------------------------------------------------------------------------------
class LicenceNoteTests(DFineBase):
    def test_quotes_the_publisher_and_keeps_the_coco_question_open(self) -> None:
        note = self.backend.license_note
        for needle in (
            "Apache License 2.0", "Apache-2.0", "license: apache-2.0", "datasets: coco", "ustc-community/dfine-nano-coco",
            "066438d3d8f0da137a37b38fdf3368fd4afceced", "f79e65b5fbb33ceb9d3ebba042955d7410c608f8",
            "is NOT a statement about the licences of the data", "UNCHANGED", "CC BY 4.0", "Flickr",
            "should not be assumed to be commercially cleared under the D-FINE license", "Objects365",
            "academic purpose only", "Refused on purpose", "not legal advice", "ask a lawyer", "unverified",
            "ATHENA_TRAINING_ALLOW_DOWNLOAD=1", "SHA-256",
        ):
            self.assertIn(needle, note)
        lowered = note.lower()
        for forbidden in ("commercial use is allowed", "commercially safe", "is cleared", "fully licensed"):
            self.assertNotIn(forbidden, lowered)

    def test_the_licence_text_shown_in_the_panel_does_not_claim_clearance(self) -> None:
        option = self.backend.start_options(WORKER_ON)[0]
        text = option["licence_plain"]
        self.assertIn("Apache-2.0", text)
        self.assertIn("not a statement about the photos", text)
        self.assertIn("Ask a lawyer", text)
        self.assertTrue(option["needs_acknowledgement"])

    def test_it_is_described_without_importing_anything_heavy_and_is_not_the_default(self) -> None:
        info = training.describe_backend(self.backend)
        self.assertEqual(info["name"], "dfine-coco")
        self.assertEqual(info["license_note"], dfine.LICENSE_NOTE)
        self.assertEqual(info["dataset_format"], "yolo")
        self.assertEqual(training.training_settings()["DEFAULT_BACKEND"], "torchvision-fasterrcnn")


# -- config ---------------------------------------------------------------------------------------------
class ConfigTests(DFineBase):
    def test_defaults_validate_to_themselves(self) -> None:
        defaults = self.backend.default_config()
        self.assertEqual(self.backend.validate_config({}), {**defaults})
        self.assertEqual((defaults["pretrained"], defaults["size"]), ("coco", "nano"))

    def test_bounds(self) -> None:
        ok = self.backend.validate_config({"epochs": 100, "batch_size": 16, "seed": 2**31 - 1, "threads": 64, "size": "small",
                                           "learning_rate": 1})
        self.assertEqual((ok["epochs"], ok["batch_size"], ok["size"], ok["learning_rate"]), (100, 16, "small", 1.0))
        for key, bad in (("epochs", 0), ("epochs", 101), ("epochs", True), ("epochs", 2.5), ("batch_size", 0), ("batch_size", 17),
                         ("seed", -1), ("threads", 65), ("threads", "4")):
            with self.subTest(key=key, bad=bad), self.assertRaisesMessage(ValueError, f"{key} must be a whole number between"):
                self.backend.validate_config({key: bad})
        for bad in (0, -1, 1.5, True, "0.1", None):
            with self.subTest(rate=bad), self.assertRaisesMessage(ValueError, "learning_rate must be a number"):
                self.backend.validate_config({"learning_rate": bad})
        for bad in ("medium", "NANO", None, 3):
            with self.subTest(size=bad), self.assertRaisesMessage(ValueError, "size must be one of"):
                self.backend.validate_config({"size": bad})

    def test_a_huge_or_odd_learning_rate_is_a_plain_refusal_never_a_crash(self) -> None:
        for bad in (10**400, -(10**400), 10**309, float("nan"), float("inf"), [1], {}, object()):
            with self.subTest(rate=repr(bad)[:20]), self.assertRaisesMessage(ValueError, "learning_rate must be a number"):
                self.backend.validate_config({"learning_rate": bad})

    def test_a_malformed_derived_list_is_a_plain_refusal_never_a_type_error(self) -> None:
        for bad in ([[1]], [{}], [None], [["epochs"]], [1], "epochs", {"epochs": 1}, ["epochs", {}]):
            with self.subTest(derived=bad), self.assertRaisesMessage(ValueError, "derived must list which of"):
                self.backend.validate_config({"derived": bad})

    def test_from_scratch_is_refused_with_a_plain_reason(self) -> None:
        for mode in ("none", "imagenet_backbone"):
            with self.subTest(mode=mode), self.assertRaises(ValueError) as caught:
                self.backend.validate_config({"pretrained": mode})
            self.assertIn("pretrained must be 'coco'", str(caught.exception))
            self.assertIn("far more pictures", str(caught.exception))
        for bad in ("COCO", "", None, True, ["coco"], "https://example.com/w.pt"):
            with self.subTest(bad=bad), self.assertRaisesMessage(ValueError, "pretrained must be 'coco'"):
                self.backend.validate_config({"pretrained": bad})
        self.assertEqual(self.backend.validate_config({"pretrained": "auto"})["pretrained"], "auto")

    def test_unknown_keys_are_refused(self) -> None:
        with self.assertRaisesMessage(ValueError, "Unsupported training setting: image_size"):
            self.backend.validate_config({"image_size": 320})

    def test_extra_keys_follow_the_reference_backends_rules(self) -> None:
        normalized = self.backend.validate_config(
            {"quality": "better", "pretrained_requested": "auto", "derived": ["epochs", "batch_size"]}
        )
        self.assertEqual((normalized["quality"], normalized["pretrained_requested"], normalized["derived"]),
                         ("better", "auto", ["batch_size", "epochs"]))
        for bad in ({"quality": "best"}, {"pretrained_requested": "coco"}, {"derived": []}, {"derived": ["seed"]},
                    {"derived": ["epochs", "epochs"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.backend.validate_config(bad)


# -- hooks the one-click path and the panel use -----------------------------------------------------------
class HookTests(DFineBase):
    def test_derive_settings_is_pure_and_bounded(self) -> None:
        self.assertEqual(dfine.derive_settings(60, "quick"), dfine.derive_settings(60, "quick"))
        for pictures in (1, 2, 7, 60, 200, 5000):
            for quality in dfine.QUALITIES:
                derived = dfine.derive_settings(pictures, quality)
                with self.subTest(pictures=pictures, quality=quality):
                    self.assertTrue(1 <= derived["epochs"] <= 100 and 1 <= derived["batch_size"] <= 16)
                    self.assertLessEqual(derived["batch_size"], pictures)
        better, quick = dfine.derive_settings(60, "better"), dfine.derive_settings(60, "quick")
        self.assertGreaterEqual(better["epochs"], quick["epochs"])
        self.assertEqual(dfine.derive_settings(60, "quick", batch_size=2)["batch_size"], 2)
        for bad in ((0, "quick"), (True, "quick"), (5, "best")):
            with self.assertRaises(ValueError):
                dfine.derive_settings(*bad)

    def test_derive_settings_refuses_a_bad_batch_size_in_plain_words(self) -> None:
        for bad in (0, -1, True, 2.5, float("nan"), float("inf"), "4"):
            with self.subTest(batch=bad), self.assertRaisesMessage(ValueError, "batch_size must be a whole number"):
                dfine.derive_settings(10, "quick", bad)
        self.assertEqual(dfine.derive_settings(10, "quick", 4)["batch_size"], 4)
        self.assertEqual(dfine.derive_settings(3, "quick", 16)["batch_size"], 3)  # still capped by the pictures
        self.assertEqual(dfine.derive_settings(3, "quick", 10**400)["batch_size"], 3)

    def test_prepare_config_resolves_auto_to_coco_and_fills_derived_settings(self) -> None:
        os.environ[tv.DOWNLOAD_ENV] = "1"
        prepared = self.backend.prepare_config(
            {"pretrained": "auto", "quality": "quick"}, train_pictures=60, worker={"online": False, "downloads_allowed": None}
        )
        self.assertEqual(prepared["pretrained"], "coco")
        self.assertEqual(prepared["pretrained_requested"], "auto")
        self.assertEqual(sorted(prepared["derived"]), ["batch_size", "epochs"])
        self.assertEqual(self.backend.validate_config(prepared)["pretrained"], "coco")

    def test_explicit_settings_win_over_derived_ones(self) -> None:
        prepared = self.backend.prepare_config(
            {"pretrained": "coco", "quality": "quick", "epochs": 7}, train_pictures=60, worker=WORKER_ON
        )
        self.assertEqual(prepared["epochs"], 7)
        self.assertEqual(prepared["derived"], ["batch_size"])

    def test_forged_derived_keys_are_refused(self) -> None:
        for key, value in (("pretrained_requested", "auto"), ("derived", ["epochs"])):
            with self.assertRaisesMessage(ValueError, "filled in by Athena"):
                self.backend.prepare_config({key: value}, train_pictures=10, worker=WORKER_ON)

    def test_auto_is_blocked_in_plain_words_when_nothing_can_run_and_never_falls_back_to_scratch(self) -> None:
        with self.assertRaises(StartBlocked) as caught:
            self.backend.prepare_config({"pretrained": "auto"}, train_pictures=10, worker=WORKER_OFF)
        self.assertIn("starting it from nothing is not offered", str(caught.exception).lower())
        self.assertIsNone(caught.exception.details["start_from"]["resolved"])

    def test_resolve_start_only_ever_resolves_to_coco(self) -> None:
        self.assertEqual(self.backend.resolve_start(WORKER_ON)["resolved"], "coco")
        self.assertIsNone(self.backend.resolve_start(WORKER_OFF)["resolved"])

    def test_start_options_say_what_is_missing_and_how_to_fix_it(self) -> None:
        [off] = self.backend.start_options(WORKER_OFF)
        self.assertEqual(off["value"], "coco")
        self.assertFalse(off["available"])
        self.assertFalse(off["ready"])
        self.assertIn("switched off", off["unavailable_reason"])
        self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", off["how_to_enable"])
        [on] = self.backend.start_options(WORKER_ON)
        self.assertTrue(on["available"])
        self.assertFalse(on["ready"])

    def test_the_download_switch_is_inherited_from_the_torchvision_helper(self) -> None:
        from .console_settings import OFF, ON, TRAINING_DOWNLOADS
        from .models import ConsoleSetting

        no_report = {"online": False, "downloads_allowed": None}
        self.assertFalse(self.backend.start_options(no_report)[0]["available"])  # nothing set: off
        os.environ[tv.DOWNLOAD_ENV] = "1"
        self.assertTrue(self.backend.start_options(no_report)[0]["available"])  # the environment
        ConsoleSetting.objects.create(key=TRAINING_DOWNLOADS, value=OFF)  # the console switch wins over the environment
        self.assertFalse(self.backend.start_options(no_report)[0]["available"])
        self.assertFalse(self.backend.start_options(WORKER_ON)[0]["available"])
        ConsoleSetting.objects.filter(pk=TRAINING_DOWNLOADS).update(value=ON)
        os.environ.pop(tv.DOWNLOAD_ENV)
        self.assertTrue(self.backend.start_options(WORKER_OFF)[0]["available"])
        name = "downloads_for" if hasattr(tv, "downloads_for") else "_downloads_for"  # public once the other helper lands
        with mock.patch.object(tv, name, return_value=(True, "helper")) as helper:
            self.backend.start_options(no_report)
        self.assertTrue(helper.called)  # one rule for every backend that downloads

    def test_start_check_always_asks_for_the_tick_and_blocks_only_without_a_way_to_get_the_model(self) -> None:
        blocked = self.backend.start_check({"size": "small"}, WORKER_OFF)
        self.assertTrue(blocked["needs_acknowledgement"])
        self.assertIn("switched off", blocked["blocked"])
        allowed = self.backend.start_check({"size": "small"}, WORKER_ON)
        self.assertEqual((allowed["needs_acknowledgement"], allowed["blocked"]), (True, ""))

    def test_a_cached_verified_model_is_usable_with_downloads_off(self) -> None:
        patch, payload = self.fake_files("nano")
        with patch:
            folder = dfine.checkpoint_dir("nano")
            folder.mkdir(parents=True)
            for name in dfine.CHECKPOINTS["nano"]["files"]:
                (folder / name).write_bytes(payload(name))
            [option] = self.backend.start_options(WORKER_OFF)
            self.assertTrue(option["available"] and option["ready"])
            self.assertEqual(self.backend.start_check({"size": "nano"}, WORKER_OFF)["blocked"], "")

    def test_the_time_estimate_leans_long_and_only_covers_what_was_measured(self) -> None:
        self.assertEqual(set(dfine.SECONDS_PER_PICTURE_EPOCH), {"nano"})
        self.assertGreaterEqual(dfine.SECONDS_PER_PICTURE_EPOCH["nano"], 0.326)  # the slowest measured single pass
        self.assertEqual(dfine.estimate_minutes(200, 8, "nano"), 10)  # 200 x 8 x 0.35 s = 560 s
        self.assertIsNone(dfine.estimate_minutes(200, 8, "small"))
        self.assertEqual((dfine.QUALITY_PLANS["quick"]["target_steps"], dfine.QUALITY_PLANS["better"]["target_steps"]), (400, 600))
        self.assertEqual(dfine.derive_settings(200, "quick"), {"epochs": 8, "batch_size": 4})
        self.assertEqual(dfine.derive_settings(200, "better"), {"epochs": 12, "batch_size": 4})

    def test_teach_preview_gives_both_qualities_and_a_time_estimate_only_with_a_start(self) -> None:
        preview = self.backend.teach_preview(60, "coco")
        self.assertEqual(preview["quick"]["epochs"], dfine.derive_settings(60, "quick")["epochs"])
        self.assertGreaterEqual(preview["quick"]["estimated_minutes"], 1)
        self.assertIn("not a promise", preview["estimate_note"])
        self.assertIsNone(self.backend.teach_preview(60, None)["quick"]["estimated_minutes"])
        self.assertIsNone(self.backend.teach_preview(None, "coco")["quick"])

    def test_teach_preview_with_a_count_that_is_not_a_number_gives_no_estimate_instead_of_crashing(self) -> None:
        for bad in ("x", 2.5, True, float("nan"), [3]):
            with self.subTest(count=bad):
                preview = self.backend.teach_preview(bad, "coco")
                self.assertIsNone(preview["quick"])
                self.assertIsNone(preview["better"])
                self.assertIsNone(preview["train_pictures"])

    def test_the_comparability_caveat_is_visible_to_users_not_only_in_code_comments(self) -> None:
        caveat = "synthetic shapes on one computer and are not comparable with the default tool"
        self.assertIn(caveat, " ".join(dfine.CONSTRAINTS))
        self.assertIn(caveat, dfine.ESTIMATE_NOTE)
        self.assertIn(caveat, self.backend.teach_preview(60, "coco")["estimate_note"])

    def test_the_speed_wording_matches_what_was_measured(self) -> None:
        text = " ".join(dfine.CONSTRAINTS)
        self.assertNotIn("several seconds per step", text)
        self.assertIn("about a second or two per step", text)

    def test_the_objects365_line_names_what_the_readme_says_and_the_org_is_described(self) -> None:
        note = dfine.LICENSE_NOTE
        self.assertIn("(*_obj365, *_obj2coco)", note)
        self.assertNotIn("coco_o365", note)
        self.assertIn("Transformers team", note)
        self.assertIn("not the authors of the D-FINE paper", note)


# -- downloading, caching and hash checks ---------------------------------------------------------------
class DownloadTests(DFineBase):
    def test_downloads_off_fails_in_plain_language_and_fetches_nothing(self) -> None:
        with mock.patch.object(dfine, "_fetch", side_effect=AssertionError("network")), \
                mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")):
            for size in dfine.SIZES:
                with self.subTest(size=size), self.assertRaises(ValueError) as caught:
                    dfine.ensure_checkpoint(size)
                message = str(caught.exception)
                self.assertIn("downloading is switched off", message)
                self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", message)
                self.assertIn("licence", message)
                self.assertNotIn("'none'", message)  # from-scratch is not an alternative here
                self.assertNotIn(self.home.name, message)
        self.assertFalse((Path(self.home.name) / "models").exists())

    def test_a_console_off_choice_is_named_as_the_reason_and_the_environment_is_said_to_be_ignored(self) -> None:
        from .console_settings import OFF, TRAINING_DOWNLOADS
        from .models import ConsoleSetting

        os.environ[tv.DOWNLOAD_ENV] = "1"  # the console choice wins, so this must not be offered as the fix
        ConsoleSetting.objects.create(key=TRAINING_DOWNLOADS, value=OFF)
        with mock.patch.object(dfine, "_fetch", side_effect=AssertionError("network")):
            with self.assertRaises(ValueError) as caught:
                dfine.ensure_checkpoint("nano")
        message = str(caught.exception)
        self.assertIn("Downloads were switched off in the console (Access > Ready-made model downloads)", message)
        self.assertIn("the environment setting is ignored while a console choice exists", message)
        self.assertNotIn("set ATHENA_TRAINING_ALLOW_DOWNLOAD=1", message)

    def test_the_user_facing_texts_name_every_host_a_download_may_come_from(self) -> None:
        with self.assertRaises(ValueError) as caught:
            dfine.ensure_checkpoint("nano")
        self.assertIn("huggingface.co and *.hf.co", str(caught.exception))
        [option] = self.backend.start_options(WORKER_OFF)
        self.assertIn("huggingface.co and *.hf.co", option["size_note"])

    def test_downloads_on_fetches_each_pinned_file_once_then_uses_the_cache(self) -> None:
        patch, payload = self.fake_files("nano")
        os.environ[tv.DOWNLOAD_ENV] = "1"
        with patch, self.fake_fetch(payload) as fetch:
            first = dfine.ensure_checkpoint("nano")
            second = dfine.ensure_checkpoint("nano")
        self.assertEqual(fetch.call_count, 2)  # config.json + model.safetensors, only on the first call
        urls = sorted(call.args[0] for call in fetch.call_args_list)
        self.assertEqual(urls, sorted(dfine.file_url("nano", name) for name in ("config.json", "model.safetensors")))
        for call in fetch.call_args_list:
            self.assertEqual(Path(call.args[1]).parent, dfine.checkpoint_dir("nano"))
        self.assertEqual((first["from"], second["from"]), ("download", "cache"))
        self.assertEqual(first["repo_id"], "ustc-community/dfine-nano-coco")
        self.assertEqual(first["revision"], "066438d3d8f0da137a37b38fdf3368fd4afceced")
        self.assertEqual(first["mode"], "coco")
        self.assertEqual(first["sha256"], hashlib.sha256(payload("model.safetensors")).hexdigest())
        self.assertEqual(first["bytes"], len(payload("model.safetensors")))
        self.assertEqual(set(first["files"]), {"config.json", "model.safetensors"})

    def test_the_cache_lives_under_the_data_home_pretrained_folder_with_revision(self) -> None:
        folder = dfine.checkpoint_dir("small")
        self.assertEqual(
            folder,
            Path(self.home.name) / "models" / "pretrained" / "dfine" / "ustc-community__dfine-small-coco"
            / "f79e65b5fbb33ceb9d3ebba042955d7410c608f8",
        )

    def test_the_console_switch_helper_is_the_one_that_decides(self) -> None:
        patch, payload = self.fake_files("nano")
        from .console_settings import ON, TRAINING_DOWNLOADS
        from .models import ConsoleSetting

        ConsoleSetting.objects.create(key=TRAINING_DOWNLOADS, value=ON)  # the console switch, no environment variable
        with patch, self.fake_fetch(payload) as fetch:
            self.assertEqual(dfine.ensure_checkpoint("nano")["from"], "download")
        self.assertEqual(fetch.call_count, 2)

    def test_a_corrupt_or_wrong_size_cached_file_is_not_trusted_and_is_replaced(self) -> None:
        patch, payload = self.fake_files("nano")
        with patch:
            folder = dfine.checkpoint_dir("nano")
            folder.mkdir(parents=True)
            for name in dfine.CHECKPOINTS["nano"]["files"]:
                (folder / name).write_bytes(b"corrupt")
            self.assertFalse(dfine.cached_checkpoint_ok("nano"))
            with self.assertRaisesMessage(ValueError, "downloading is switched off"):
                dfine.ensure_checkpoint("nano")
            os.environ[tv.DOWNLOAD_ENV] = "1"
            with self.fake_fetch(payload) as fetch:
                dfine.ensure_checkpoint("nano")
            self.assertEqual(fetch.call_count, 2)
            self.assertTrue(dfine.cached_checkpoint_ok("nano"))

    def test_changing_a_cached_file_after_it_was_verified_is_noticed(self) -> None:
        patch, payload = self.fake_files("nano")
        with patch:
            folder = dfine.checkpoint_dir("nano")
            folder.mkdir(parents=True)
            for name in dfine.CHECKPOINTS["nano"]["files"]:
                (folder / name).write_bytes(payload(name))
            self.assertTrue(dfine.cached_checkpoint_ok("nano"))
            (folder / "model.safetensors").write_bytes(b"x" * len(payload("model.safetensors")))  # same size, other bytes
            self.assertFalse(dfine.cached_checkpoint_ok("nano"))


class FetchTests(DFineBase):
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
        destination = Path(self.home.name) / "x" / "w.safetensors"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(b"tampered")):
            with self.assertRaisesMessage(ValueError, "did not match its pinned checksum"):
                dfine._fetch("https://example.invalid/w", destination, "0" * 64)
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_matching_checksum_is_kept(self) -> None:
        data = b"good-bytes"
        destination = Path(self.home.name) / "x" / "w.safetensors"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(data)):
            dfine._fetch("https://example.invalid/w", destination, hashlib.sha256(data).hexdigest())
        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual([p.name for p in destination.parent.iterdir()], ["w.safetensors"])

    class Endless:
        """A server that never stops: counts what was asked for and what was handed out."""

        def __init__(self, total: int) -> None:
            self.left, self.given = total, 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            size = self.left if size is None or size < 0 else min(size, self.left)
            self.left -= size
            self.given += size
            return b"x" * size

    def test_a_download_longer_than_the_pinned_size_is_stopped_early_and_discarded(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"
        server = self.Endless(50 * 1024 * 1024)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=server):
            with self.assertRaisesMessage(ValueError, "larger than expected"):
                dfine._fetch("https://example.invalid/w", destination, "0" * 64, expected_bytes=16)
        self.assertLessEqual(server.given, 17)  # expected + 1, not 50 MB
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_exactly_the_pinned_size_is_accepted(self) -> None:
        data = b"a" * 16
        destination = Path(self.home.name) / "x" / "w.safetensors"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(data)):
            dfine._fetch("https://example.invalid/w", destination, hashlib.sha256(data).hexdigest(), expected_bytes=16)
        self.assertEqual(destination.read_bytes(), data)

    def test_ensure_checkpoint_passes_the_pinned_size_to_the_fetch(self) -> None:
        patch, payload = self.fake_files("nano")
        os.environ[tv.DOWNLOAD_ENV] = "1"
        with patch, self.fake_fetch(payload) as fetch:
            dfine.ensure_checkpoint("nano")
        sizes = sorted(call.kwargs.get("expected_bytes") for call in fetch.call_args_list)
        self.assertEqual(sizes, sorted(len(payload(n)) for n in ("config.json", "model.safetensors")))

    @skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_a_planted_part_symlink_is_not_followed(self) -> None:
        folder = Path(self.home.name) / "x"
        folder.mkdir()
        victim = Path(self.home.name) / "victim.txt"
        victim.write_text("precious")
        (folder / "w.safetensors.part").symlink_to(victim)
        data = b"good-bytes"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(data)):
            dfine._fetch("https://example.invalid/w", folder / "w.safetensors", hashlib.sha256(data).hexdigest())
        self.assertEqual(victim.read_text(), "precious")
        self.assertEqual((folder / "w.safetensors").read_bytes(), data)
        self.assertFalse((folder / "w.safetensors").is_symlink())
        self.assertEqual([p.name for p in folder.iterdir()], ["w.safetensors"])

    def test_a_read_failure_is_reported_as_a_download_problem_not_a_save_problem(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"

        class Slow(self.Response):
            def read(self, size=-1):
                raise TimeoutError("timed out")

        for error in (TimeoutError("timed out"), ConnectionResetError("reset")):
            response = Slow(b"")
            with self.subTest(error=type(error).__name__), mock.patch.object(Slow, "read", side_effect=error), \
                    mock.patch("urllib.request.OpenerDirector.open", return_value=response):
                with self.assertRaises(ValueError) as caught:
                    dfine._fetch("https://example.invalid/w", destination, "0" * 64)
            message = str(caught.exception)
            self.assertIn("Could not download the pretrained weights", message)
            self.assertNotIn("save", message)
            self.assertNotIn("Error", message)
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_a_write_failure_is_reported_as_a_save_problem(self) -> None:
        import errno

        destination = Path(self.home.name) / "x" / "w.safetensors"
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(b"abc")), \
                mock.patch("os.fdopen", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.assertRaises(ValueError) as caught:
                dfine._fetch("https://example.invalid/w", destination, "0" * 64)
        message = str(caught.exception)
        self.assertIn("Could not save the pretrained weights", message)
        self.assertIn("disk", message)
        self.assertNotIn("OSError", message)
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_a_folder_where_a_file_belongs_is_a_plain_message_not_a_permission_error(self) -> None:
        patch, payload = self.fake_files("nano")
        os.environ[tv.DOWNLOAD_ENV] = "1"
        with patch, self.fake_fetch(payload):
            folder = dfine.checkpoint_dir("nano")
            (folder / "config.json").mkdir(parents=True)
            with self.assertRaises(ValueError) as caught:
                dfine.ensure_checkpoint("nano")
        message = str(caught.exception)
        self.assertIn("config.json", message)
        self.assertIn("folder", message)
        self.assertNotIn(self.home.name, message)
        self.assertTrue((folder / "config.json").is_dir())  # nothing was deleted on the user's behalf

    def test_a_part_folder_in_the_way_is_a_plain_message_too(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"
        (destination.parent / "w.safetensors.part").mkdir(parents=True)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.Response(b"abc")):
            with self.assertRaisesMessage(ValueError, "Could not save the pretrained weights"):
                dfine._fetch("https://example.invalid/w", destination, "0" * 64)

    def test_network_failure_is_a_plain_message(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(ValueError) as caught:
                dfine._fetch("https://example.invalid/w", destination, "0" * 64)
        self.assertIn("Could not download the pretrained weights (no route)", str(caught.exception))
        self.assertNotIn("'none'", str(caught.exception))


class RedirectRestrictionTests(DFineBase):
    """Redirects are followed only to huggingface.co and hosts ending in .hf.co, over https (hygiene H5)."""

    ALLOWED = (
        "https://huggingface.co/ustc-community/dfine-nano-coco/resolve/abc/model.safetensors",
        "https://HuggingFace.co/x",
        "https://cdn-lfs.hf.co/repos/aa/bb/file",
        "https://cas-bridge.xethub.hf.co/xet-bridge-us/file",
        "https://cdn-lfs-us-1.hf.co/x",
    )
    REFUSED = {
        "https://hf.co/x": "hf.co",  # only hosts UNDER hf.co
        "https://evilhf.co/x": "evilhf.co",
        "https://hf.co.evil.example/x": "hf.co.evil.example",
        "https://huggingface.co.evil.example/x": "huggingface.co.evil.example",
        "https://xhuggingface.co/x": "xhuggingface.co",
        "https://huggingface.co@evil.example/x": "evil.example",
        "https://cdn.hf.co.evil.example/x": "cdn.hf.co.evil.example",
        "https://cdn-lfs.hf.co:8443/x": "cdn-lfs.hf.co:8443",
        "https://download.pytorch.org/x": "download.pytorch.org",
        "http://cdn-lfs.hf.co/x": "cdn-lfs.hf.co",
        "http://huggingface.co/x": "huggingface.co",
    }

    def test_the_pinned_hosts_are_the_two_the_texts_name(self) -> None:
        self.assertEqual(dfine.PINNED_HOSTS.exact, ("huggingface.co",))
        self.assertEqual(dfine.PINNED_HOSTS.suffixes, (".hf.co",))
        self.assertEqual(dfine.PINNED_HOSTS.described, dfine.DOWNLOAD_HOSTS)  # "huggingface.co and *.hf.co"

    def test_the_decision_function(self) -> None:
        for url in self.ALLOWED:
            with self.subTest(url=url):
                self.assertIsNone(tv.redirect_refusal(url, dfine.PINNED_HOSTS))
        for url, named in self.REFUSED.items():
            with self.subTest(url=url):
                sentence = tv.redirect_refusal(url, dfine.PINNED_HOSTS)
                self.assertIsNotNone(sentence)
                self.assertIn(named, sentence)
                self.assertIn("nothing was saved", sentence)

    def test_the_exact_sentence(self) -> None:
        self.assertEqual(
            tv.redirect_refusal("https://evil.example/x", dfine.PINNED_HOSTS),
            "The download was redirected to evil.example, which is not where Athena fetches ready-made models from "
            "(huggingface.co and *.hf.co), so it was stopped and nothing was saved.",
        )

    def serve(self, hops):
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

    def test_end_to_end_the_real_two_hop_huggingface_download_works(self) -> None:
        data = b"x" * 16
        destination = Path(self.home.name) / "x" / "w.safetensors"
        asked, patched = self.serve([(302, "https://cdn-lfs.hf.co/repos/aa/file"), (200, data)])
        with patched:
            dfine._fetch("https://huggingface.co/r/resolve/rev/w.safetensors", destination, hashlib.sha256(data).hexdigest(), expected_bytes=16)
        self.assertEqual(asked, ["https://huggingface.co/r/resolve/rev/w.safetensors", "https://cdn-lfs.hf.co/repos/aa/file"])
        self.assertEqual(destination.read_bytes(), data)

    def test_end_to_end_a_redirect_elsewhere_is_refused_naming_the_host_and_never_followed(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"
        asked, patched = self.serve([(302, "https://evil.example/w"), (200, b"never read")])
        with patched, self.assertRaisesMessage(ValueError, "redirected to evil.example"):
            dfine._fetch("https://huggingface.co/r/resolve/rev/w.safetensors", destination, "0" * 64, expected_bytes=16)
        self.assertEqual(asked, ["https://huggingface.co/r/resolve/rev/w.safetensors"])
        self.assertEqual(list(destination.parent.iterdir()) if destination.parent.exists() else [], [])

    def test_end_to_end_a_second_redirect_to_a_refused_host_is_caught_too(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"
        asked, patched = self.serve(
            [(302, "https://cdn-lfs.hf.co/a"), (302, "http://cdn-lfs.hf.co/b"), (200, b"never read")]
        )
        with patched, self.assertRaisesMessage(ValueError, "unencrypted"):
            dfine._fetch("https://huggingface.co/r/w", destination, "0" * 64, expected_bytes=16)
        self.assertEqual(len(asked), 2)

    def test_the_size_cap_and_the_checksum_still_apply_after_a_redirect(self) -> None:
        destination = Path(self.home.name) / "x" / "w.safetensors"
        asked, patched = self.serve([(302, "https://cdn-lfs.hf.co/a"), (200, b"y" * 50)])
        with patched, self.assertRaisesMessage(ValueError, "larger than expected"):
            dfine._fetch("https://huggingface.co/r/w", destination, "0" * 64, expected_bytes=16)
        asked, patched = self.serve([(302, "https://cdn-lfs.hf.co/a"), (200, b"z" * 16)])
        with patched, self.assertRaisesMessage(ValueError, "did not match its pinned checksum"):
            dfine._fetch("https://huggingface.co/r/w", destination, "0" * 64, expected_bytes=16)
        self.assertFalse(destination.exists())

    def test_the_fetch_goes_through_the_restricted_opener(self) -> None:
        source = Path(dfine.__file__).read_text()
        self.assertFalse("urllib.request.urlopen(" in source, "a download must go through tv.open_pinned")


# -- registry: nothing heavy in the web process -------------------------------------------------------------
class RegistryTests(TrainingBase):
    def run_python(self, code: str, **env: str):
        return subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
            env={"DJANGO_DEBUG": "1", "PATH": "/usr/bin", "ATHENA_DATA_HOME": self.home.name, **env},
        )

    def test_it_is_registered_through_settings_and_the_default_is_unchanged(self) -> None:
        self.assertIn("dfine-coco", training.backend_classes())
        self.assertIs(training.backend_classes()["dfine-coco"], DFineBackend)
        self.assertEqual(training.training_settings()["DEFAULT_BACKEND"], "torchvision-fasterrcnn")
        self.assertIn("projects.training.dfine:DFineBackend", training.training_settings()["BACKENDS"])

    def test_the_web_process_lists_it_with_torch_and_transformers_blocked(self) -> None:
        code = (
            "import sys\n"
            "for name in ('torch', 'torchvision', 'transformers', 'scipy'): sys.modules[name] = None\n"
            "import os, django; os.environ['DJANGO_SETTINGS_MODULE'] = 'athena_web.settings'; django.setup()\n"
            "from projects import training\n"
            "b = training.get_backend('dfine-coco'); info = training.describe_backend(b)\n"
            "assert info['license_note'] and info['name'] == 'dfine-coco'\n"
            "assert info['available_in_web_process'] is False\n"
            "assert b.start_options({'online': False, 'downloads_allowed': None})\n"
            "assert b.resolve_start({'online': False, 'downloads_allowed': None}) is not None\n"
            "assert b.teach_preview(60, 'coco')['quick']\n"
            "print(info['availability']['message']); print(info['availability']['how_to_enable'])\n"
            "assert training.training_settings()['DEFAULT_BACKEND'] == 'torchvision-fasterrcnn'\n"
        )
        result = self.run_python(code)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("torch, transformers and scipy are not installed in this environment.", result.stdout)
        self.assertIn("pip install -r web/requirements-autolabel.txt", result.stdout)

    def test_the_module_never_imports_a_heavy_framework_at_import_time(self) -> None:
        code = (
            "import sys, os, django; os.environ['DJANGO_SETTINGS_MODULE'] = 'athena_web.settings'; django.setup()\n"
            "from projects import training\n"
            "b = training.get_backend('dfine-coco'); training.describe_backend(b); b.start_options({}); b.teach_preview(5, 'coco')\n"
            "heavy = [m for m in ('torch', 'torchvision', 'transformers', 'scipy', 'safetensors') if m in sys.modules]\n"
            "assert not heavy, heavy\nprint('ok')\n"
        )
        result = self.run_python(code)
        self.assertEqual(result.stdout.strip(), "ok", result.stderr[-800:])

    def test_check_available_names_what_is_missing_in_plain_words(self) -> None:
        for blocked in ("transformers", "scipy"):
            with self.subTest(blocked=blocked):
                code = (
                    f"import sys\nsys.modules[{blocked!r}] = None\n"
                    "import os, django; os.environ['DJANGO_SETTINGS_MODULE'] = 'athena_web.settings'; django.setup()\n"
                    "from projects import training\n"
                    "a = training.get_backend('dfine-coco').check_available()\n"
                    "assert not a.ok; print(a.message)\n"
                )
                result = self.run_python(code)
                self.assertEqual(result.returncode, 0, result.stderr[-800:])
                message = result.stdout.strip()
                self.assertIn(blocked, message)  # in a lean environment torch is named as well
                self.assertTrue(message.endswith("not installed in this environment."), message)
                self.assertRegex(message, r"\b(is|are) not installed")

    @NEEDS_ML
    def test_available_when_everything_is_installed(self) -> None:
        self.assertTrue(DFineBackend().check_available().ok)


# -- a tiny real run (random tiny network, no download) -------------------------------------------------
TINY_CONFIG = dict(
    backbone_config=dict(
        model_type="hgnet_v2", depths=[1, 1], embedding_size=8, hidden_sizes=[16, 32], stem_channels=[3, 8, 8],
        stage_in_channels=[8, 16], stage_mid_channels=[8, 8], stage_out_channels=[16, 32], stage_num_blocks=[1, 1],
        stage_numb_of_layers=[1, 1], stage_downsample=[False, True], stage_light_block=[False, True],
        stage_kernel_size=[3, 3], stage_names=["stem", "stage1", "stage2"], out_features=["stage1", "stage2"],
        out_indices=[1, 2],
    ),
    d_model=32, encoder_hidden_dim=32, decoder_in_channels=[32, 32], encoder_in_channels=[16, 32], decoder_ffn_dim=64,
    encoder_ffn_dim=64, decoder_layers=2, num_queries=12, num_denoising=8, lqe_hidden_dim=16, feat_strides=[16, 32],
    encoder_attention_heads=2, decoder_attention_heads=2, max_num_bins=8, encode_proj_layers=[1], num_feature_levels=2,
    decoder_n_points=[2, 2],
)
TINY_SIZE = 64


def tiny_pretrained(size, num_labels):
    """Stands in for the COCO checkpoint: same class, same head replacement, random weights, nothing downloaded."""
    from transformers import DFineConfig, DFineForObjectDetection

    return DFineForObjectDetection(DFineConfig(num_labels=num_labels, **TINY_CONFIG))


FAKE_PROVENANCE = {
    "mode": "coco", "what": "tiny test model", "backend": "dfine-coco", "size": "nano", "repo_id": "ustc-community/dfine-nano-coco",
    "revision": "066438d3d8f0da137a37b38fdf3368fd4afceced", "source_url": "https://example.invalid/model.safetensors",
    "sha256": "0" * 64, "bytes": 1, "files": {}, "licence_declared": "test", "from": "cache",
}


def make_yolo_dataset(root: Path, count: int = 6) -> Path:
    dataset = root / "dataset"
    for split in ("train", "test"):
        (dataset / "images" / split).mkdir(parents=True)
        (dataset / "labels" / split).mkdir(parents=True)
    (dataset / "classes.txt").write_text("circle\nsquare\n")
    for split, n in (("train", count), ("test", 2)):
        for index in range(n):
            image = Image.new("RGB", (48, 36), (20 + index * 7, 40, 60))
            ImageDraw.Draw(image).rectangle([6, 8, 20, 22], fill=(250, 250, 40))
            image.save(dataset / "images" / split / f"{index}.png")
            (dataset / "labels" / split / f"{index}.txt").write_text(
                "0 0.270833 0.416667 0.291667 0.388889\n" if index else ""  # one picture has no boxes at all
            )
    return dataset


class TinyRunBase(SimpleTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = DFineBackend()
        for patch in (
            mock.patch.object(dfine, "INPUT_SIZE", TINY_SIZE),
            mock.patch.object(dfine, "ensure_checkpoint", return_value=dict(FAKE_PROVENANCE)),
            mock.patch.object(DFineBackend, "_load_pretrained", staticmethod(tiny_pretrained)),
            mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network used")),
        ):
            patch.start()
            self.addCleanup(patch.stop)


@NEEDS_ML
class TinyRunTests(TinyRunBase):
    def train(self, **config):
        dataset = make_yolo_dataset(Path(self.tmp.name))
        calls = []
        result = self.backend.train(
            dataset, {"epochs": 2, "batch_size": 3, "threads": 2, **config}, lambda f, m: calls.append((f, m)), lambda: False
        )
        return dataset, result, calls

    def test_trains_saves_and_predicts_in_project_box_convention(self) -> None:
        dataset, result, calls = self.train()
        self.assertEqual(result.label_names, ["circle", "square"])
        self.assertEqual(result.artifact_path, dataset.parent / dfine.ARTIFACT_NAME)
        self.assertTrue(result.artifact_path.is_file())
        epochs = result.metrics["epochs"]
        self.assertEqual([e["epoch"] for e in epochs], [1, 2])
        self.assertTrue(all(e["mean_loss"] > 0 and e["seconds"] >= 0 for e in epochs))
        self.assertEqual((result.metrics["train_images"], result.metrics["train_boxes"]), (6, 5))
        self.assertEqual(result.metrics["pretrained"]["revision"], FAKE_PROVENANCE["revision"])
        self.assertEqual(result.metrics["input_size"], TINY_SIZE)
        self.assertEqual(len(calls), 4)  # 2 epochs x 2 steps
        self.assertAlmostEqual(calls[-1][0], 1.0)
        with Image.open(dataset / "images" / "test" / "1.png") as image:
            found = self.backend.predict(result.artifact_path, image, {"confidence": 0.0, "max_boxes": 5})
        self.assertTrue(0 < len(found) <= 5)
        for box in found:
            self.assertIn(box.label, ["circle", "square"])
            self.assertTrue(0 <= box.x <= 1 and 0 <= box.y <= 1 and box.width > 0 and box.height > 0)
            self.assertTrue(box.x + box.width <= 1 + 1e-9 and box.y + box.height <= 1 + 1e-9)
            self.assertTrue(0 <= box.score <= 1)
        self.assertEqual([b.score for b in found], sorted((b.score for b in found), reverse=True))

    def test_the_artifact_round_trips_and_gives_identical_boxes(self) -> None:
        import torch

        dataset, result, _ = self.train(seed=5)
        checkpoint = torch.load(result.artifact_path, map_location="cpu", weights_only=True)  # no pickle needed
        self.assertEqual(checkpoint["format"], dfine.ARTIFACT_FORMAT)
        self.assertEqual(checkpoint["labels"], ["circle", "square"])
        self.assertEqual(checkpoint["pretrained"]["repo_id"], "ustc-community/dfine-nano-coco")
        with Image.open(dataset / "images" / "test" / "1.png") as image:
            first = self.backend.predict(result.artifact_path, image, {"confidence": 0.0, "max_boxes": 100})
            again = self.backend.predict(result.artifact_path, image, {"confidence": 0.0, "max_boxes": 100})
            fresh = DFineBackend().predict(result.artifact_path, image, {"confidence": 0.0, "max_boxes": 100})
            copy = Path(self.tmp.name) / "copy.bin"  # the runner stores the file as model.bin
            copy.write_bytes(result.artifact_path.read_bytes())
            moved = DFineBackend().predict(copy, image, {"confidence": 0.0, "max_boxes": 100})
        self.assertTrue(first)
        for other in (again, fresh, moved):
            self.assertEqual(first, other)

    def test_a_file_that_is_not_a_dfine_artifact_is_refused(self) -> None:
        import torch

        path = Path(self.tmp.name) / "other.pt"
        torch.save({"format": "athena.torchvision-fasterrcnn/1", "labels": []}, path)
        with Image.new("RGB", (8, 8)) as image, self.assertRaisesMessage(ValueError, "not a model saved by the dfine-coco backend"):
            self.backend.predict(path, image, {})

    def test_cancel_stops_training(self) -> None:
        dataset = make_yolo_dataset(Path(self.tmp.name))
        with self.assertRaises(TrainingCancelled):
            self.backend.train(dataset, {"epochs": 2, "batch_size": 2}, lambda f, m: None, lambda: True)

    def test_a_progress_callback_can_stop_training(self) -> None:
        dataset = make_yolo_dataset(Path(self.tmp.name))

        def stop(fraction, message):
            raise RuntimeError("time limit")

        with self.assertRaisesMessage(RuntimeError, "time limit"):
            self.backend.train(dataset, {"epochs": 2, "batch_size": 2}, stop, lambda: False)

    def test_an_unresolved_auto_or_a_from_scratch_run_is_refused_before_any_work(self) -> None:
        dataset = make_yolo_dataset(Path(self.tmp.name))
        with mock.patch.object(dfine, "ensure_checkpoint", side_effect=AssertionError("must not be reached")):
            with self.assertRaisesMessage(ValueError, "never resolved"):
                self.backend.train(dataset, {"pretrained": "auto"}, lambda f, m: None, lambda: False)
            with self.assertRaisesMessage(ValueError, "pretrained must be 'coco'"):
                self.backend.train(dataset, {"pretrained": "none"}, lambda f, m: None, lambda: False)

    def test_an_empty_train_split_is_refused(self) -> None:
        dataset = make_yolo_dataset(Path(self.tmp.name))
        for path in (dataset / "images" / "train").iterdir():
            path.unlink()
        with self.assertRaisesMessage(ValueError, "train split has no images"):
            self.backend.train(dataset, {"epochs": 1}, lambda f, m: None, lambda: False)

    def test_a_missing_checkpoint_stops_the_run_with_the_plain_message_and_no_network(self) -> None:
        dataset = make_yolo_dataset(Path(self.tmp.name))
        with mock.patch.object(dfine, "ensure_checkpoint", wraps=None, side_effect=ValueError("downloading is switched off")):
            with self.assertRaisesMessage(ValueError, "downloading is switched off"):
                self.backend.train(dataset, {"epochs": 1}, lambda f, m: None, lambda: False)

    def test_evaluate_uses_the_shared_evaluation_code(self) -> None:
        dataset, result, _ = self.train()
        items = self.backend.eval_items(dataset, "test")
        evaluation = self.backend.evaluate(result.artifact_path, items, ["circle", "square"], {"confidence": 0.5, "max_boxes": 100})
        self.assertEqual(evaluation["schema"], "athena.evaluation/1")
        self.assertTrue(evaluation["available"])
        self.assertEqual(evaluation["confidence_threshold"], 0.5)


@NEEDS_ML
class PostProcessingTests(SimpleTestCase):
    """The box convention, checked on a stand-in network with known outputs."""

    class Fake:
        def __init__(self, logits, boxes):
            import torch

            self.logits, self.boxes = torch.tensor([logits]), torch.tensor([boxes])

        def __call__(self, pixel_values):
            from types import SimpleNamespace

            return SimpleNamespace(logits=self.logits, pred_boxes=self.boxes)

    def predict(self, logits, boxes, **thresholds):
        backend = DFineBackend()
        with mock.patch.object(backend, "_load", return_value=(self.Fake(logits, boxes), {"labels": ["a", "b"], "input_size": 32})):
            return backend.predict(Path("unused"), Image.new("RGB", (100, 50)), thresholds)

    def test_centre_boxes_become_top_left_x_y_width_height(self) -> None:
        [box] = self.predict([[8.0, -8.0]], [[0.5, 0.4, 0.2, 0.2]], confidence=0.5)
        self.assertEqual(box.label, "a")
        self.assertAlmostEqual(box.x, 0.4)
        self.assertAlmostEqual(box.y, 0.3)
        self.assertAlmostEqual(box.width, 0.2)
        self.assertAlmostEqual(box.height, 0.2)
        self.assertGreater(box.score, 0.99)

    def test_boxes_are_clamped_filtered_ordered_and_capped(self) -> None:
        logits = [[-8.0, 3.0], [2.0, -8.0], [-8.0, -8.0], [0.0, 0.0]]
        boxes = [[0.95, 0.5, 0.3, 0.2], [0.3, 0.3, 0.2, 0.2], [0.5, 0.5, 0.1, 0.1], [0.5, 0.5, 0.0, 0.0]]
        found = self.predict(logits, boxes, confidence=0.5, max_boxes=10)
        self.assertEqual([b.label for b in found], ["b", "a", "a", "b"][: len(found)])
        self.assertEqual(len(found), 2)  # the degenerate and the near-zero-score boxes are dropped
        first = found[0]
        self.assertEqual(first.label, "b")
        self.assertAlmostEqual(first.x + first.width, 1.0)  # clamped at the picture edge
        self.assertEqual(len(self.predict(logits, boxes, confidence=0.0, max_boxes=1)), 1)
        self.assertEqual(self.predict(logits, boxes, confidence=0.999), [])

    def test_a_nan_box_is_dropped_and_never_becomes_a_stray_box(self) -> None:
        nan = float("nan")
        found = self.predict([[8.0, -8.0], [7.0, -8.0], [6.0, -8.0]],
                             [[nan, 0.5, 0.2, 0.2], [0.5, 0.5, float("inf"), 0.2], [0.5, 0.5, 0.2, 0.2]], confidence=0.5)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].score, 0.9975, places=3)

    def test_a_confidence_or_cap_that_is_not_a_usable_number_is_a_plain_refusal(self) -> None:
        for key, bad in (("confidence", float("nan")), ("confidence", float("inf")), ("confidence", 1.5), ("confidence", -0.1),
                         ("confidence", "high"), ("confidence", None), ("max_boxes", float("nan")), ("max_boxes", float("inf")),
                         ("max_boxes", 0), ("max_boxes", "x"), ("max_boxes", 10**400)):
            with self.subTest(key=key, bad=bad), self.assertRaisesMessage(ValueError, f"{key} must be"):
                self.predict([[8.0, -8.0]], [[0.5, 0.4, 0.2, 0.2]], **{key: bad})


# -- the whole run through the product's own runner ------------------------------------------------------------
@NEEDS_ML
class RunnerIntegrationTests(TrainingBase):
    def setUp(self) -> None:
        super().setUp()
        for patch in (
            mock.patch.object(dfine, "INPUT_SIZE", TINY_SIZE),
            mock.patch.object(dfine, "ensure_checkpoint", return_value=dict(FAKE_PROVENANCE)),
            mock.patch.object(DFineBackend, "_load_pretrained", staticmethod(tiny_pretrained)),
            mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "1"}),
            mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network used")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_run_becomes_a_candidate_with_evaluation_and_a_verifiable_hash(self) -> None:
        from .models import ModelVersion
        from .training import lifecycle

        dataset = self.make_dataset(count=16)
        refused = self.post(
            self.api("/training/runs"),
            {"backend": "dfine-coco", "dataset_id": str(dataset.id), "config": {"epochs": 1, "batch_size": 4}},
        )
        self.assertEqual(refused.status_code, 400)  # the licence tick is required, same as the torchvision COCO start
        self.assertTrue(refused.json().get("needs_acknowledgement"))
        response = self.post(
            self.api("/training/runs"),
            {"backend": "dfine-coco", "dataset_id": str(dataset.id), "config": {"epochs": 1, "batch_size": 4, "threads": 2},
             "acknowledge_pretrained_terms": True},
        )
        self.assertEqual(response.status_code, 202, response.content)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded", finished.error)
        self.assertEqual(finished.backend, "dfine-coco")
        self.assertEqual(finished.runtime_info["device"], "cpu")
        self.assertIn("transformers", finished.runtime_info)
        self.assertEqual(finished.config["pretrained"], "coco")
        self.assertEqual(finished.metrics["pretrained"]["revision"], FAKE_PROVENANCE["revision"])
        model = finished.model_versions.get()
        self.assertEqual((model.stage, model.backend), (ModelVersion.CANDIDATE, "dfine-coco"))
        self.assertTrue(model.evaluation["available"])
        artifact = lifecycle.verify_artifact(model)  # hash re-check
        with Image.open(self.make_asset_path()) as image:
            found = DFineBackend().predict(artifact, image, {"confidence": 0.0, "max_boxes": 3})
        self.assertTrue(all(isinstance(box, BoxPrediction) for box in found))
        Path(artifact).write_bytes(Path(artifact).read_bytes() + b"x")  # tamper with the stored file
        with self.assertRaises(Exception):
            lifecycle.verify_artifact(model)

    def make_asset_path(self):
        from django.core.files.storage import default_storage

        return default_storage.path(self.project.assets.first().storage_key)

    def test_from_scratch_is_refused_by_the_api_in_plain_words(self) -> None:
        dataset = self.make_dataset(count=14)
        response = self.post(
            self.api("/training/runs"),
            {"backend": "dfine-coco", "dataset_id": str(dataset.id), "config": {"pretrained": "none"},
             "acknowledge_pretrained_terms": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("pretrained must be 'coco'", response.json()["error"])

    def test_the_one_click_start_picks_coco_when_this_tool_is_chosen(self) -> None:
        from .training import lifecycle

        config = lifecycle.snapshot_default_config("dfine-coco", {})
        self.assertEqual(config, {"pretrained": "auto", "quality": "quick"})
        backend, normalized, check = lifecycle.prepare_start(
            "dfine-coco", config, train_pictures=40, acknowledged_terms=True
        )
        self.assertEqual((normalized["pretrained"], normalized["pretrained_requested"]), ("coco", "auto"))
        self.assertEqual(sorted(normalized["derived"]), ["batch_size", "epochs"])
        self.assertTrue(check["needs_acknowledgement"])
        # ... and the default tool is untouched
        self.assertEqual(training.training_settings()["DEFAULT_BACKEND"], "torchvision-fasterrcnn")
