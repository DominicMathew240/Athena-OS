"""The "start from a ready-made model" choice in the Teach panel: what the server tells the page, what it
refuses, and what is written down. No test downloads anything: the weights files are random bytes written to
a temporary data folder and the checksum check is patched.

The page itself (disabled state, licence box, tick-box, Start staying off) is verified in a real browser:
see docs/dogfood/2026-10-cross-browser.md ("W15 pretrained choice") and web/scripts/browser_smoke.mjs.
"""

from __future__ import annotations

import os
import re
from unittest import mock

from django.core.cache import cache

from .tests_dataset_ui import STATIC
from .tests_training import FakeBackend, TrainingBase
from .training import lifecycle, runner, torchvision_frcnn as tv
from .models import ModelEvent, TrainingRun


def plain_texts(option: dict) -> list[str]:
    return [str(option.get(key) or "") for key in ("title", "summary", "licence_plain", "unavailable_reason", "how_to_enable", "size_note")]


class StartOptionsTests(TrainingBase):
    """TorchvisionFasterRCNN.start_options / start_check (no torch needed)."""

    def setUp(self) -> None:
        super().setUp()
        self.backend = tv.TorchvisionFasterRCNN()
        patcher = mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def options(self, worker=None):
        return {o["value"]: o for o in self.backend.start_options(worker or {"online": False, "downloads_allowed": None})}

    def cache_file(self, mode: str) -> None:
        path = tv._weights_path(mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not real weights")

    def test_three_choices_scratch_first_and_scratch_always_available(self) -> None:
        options = self.backend.start_options({"online": False, "downloads_allowed": None})
        self.assertEqual([o["value"] for o in options], ["none", "imagenet_backbone", "coco"])
        self.assertTrue(options[0]["available"])
        self.assertFalse(options[0]["needs_acknowledgement"])
        self.assertTrue(options[1]["needs_acknowledgement"] and options[2]["needs_acknowledgement"])

    def test_titles_name_the_two_collections_in_plain_words(self) -> None:
        options = self.options()
        self.assertEqual(options["none"]["title"], "Start from scratch")
        self.assertEqual(options["imagenet_backbone"]["title"], "Start from a general picture model (ImageNet)")
        self.assertEqual(options["coco"]["title"], "Start from a general object model (COCO)")

    def test_scratch_is_honest_that_it_needs_many_pictures(self) -> None:
        summary = self.options()["none"]["summary"].lower()
        self.assertIn("many", summary)
        self.assertIn("pictures", summary)
        self.assertIn("nothing is downloaded", summary)

    def test_downloads_off_and_nothing_cached_disables_both_and_says_how_to_turn_it_on(self) -> None:
        for mode in ("imagenet_backbone", "coco"):
            option = self.options()[mode]
            with self.subTest(mode=mode):
                self.assertFalse(option["available"])
                self.assertFalse(option["ready"])
                self.assertIn("switched off", option["unavailable_reason"])
                self.assertIn("administrator", option["unavailable_reason"])
                self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", option["how_to_enable"])
                self.assertIn("manage.py train_worker", option["how_to_enable"])

    def test_downloads_on_makes_them_available_but_not_ready(self) -> None:
        with mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "1"}):
            option = self.options()["coco"]
        self.assertTrue(option["available"])
        self.assertFalse(option["ready"])
        self.assertEqual(option["unavailable_reason"], "")
        self.assertIn("downloaded once", option["size_note"])

    def test_a_verified_cached_file_is_usable_with_downloads_off(self) -> None:
        self.cache_file("coco")
        with mock.patch.object(tv, "_verify", return_value=True):
            option = self.options()["coco"]
        self.assertTrue(option["available"] and option["ready"])
        self.assertIn("nothing will be downloaded", option["size_note"].lower())

    def test_a_cached_file_that_fails_verification_is_not_trusted(self) -> None:
        self.cache_file("coco")
        with mock.patch.object(tv, "_verify", return_value=False):
            option = self.options()["coco"]
        self.assertFalse(option["available"] or option["ready"])

    def test_the_helper_setting_wins_over_the_console_setting(self) -> None:
        with mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "1"}):
            off = self.options({"online": True, "downloads_allowed": False})["coco"]
        self.assertFalse(off["available"])
        on = self.options({"online": True, "downloads_allowed": True})["coco"]
        self.assertTrue(on["available"])

    def test_without_a_running_helper_the_reason_says_the_console_setting_is_a_guess(self) -> None:
        reason = self.options({"online": False, "downloads_allowed": None})["coco"]["unavailable_reason"]
        self.assertIn("not running", reason)

    def test_pretrained_choices_carry_the_ask_a_lawyer_warning(self) -> None:
        for mode in ("imagenet_backbone", "coco"):
            with self.subTest(mode=mode):
                self.assertIn("Ask a lawyer before commercial use", self.options()[mode]["licence_plain"])
        self.assertEqual(self.options()["none"]["licence_plain"], "")

    def test_no_accuracy_figure_is_restated_and_no_jargon_leaks(self) -> None:
        for option in self.backend.start_options({"online": True, "downloads_allowed": False}):
            for text in plain_texts(option):
                with self.subTest(option=option["value"], text=text[:40]):
                    self.assertNotRegex(text, r"%|\bmAP|\bAP50|accuracy|\bscore")
                    self.assertNotRegex(text, r"(?i)\bepoch|\bbackend|\bartifact|\bcheckpoint|\bSHA")

    def test_start_check_asks_for_the_tick_only_for_ready_made_starts(self) -> None:
        worker = {"online": True, "downloads_allowed": True}
        self.assertEqual(self.backend.start_check({"pretrained": "none"}, worker), {"needs_acknowledgement": False, "blocked": ""})
        coco = self.backend.start_check({"pretrained": "coco"}, worker)
        self.assertTrue(coco["needs_acknowledgement"])
        self.assertEqual(coco["blocked"], "")

    def test_start_check_blocks_when_it_could_not_run(self) -> None:
        check = self.backend.start_check({"pretrained": "coco"}, {"online": True, "downloads_allowed": False})
        self.assertIn("switched off", check["blocked"])
        self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD=1", check["blocked"])


