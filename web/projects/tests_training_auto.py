"""One-click Teach backend: ``pretrained: "auto"``, derived settings, and a start that creates its own snapshot.

Prototype, synthetic data. No test downloads anything: the "published weights" are junk bytes in a temporary data
folder with the checksum check patched, exactly as ``tests_pretrained_ui`` does. The reference backend's torch-free
methods are used, and a live-helper heartbeat stands in for torch so a run can be queued in the lean test venv.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import connection
from django.db.models.query import QuerySet
from django.test import Client
from django.test.utils import CaptureQueriesContext

from .datasets import service as dataset_service, validation
from .models import Asset, BoxAnnotation, DatasetVersion, ModelEvent, Project, TrainingRun
from .tests import grant
from .tests_training import FakeBackend, TrainingBase
from .training import lifecycle, torchvision_frcnn as tv

BACKEND = "torchvision-fasterrcnn"

# (pictures in the training split, quality) -> (epochs, batch_size). Worked out by hand from the rules in
# ``derive_settings``; the benchmark's own setting (200 pictures, 8 epochs, batch 4) is the "better" row for 200.
DERIVE_TABLE = {
    (10, "quick"): (40, 4),
    (30, "quick"): (30, 4),
    (60, "quick"): (16, 4),
    (200, "quick"): (5, 4),
    (1000, "quick"): (2, 4),
    (10, "better"): (80, 4),
    (30, "better"): (50, 4),
    (60, "better"): (27, 4),
    (200, "better"): (8, 4),
    (1000, "better"): (3, 4),
    (1, "quick"): (40, 1),
    (2, "better"): (80, 2),
    (3, "quick"): (40, 3),
}

# Seconds of training measured in docs/guides/model-quality.md: (mode, seconds) for 200 pictures x 8 epochs.
BENCHMARK_SECONDS = {"coco": (330.7, 326.1), "imagenet_backbone": (444.3, 541.8), "none": (859.6,)}
BENCHMARK_PICTURE_EPOCHS = 200 * 8


class DeriveSettingsTests(TrainingBase):
    def test_table(self) -> None:
        for (pictures, quality), (epochs, batch) in DERIVE_TABLE.items():
            with self.subTest(pictures=pictures, quality=quality):
                self.assertEqual(
                    tv.derive_settings(pictures, quality), {"epochs": epochs, "batch_size": batch}
                )

    def test_rules_hold_for_every_size(self) -> None:
        for quality in tv.QUALITIES:
            plan = tv.QUALITY_PLANS[quality]
            for pictures in list(range(1, 80)) + [100, 150, 200, 500, 1000, 5000, 50000]:
                got = tv.derive_settings(pictures, quality)
                with self.subTest(pictures=pictures, quality=quality):
                    self.assertLessEqual(got["batch_size"], pictures)  # never larger than the training set
                    self.assertTrue(1 <= got["epochs"] <= 100 and 1 <= got["batch_size"] <= 16)
                    # Time cap: never above the picture-epoch budget unless the floor of two passes forces it.
                    self.assertTrue(got["epochs"] * pictures <= plan["budget"] or got["epochs"] == tv.FLOOR_EPOCHS)
                    # The tool's own validator accepts what the function derives.
                    tv.TorchvisionFasterRCNN().validate_config(got)

    def test_better_never_trains_less_than_quick(self) -> None:
        for pictures in range(1, 500, 7):
            quick, better = (tv.derive_settings(pictures, q) for q in ("quick", "better"))
            with self.subTest(pictures=pictures):
                self.assertGreaterEqual(better["epochs"], quick["epochs"])

    def test_small_sets_get_enough_steps_large_sets_stay_bounded(self) -> None:
        def steps(pictures, quality):
            got = tv.derive_settings(pictures, quality)
            return got["epochs"] * -(-pictures // got["batch_size"])

        self.assertGreaterEqual(steps(30, "quick"), 200)
        self.assertGreaterEqual(steps(60, "better"), 400)
        self.assertGreaterEqual(steps(200, "better"), 400)  # the benchmark's 8 epochs x 50 steps
        for quality in tv.QUALITIES:
            self.assertLessEqual(tv.derive_settings(5000, quality)["epochs"], 3)

    def test_bad_input_is_a_plain_error(self) -> None:
        for pictures in (0, -3, True, 2.5, "10", None):
            with self.subTest(pictures=pictures), self.assertRaises(ValueError):
                tv.derive_settings(pictures, "quick")
        with self.assertRaisesMessage(ValueError, "quality"):
            tv.derive_settings(10, "slow")

    def test_the_time_constant_is_not_faster_than_any_benchmark_run(self) -> None:
        for mode, runs in BENCHMARK_SECONDS.items():
            for seconds in runs:
                with self.subTest(mode=mode, seconds=seconds):
                    self.assertGreaterEqual(tv.SECONDS_PER_PICTURE_EPOCH[mode], seconds / BENCHMARK_PICTURE_EPOCHS)

    def test_estimate_for_the_benchmark_setup_is_close_to_what_was_measured(self) -> None:
        preview = tv.TorchvisionFasterRCNN().teach_preview(200, "coco")
        self.assertEqual(preview["better"]["epochs"], 8)
        minutes = preview["better"]["estimated_minutes"]
        self.assertEqual(minutes, 6)  # 1600 picture-epochs x 0.21 s = 336 s; the benchmark took 326-331 s
        self.assertIn("estimate", preview["estimate_note"].lower())
        self.assertIn("not a promise", preview["estimate_note"].lower())

    def test_no_start_means_no_estimate_not_a_guess(self) -> None:
        preview = tv.TorchvisionFasterRCNN().teach_preview(200, None)
        self.assertIsNone(preview["quick"]["estimated_minutes"])
        self.assertEqual(preview["quick"]["epochs"], 5)
        self.assertEqual(tv.TorchvisionFasterRCNN().teach_preview(None, "coco")["quick"], None)


class ConfigValidationTests(TrainingBase):
    backend = tv.TorchvisionFasterRCNN()

    def test_auto_and_quality_are_accepted(self) -> None:
        got = self.backend.validate_config({"pretrained": "auto", "quality": "better"})
        self.assertEqual((got["pretrained"], got["quality"]), ("auto", "better"))

    def test_old_configs_validate_to_exactly_what_they_did_before(self) -> None:
        self.assertEqual(tv.DEFAULTS["pretrained"], "none")
        self.assertEqual(
            self.backend.validate_config({}),
            {"pretrained": "none", "epochs": 5, "batch_size": 4, "learning_rate": 0.005, "seed": 0, "threads": 0},
        )
        again = self.backend.validate_config({"pretrained": "coco", "epochs": 3})
        self.assertEqual(self.backend.validate_config(again), again)

    def test_bad_values_are_refused_in_plain_words(self) -> None:
        for bad in ({"quality": "slow"}, {"quality": 1}, {"pretrained": "AUTO"}, {"pretrained_requested": "coco"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.backend.validate_config(bad)

    def test_training_refuses_an_unresolved_auto(self) -> None:
        # "auto" is only ever a request; a run that still carries it must fail loudly, not index the weights table.
        with self.assertRaisesMessage(ValueError, "resolved"):
            tv.ensure_pretrained_weights("auto")

    def test_a_huge_or_odd_learning_rate_is_a_plain_refusal_never_a_crash(self) -> None:
        # 10**400 is an int too large for a float: float() raises OverflowError, which used to escape as a 500.
        for bad in (10**400, -(10**400), 10**309, float("nan"), float("inf"), [1], {}, object(), True, "0.1", None):
            with self.subTest(rate=repr(bad)[:20]), self.assertRaisesMessage(ValueError, "learning_rate must be a number"):
                self.backend.validate_config({"learning_rate": bad})


class AutoBase(TrainingBase):
    """The reference backend, a helper that says torch is available, and a data home with no weights."""

    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "0"})
        env.start()
        self.addCleanup(env.stop)
        self.helper(downloads=False)
        self.runs_url = self.api("/training/runs")

    def helper(self, *, downloads: bool | None) -> None:
        extras = {} if downloads is None else {"downloads_allowed": downloads}
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {BACKEND: True}, extras)

    def cache(self, *modes: str) -> None:
        for mode in modes:
            path = tv._weights_path(mode)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not real weights")
        patcher = mock.patch.object(tv, "_verify", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post_run(self, **payload):
        return self.post(self.runs_url, {"backend": BACKEND, **payload})

    def add_pictures(self, count: int) -> list[Asset]:
        assets = []
        for _ in range(count):
            asset = self.make_asset()
            self.make_box(asset)
            assets.append(asset)
        return assets

    def readiness(self) -> dict:
        response = self.client.get(self.api(f"/training/readiness?backend={BACKEND}"))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class HugeNumberTests(AutoBase):
    """A whole number too big for a float is a plain 400 from the API, never a 500 (hygiene H4)."""

    def test_a_huge_learning_rate_through_the_api_is_a_400_not_a_500(self) -> None:
        dataset = self.make_dataset()
        for bad in (10**400, -(10**400)):
            with self.subTest(rate=len(str(bad))):
                response = self.post(
                    self.runs_url,
                    {"backend": BACKEND, "dataset_id": str(dataset.id), "config": {"learning_rate": bad}},
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("learning_rate must be a number above 0 and at most 1", response.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_a_huge_whole_number_in_the_other_settings_is_also_a_400(self) -> None:
        dataset = self.make_dataset()
        for key in ("epochs", "batch_size", "seed", "threads"):
            with self.subTest(key=key):
                response = self.post(
                    self.runs_url,
                    {"backend": BACKEND, "dataset_id": str(dataset.id), "config": {key: 10**400}},
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(f"{key} must be a whole number", response.json()["error"])


class AutoResolutionTests(AutoBase):
    def setUp(self) -> None:
        super().setUp()
        self.dataset = self.make_dataset()

    def start(self, config=None, **extra):
        return self.post_run(dataset_id=str(self.dataset.id), config={"pretrained": "auto", **(config or {})}, **extra)

    def test_coco_is_preferred_when_both_are_cached(self) -> None:
        self.cache("coco", "imagenet_backbone")
        response = self.start(acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 202, response.content)
        config = TrainingRun.objects.get().config
        self.assertEqual((config["pretrained"], config["pretrained_requested"]), ("coco", "auto"))
        self.assertEqual(response.json()["run"]["config"], config)

    def test_falls_back_to_the_imagenet_start_when_only_that_is_cached(self) -> None:
        self.cache("imagenet_backbone")
        self.assertEqual(self.start(acknowledge_pretrained_terms=True).status_code, 202)
        self.assertEqual(TrainingRun.objects.get().config["pretrained"], "imagenet_backbone")

    def test_downloads_allowed_counts_as_runnable_and_prefers_coco(self) -> None:
        self.helper(downloads=True)
        self.assertEqual(self.start(acknowledge_pretrained_terms=True).status_code, 202)
        self.assertEqual(TrainingRun.objects.get().config["pretrained"], "coco")

    def test_a_cached_file_that_fails_its_checksum_is_not_runnable(self) -> None:
        self.cache("coco")
        with mock.patch.object(tv, "_verify", return_value=False):
            response = self.start(acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 409)

    def test_nothing_runnable_is_a_409_naming_the_administrator_step_never_a_scratch_run(self) -> None:
        for ack in (True, None):
            with self.subTest(acknowledged=ack):
                extra = {} if ack is None else {"acknowledge_pretrained_terms": ack}
                response = self.start(**extra)
                self.assertEqual(response.status_code, 409, response.content)
                body = response.json()
                self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD", body["error"])
                self.assertIn("manage.py train_worker", body["error"])
                self.assertIn("administrator", body["error"].lower())
                self.assertIn("models/pretrained/checkpoints", body["error"])
                self.assertIsNone(body["start_from"]["resolved"])
                self.assertEqual(body["start_from"]["available"], [])
                self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD", body["start_from"]["how_to_enable"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_the_helper_switch_wins_over_the_console_switch(self) -> None:
        with mock.patch.dict(os.environ, {tv.DOWNLOAD_ENV: "1"}):
            self.assertEqual(self.start(acknowledge_pretrained_terms=True).status_code, 409)  # helper says off
            self.helper(downloads=None)  # an older helper that does not say: the console's own setting counts
            self.assertEqual(self.start(acknowledge_pretrained_terms=True).status_code, 202)

    def test_the_terms_tick_is_still_enforced_server_side(self) -> None:
        self.cache("coco")
        for bad in (None, False, "true", 1):
            extra = {} if bad is None else {"acknowledge_pretrained_terms": bad}
            with self.subTest(bad=bad):
                response = self.start(**extra)
                self.assertEqual(response.status_code, 400)
                self.assertIn("Tick the box", response.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_the_audit_event_says_it_was_picked_automatically_and_keeps_the_licence_note(self) -> None:
        self.cache("coco")
        self.start(acknowledge_pretrained_terms=True)
        note = ModelEvent.objects.get(kind="run_created").note
        self.assertIn("coco", note)
        self.assertIn("automatically", note)
        self.assertIn("lawyer", note.lower())

    def test_the_resolved_value_is_write_once(self) -> None:
        self.cache("coco")
        self.start(acknowledge_pretrained_terms=True)
        run = TrainingRun.objects.get()
        self.assertEqual(run.config_sha256, lifecycle.config_digest(run.config))
        run.config = {**run.config, "pretrained": "none"}
        with self.assertRaisesMessage(ValueError, "immutable"):
            run.save()

    def test_a_caller_cannot_forge_the_server_side_fields(self) -> None:
        self.cache("coco")
        for key, value in (("pretrained_requested", "auto"), ("derived", ["epochs"])):
            with self.subTest(key=key):
                response = self.start({key: value}, acknowledge_pretrained_terms=True)
                self.assertEqual(response.status_code, 400)
                self.assertIn(key, response.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_explicit_choices_behave_exactly_as_before(self) -> None:
        response = self.post_run(dataset_id=str(self.dataset.id), config={"pretrained": "none", "epochs": 3})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(
            TrainingRun.objects.get().config,
            {"pretrained": "none", "epochs": 3, "batch_size": 4, "learning_rate": 0.005, "seed": 0, "threads": 0},
        )

    def test_explicit_coco_that_cannot_run_is_still_the_old_409_not_a_swap(self) -> None:
        response = self.post_run(
            dataset_id=str(self.dataset.id), config={"pretrained": "coco"}, acknowledge_pretrained_terms=True
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("start_from", response.json())

    def test_other_backends_do_not_understand_auto(self) -> None:
        response = self.post(
            self.runs_url,
            {"backend": "fake-backend", "dataset_id": str(self.dataset.id), "config": {"pretrained": "auto"}},
        )
        self.assertEqual(response.status_code, 400)


class QualityDerivationTests(AutoBase):
    def setUp(self) -> None:
        super().setUp()
        self.dataset = self.make_dataset()
        self.train_pictures = self.dataset.manifest["split_counts"]["train"]["images"]
        self.assertGreater(self.train_pictures, 0)

    def test_quality_fills_in_what_the_caller_did_not_set_and_records_it(self) -> None:
        response = self.post_run(dataset_id=str(self.dataset.id), config={"pretrained": "none", "quality": "better"})
        self.assertEqual(response.status_code, 202, response.content)
        config = TrainingRun.objects.get().config
        expected = tv.derive_settings(self.train_pictures, "better")
        self.assertEqual((config["epochs"], config["batch_size"]), (expected["epochs"], expected["batch_size"]))
        self.assertEqual(config["quality"], "better")
        self.assertEqual(sorted(config["derived"]), ["batch_size", "epochs"])

    def test_explicit_settings_always_win(self) -> None:
        self.post_run(dataset_id=str(self.dataset.id), config={"pretrained": "none", "quality": "quick", "epochs": 2})
        config = TrainingRun.objects.get().config
        self.assertEqual(config["epochs"], 2)
        self.assertEqual(config["derived"], ["batch_size"])

    def test_no_quality_means_no_derivation_and_no_new_keys(self) -> None:
        self.post_run(dataset_id=str(self.dataset.id), config={"pretrained": "none"})
        config = TrainingRun.objects.get().config
        self.assertEqual((config["epochs"], config["batch_size"]), (5, 4))
        self.assertTrue({"quality", "derived", "pretrained_requested"}.isdisjoint(config))

    def test_a_bad_quality_is_a_400_and_queues_nothing(self) -> None:
        response = self.post_run(dataset_id=str(self.dataset.id), config={"pretrained": "none", "quality": "fast"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TrainingRun.objects.count(), 0)


class SnapshotStartTests(AutoBase):
    """``{"snapshot": "latest"}``: freeze the current accepted work first if needed, then start, all or nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.cache("coco")

    def start(self, **extra):
        return self.post_run(snapshot="latest", acknowledge_pretrained_terms=True, **extra)

    def finish_active_run(self) -> None:
        run = TrainingRun.objects.get(status__in=TrainingRun.ACTIVE_STATUSES)
        lifecycle.request_cancel(run, self.user)

    def test_first_start_creates_the_snapshot_and_the_run_on_it(self) -> None:
        self.add_pictures(10)
        response = self.start()
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        version = DatasetVersion.objects.get()
        self.assertEqual(body["snapshot"]["id"], str(version.id))
        self.assertTrue(body["snapshot"]["newly_created"])
        self.assertEqual((body["snapshot"]["version"], body["snapshot"]["image_count"]), (1, 10))
        self.assertEqual(body["run"]["dataset"]["id"], str(version.id))
        self.assertEqual(version.split_config["seed"], 0)  # the default split
        self.assertEqual((version.split_config["train"], version.split_config["test"]), (0.8, 0.1))
        self.assertEqual(version.created_by_id, self.user.pk)

    def test_new_requests_default_to_auto_start_and_quick_settings(self) -> None:
        self.add_pictures(10)
        body = self.start().json()
        config = body["run"]["config"]
        train = body["snapshot"]["split_counts"]["train"]["images"]
        self.assertEqual((config["pretrained"], config["pretrained_requested"], config["quality"]), ("coco", "auto", "quick"))
        derived = tv.derive_settings(train, "quick")
        self.assertEqual((config["epochs"], config["batch_size"]), (derived["epochs"], derived["batch_size"]))
        self.assertEqual(sorted(config["derived"]), ["batch_size", "epochs"])

    def test_an_explicit_start_in_the_request_is_respected(self) -> None:
        self.add_pictures(10)
        body = self.post_run(snapshot="latest", config={"pretrained": "none", "quality": "better"}).json()
        self.assertEqual(body["run"]["config"]["pretrained"], "none")
        self.assertEqual(body["run"]["config"]["quality"], "better")

    def test_unchanged_work_reuses_the_snapshot(self) -> None:
        self.add_pictures(10)
        first = self.start().json()
        self.finish_active_run()
        again = self.start()
        self.assertEqual(again.status_code, 202, again.content)
        self.assertFalse(again.json()["snapshot"]["newly_created"])
        self.assertEqual(again.json()["snapshot"]["id"], first["snapshot"]["id"])
        self.assertEqual(DatasetVersion.objects.count(), 1)
        self.assertEqual(TrainingRun.objects.count(), 2)

    def test_suggested_boxes_and_other_unaccepted_work_do_not_trigger_a_new_snapshot(self) -> None:
        assets = self.add_pictures(10)
        self.start()
        self.finish_active_run()
        self.make_suggestion(assets[0])
        self.assertFalse(self.start().json()["snapshot"]["newly_created"])

    def test_every_kind_of_accepted_change_makes_a_new_snapshot(self) -> None:
        assets = self.add_pictures(10)
        self.start()
        changes = {
            "a box moved": lambda: BoxAnnotation.objects.filter(pk=BoxAnnotation.objects.first().pk).update(x=Decimal("0.5")),
            "a box relabelled": lambda: BoxAnnotation.objects.filter(pk=BoxAnnotation.objects.last().pk).update(label="normal"),
            "a box deleted": lambda: BoxAnnotation.objects.first().delete(),
            "a picture added": lambda: self.add_pictures(1),
            "a box accepted": lambda: self.make_box(assets[3], x="0.5", y="0.5"),
        }
        for number, (name, change) in enumerate(changes.items(), start=2):
            self.finish_active_run()
            change()
            with self.subTest(change=name):
                response = self.start()
                self.assertEqual(response.status_code, 202, response.content)
                self.assertTrue(response.json()["snapshot"]["newly_created"], name)
                self.assertEqual(response.json()["snapshot"]["version"], number)
        self.assertEqual(DatasetVersion.objects.count(), 1 + len(changes))

    def test_a_label_list_change_alone_makes_a_new_snapshot(self) -> None:
        self.add_pictures(10)
        self.start()
        self.finish_active_run()
        self.project.labels = [*self.project.labels, "scratched"]
        self.project.save()
        self.assertTrue(self.start().json()["snapshot"]["newly_created"])

    def test_nothing_accepted_is_the_existing_refusal_and_creates_nothing(self) -> None:
        response = self.start()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], dataset_service.NO_ACCEPTED_MESSAGE)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_all_accepted_work_removed_after_a_snapshot_is_refused_not_trained_on_the_old_one(self) -> None:
        self.add_pictures(10)
        self.start()
        self.finish_active_run()
        BoxAnnotation.objects.all().delete()
        self.assertEqual(self.start().status_code, 400)
        self.assertEqual(TrainingRun.objects.count(), 1)

    def test_unsafe_labels_and_missing_files_are_the_existing_readiness_refusal(self) -> None:
        assets = self.add_pictures(10)
        default_storage.delete(assets[0].storage_key)
        response = self.start()
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertIn("Nothing was created", body["error"])
        self.assertTrue(any("missing from storage" in line for line in body["blockers"]))
        self.assertEqual(body["problem_count"], 1)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

        self.project.labels = ["damaged\nnormal", "normal"]
        self.project.save()
        self.assertEqual(self.start().status_code, 422)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_a_missing_file_blocks_even_when_the_snapshot_would_otherwise_be_current(self) -> None:
        assets = self.add_pictures(10)
        self.start()
        self.finish_active_run()
        default_storage.delete(assets[1].storage_key)
        self.assertEqual(self.start().status_code, 422)
        self.assertEqual(TrainingRun.objects.count(), 1)

    def test_a_refused_start_rolls_the_snapshot_back(self) -> None:
        self.add_pictures(10)
        missing_tick = self.post_run(snapshot="latest")
        self.assertEqual(missing_tick.status_code, 400)
        self.assertIn("Tick the box", missing_tick.json()["error"])
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

        with mock.patch.object(lifecycle, "create_run", side_effect=lifecycle.LifecycleError("boom", 500)):
            self.assertEqual(self.start().status_code, 500)  # the run failed after the snapshot was written
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_no_ready_made_start_is_refused_before_any_snapshot_is_written(self) -> None:
        self.add_pictures(10)
        for path in tv.pretrained_dir().rglob("*.pth"):
            path.unlink()
        with mock.patch.object(dataset_service, "create_version", wraps=dataset_service.create_version) as spy:
            response = self.start()
        self.assertEqual(response.status_code, 409)
        self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD", response.json()["error"])
        spy.assert_not_called()
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_an_active_run_blocks_before_a_new_snapshot_is_written(self) -> None:
        assets = self.add_pictures(10)
        self.start()
        self.make_box(assets[2], x="0.5", y="0.5")
        response = self.start()
        self.assertEqual(response.status_code, 409)
        self.assertIn("already queued or running", response.json()["error"])
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (1, 1))

    def test_two_stale_requests_make_one_run_and_one_snapshot(self) -> None:
        """Both requests hold a project object loaded before either started; neither trusts it."""
        self.add_pictures(10)
        stale_a = type(self.project).objects.get(pk=self.project.pk)
        stale_b = type(self.project).objects.get(pk=self.project.pk)
        self.assertIsNone(lifecycle.latest_dataset(stale_b))
        kwargs = dict(backend_name=BACKEND, config={"pretrained": "auto", "quality": "quick"}, acknowledged_terms=True)
        run, dataset, created = lifecycle.start_run_on_latest_snapshot(stale_a, self.user, **kwargs)
        self.assertTrue(created)
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            lifecycle.start_run_on_latest_snapshot(stale_b, self.user, **kwargs)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(DatasetVersion.objects.count(), 1)
        self.assertEqual(TrainingRun.objects.get().id, run.id)

    def test_two_requests_in_a_row_over_http_make_one_run_and_one_snapshot(self) -> None:
        self.add_pictures(10)
        statuses = sorted(self.start().status_code for _ in range(2))
        self.assertEqual(statuses, [202, 409])
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (1, 1))

    def test_a_request_that_changed_the_work_between_its_checks_is_not_trusted(self) -> None:
        """The snapshot is frozen from the exact analysis it was checked against, or the whole start is refused."""
        self.add_pictures(10)
        real = dataset_service.analyse

        def analyse_then_edit(project, config, **kwargs):
            analysis = real(project, config, **kwargs)
            if not kwargs.get("use_cache"):
                self.make_box(Asset.objects.first(), x="0.6", y="0.6")  # someone else edits right now
            return analysis

        with mock.patch.object(dataset_service, "analyse", side_effect=analyse_then_edit):
            response = self.start()
        self.assertEqual(response.status_code, 409)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_the_run_event_records_the_automatic_snapshot(self) -> None:
        self.add_pictures(10)
        self.start()
        self.assertIn("frozen automatically", ModelEvent.objects.get(kind="run_created").note)

    def test_bad_snapshot_requests(self) -> None:
        dataset = self.make_dataset()
        for bad in ("newest", True, 3, ["latest"]):
            with self.subTest(bad=bad):
                self.assertEqual(self.post_run(snapshot=bad).status_code, 400)
        both = self.post_run(snapshot="latest", dataset_id=str(dataset.id))
        self.assertEqual(both.status_code, 400)
        self.assertIn("dataset_id", both.json()["error"])
        self.assertEqual(TrainingRun.objects.count(), 0)

    def test_without_snapshot_the_old_behaviour_and_messages_are_unchanged(self) -> None:
        self.add_pictures(10)
        response = self.post_run(config={"pretrained": "none"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("no dataset version", response.json()["error"])
        self.assertEqual(DatasetVersion.objects.count(), 0)
        dataset = self.make_dataset(count=4)
        ok = self.post_run(config={"pretrained": "none"})
        self.assertEqual(ok.status_code, 202)
        self.assertEqual(ok.json()["run"]["dataset"]["id"], str(dataset.id))
        self.assertEqual(ok.json()["snapshot"]["id"], str(dataset.id))  # additive: which snapshot was used
        self.assertFalse(ok.json()["snapshot"]["newly_created"])

    def test_viewers_and_labelers_cannot_start_and_nothing_is_created(self) -> None:
        self.add_pictures(10)
        for role in ("viewer", "labeler"):
            user = get_user_model().objects.create_user(role)
            grant(user, role)
            client = Client()
            client.force_login(user)
            with self.subTest(role=role):
                self.assertEqual(self.post(self.runs_url, {"backend": BACKEND, "snapshot": "latest"}, client).status_code, 403)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_a_trainer_can_start_which_includes_freezing_the_work(self) -> None:
        self.add_pictures(10)
        user = get_user_model().objects.create_user("trainer")
        grant(user, "trainer")
        client = Client()
        client.force_login(user)
        response = self.post(
            self.runs_url,
            {"backend": BACKEND, "snapshot": "latest", "acknowledge_pretrained_terms": True},
            client,
        )
        self.assertEqual(response.status_code, 202, response.content)


class StartLockTests(AutoBase):
    """The start's write lock is held only for the cheap re-checks and the writes, never across file hashing, and it
    never trusts what the caller or its own earlier analysis saw."""

    KWARGS = dict(backend_name=BACKEND, config={"pretrained": "auto", "quality": "quick"}, acknowledged_terms=True)

    def setUp(self) -> None:
        super().setUp()
        self.cache("coco")

    def start_direct(self, project=None):
        return lifecycle.start_run_on_latest_snapshot(project or self.project, self.user, **self.KWARGS)

    def finish_active_run(self) -> None:
        lifecycle.request_cancel(TrainingRun.objects.get(status__in=TrainingRun.ACTIVE_STATUSES), self.user)

    def before_lock(self, hook):
        """Run ``hook()`` at the moment the start is about to take the project lock (after all its analysis)."""
        real = lifecycle._lock_project

        def lock(project):
            hook()
            return real(project)

        return mock.patch.object(lifecycle, "_lock_project", side_effect=lock)

    def test_no_file_is_hashed_while_the_write_lock_is_held(self) -> None:
        self.add_pictures(10)
        outside = len(connection.savepoint_ids)  # the test runner's own wrapper; a start's atomic() adds to it
        depth_at_hash: list[int] = []
        real = validation.storage_sha256

        def spy(key):
            depth_at_hash.append(len(connection.savepoint_ids))
            return real(key)

        with mock.patch.object(validation, "storage_sha256", side_effect=spy):
            self.start_direct()
        self.assertTrue(depth_at_hash, "the new snapshot's files must be hashed")
        self.assertEqual(set(depth_at_hash), {outside}, "files were hashed inside a transaction")

    def test_the_project_is_locked_after_all_analysis_and_before_anything_is_written(self) -> None:
        self.add_pictures(10)
        events: list[str] = []

        def wrap(owner, name, label):
            real = getattr(owner, name)

            def call(*args, **kwargs):
                events.append(label)
                return real(*args, **kwargs)

            return mock.patch.object(owner, name, side_effect=call)

        with wrap(dataset_service, "analyse", "analyse"), wrap(lifecycle, "_lock_project", "lock"), wrap(
            dataset_service, "create_version", "create_version"
        ), wrap(lifecycle, "create_run", "create_run"):
            self.start_direct()
        self.assertEqual(events.count("lock"), 1)
        self.assertGreaterEqual(events.count("analyse"), 2)  # the cheap look, then the hashing one
        self.assertEqual(events[-3:], ["lock", "create_version", "create_run"])
        self.assertNotIn("analyse", events[events.index("lock"):])

    def test_select_for_update_is_asked_for_on_the_project_even_when_nothing_new_is_frozen(self) -> None:
        self.add_pictures(10)
        self.start_direct()
        self.finish_active_run()
        locked: list[type] = []
        real = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            locked.append(queryset.model)
            return real(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", spy), mock.patch.object(
            dataset_service, "create_version"
        ) as create_version:
            _, _, created = self.start_direct()
        self.assertFalse(created)  # reuse: create_version (which takes the same lock) was never called
        create_version.assert_not_called()
        self.assertIn(Project, locked)

    def test_a_project_object_loaded_before_a_label_change_starts_on_the_new_labels(self) -> None:
        self.add_pictures(10)
        stale = Project.objects.get(pk=self.project.pk)
        other = Project.objects.get(pk=self.project.pk)
        other.labels = [*other.labels, "scratch"]
        other.save()
        self.assertNotIn("scratch", stale.labels)
        _, version, created = self.start_direct(stale)
        self.assertTrue(created)
        self.assertEqual(version.label_names, other.labels)
        self.assertEqual(TrainingRun.objects.count(), 1)

    def test_a_box_edited_after_the_analysis_but_before_the_lock_is_a_409_and_writes_nothing(self) -> None:
        self.add_pictures(10)
        with self.before_lock(lambda: self.make_box(Asset.objects.first(), x="0.6", y="0.6")):
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                self.start_direct()
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(str(caught.exception), lifecycle.BOXES_CHANGED_MESSAGE)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_labels_changed_after_the_analysis_but_before_the_lock_are_a_409_and_write_nothing(self) -> None:
        """Labels are not in the boxes' fingerprint, so only re-reading the project under the lock notices."""
        self.add_pictures(10)

        def rename_label() -> None:
            other = Project.objects.get(pk=self.project.pk)
            other.labels = [*other.labels, "scratch"]
            other.save()

        with self.before_lock(rename_label):
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                self.start_direct()
        self.assertEqual((caught.exception.status, str(caught.exception)), (409, lifecycle.BOXES_CHANGED_MESSAGE))
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_reusing_the_latest_snapshot_is_also_refused_when_the_work_moved_before_the_lock(self) -> None:
        self.add_pictures(10)
        self.start_direct()
        self.finish_active_run()
        with self.before_lock(lambda: self.make_box(Asset.objects.first(), x="0.6", y="0.6")):
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                self.start_direct()
        self.assertEqual((caught.exception.status, str(caught.exception)), (409, lifecycle.BOXES_CHANGED_MESSAGE))
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (1, 1))  # the finished one

    def test_over_http_the_race_is_the_same_plain_409(self) -> None:
        self.add_pictures(10)
        with self.before_lock(lambda: self.make_box(Asset.objects.first(), x="0.6", y="0.6")):
            response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], lifecycle.BOXES_CHANGED_MESSAGE)


