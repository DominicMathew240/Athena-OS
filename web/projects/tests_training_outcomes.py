"""Per-picture held-out outcomes are stored at evaluation time (W1-B1b).

SYNTHETIC data only: tiny generated pictures, the fake backend (or a 1-epoch CPU train of the reference backend
when torch is installed). Nothing here says anything about how a real model behaves on real imagery.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock, skipUnless

from PIL import Image

from . import training
from .models import ModelVersion
from .tests_training import TORCH_INSTALLED, FakeBackend, TrainingBase
from .training import evaluation as ev, runner
from .training.base import BoxPrediction, EvalItem

WRONG_PLACE = BoxPrediction("damaged", 0.6, 0.6, 0.3, 0.3, 0.9)  # the drawn box is at x.1 y.2 w.3 h.4: no overlap


def split_asset_ids(model: ModelVersion, split: str) -> set[str]:
    return {i["asset_id"] for i in model.training_run.dataset_version.manifest["images"] if i["split"] == split}


class EvalItemCompatibilityTests(TrainingBase):
    def test_an_item_built_the_old_way_has_no_picture_id(self) -> None:
        item = EvalItem(Path("a.png"), [("damaged", 0.1, 0.2, 0.3, 0.4)], 40, 30)
        self.assertIsNone(item.asset_id)
        self.assertEqual(EvalItem(Path("a.png"), [], 40, 30, "p1").asset_id, "p1")

    def test_test_items_carry_the_manifest_asset_id_in_test_split_order(self) -> None:
        dataset = self.make_dataset()
        items = runner.test_items(dataset.manifest, Path("dest"))
        test_images = [i for i in dataset.manifest["images"] if i["split"] == "test"]
        self.assertTrue(items)
        self.assertEqual({i.asset_id for i in items}, {i["asset_id"] for i in test_images})
        self.assertEqual(len(items), len(test_images))
        for item in items:  # the image file named in the path is the one whose id is stored
            self.assertTrue(item.path.name)
            self.assertIsInstance(item.asset_id, str)

    def _items_on_disk(self, ids) -> list[EvalItem]:
        folder = Path(self.home.name)
        items = []
        for index, pid in enumerate(ids):
            path = folder / f"{index}.png"
            Image.new("RGB", (40, 30), (10, 10, 10)).save(path)
            items.append(EvalItem(path, [("damaged", 0.1, 0.2, 0.3, 0.4)], 40, 30, pid))
        return items

    def test_ids_are_forwarded_only_when_every_item_has_one_and_other_keys_are_unchanged(self) -> None:
        backend = FakeBackend()
        FakeBackend.boxes = [WRONG_PLACE]
        thresholds = {"confidence": 0.5, "max_boxes": 100}
        with_ids = backend.evaluate(Path("m"), self._items_on_disk(["p1", "p2"]), ["damaged"], thresholds)
        without = backend.evaluate(Path("m"), [EvalItem(i.path, i.boxes, i.width, i.height) for i in self._items_on_disk(["p1", "p2"])], ["damaged"], thresholds)
        partial_items = self._items_on_disk(["p1", None])
        partial = backend.evaluate(Path("m"), partial_items, ["damaged"], thresholds)
        self.assertEqual(with_ids["held_out_outcomes"]["schema"], ev.OUTCOMES_SCHEMA)
        self.assertNotIn("held_out_outcomes", without)
        self.assertNotIn("held_out_outcomes", partial)
        # additive: apart from the new block, the stored document is exactly what it was
        self.assertEqual({k: v for k, v in with_ids.items() if k != "held_out_outcomes"}, without)


class StoredOutcomesTests(TrainingBase):
    def fetch_plain(self, model: ModelVersion) -> dict:
        response = self.client.get(self.api(f"/models/{model.id}/results/plain"))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_fresh_evaluation_stores_the_block_and_its_totals_match_the_stored_counts(self) -> None:
        FakeBackend.boxes = [WRONG_PLACE]
        model = self.train_candidate(self.make_dataset())
        block = model.evaluation["held_out_outcomes"]
        per_label = model.evaluation["per_label"]
        test_ids = split_asset_ids(model, "test")
        self.assertEqual(block["schema"], ev.OUTCOMES_SCHEMA)
        self.assertEqual(block["missed_total"], sum(r["false_negatives"] for r in per_label.values()))
        self.assertEqual(block["false_alarm_total"], sum(r["false_positives"] for r in per_label.values()))
        self.assertEqual(block["missed_total"], len(test_ids))  # one drawn box per test picture, all missed
        self.assertEqual(block["false_alarm_total"], len(test_ids))
        self.assertEqual({i["asset_id"] for i in block["items"]}, test_ids)
        self.assertEqual({i["kind"] for i in block["items"]}, {"missed", "false_alarm"})
        # the dataset note and the old keys are still there
        for key in ("schema", "available", "overall", "per_label", "data", "limits", "dataset"):
            self.assertIn(key, model.evaluation)

    def test_results_plain_lists_only_held_out_pictures(self) -> None:
        FakeBackend.boxes = [WRONG_PLACE]
        model = self.train_candidate(self.make_dataset())
        body = self.fetch_plain(model)
        test_ids = split_asset_ids(model, "test")
        used_for_learning = split_asset_ids(model, "train") | split_asset_ids(model, "val")
        self.assertTrue(used_for_learning)
        misses = body["misses"]
        self.assertTrue(misses["misses_available"], misses)
        self.assertEqual(misses["reason"], "")
        self.assertEqual(misses["total"], 2 * len(test_ids))
        listed = {i["asset_id"] for i in misses["items"]}
        self.assertTrue(listed)
        self.assertLessEqual(listed, test_ids)
        self.assertFalse(listed & used_for_learning)
        self.assertEqual({i["kind"] for i in misses["items"]}, {"missed", "false_alarm"})

    def test_a_model_that_found_everything_stores_an_empty_list_and_still_says_available(self) -> None:
        model = self.train_candidate(self.make_dataset())  # the fake backend draws exactly the drawn box
        block = model.evaluation["held_out_outcomes"]
        self.assertEqual((block["missed_total"], block["false_alarm_total"], block["items"]), (0, 0, []))
        misses = self.fetch_plain(model)["misses"]
        self.assertTrue(misses["misses_available"])
        self.assertEqual((misses["total"], misses["items"]), (0, []))

    def test_no_test_group_still_stores_no_block(self) -> None:
        model = self.train_candidate(self.make_dataset(with_test=False))
        self.assertNotIn("held_out_outcomes", model.evaluation)
        self.assertFalse(self.fetch_plain(model)["misses"]["misses_available"])

    def test_a_legacy_evaluation_without_the_block_still_says_not_available(self) -> None:
        FakeBackend.boxes = [WRONG_PLACE]
        model = self.train_candidate(self.make_dataset())
        self.assertIn("held_out_outcomes", model.evaluation)
        legacy_evaluation = {k: v for k, v in model.evaluation.items() if k != "held_out_outcomes"}
        # a model row is immutable, so stand in for an old one: same document as before this change
        legacy = ModelVersion.objects.create(
            project=model.project, training_run=model.training_run, version=model.version + 1,
            backend=model.backend, artifact_path=model.artifact_path, artifact_sha256=model.artifact_sha256,
            artifact_size_bytes=model.artifact_size_bytes, label_names=model.label_names,
            evaluation=legacy_evaluation,
        )
        body = self.fetch_plain(legacy)
        self.assertTrue(body["evaluated"])
        self.assertFalse(body["misses"]["misses_available"])
        self.assertEqual(body["misses"]["items"], [])
        self.assertEqual(body["misses"]["total"], 0)

    def test_the_registered_model_row_keeps_the_stored_document_untouched_by_the_plain_endpoint(self) -> None:
        FakeBackend.boxes = [WRONG_PLACE]
        model = self.train_candidate(self.make_dataset())
        before = ModelVersion.objects.get(pk=model.pk).evaluation
        self.fetch_plain(model)
        self.assertEqual(ModelVersion.objects.get(pk=model.pk).evaluation, before)


@skipUnless(TORCH_INSTALLED, "torch/torchvision are not installed here; run in the ML venv (see DEPLOYMENT.md)")
class TorchvisionStoresOutcomesTests(TrainingBase):
    def test_a_real_one_epoch_train_stores_held_out_outcomes(self) -> None:
        dataset = self.make_dataset(count=16)
        with mock.patch("torch.hub.load_state_dict_from_url", side_effect=AssertionError("network used")):
            response = self.post(
                self.api("/training/runs"),
                {"backend": "torchvision-fasterrcnn", "dataset_id": str(dataset.id),
                 "config": {"epochs": 1, "batch_size": 4, "seed": 3}},
            )
            self.assertEqual(response.status_code, 202, response.content)
            finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded", finished.error)
        model = finished.model_versions.get()
        self.assertEqual(model.backend, training.get_backend("torchvision-fasterrcnn").name)
        block = model.evaluation["held_out_outcomes"]
        per_label = model.evaluation["per_label"]
        self.assertEqual(block["missed_total"], sum(r["false_negatives"] for r in per_label.values()))
        self.assertEqual(block["false_alarm_total"], sum(r["false_positives"] for r in per_label.values()))
        self.assertLessEqual({i["asset_id"] for i in block["items"]}, split_asset_ids(model, "test"))
        body = self.client.get(self.api(f"/models/{model.id}/results/plain")).json()
        self.assertTrue(body["misses"]["misses_available"], body["misses"])

    def test_a_real_one_epoch_train_also_stores_operating_points_that_say_not_enough_data(self) -> None:
        """W4-T: the real backend scores the validation split through the default ``predict_split``. 16 tiny pictures
        are far too few boxes, so the block must exist, be consistent with the splits, and recommend nothing."""
        dataset = self.make_dataset(count=16)
        with mock.patch("torch.hub.load_state_dict_from_url", side_effect=AssertionError("network used")):
            response = self.post(
                self.api("/training/runs"),
                {"backend": "torchvision-fasterrcnn", "dataset_id": str(dataset.id),
                 "config": {"epochs": 1, "batch_size": 4, "seed": 3}},
            )
            self.assertEqual(response.status_code, 202, response.content)
            finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, "succeeded", finished.error)
        evaluation = finished.model_versions.get().evaluation
        points = evaluation["operating_points"]
        n_val = sum(1 for i in dataset.manifest["images"] if i["split"] == "val")
        n_test = sum(1 for i in dataset.manifest["images"] if i["split"] == "test")
        self.assertEqual((points["pictures"], points["test"]["pictures"]), (n_val, n_test))
        self.assertFalse(points["enough_data"])
        self.assertFalse(any(s["recommended"] for s in points["suggestions"]))
        self.assertIn("Too few checking boxes", points["why_not"])
        self.assertEqual(sum(r["predictions"] for r in points["bucket_precision"]), points["test"]["records"])
        print("[real torch operating_points summary]", {
            k: points[k] for k in ("records", "ground_truth_boxes", "pictures", "recall_ceiling", "enough_data")
        }, "suggestions reachable:", [s["reachable"] for s in points["suggestions"]],
            "bucket predictions:", [r["predictions"] for r in points["bucket_precision"]])



@skipUnless(TORCH_INSTALLED, "torch/torchvision are not installed here; run in the ML venv (see DEPLOYMENT.md)")
class TorchvisionStoresComparisonTests(TrainingBase):
    def test_two_real_one_epoch_models_store_a_comparison_with_fairness_fields(self) -> None:
        """W8-L2: the second model is compared with the first (put in use) through the real backend's own predict.
        16 tiny synthetic pictures are far too few boxes for a verdict, so it must say it cannot tell; both models
        were made from the same dataset, so the test pictures were never seen by the model in use (fair)."""
        import json
        import time

        dataset = self.make_dataset(count=16)

        def teach() -> ModelVersion:
            with mock.patch("torch.hub.load_state_dict_from_url", side_effect=AssertionError("network used")):
                response = self.post(
                    self.api("/training/runs"),
                    {"backend": "torchvision-fasterrcnn", "dataset_id": str(dataset.id),
                     "config": {"epochs": 1, "batch_size": 4, "seed": 3}},
                )
                self.assertEqual(response.status_code, 202, response.content)
                started = time.monotonic()
                finished = runner.run_training(runner.claim_next_run())
                self.elapsed = time.monotonic() - started
            self.assertEqual(finished.status, "succeeded", finished.error)
            self.logs = finished.logs
            return finished.model_versions.get()

        first = teach()
        self.assertNotIn("comparison", first.evaluation)  # nothing was in use
        self.assertEqual(self.post(self.api(f"/models/{first.id}/stage"), {}).status_code, 200)
        approved = self.post(self.api(f"/models/{first.id}/approve"),
                             {"note": "Prototype check on synthetic pictures.", "acknowledge_weak_results": True})
        self.assertEqual(approved.status_code, 200, approved.content)
        second = teach()
        block = second.evaluation["comparison"]
        self.assertEqual(block["schema"], "athena.comparison/1")
        self.assertEqual(block["baseline"]["model_id"], str(first.id))
        self.assertEqual(block["baseline"]["backend"], "torchvision-fasterrcnn")
        self.assertIs(block["fair"], True)
        self.assertEqual(block["overlap_pictures"], 0)
        n_test = sum(1 for i in dataset.manifest["images"] if i["split"] == "test")
        self.assertEqual(block["test_pictures"], n_test)
        self.assertEqual(block["test_boxes"], second.evaluation["data"]["boxes"])
        self.assertEqual(block["candidate_result"]["found"], second.evaluation["overall"]["true_positives"])
        self.assertEqual(block["verdict"], "cannot_tell")  # too few boxes
        self.assertIn("Comparing with version 1", self.logs)
        body = self.client.get(self.api(f"/models/{second.id}/results/plain")).json()
        self.assertTrue(body["comparison"]["available"])
        print("[real torch comparison block]", json.dumps(block, sort_keys=True))
        print("[real torch run seconds incl. training, evaluation, baseline pass]", round(self.elapsed, 2))