class PretrainedFake(FakeBackend):
    """A backend with the same hook, so create_run can be tested without torch."""

    name = "fake-pretrained"
    ask = True
    blocked = ""

    def validate_config(self, config):
        return {"epochs": 1, "pretrained": config.get("pretrained", "none")}

    def start_check(self, config, worker):
        return {"needs_acknowledgement": config["pretrained"] != "none", "blocked": type(self).blocked if config["pretrained"] != "none" else ""}


class AcknowledgementTests(TrainingBase):
    def setUp(self) -> None:
        super().setUp()
        from . import training

        PretrainedFake.blocked = ""
        training.register_backend(PretrainedFake, replace=True)
        self.addCleanup(training.unregister_backend, PretrainedFake.name)
        self.snap = self.make_dataset()
        self.runs_url = self.api("/training/runs")

    def post_run(self, **extra):
        body = {"backend": "fake-pretrained", "dataset_id": str(self.snap.id), **extra}
        return self.post(self.runs_url, body)

    def test_from_scratch_needs_no_tick(self) -> None:
        response = self.post_run(config={"pretrained": "none"})
        self.assertEqual(response.status_code, 202, response.content)

    def test_a_ready_made_start_without_the_tick_is_refused_in_plain_words(self) -> None:
        response = self.post_run(config={"pretrained": "coco"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("tick", response.json()["error"].lower())
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_the_tick_must_be_literally_true(self) -> None:
        for bad in ("yes", 1, "true", None):
            with self.subTest(bad=bad):
                response = self.post_run(config={"pretrained": "coco"}, acknowledge_pretrained_terms=bad)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_with_the_tick_the_choice_is_in_the_immutable_config_and_the_event_log(self) -> None:
        response = self.post_run(config={"pretrained": "coco"}, acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 202, response.content)
        run = TrainingRun.objects.get()
        self.assertEqual(run.config["pretrained"], "coco")
        self.assertEqual(run.config_sha256, lifecycle.config_digest(run.config))
        self.assertEqual(response.json()["run"]["config"]["pretrained"], "coco")
        event = ModelEvent.objects.get(kind="run_created")
        self.assertIn("coco", event.note)
        self.assertIn("licence", event.note.lower())
        self.assertIn("lawyer", event.note.lower())
        run.config = {**run.config, "pretrained": "none"}
        with self.assertRaises(Exception):
            run.save()

    def test_a_blocked_start_is_refused_before_anything_is_queued(self) -> None:
        PretrainedFake.blocked = "Downloading is switched off. Ask your administrator."
        response = self.post_run(config={"pretrained": "coco"}, acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn("switched off", response.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_unknown_payload_keys_are_still_rejected(self) -> None:
        response = self.post_run(config={}, surprise=True)
        self.assertEqual(response.status_code, 400)

    def test_a_backend_without_the_hook_never_asks(self) -> None:
        response = self.post(self.runs_url, {"backend": "fake-backend", "dataset_id": str(self.snap.id)})
        self.assertEqual(response.status_code, 202, response.content)


class BackendsEndpointTests(TrainingBase):
    def test_backends_list_carries_start_options_and_the_downloads_flag(self) -> None:
        with mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "0"}):
            body = self.client.get(self.api("/training/backends")).json()
        torch_backend = next(b for b in body["backends"] if b["name"] == "torchvision-fasterrcnn")
        self.assertEqual([o["value"] for o in torch_backend["start_options"]], ["none", "imagenet_backbone", "coco"])
        self.assertEqual(body["downloads"], {"allowed": False, "reported_by": "console"})
        fake = next(b for b in body["backends"] if b["name"] == "fake-backend")
        self.assertEqual(fake["start_options"], [])

    def test_the_running_helper_decides_the_flag(self) -> None:
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {"torchvision-fasterrcnn": True}, {"downloads_allowed": True})
        with mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "0"}):
            body = self.client.get(self.api("/training/backends")).json()
        self.assertEqual(body["downloads"], {"allowed": True, "reported_by": "helper"})
        coco = next(o for b in body["backends"] if b["name"] == "torchvision-fasterrcnn" for o in b["start_options"] if o["value"] == "coco")
        self.assertTrue(coco["available"])

    def test_the_helper_reports_its_own_setting_in_every_heartbeat(self) -> None:
        for value, expected in (("1", True), ("0", False)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: value}):
                cache.clear()
                runner.heartbeat("ready", {"torchvision-fasterrcnn": True})
                self.assertIs(lifecycle.worker_status()["downloads_allowed"], expected)

    def test_a_heartbeat_from_an_older_helper_leaves_the_flag_unknown(self) -> None:
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {})
        self.assertIsNone(lifecycle.worker_status()["downloads_allowed"])