class AdminMessageTests(TrainingBase):
    """What an administrator is told when no ready-made starting model can be used: who, where and how."""

    WEB = Path(__file__).resolve().parent.parent

    def test_the_reason_states_only_what_was_measured(self) -> None:
        reason = tv.AUTO_NONE_REASON
        self.assertIn("In Athena’s one test run, a model started from nothing found none of the test objects", reason)
        self.assertIn("will not start one for you", reason)
        self.assertNotIn("usually learns almost nothing", reason)
        self.assertNotIn("with only a few pictures", reason)
        self.assertIn("administrator", reason.lower())

    def test_the_steps_open_with_the_console_switch_and_keep_the_environment_steps_as_the_alternative(self) -> None:
        steps = tv.AUTO_NONE_STEPS
        self.assertTrue(steps.startswith("An administrator can switch this on in the console, under “Access”, in "
                                         "“Ready-made model downloads”"))
        self.assertLess(steps.index("switch this on in the console"), steps.index("ATHENA_TRAINING_ALLOW_DOWNLOAD"))
        self.assertIn("wins over the setting below", steps)
        for text in (tv.ENABLE_STEPS, tv.AUTO_NONE_STEPS):
            with self.subTest(text=text[:30]):
                self.assertTrue(text.startswith("An administrator can switch this on in the console"))
                self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD", text)

    def test_the_screen_the_steps_name_is_the_one_the_console_has(self) -> None:
        """Pins the words: the Access link in the console header and the section the switch will sit under."""
        console = (Path(__file__).resolve().parent / "templates" / "projects" / "console.html").read_text()
        self.assertIn(">Access</a>", console)

    def test_the_steps_say_who_where_and_how_and_that_nothing_is_typed_into_the_page(self) -> None:
        steps = tv.AUTO_NONE_STEPS
        for needle in (
            "one-time step for the person who installed Athena",
            "not something to type into this page",
            "On the computer that runs Athena",
            "ATHENA_TRAINING_ALLOW_DOWNLOAD",
            "`python manage.py train_worker`",
            "restart that helper",
            "web/.env",
            "ATHENA_TRAINING_ALLOW_DOWNLOAD=0 to 1",
            "`docker compose up -d train_worker` in the web folder",
            "web/DEPLOYMENT.md",
            "Ready-made starting models in the Teach panel",
            "own terms",
            "download.pytorch.org",
            "models/pretrained/checkpoints/",
            "mobilenet_v3_large-8738ca79.pth",
            "fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, steps)

    def test_every_claim_in_the_steps_is_true_of_the_repo(self) -> None:
        env_example = (self.WEB / ".env.example").read_text()
        self.assertIn("\nATHENA_TRAINING_ALLOW_DOWNLOAD=0\n", env_example)  # the line the steps tell them to change
        compose = (self.WEB / "docker-compose.yml").read_text()
        service = compose.split("  train_worker:\n", 1)[1].split("\n  inference_worker:", 1)[0]
        self.assertIn('command: ["python", "manage.py", "train_worker"]', service)
        self.assertIn("env_file: .env", service)  # so editing web/.env and recreating that service is enough
        self.assertIn("### Ready-made starting models in the Teach panel", (self.WEB / "DEPLOYMENT.md").read_text())

    def test_the_estimate_note_says_the_machine_was_shared(self) -> None:
        note = tv.ESTIMATE_NOTE
        self.assertIn("an Apple M3", note)
        self.assertIn("while other programs were running", note)
        self.assertIn("estimate, not a promise", note)


class ReadinessPreviewTests(AutoBase):
    def test_nothing_ready_made_runnable_says_so_with_the_administrator_step(self) -> None:
        self.add_pictures(10)
        body = self.readiness()
        start = body["start_from"]
        self.assertIsNone(start["resolved"])
        self.assertEqual(start["available"], [])
        self.assertIn("administrator", start["reason_if_none"].lower())
        self.assertIn("ATHENA_TRAINING_ALLOW_DOWNLOAD", start["how_to_enable"])
        self.assertFalse(body["one_click"]["ready"])
        self.assertTrue(any("ready-made" in line for line in body["one_click"]["blockers"]))

    def test_cached_start_is_resolved_and_the_estimate_follows_it(self) -> None:
        self.cache("coco", "imagenet_backbone")
        self.add_pictures(10)
        body = self.readiness()
        self.assertEqual(body["start_from"]["resolved"], "coco")
        self.assertEqual([o["value"] for o in body["start_from"]["available"]], ["coco", "imagenet_backbone"])
        first = body["start_from"]["available"][0]
        self.assertTrue(first["ready"] and first["needs_acknowledgement"])
        self.assertIn("Ask a lawyer", first["licence_plain"])
        self.assertEqual(body["start_from"]["reason_if_none"], "")
        derived = body["derived"]
        train = derived["train_pictures"]
        for quality in ("quick", "better"):
            expected = tv.derive_settings(train, quality)
            self.assertEqual(
                (derived[quality]["epochs"], derived[quality]["batch_size"]), (expected["epochs"], expected["batch_size"])
            )
            seconds = expected["epochs"] * train * tv.SECONDS_PER_PICTURE_EPOCH["coco"]
            self.assertEqual(derived[quality]["estimated_minutes"], max(1, math.ceil(seconds / 60)))
        self.assertEqual(derived["estimate_for_start"], "coco")
        self.assertIn("not a promise", derived["estimate_note"].lower())
        self.assertTrue(body["one_click"]["ready"], body["one_click"])

    def test_the_preview_counts_the_snapshot_that_would_be_used(self) -> None:
        self.cache("coco")
        dataset = self.make_dataset(count=20)
        body = self.readiness()
        self.assertEqual(body["derived"]["based_on"], "latest_snapshot")
        self.assertEqual(body["derived"]["train_pictures"], dataset.manifest["split_counts"]["train"]["images"])
        self.assertFalse(body["one_click"]["snapshot"]["would_create_new"])
        self.assertEqual(body["one_click"]["snapshot"]["latest_version"], 1)

        self.add_pictures(10)  # accepted work changed: a new snapshot would be made and its split is previewed
        body = self.readiness()
        self.assertEqual(body["derived"]["based_on"], "new_snapshot")
        self.assertTrue(body["one_click"]["snapshot"]["would_create_new"])
        self.start_and_compare_planned_train_count(body)

    def start_and_compare_planned_train_count(self, body: dict) -> None:
        planned = body["derived"]["train_pictures"]
        response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()["snapshot"]["split_counts"]["train"]["images"], planned)

    def test_no_accepted_boxes_is_a_one_click_blocker_not_an_error(self) -> None:
        self.cache("coco")
        body = self.readiness()
        self.assertFalse(body["one_click"]["ready"])
        self.assertIn(dataset_service.NO_ACCEPTED_MESSAGE, body["one_click"]["blockers"])
        self.assertIsNone(body["derived"]["train_pictures"])
        self.assertIsNone(body["derived"]["quick"])
        self.assertTrue(body["start_from"]["resolved"])

    def test_existing_readiness_keys_are_untouched_and_fake_backends_get_nulls(self) -> None:
        self.make_dataset()
        body = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        for key in ("ready", "blockers", "warnings", "backend", "worker", "dataset", "active_run_id"):
            self.assertIn(key, body)
        self.assertIsNone(body["start_from"])
        self.assertIsNone(body["derived"])
        self.assertIn("one_click", body)

    def test_an_active_run_blocks_one_click(self) -> None:
        self.cache("coco")
        self.add_pictures(10)
        self.assertEqual(self.post_run(snapshot="latest", acknowledge_pretrained_terms=True).status_code, 202)
        body = self.readiness()
        self.assertFalse(body["one_click"]["ready"])
        self.assertTrue(any("already queued or running" in line for line in body["one_click"]["blockers"]))

    # -- no pictures held back (final integration review I1) ------------------------------------------------
    NO_HELD_OUT = "No pictures would be kept back to check the result, so you could not approve this model."

    def test_a_default_split_with_no_test_pictures_adds_one_click_warning_but_stays_ready(self) -> None:
        self.cache("coco")
        self.add_pictures(2)
        with mock.patch.object(lifecycle, "planned_test_pictures", return_value=0) as planned:
            body = self.readiness()
        planned.assert_called_once()
        self.assertEqual(body["one_click"]["warnings"], [self.NO_HELD_OUT])
        self.assertTrue(body["one_click"]["ready"], body["one_click"])  # a warning, not a blocker
        self.assertEqual(body["one_click"]["blockers"], [])

    def test_a_real_two_picture_project_gets_the_warning_from_the_same_splitter_the_health_page_uses(self) -> None:
        self.cache("coco")
        self.add_pictures(2)
        analysis = dataset_service.analyse(self.project, dataset_service.splits.SplitConfig.default())
        expected_test = dataset_service.preview(analysis)["split_counts"]["test"]["images"]
        body = self.readiness()
        self.assertEqual(lifecycle.planned_test_pictures(analysis), expected_test)
        self.assertEqual(expected_test, 0)  # two pictures: the splitter has nothing to spare for the final test
        self.assertEqual(body["one_click"]["warnings"], [self.NO_HELD_OUT])

    def test_a_project_with_test_pictures_has_an_empty_warnings_list(self) -> None:
        self.cache("coco")
        self.add_pictures(20)
        analysis = dataset_service.analyse(self.project, dataset_service.splits.SplitConfig.default())
        self.assertGreater(lifecycle.planned_test_pictures(analysis), 0)
        self.assertEqual(self.readiness()["one_click"]["warnings"], [])

    def test_an_up_to_date_snapshot_is_judged_by_its_own_test_split(self) -> None:
        self.cache("coco")
        self.make_dataset(with_test=False)
        body = self.readiness()
        self.assertTrue(body["one_click"]["snapshot"]["up_to_date"])
        self.assertEqual(body["one_click"]["warnings"], [self.NO_HELD_OUT])

    def test_warnings_key_is_always_there_and_empty_when_there_is_nothing_to_say(self) -> None:
        self.cache("coco")
        self.assertEqual(self.readiness()["one_click"]["warnings"], [])  # no accepted boxes: a blocker, not a warning
        fake = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertEqual(fake["one_click"]["warnings"], [])

    def test_readiness_writes_nothing(self) -> None:
        self.cache("coco")
        self.add_pictures(10)
        self.readiness()
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def test_readiness_cost_does_not_grow_with_the_number_of_pictures(self) -> None:
        self.cache("coco")
        self.add_pictures(5)
        with CaptureQueriesContext(connection) as small:
            self.readiness()
        self.add_pictures(25)
        with CaptureQueriesContext(connection) as large:
            self.readiness()
        self.assertEqual(len(small), len(large))


class ReadinessFileBudgetTests(AutoBase):
    """The readiness preview stops re-checking picture files after a budget; starting never does (hygiene H3)."""

    NOT_RECHECKED = (
        "The picture files were not re-checked for this preview, to keep the page quick. Missing or changed "
        "files are checked when you start."
    )

    def setUp(self) -> None:
        super().setUp()
        self.cache("coco")
        validation.clear_verification_cache()
        self.addCleanup(validation.clear_verification_cache)

    def slow_clock(self):
        """A clock that jumps a minute every time it is read: any budget is gone after the first look."""
        state = {"now": -60.0}

        def clock() -> float:
            state["now"] += 60.0
            return state["now"]

        return mock.patch.object(dataset_service, "_clock", clock)

    def test_the_budget_is_one_named_constant_and_a_short_one(self) -> None:
        self.assertIsInstance(lifecycle.READINESS_FILE_CHECK_SECONDS, (int, float))
        self.assertTrue(0 < lifecycle.READINESS_FILE_CHECK_SECONDS <= 5)

    def test_the_preview_asks_for_that_budget_and_start_asks_for_the_full_check(self) -> None:
        self.add_pictures(10)
        with mock.patch.object(dataset_service, "analyse", wraps=dataset_service.analyse) as analyse:
            self.readiness()
        self.assertEqual(analyse.call_args.kwargs["file_check"], lifecycle.READINESS_FILE_CHECK_SECONDS)
        with mock.patch.object(dataset_service, "analyse", wraps=dataset_service.analyse) as analyse:
            self.assertEqual(self.post_run(snapshot="latest", acknowledge_pretrained_terms=True).status_code, 202)
        self.assertGreaterEqual(analyse.call_count, 2)  # the look and the one that freezes
        for call in analyse.call_args_list:
            self.assertEqual(call.kwargs.get("file_check", "full"), "full")

    def test_a_normal_preview_has_no_notes_and_says_the_files_were_checked(self) -> None:
        self.add_pictures(10)
        one_click = self.readiness()["one_click"]
        self.assertEqual(one_click["notes"], [])
        self.assertIs(one_click["files_rechecked"], True)
        self.assertTrue(one_click["ready"], one_click)

    def test_when_the_budget_runs_out_the_preview_says_so_and_stays_honest_about_readiness(self) -> None:
        assets = self.add_pictures(10)
        default_storage.delete(assets[-1].storage_key)  # the last file, which the preview never reaches
        with self.slow_clock():
            body = self.readiness()
        one_click = body["one_click"]
        self.assertEqual(one_click["notes"], [self.NOT_RECHECKED])
        self.assertIs(one_click["files_rechecked"], False)
        for key in ("supported", "ready", "blockers", "warnings", "snapshot"):  # nothing renamed or removed
            self.assertIn(key, one_click)
        self.assertEqual(one_click["blockers"], [])  # nothing was looked at, so nothing is claimed missing
        self.assertTrue(one_click["ready"])  # what was checked is fine; the note says what was not
        # ...and the start, which checks every file, does refuse
        response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 422)
        self.assertTrue(any("missing from storage" in line for line in response.json()["blockers"]))

    def test_a_missing_file_inside_the_budget_is_still_a_blocker(self) -> None:
        assets = self.add_pictures(3)
        default_storage.delete(assets[0].storage_key)
        one_click = self.readiness()["one_click"]
        self.assertFalse(one_click["ready"])
        self.assertTrue(any("missing from storage" in line for line in one_click["blockers"]))
        self.assertEqual(one_click["notes"], [])

    def test_start_ignores_a_clock_that_has_run_out(self) -> None:
        assets = self.add_pictures(10)
        default_storage.delete(assets[0].storage_key)
        with self.slow_clock():
            response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 422)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (0, 0))

    def finish_runs(self) -> None:
        TrainingRun.objects.update(status=TrainingRun.FAILED)

    def test_a_start_on_a_current_snapshot_trusts_the_check_made_when_it_was_frozen(self) -> None:
        """B5 probe, kept as documentation of what "checked when you start" means.

        Reusing the current snapshot answers from the stat-keyed cache, so a picture whose bytes change without its
        size or modified time changing is not re-hashed there. A size change is seen; and a new snapshot is always
        built from a full re-hash (``test_the_preview_asks_for_that_budget_and_start_asks_for_the_full_check``)."""
        assets = self.add_pictures(3)
        first = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(first.status_code, 202, first.content)
        self.finish_runs()
        path = Path(default_storage.path(assets[0].storage_key))
        stat = path.stat()
        data = bytearray(path.read_bytes())
        data[-1] ^= 0xFF  # same length
        path.write_bytes(bytes(data))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same modified time
        second = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(second.status_code, 202, second.content)  # not re-hashed: the snapshot is reused
        self.assertEqual(DatasetVersion.objects.count(), 1)
        self.finish_runs()
        path.write_bytes(bytes(data) + b"more")  # a size change is seen
        third = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(third.status_code, 422, third.content)

    def test_the_notes_key_exists_for_a_backend_with_no_one_click_start_too(self) -> None:
        self.add_pictures(3)
        fake = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        self.assertEqual(fake["one_click"]["notes"], [])