class PanelMarkupTests(TrainingBase):
    def setUp(self) -> None:
        super().setUp()
        self.html = self.client.get("/").content.decode()

    def test_the_control_is_collapsed_and_has_a_radio_group_a_licence_box_and_a_tick(self) -> None:
        self.assertRegex(self.html, r'<details class="dataset-advanced" id="tr-start-from"(?![^>]*\bopen\b)[^>]*hidden')
        for element_id in ("tr-start-from-why", "tr-pre-none", "tr-pre-imagenet_backbone", "tr-pre-coco", "tr-pre-licence", "tr-pre-ack"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        self.assertRegex(self.html, r'<fieldset[^>]*id="tr-start-from-options"[^>]*>\s*<legend>')
        self.assertRegex(self.html, r'<input id="tr-pre-ack" type="checkbox"')

    def test_summary_text(self) -> None:
        self.assertIn("Advanced: start from a ready-made model", self.html)

    def test_script_sends_the_choice_and_the_tick_and_never_trusts_innerhtml(self) -> None:
        js = (STATIC / "console.js").read_text()
        section = js[js.index("// --- Teach the computer") : js.index("// --- end of teaching ---")]
        self.assertIn("acknowledge_pretrained_terms", section)
        self.assertRegex(section, r"pretrained")
        self.assertNotRegex(section, r"innerHTML|insertAdjacentHTML|outerHTML")

    def test_script_restates_no_accuracy_number_and_the_guide_is_linked(self) -> None:
        js = (STATIC / "console.js").read_text()
        section = js[js.index("function trRenderStartFrom") : js.index("function trUpdateStart")]
        self.assertNotRegex(section, r"\d\.\d{2,}|mAP|AP50|accuracy|\d+%")
        self.assertIn("docs/guides/model-quality.md", self.html)
        self.assertNotRegex(self.html[self.html.index('id="tr-start-from"') : self.html.index('id="tr-advanced"')], r"\d\.\d{2,}|mAP|AP50|accuracy|\d+%")

    def test_the_built_in_tool_licence_text_no_longer_claims_nothing_is_ever_downloaded(self) -> None:
        js = (STATIC / "console.js").read_text()
        self.assertNotIn("no ready-made model is downloaded, so there is no second licence to check", js)
        self.assertRegex(js, r"(?i)unless you choose a ready-made start")