class ZeroTrainSnapshotTests(AutoBase):
    """A version whose split left nothing in train can never be started on, so it must never count as current."""

    def setUp(self) -> None:
        super().setUp()
        self.cache("coco")
        self.add_pictures(10)
        analysis = dataset_service.analyse(self.project, dataset_service.splits.SplitConfig(0, 0.0, 0.5, 0.5))
        self.empty_train = dataset_service.create_version(analysis, self.user)
        self.assertEqual(self.empty_train.manifest["split_counts"]["train"]["images"], 0)

    def test_readiness_says_a_new_snapshot_would_be_made_not_that_the_empty_one_is_current(self) -> None:
        body = self.readiness()
        self.assertFalse(body["one_click"]["snapshot"]["up_to_date"])
        self.assertTrue(body["one_click"]["snapshot"]["would_create_new"])
        self.assertEqual(body["derived"]["based_on"], "new_snapshot")
        self.assertGreater(body["derived"]["train_pictures"], 0)
        self.assertTrue(body["one_click"]["ready"], body["one_click"])

    def test_the_start_re_freezes_the_default_split_and_agrees_with_the_preview(self) -> None:
        planned = self.readiness()["derived"]["train_pictures"]
        response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        self.assertTrue(body["snapshot"]["newly_created"])
        self.assertEqual(body["snapshot"]["version"], 2)
        self.assertEqual(body["snapshot"]["split_counts"]["train"]["images"], planned)
        self.assertEqual(DatasetVersion.objects.count(), 2)

    def test_a_default_split_that_trains_on_nothing_is_a_readiness_blocker_not_a_surprise_422(self) -> None:
        with mock.patch.object(lifecycle, "planned_train_pictures", return_value=0):
            body = self.readiness()
        self.assertFalse(body["one_click"]["ready"])
        self.assertTrue(any("train" in line for line in body["one_click"]["blockers"]), body["one_click"])

    def test_and_the_start_refuses_it_before_freezing_anything(self) -> None:
        with mock.patch.object(lifecycle, "planned_train_pictures", return_value=0):
            response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["error"], lifecycle.NO_PLANNED_TRAIN_MESSAGE)
        self.assertEqual((DatasetVersion.objects.count(), TrainingRun.objects.count()), (1, 0))


class BackendsWithoutOneClickSettingsTests(AutoBase):
    """Only a backend that understands ``pretrained: auto`` / ``quality`` is sent them; others start on their own defaults."""

    def test_a_backend_with_no_ready_made_starts_is_not_sent_settings_it_does_not_know(self) -> None:
        self.make_dataset()
        lifecycle.record_worker_heartbeat(lifecycle.WORKER_KEY, "ready", {"fake-backend": True})
        response = self.post(self.runs_url, {"backend": "fake-backend", "snapshot": "latest"})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(TrainingRun.objects.get().config, FakeBackend().validate_config({}))  # its own defaults only

    def test_what_the_caller_sent_is_still_validated_by_that_backend(self) -> None:
        self.make_dataset()
        response = self.post(self.runs_url, {"backend": "fake-backend", "snapshot": "latest", "config": {"quality": "quick"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("quality", response.json()["error"])
        self.assertNotIn("pretrained", response.json()["error"])

    def test_an_unknown_backend_is_still_the_plain_400(self) -> None:
        self.make_dataset()
        response = self.post(self.runs_url, {"backend": "no-such-backend", "snapshot": "latest"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("backends", response.json()["error"])

    def test_the_reference_backend_still_gets_both_defaults(self) -> None:
        self.cache("coco")
        self.add_pictures(10)
        config = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True).json()["run"]["config"]
        self.assertEqual((config["pretrained_requested"], config["quality"]), ("auto", "quick"))


class ExplicitBatchSizeTests(AutoBase):
    """An explicit ``batch_size`` wins over the default 4 (never above the training set); epochs follow the batch used."""

    # (pictures in train, quality, explicit batch) -> (epochs, batch_size), worked by hand from the derive rules.
    TABLE = {
        (60, "quick", 16): (26, 16),  # 4 steps/epoch -> 60 epochs wanted, 40 max, time cap 1600 // 60 = 26
        (10, "quick", 1): (24, 1),  # 10 steps/epoch -> 24 epochs, cap 160
        (9, "quick", 16): (40, 9),  # batch clamped to the 9 pictures: 1 step/epoch -> 40 (max)
        (200, "better", 8): (16, 8),  # 25 steps/epoch -> 16 epochs, cap 24
        (200, "quick", 16): (8, 16),  # 13 steps -> 19 epochs, cap 1600 // 200 = 8
        (30, "quick", 2): (16, 2),  # 15 steps -> 16 epochs, cap 53
        (30, "quick", 4): (30, 4),  # the default batch gives the default answer
    }

    def test_table(self) -> None:
        for (pictures, quality, batch), (epochs, got_batch) in self.TABLE.items():
            with self.subTest(pictures=pictures, quality=quality, batch=batch):
                self.assertEqual(
                    tv.derive_settings(pictures, quality, batch_size=batch), {"epochs": epochs, "batch_size": got_batch}
                )

    def test_no_explicit_batch_is_exactly_what_it_was(self) -> None:
        for (pictures, quality), (epochs, batch) in DERIVE_TABLE.items():
            self.assertEqual(tv.derive_settings(pictures, quality, batch_size=None), {"epochs": epochs, "batch_size": batch})

    def test_a_snapshot_start_with_an_oversized_batch_stores_a_batch_the_epochs_were_worked_out_for(self) -> None:
        self.cache("coco")
        self.add_pictures(10)
        body = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True, config={"batch_size": 16}).json()
        train = body["snapshot"]["split_counts"]["train"]["images"]
        self.assertLess(train, 16)
        config = body["run"]["config"]
        self.assertEqual(config["batch_size"], train)
        self.assertEqual(config["epochs"], tv.derive_settings(train, "quick", batch_size=16)["epochs"])
        self.assertEqual(config["derived"], ["epochs"])  # the batch was the caller's; only epochs was Athena's choice

    def test_an_in_range_explicit_batch_is_kept_and_epochs_match_it(self) -> None:
        self.cache("coco")
        self.add_pictures(10)
        body = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True, config={"batch_size": 2}).json()
        train = body["snapshot"]["split_counts"]["train"]["images"]
        config = body["run"]["config"]
        self.assertEqual(config["batch_size"], 2)
        self.assertEqual(config["epochs"], tv.derive_settings(train, "quick", batch_size=2)["epochs"])

    def test_explicit_epochs_still_win_and_a_bad_batch_is_still_the_plain_400(self) -> None:
        self.cache("coco")
        self.add_pictures(10)
        ok = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True, config={"batch_size": 16, "epochs": 3})
        self.assertEqual((ok.json()["run"]["config"]["epochs"]), 3)
        lifecycle.request_cancel(TrainingRun.objects.get(), self.user)
        for bad in ("4", 0, 17, True, 2.5, None, -1):
            with self.subTest(batch=bad):
                response = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True, config={"batch_size": bad})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("batch_size", response.json()["error"])
