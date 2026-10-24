"""Threshold selection from the validation split and wording from measured precision (W4-T).

SYNTHETIC data only: generated boxes with a known score distribution, tiny generated pictures and the fake backend.
Nothing here says anything about how a real model behaves on real imagery.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from .tests_training import FakeBackend, TrainingBase
from .training import evaluation as ev, operating_points as op, runner
from .training.base import EvalItem, TrainingBackend

BANNED = ("accurate", "accuracy", "certified", "safe", "guarantee")


def synthetic_split(seed: int, pictures: int, labels=("a", "b"), hit_rate: float = 0.88, false_per_picture: float = 1.5):
    """Ground truth and predictions with a KNOWN score distribution: each drawn box is found with probability
    ``hit_rate`` (score ~ Beta(4, 2)); each picture also gets Poisson(``false_per_picture``) wrong boxes
    (score ~ Beta(1.5, 4)). So the ceiling is ``hit_rate`` and a high score really is right more often."""
    rng = np.random.default_rng(seed)
    ground_truth, predictions = [], []
    for _ in range(pictures):
        gt, pred = [], []
        for j in range(int(rng.integers(1, 4))):
            label = labels[int(rng.integers(0, len(labels)))]
            box = (0.05 + 0.3 * j, 0.1, 0.25, 0.25)
            gt.append((label, box))
            if rng.random() < hit_rate:
                pred.append((label, box, float(np.clip(rng.beta(4, 2), 0.05, 1.0))))
        for _ in range(int(rng.poisson(false_per_picture))):
            label = labels[int(rng.integers(0, len(labels)))]
            pred.append((label, (0.05, 0.6, 0.2, 0.2), float(np.clip(rng.beta(1.5, 4), 0.05, 1.0))))
        ground_truth.append(gt)
        predictions.append(pred)
    return ground_truth, predictions


def synthetic_operating_points(seed: int = 1, val_pictures: int = 260, test_pictures: int = 300, **kw) -> dict:
    val_gt, val_pred = synthetic_split(seed, val_pictures, **kw)
    test_gt, test_pred = synthetic_split(seed + 1000, test_pictures, **kw)
    return op.build_operating_points(val_gt, val_pred, test_gt, test_pred, ["a", "b"])


# 4 drawn boxes; predictions sorted by score: .9 hit, .8 hit, .7 hit, .6 miss, .5 miss, .3 hit  ->  TP 1,2,3,3,3,4
def hand_split(gt_per_image=(2, 1, 1)) -> op.ScoredSplit:
    return op.ScoredSplit.from_records(
        image=[0, 0, 0, 1, 1, 2], score=[0.9, 0.6, 0.3, 0.8, 0.5, 0.7], hit=[1, 0, 1, 1, 0, 1], gt_per_image=gt_per_image
    )


class HandComputedMathsTests(SimpleTestCase):
    def test_the_highest_threshold_that_reaches_each_target(self) -> None:
        split = hand_split()
        self.assertEqual(split.n_gt, 4)
        self.assertEqual(op.pick_threshold(split, 0.5), 0.8)  # 2 of 4 needed: reached at the 0.8 prediction
        self.assertEqual(op.pick_threshold(split, 0.75), 0.7)  # 3 of 4
        self.assertEqual(op.pick_threshold(split, 0.8), 0.3)  # ceil(3.2) = 4 of 4: only the last prediction does it
        self.assertEqual(op.pick_threshold(split, 1.0), 0.3)
        self.assertEqual(op.recall_ceiling(split), 1.0)

    def test_precision_and_recall_at_a_threshold_count_every_drawn_box(self) -> None:
        split = hand_split()
        self.assertEqual(op.precision_recall_at(split, 0.7), {"precision": 1.0, "recall": 0.75, "n_gt": 4})
        self.assertEqual(op.precision_recall_at(split, 0.3), {"precision": 0.6666, "recall": 1.0, "n_gt": 4})
        self.assertEqual(op.precision_recall_at(split, 0.95), {"precision": None, "recall": 0.0, "n_gt": 4})

    def test_a_box_the_model_never_proposed_lowers_the_ceiling_and_makes_a_target_unreachable(self) -> None:
        split = hand_split(gt_per_image=(2, 1, 2))  # 5 drawn boxes, only 4 can ever be found
        self.assertEqual(op.recall_ceiling(split), 0.8)
        self.assertEqual(op.pick_threshold(split, 0.8), 0.3)  # exactly at the ceiling: reachable
        self.assertIsNone(op.pick_threshold(split, 0.9))
        self.assertIsNone(op.pick_threshold(split, 0.95))

    def test_tied_scores_are_taken_together(self) -> None:
        split = op.ScoredSplit.from_records([0, 0, 1], [0.5, 0.5, 0.5], [1, 0, 1], [1, 1])
        self.assertEqual(op.pick_threshold(split, 0.5), 0.5)
        self.assertEqual(op.precision_recall_at(split, 0.5), {"precision": 0.6666, "recall": 1.0, "n_gt": 2})

    def test_no_drawn_boxes_or_no_predictions_means_no_threshold_and_no_ceiling(self) -> None:
        empty_gt = op.ScoredSplit.from_records([0], [0.9], [0], [0])
        self.assertIsNone(op.pick_threshold(empty_gt, 0.8))
        self.assertIsNone(op.recall_ceiling(empty_gt))
        silent = op.ScoredSplit.from_records([], [], [], [3])
        self.assertIsNone(op.pick_threshold(silent, 0.8))
        self.assertEqual(op.recall_ceiling(silent), 0.0)

    def test_the_cutoff_is_rounded_down_so_the_prediction_that_set_it_is_kept(self) -> None:
        self.assertEqual(op.round_threshold_down(0.23519999), 0.2351)
        self.assertEqual(op.round_threshold_down(0.2351), 0.2351)  # 0.2351 * 1e4 is 2350.9999999999995 in floats
        self.assertEqual(op.round_threshold_down(1.0), 1.0)

    def test_wilson_interval(self) -> None:
        low, high = op.wilson_interval(50, 100)
        self.assertAlmostEqual(low, 0.419, places=3)
        self.assertAlmostEqual(high, 0.581, places=3)
        self.assertIsNone(op.wilson_interval(0, 0))
        self.assertEqual(op.wilson_interval(10, 10)[1], 1.0)

    def test_scoring_uses_the_evaluation_matching_and_agrees_with_its_counts(self) -> None:
        gt, pred = synthetic_split(5, 120)
        scored = op.score_split(gt, pred, ["a", "b"])
        evaluation = ev.evaluate_detections(gt, pred, ["a", "b"], confidence=0.5)
        kept = scored.score >= 0.5
        self.assertEqual(int(scored.hit[kept].sum()), evaluation["overall"]["true_positives"])
        self.assertEqual(int((~scored.hit[kept]).sum()), evaluation["overall"]["false_positives"])
        self.assertEqual(scored.n_gt, evaluation["data"]["boxes"])
        self.assertEqual(scored.gt_per_label, evaluation["data"]["boxes_per_label"])


class BucketTests(SimpleTestCase):
    def test_edges_are_fixed_and_cover_zero_to_one(self) -> None:
        self.assertEqual(op.BUCKET_EDGES[0], 0.0)
        self.assertEqual(op.BUCKET_EDGES[-1], 1.0)
        self.assertEqual(list(op.BUCKET_EDGES), sorted(set(op.BUCKET_EDGES)))

    def test_low_is_inclusive_high_is_exclusive_and_one_belongs_to_the_last_bucket(self) -> None:
        split = op.ScoredSplit.from_records([0] * 4, [0.5, 0.4999, 1.0, 0.9], [1, 0, 1, 0], [4])
        rows = {(r["low"], r["high"]): r for r in op.bucket_precision(split)}
        self.assertEqual(rows[(0.5, 0.6)]["predictions"], 1)
        self.assertEqual(rows[(0.4, 0.5)]["predictions"], 1)
        last = rows[(0.9, 1.0)]
        self.assertEqual((last["predictions"], last["correct"], last["precision"]), (2, 1, 0.5))
        self.assertEqual(sum(r["predictions"] for r in rows.values()), 4)

    def test_a_bucket_under_twenty_predictions_is_marked_not_enough(self) -> None:
        split = op.ScoredSplit.from_records([0] * 19 + [1] * 20, [0.85] * 19 + [0.65] * 20, [1] * 39, [19, 20])
        rows = {r["low"]: r for r in op.bucket_precision(split)}
        self.assertFalse(rows[0.8]["enough"])
        self.assertEqual(rows[0.8]["predictions"], 19)
        self.assertTrue(rows[0.6]["enough"])
        empty = rows[0.0]
        self.assertEqual((empty["predictions"], empty["precision"], empty["interval90"], empty["enough"]), (0, None, None, False))

    def test_recovers_the_known_precision_of_each_score_range(self) -> None:
        reference = op.bucket_precision(op.score_split(*synthetic_split(99, 8000), ["a", "b"]))
        measured = op.bucket_precision(op.score_split(*synthetic_split(7, 1500), ["a", "b"]))
        checked = 0
        for ref, got in zip(reference, measured):
            if got["predictions"] >= 150:
                checked += 1
                self.assertLess(abs(ref["precision"] - got["precision"]), 0.08, (ref, got))
        self.assertGreaterEqual(checked, 3)

    def test_the_cutoff_chosen_on_one_split_reaches_about_the_target_on_another(self) -> None:
        val = op.score_split(*synthetic_split(3, 600), ["a", "b"])
        test = op.score_split(*synthetic_split(4, 600), ["a", "b"])
        threshold = op.pick_threshold(val, 0.8)
        self.assertIsNotNone(threshold)
        self.assertAlmostEqual(op.precision_recall_at(test, threshold)["recall"], 0.8, delta=0.05)
        self.assertLessEqual(op.recall_ceiling(val), 0.92)  # the generator's ceiling is 0.88 of the drawn boxes


class BootstrapTests(SimpleTestCase):
    def setUp(self) -> None:
        self.val = op.score_split(*synthetic_split(11, 260), ["a", "b"])
        self.test = op.score_split(*synthetic_split(12, 300), ["a", "b"])

    def test_same_seed_same_answer_other_seed_other_answer(self) -> None:
        first = op.bootstrap_intervals(self.val, self.test, resamples=200, seed=5)
        again = op.bootstrap_intervals(self.val, self.test, resamples=200, seed=5)
        other = op.bootstrap_intervals(self.val, self.test, resamples=200, seed=6)
        self.assertEqual(first, again)
        self.assertNotEqual(first[0.8]["recall"], other[0.8]["recall"])

    def test_the_interval_brackets_the_measured_recall_and_is_not_absurdly_wide(self) -> None:
        threshold = op.pick_threshold(self.val, 0.8)
        measured = op.precision_recall_at(self.test, threshold)["recall"]
        low, high = op.bootstrap_intervals(self.val, self.test, resamples=300)[0.8]["recall"]
        self.assertLess(low, measured + 0.03)
        self.assertGreater(high, measured - 0.03)
        self.assertLess(high - low, 0.25)
        self.assertLessEqual(low, high)

    def test_an_unreachable_target_has_no_interval(self) -> None:
        out = op.bootstrap_intervals(self.val, self.test, resamples=100)  # ceiling is about 0.88
        self.assertIsNone(out[0.95]["recall"])
        self.assertLess(out[0.95]["resamples_used"], op.MIN_RESAMPLES_FOR_INTERVAL)

    def test_without_a_test_split_the_basis_says_it_is_optimistic(self) -> None:
        out = op.bootstrap_intervals(self.val, None, resamples=100)
        self.assertIn("optimistic", out[0.8]["basis"])
        self.assertIsNotNone(out[0.8]["recall"])

    def test_a_thousand_boxes_and_a_thousand_resamples_take_well_under_a_second(self) -> None:
        val = op.score_split(*synthetic_split(21, 500), ["a", "b"])
        test = op.score_split(*synthetic_split(22, 500), ["a", "b"])
        self.assertGreaterEqual(val.n_gt, 900)
        started = time.perf_counter()
        op.bootstrap_intervals(val, test)
        elapsed = time.perf_counter() - started
        print(f"[bootstrap timing] {val.n_gt} val boxes / {val.n_records} records, {test.n_records} test records: {elapsed:.3f}s")
        self.assertLess(elapsed, 2.0)  # measured well under 1 s; the slack is for a busy machine


class BuildDocumentTests(SimpleTestCase):
    def test_enough_boxes_gives_a_recommended_threshold_with_numbers_on_both_splits(self) -> None:
        doc = synthetic_operating_points()
        json.dumps(doc)  # JSON only
        self.assertEqual(doc["schema"], op.SCHEMA)
        self.assertEqual(doc["source"], "validation split")
        self.assertGreaterEqual(doc["ground_truth_boxes"], op.MIN_VALIDATION_BOXES)
        self.assertTrue(doc["enough_data"])
        self.assertEqual(doc["why_not"], "")
        self.assertEqual([s["target_recall"] for s in doc["suggestions"]], [0.8, 0.9, 0.95])
        by_target = {s["target_recall"]: s for s in doc["suggestions"]}
        eighty = by_target[0.8]
        self.assertTrue(eighty["reachable"] and eighty["recommended"] and eighty["enough_data"])
        self.assertGreaterEqual(eighty["validation"]["recall"], 0.8)
        self.assertAlmostEqual(eighty["test"]["recall"], 0.8, delta=0.06)
        self.assertLess(eighty["interval90"]["recall"][0], eighty["interval90"]["recall"][1])
        self.assertIn("A person chooses the setting", eighty["plain"])
        # ceiling is about 0.88: 0.90 and 0.95 are unreachable and say so, with nothing recommended
        self.assertLess(doc["recall_ceiling"], 0.9)
        for target in (0.9, 0.95):
            item = by_target[target]
            self.assertFalse(item["reachable"])
            self.assertIsNone(item["threshold"])
            self.assertFalse(item["recommended"])
            self.assertIsNone(item["validation"])
            self.assertRegex(item["plain"], r"^On the Checking pictures, no cut-off found more than \d+% of the drawn boxes, so a target of \d+% cannot be reached\.$")
            self.assertNotIn("never", item["plain"])
        self.assertEqual(len(doc["bucket_precision"]), len(op.BUCKET_EDGES) - 1)
        self.assertEqual(doc["bootstrap"], {"by": "picture", "resamples": 1000, "seed": op.BOOTSTRAP_SEED, "level": 0.9})

    def test_too_few_checking_boxes_keeps_the_numbers_but_never_recommends(self) -> None:
        doc = synthetic_operating_points(val_pictures=40, test_pictures=40)
        self.assertLess(doc["ground_truth_boxes"], op.MIN_VALIDATION_BOXES)
        self.assertFalse(doc["enough_data"])
        self.assertIn("Too few checking boxes to choose a threshold you can trust", doc["why_not"])
        eighty = doc["suggestions"][0]
        self.assertTrue(eighty["reachable"])
        self.assertIsNotNone(eighty["threshold"])  # still returned...
        self.assertIsNotNone(eighty["validation"]["recall"])
        self.assertFalse(eighty["enough_data"])
        self.assertFalse(eighty["recommended"])  # ...but never recommended
        self.assertTrue(eighty["plain"].startswith("Too few checking boxes to choose a threshold you can trust"))
        self.assertFalse(any(s["recommended"] for s in doc["suggestions"]))

    def test_a_label_with_too_few_boxes_blocks_a_recommendation_even_with_many_boxes(self) -> None:
        val_gt, val_pred = synthetic_split(1, 400, labels=("a",))
        rare_gt, rare_pred = synthetic_split(2, 8, labels=("b",))
        doc = op.build_operating_points(val_gt + rare_gt, val_pred + rare_pred, None, None, ["a", "b"])
        self.assertGreaterEqual(doc["ground_truth_boxes"], op.MIN_VALIDATION_BOXES)
        self.assertLess(doc["boxes_per_label"]["b"], op.MIN_VALIDATION_BOXES_PER_LABEL)
        self.assertFalse(doc["enough_data"])
        self.assertIn("“b” has", doc["why_not"])

    def test_no_test_split_means_no_bucket_table_and_no_test_numbers(self) -> None:
        val_gt, val_pred = synthetic_split(1, 260)
        doc = op.build_operating_points(val_gt, val_pred, None, None, ["a", "b"])
        self.assertEqual(doc["bucket_precision"], [])
        self.assertIsNone(doc["test"])
        self.assertIsNone(doc["suggestions"][0]["test"])
        self.assertIn("optimistic", doc["suggestions"][0]["interval90"]["basis"])

    def test_nothing_predicted_at_all(self) -> None:
        gt, _ = synthetic_split(1, 30)
        doc = op.build_operating_points(gt, [[] for _ in gt], gt, [[] for _ in gt], ["a", "b"])
        self.assertEqual(doc["recall_ceiling"], 0.0)
        self.assertTrue(all(not s["reachable"] for s in doc["suggestions"]))
        self.assertTrue(doc["suggestions"][0]["plain"].startswith(
            "On the Checking pictures, no cut-off found any of the drawn boxes, so a target of 80% cannot be reached."))
        self.assertNotIn("never", doc["suggestions"][0]["plain"])

    def test_a_stored_document_is_the_same_every_time(self) -> None:
        self.assertEqual(synthetic_operating_points(), synthetic_operating_points())

    def test_sentences_never_use_the_forbidden_words(self) -> None:
        for doc in (synthetic_operating_points(), synthetic_operating_points(val_pictures=40, test_pictures=40)):
            text = " ".join(s["plain"] for s in doc["suggestions"]).lower()
            text += " " + " ".join(op.bucket_sentence({**r, "enough": r["predictions"] >= 20}) for r in doc["bucket_precision"]).lower()
            text += " " + json.dumps(op.confidence_wording({"operating_points": doc})).lower()
            for word in BANNED:
                self.assertNotIn(word, text)


class WordingTests(SimpleTestCase):
    def test_the_bucket_sentence_for_the_spec_example(self) -> None:
        row = {"low": 0.8, "high": 0.9, "predictions": 96, "correct": 73, "precision": 0.7604, "enough": True}
        self.assertEqual(
            op.bucket_sentence(row),
            "Scores between 0.8 and 0.9 were right 76% of the time on the Final test pictures (96 boxes).",
        )
        thin = {**row, "predictions": 12, "correct": 9, "enough": False}
        self.assertEqual(
            op.bucket_sentence(thin),
            "Scores between 0.8 and 0.9: only 12 boxes on the Final test pictures, too few to say how often they were right.",
        )
        self.assertIn("no boxes", op.bucket_sentence({**thin, "predictions": 0, "correct": 0}))

    def test_odds_labels_are_neutral_and_quantitative(self) -> None:
        self.assertEqual(op.odds_label(0.76), "right about 3 in 4 times in our check")
        self.assertEqual(op.odds_label(0.5), "right about 1 in 2 times in our check")
        self.assertEqual(op.odds_label(0.26), "right about 1 in 4 times in our check")
        self.assertEqual(op.odds_label(0.22), "right about 2 in 10 times in our check")
        self.assertEqual(op.odds_label(0.54), "right about 1 in 2 times in our check")  # neighbours read alike
        self.assertEqual(op.odds_label(0.47), "right about 4 in 10 times in our check")  # read down, never up
        self.assertEqual(op.odds_label(0.4), "right about 4 in 10 times in our check")
        self.assertEqual(op.odds_label(0.02), "right fewer than 1 in 20 times in our check")
        self.assertEqual(op.odds_label(0.97), "right about 19 in 20 times in our check")
        for p in (0.0, 0.1, 0.33, 0.6, 0.9, 1.0):
            label = op.odds_label(p)
            self.assertTrue(label.startswith("right ") and label.endswith("in our check"), label)
            for word in ("sure", *BANNED):
                self.assertNotIn(word, label)

    def test_reading_a_stored_document_never_raises_and_never_trusts_stored_ratios(self) -> None:
        for bad in (None, {}, [], "x", {"operating_points": 3}, {"operating_points": {"schema": "other"}},
                    {"operating_points": {"schema": op.SCHEMA, "bucket_precision": "no"}},
                    {"operating_points": {"schema": op.SCHEMA, "bucket_precision": [{"low": 0, "high": 2}]}},
                    {"operating_points": {"schema": op.SCHEMA, "bucket_precision": [
                        {"low": 0, "high": 0.5, "predictions": 5, "correct": 9}]}},
                    {"operating_points": {"schema": op.SCHEMA, "bucket_precision": [
                        {"low": 0, "high": 0.6, "predictions": 50, "correct": 9},
                        {"low": 0.5, "high": 1, "predictions": 50, "correct": 9}]}}):
            self.assertFalse(op.confidence_wording(bad)["measured"], bad)
            self.assertFalse(op.wording_table(bad)["available"], bad)
            self.assertIsNone(op.suggested_threshold(bad)["value"], bad)
        stored = {"operating_points": {"schema": op.SCHEMA, "test": {"pictures": 60, "ground_truth_boxes": 90, "records": 50},
                                       "bucket_precision": [
            {"low": 0.0, "high": 1.0, "predictions": 50, "correct": 25, "precision": 0.99, "enough": False}]}}
        band = op.confidence_wording(stored)["bands"][0]
        self.assertEqual((band["precision"], band["enough"], band["n"]), (0.5, True, 50))


class SpikeTableTests(SimpleTestCase):
    """The spike's measured test-split counts (calibration REPORT.md; synthetic shapes, one weak from-scratch model),
    typed in as a table: the wording and bands the product derives from them."""

    # (low, high, predictions, correct) from the spike's 10 equal-width bins merged into this module's edges
    SPIKE = [(0.0, 0.2, 1095, 66), (0.2, 0.4, 507, 70), (0.4, 0.5, 159, 48), (0.5, 0.6, 127, 36), (0.6, 0.7, 114, 54),
             (0.7, 0.8, 144, 78), (0.8, 0.9, 177, 145), (0.9, 1.0, 399, 365)]

    def evaluation(self) -> dict:
        rows = [{"low": lo, "high": hi, "predictions": n, "correct": c} for lo, hi, n, c in self.SPIKE]
        test = {"pictures": 400, "ground_truth_boxes": 995, "records": 2722}
        return {"operating_points": {"schema": op.SCHEMA, "test": test, "bucket_precision": rows}}

    def test_a_test_split_of_a_handful_of_pictures_measures_nothing_however_many_predictions_it_has(self) -> None:
        small = self.evaluation()
        small["operating_points"]["test"] = {"pictures": 11, "ground_truth_boxes": 19, "records": 2722}
        self.assertFalse(op.confidence_wording(small)["measured"])
        table = op.wording_table(small)
        self.assertTrue(table["available"] and not any(b["enough"] for b in table["buckets"]))
        gone = self.evaluation()
        del gone["operating_points"]["test"]
        self.assertFalse(op.confidence_wording(gone)["measured"])
        self.assertTrue(op.test_split_is_adequate(30, 30))
        self.assertFalse(op.test_split_is_adequate(29, 500))
        self.assertFalse(op.test_split_is_adequate(500, 29))
        self.assertFalse(op.test_split_is_adequate(True, 99))
        # and at build time: 11 pictures, 1100 predictions in one range, still "not enough"
        gt, pred = synthetic_split(1, 260)
        tgt, tpred = synthetic_split(2, 11, false_per_picture=100)
        doc = op.build_operating_points(gt, pred, tgt, tpred, ["a", "b"])
        self.assertGreaterEqual(max(r["predictions"] for r in doc["bucket_precision"]), op.MIN_PREDICTIONS_PER_BUCKET)
        self.assertFalse(any(r["enough"] for r in doc["bucket_precision"]))

    def test_the_old_fixed_bands_would_have_been_wrong_and_the_measured_ones_are_not(self) -> None:
        wording = op.confidence_wording(self.evaluation())
        self.assertTrue(wording["measured"])
        by_low = {b["low"]: b for b in wording["bands"]}
        # "Somewhat sure" (0.5 to 0.75) was right 40% of the time in the spike; here 0.5-0.6 and 0.6-0.7 say so plainly
        # each label is read from the LOW end of the 90% range (Wilson: 0.2227, 0.3983, 0.8889), never rounded up
        self.assertEqual(by_low[0.5]["label"], "right about 2 in 10 times in our check")
        self.assertEqual(by_low[0.6]["label"], "right about 3 in 10 times in our check")
        self.assertEqual(by_low[0.9]["label"], "right about 8 in 10 times in our check")
        self.assertEqual((by_low[0.9]["precision"], by_low[0.9]["n"]), (0.9147, 399))
        table = op.wording_table(self.evaluation())["buckets"]
        self.assertEqual(
            table[6]["sentence"], "Scores between 0.8 and 0.9 were right 81% of the time on the Final test pictures (177 boxes)."
        )


class EvaluateEndToEndTests(TrainingBase):
    """The runner path through the fake backend: validation split scored, additive key, old keys untouched."""

    def run_training(self, dataset=None):
        return self.train_candidate(dataset or self.make_dataset(), solid=False)

    def test_val_items_are_the_validation_pictures_in_split_order_with_their_ids(self) -> None:
        dataset = self.make_dataset()
        items = runner.val_items(dataset.manifest, Path("dest"))
        pictures = [i for i in dataset.manifest["images"] if i["split"] == "val"]
        self.assertTrue(items)
        self.assertEqual([i.asset_id for i in items], [str(p["asset_id"]) for p in pictures])
        self.assertTrue(all(i.path.parent == Path("dest/images/val") for i in items))
        test_ids = {i.asset_id for i in runner.test_items(dataset.manifest, Path("dest"))}
        self.assertFalse(test_ids & {i.asset_id for i in items})

    def test_a_trained_model_stores_operating_points_from_the_validation_split(self) -> None:
        dataset = self.make_dataset()
        model = self.run_training(dataset)
        points = model.evaluation["operating_points"]
        images = dataset.manifest["images"]
        n_val = sum(1 for i in images if i["split"] == "val")
        n_test = sum(1 for i in images if i["split"] == "test")
        self.assertEqual(points["schema"], op.SCHEMA)
        self.assertEqual(points["source"], "validation split")
        self.assertEqual(points["pictures"], n_val)
        self.assertEqual(points["records"], n_val)  # the fake backend draws exactly the drawn box, once per picture
        self.assertEqual(points["ground_truth_boxes"], n_val)
        self.assertEqual(points["recall_ceiling"], 1.0)
        self.assertEqual(points["test"], {"pictures": n_test, "ground_truth_boxes": n_test, "records": n_test})
        self.assertFalse(points["enough_data"])  # a handful of pictures is never enough
        self.assertTrue(all(not s["recommended"] for s in points["suggestions"]))
        self.assertTrue(all(s["reachable"] and s["threshold"] == 0.9 for s in points["suggestions"]))
        top = {(r["low"], r["high"]): r for r in points["bucket_precision"]}[(0.9, 1.0)]
        self.assertEqual((top["predictions"], top["correct"], top["precision"], top["enough"]), (n_test, n_test, 1.0, False))
        self.assertEqual(sum(r["predictions"] for r in points["bucket_precision"]), n_test)

    def test_existing_evaluation_keys_and_values_are_exactly_what_they_were(self) -> None:
        dataset = self.make_dataset()
        model = self.run_training(dataset)
        stored = dict(model.evaluation)
        stored.pop("operating_points")
        stored.pop("dataset")
        self.assertEqual(
            set(stored),
            {"schema", "available", "split", "iou_threshold", "confidence_threshold", "data", "per_label", "overall", "limits", "held_out_outcomes"},
        )
        self.assertEqual(stored["schema"], ev.SCHEMA)
        self.assertEqual(stored["split"], "test")
        self.assertEqual(stored["confidence_threshold"], 0.5)
        self.assertEqual(stored["overall"]["precision"], 1.0)
        self.assertEqual(stored["overall"]["recall"], 1.0)

    def test_a_backend_without_support_gets_no_key_and_the_rest_is_identical(self) -> None:
        class NoValidationScoring(FakeBackend):
            name = "fake-backend"

            def predict_split(self, artifact, items, labels, thresholds):
                return None

        from . import training

        training.register_backend(NoValidationScoring, replace=True)
        dataset = self.make_dataset()
        without = self.run_training(dataset)
        training.register_backend(FakeBackend, replace=True)
        with_points = self.run_training(dataset)
        self.assertNotIn("operating_points", without.evaluation)
        self.assertEqual(
            {k: v for k, v in with_points.evaluation.items() if k != "operating_points"}, without.evaluation
        )

    def test_a_backend_that_overrides_evaluate_still_works(self) -> None:
        class Custom(FakeBackend):
            name = "fake-backend"

            def evaluate(self, artifact, items, labels, thresholds):
                return {**ev.evaluate_items(self, artifact, items, labels, thresholds), "custom": True}

        from . import training

        training.register_backend(Custom, replace=True)
        model = self.run_training()
        self.assertTrue(model.evaluation["custom"])
        self.assertIn("operating_points", model.evaluation)

    def test_a_backend_that_cannot_predict_other_splits_does_not_fail_the_run(self) -> None:
        class OnlyTest(FakeBackend):
            name = "fake-backend"

            def evaluate(self, artifact, items, labels, thresholds):
                return ev.evaluate_detections(
                    [[(l, (x, y, w, h)) for l, x, y, w, h in i.boxes] for i in items],
                    [[("damaged", (0.1, 0.2, 0.3, 0.4), 0.9)] for _ in items],
                    labels,
                )

            def predict(self, artifact, image, thresholds):
                raise NotImplementedError

        from . import training

        training.register_backend(OnlyTest, replace=True)
        model = self.run_training()
        self.assertTrue(model.evaluation["available"])
        self.assertNotIn("operating_points", model.evaluation)

    def test_a_crash_while_choosing_thresholds_never_fails_the_run(self) -> None:
        with mock.patch.object(op, "build_operating_points", side_effect=RuntimeError("boom at /secret/dir/path")):
            model = self.run_training()
        self.assertTrue(model.evaluation["available"])
        self.assertNotIn("operating_points", model.evaluation)
        self.assertIn("Could not work out suggested thresholds", model.training_run.logs)
        self.assertNotIn("/secret/dir", model.training_run.logs)

    def test_the_default_hook_says_none_when_the_backend_cannot_predict(self) -> None:
        picture = Path(self.home.name) / "p.png"
        Image.new("RGB", (8, 8)).save(picture)
        item = EvalItem(picture, [], 8, 8)
        with mock.patch.object(FakeBackend, "predict", side_effect=NotImplementedError):
            self.assertIsNone(FakeBackend().predict_split(Path("m"), [item], ["damaged"], {}))
        gt, pred = FakeBackend().predict_split(Path("m"), [item], ["damaged"], {})
        self.assertEqual((len(gt), len(pred)), (1, 1))


# -- W6-FT: pin the numbers, honest rounding and wording, bad scores, the recommendation rule ------------------------
BOX = (0.1, 0.1, 0.2, 0.2)
ELSEWHERE = (0.6, 0.6, 0.2, 0.2)  # IoU 0 with BOX: a wrong box


def pictures(hits=(), missed=(), label="a"):
    """One picture per drawn box. ``hits``: score of a right prediction. ``missed``: a list of scores of WRONG boxes drawn
    on a picture whose drawn box was not found (an empty list = nothing drawn there)."""
    ground_truth, predictions = [], []
    for score in hits:
        ground_truth.append([(label, BOX)])
        predictions.append([(label, BOX, score)])
    for wrong in missed:
        ground_truth.append([(label, BOX)])
        predictions.append([(label, ELSEWHERE, s) for s in wrong])
    return ground_truth, predictions


def join(*parts):
    return [sum((p[0] for p in parts), []), sum((p[1] for p in parts), [])]


#: Checking split: 500 boxes, 440 findable (ceiling 0.88); the 80% target needs 400 -> the 0.6 prediction.
#: At 0.6 it draws 440 right + 60 wrong: precision 0.88, recall 0.88.
def checking_pictures():
    return join(pictures([0.9] * 110 + [0.8] * 110 + [0.7] * 110 + [0.6] * 110, [[0.65]] * 60))


#: Final test split, deliberately NOT like the checking split: 300 boxes. At 0.6 it draws 150 right + 90 wrong:
#: recall 0.5, precision 0.625 (the checking split says 0.88 and 0.88).
def final_test_pictures():
    return join(pictures([0.9] * 60 + [0.7] * 60 + [0.6] * 30 + [0.55] * 30, [[0.8]] * 90 + [[0.3]] * 10 + [[]] * 20))


def doc_from(val, test, resamples: int = 1000, labels=("a",)):
    return op.build_operating_points(val[0], val[1], test[0] if test else None, test[1] if test else None, list(labels), resamples=resamples)


class TestNumbersArePinnedTests(SimpleTestCase):
    """Validation and test splits are different on purpose, so a 'test' number computed on the checking split fails."""

    def setUp(self) -> None:
        self.doc = doc_from(checking_pictures(), final_test_pictures())
        self.eighty = self.doc["suggestions"][0]

    def test_the_cutoff_and_both_sets_of_numbers_are_exact(self) -> None:
        self.assertEqual(self.eighty["threshold"], 0.6)
        self.assertEqual(self.eighty["validation"], {"precision": 0.88, "recall": 0.88, "n_gt": 500})
        self.assertEqual(self.eighty["test"], {"precision": 0.625, "recall": 0.5, "n_gt": 300})
        self.assertEqual(self.doc["test"], {"pictures": 300, "ground_truth_boxes": 300, "records": 180 + 90 + 10})
        self.assertEqual(self.doc["recall_ceiling"], 0.88)

    def test_the_score_ranges_come_from_the_final_test_split(self) -> None:
        rows = {r["low"]: r for r in self.doc["bucket_precision"]}
        self.assertEqual((rows[0.8]["predictions"], rows[0.8]["correct"]), (90, 0))  # 110 right ones on the checking split
        self.assertEqual((rows[0.9]["predictions"], rows[0.9]["correct"]), (60, 60))
        self.assertEqual((rows[0.6]["predictions"], rows[0.6]["correct"]), (30, 30))

    def test_the_sentence_says_the_test_result_fell_short_of_the_target(self) -> None:
        self.assertIn("found 88% of the 500 boxes on the Checking pictures and 50% of the 300 boxes on the Final test pictures", self.eighty["plain"])
        self.assertIn("fell short of the 80% target", self.eighty["plain"])

    def test_399_boxes_are_not_enough_and_400_are_(self) -> None:
        test = final_test_pictures()
        for boxes, enough in ((399, False), (400, True)):
            doc = doc_from(join(pictures([0.9] * boxes)), test)
            self.assertEqual(doc["ground_truth_boxes"], boxes)
            self.assertEqual(doc["enough_data"], enough, boxes)
            self.assertEqual(doc["suggestions"][0]["recommended"], enough, boxes)

    def test_the_per_label_minimum_boundary(self) -> None:
        test = final_test_pictures()
        minimum = op.MIN_VALIDATION_BOXES_PER_LABEL
        for rare, enough in ((minimum - 1, False), (minimum, True)):
            doc = doc_from(join(pictures([0.9] * 400), pictures([0.9] * rare, label="b")), test, labels=("a", "b"))
            self.assertEqual(doc["enough_data"], enough, rare)

    def test_the_cutoff_is_rounded_down_end_to_end(self) -> None:
        val = join(pictures([0.9] * 300 + [0.23519999] * 200))
        doc = doc_from(val, join(pictures([0.9] * 100 + [0.2351] * 100)))
        eighty = doc["suggestions"][0]
        self.assertEqual(eighty["threshold"], 0.2351)  # not 0.23519999, not 0.2352
        self.assertGreaterEqual(eighty["validation"]["recall"], 0.8)
        self.assertEqual(eighty["test"]["recall"], 1.0)  # the test prediction scoring exactly 0.2351 is kept

    def test_the_interval_is_for_the_stored_rounded_cutoff(self) -> None:
        # every resample picks 0.23519999; stored cut-off is 0.2351, which keeps the test predictions scoring 0.2351
        doc = doc_from(join(pictures([0.23519999] * 100)), join(pictures([0.2351] * 100)), resamples=200)
        eighty = doc["suggestions"][0]
        self.assertEqual((eighty["threshold"], eighty["test"]["recall"]), (0.2351, 1.0))
        self.assertEqual(eighty["interval90"]["recall"], [1.0, 1.0])
        # and without a test split the checking precision also counts what scored between the two
        wrong_too = ([[("a", BOX)] for _ in range(100)], [[("a", BOX, 0.23519999), ("a", ELSEWHERE, 0.2351)] for _ in range(100)])
        doc = doc_from(wrong_too, None, resamples=200)
        eighty = doc["suggestions"][0]
        self.assertEqual(eighty["validation"]["precision"], 0.5)
        self.assertEqual(eighty["interval90"]["precision"], [0.5, 0.5])


class RecommendationRuleTests(SimpleTestCase):
    def test_without_a_test_split_nothing_is_recommended_and_no_value_is_suggested(self) -> None:
        doc = doc_from(checking_pictures(), None)
        self.assertGreaterEqual(doc["ground_truth_boxes"], op.MIN_VALIDATION_BOXES)
        self.assertFalse(doc["enough_data"])
        self.assertIn("no Final test pictures", doc["why_not"])
        for item in doc["suggestions"]:
            self.assertFalse(item["recommended"])
            self.assertFalse(item["enough_data"])
        self.assertIsNone(op.suggested_threshold({"operating_points": doc})["value"])
        self.assertIn("not been checked on pictures the model did not see", doc["suggestions"][0]["plain"])

    def test_a_test_split_with_no_drawn_boxes_counts_as_no_test_split(self) -> None:
        no_boxes = ([[] for _ in range(50)], [[("a", BOX, 0.9)] for _ in range(50)])
        doc = doc_from(checking_pictures(), no_boxes, resamples=100)
        self.assertEqual(doc["test"], {"pictures": 50, "ground_truth_boxes": 0, "records": 50})
        self.assertFalse(doc["enough_data"])

    def test_a_target_reached_in_too_few_redraws_is_not_recommended_and_the_sentence_says_so(self) -> None:
        # ceiling exactly 0.80: about half of the re-drawn pictures miss the 80% target
        val = join(pictures([0.9] * 200 + [0.8] * 200, [[]] * 100))
        doc = doc_from(val, final_test_pictures())
        eighty = doc["suggestions"][0]
        used = eighty["interval90"]["resamples_used"]
        self.assertLess(used, 900)
        self.assertTrue(doc["enough_data"])
        self.assertFalse(eighty["recommended"])
        self.assertIsNone(op.suggested_threshold({"operating_points": doc})["value"])
        self.assertIn(f"among the {used} of 1000 re-draws of the pictures that reached the target", eighty["plain"])
        self.assertIn("not recommended", eighty["plain"])

    def test_a_well_covered_target_is_recommended_and_the_value_is_suggested(self) -> None:
        doc = doc_from(checking_pictures(), final_test_pictures())
        eighty = doc["suggestions"][0]
        self.assertGreaterEqual(eighty["interval90"]["resamples_used"], 900)
        self.assertTrue(eighty["recommended"])
        self.assertEqual(op.suggested_threshold({"operating_points": doc})["value"], 0.6)
        self.assertNotIn("not recommended", eighty["plain"])


class HonestRoundingTests(SimpleTestCase):
    def item(self, **kw):
        base = {
            "target_recall": 0.8, "reachable": True, "threshold": 0.3733, "enough_data": True, "recommended": True,
            "validation": {"precision": 0.5, "recall": 0.8, "n_gt": 455},
            "test": {"precision": 0.5233, "recall": 0.7951, "n_gt": 402},
            "interval90": {"recall": [0.7466, 0.827], "precision": [0.4812, 0.5701], "resamples_used": 1000,
                           "resamples": 1000, "basis": "x"},
        }
        base.update(kw)
        return base

    def test_percentages_are_rounded_down_and_the_high_end_of_a_range_up(self) -> None:
        text = op.suggestion_sentence(self.item(), ceiling=0.88, why_not="")
        self.assertIn("and 79% of the 402 boxes on the Final test pictures", text)  # 0.7951 is not '80%'
        self.assertIn("(90% range 74% to 83%;", text)  # raw [0.7466, 0.827] is not '75% to 83%' by luck
        self.assertIn("52% of the boxes it drew there were right (90% range 48% to 58%)", text)
        self.assertNotIn("80% of the 402", text)

    def test_the_shared_helpers(self) -> None:
        self.assertEqual(op.pct_down(0.7951), "79%")
        self.assertEqual(op.pct_down(0.29), "29%")  # 0.29 * 100 is 28.999999999999996
        self.assertEqual(op.pct_down(0.8), "80%")
        self.assertEqual(op.pct_up(0.7466), "75%")
        self.assertEqual(op.pct_up(0.75), "75%")
        self.assertEqual(op.pct_up(0.29), "29%")
        self.assertEqual(op.pct_down(None), "not measurable")

    def test_stored_shares_are_never_rounded_up(self) -> None:
        split = op.ScoredSplit.from_records([0, 1, 2], [0.9, 0.9, 0.9], [1, 1, 0], [1, 1, 1])
        self.assertEqual(op.precision_recall_at(split, 0.9), {"precision": 0.6666, "recall": 0.6666, "n_gt": 3})
        self.assertEqual(op.bucket_precision(split)[-1]["precision"], 0.6666)
        rows = op.read_buckets({"operating_points": {"schema": op.SCHEMA, "bucket_precision": [
            {"low": 0.0, "high": 1.0, "predictions": 3, "correct": 2}]}})
        self.assertEqual(rows[0]["precision"], 0.6666)

    def test_the_interval_ends_are_floored_and_ceiled_in_the_stored_document(self) -> None:
        low, high = op.bootstrap_intervals(*(op.score_split(*checking_pictures(), ["a"]), op.score_split(*final_test_pictures(), ["a"])), resamples=300)[0.8]["recall"]
        again = op.bootstrap_intervals(op.score_split(*checking_pictures(), ["a"]), op.score_split(*final_test_pictures(), ["a"]), resamples=300)[0.8]["recall"]
        self.assertEqual([low, high], again)
        self.assertEqual(low, op.floor4(low))
        self.assertEqual(high, op.ceil4(high))

    def test_interval_ends_are_rounded_outward(self) -> None:
        values = np.array([0.12345] * 50)
        self.assertEqual(op.interval_ends(values, 5, 95), [0.1234, 0.1235])  # not [0.1235, 0.1235] or [0.1234, 0.1234]
        self.assertIsNone(op.interval_ends(values[: op.MIN_RESAMPLES_FOR_INTERVAL - 1], 5, 95))
        self.assertEqual(op.interval_ends(np.array([0.5] * 30), 5, 95), [0.5, 0.5])
        self.assertEqual((op.floor4(0.7951), op.ceil4(0.7951), op.ceil4(0.8), op.floor4(0.8)), (0.7951, 0.7951, 0.8, 0.8))
        self.assertEqual((op.floor4(0.12345), op.ceil4(0.12345)), (0.1234, 0.1235))

    def test_the_band_label_uses_the_lower_end_and_never_the_nearest_fraction(self) -> None:
        self.assertEqual(op.odds_label(0.8499), "right about 8 in 10 times in our check")
        self.assertEqual(op.odds_label(0.8501), "right about 8 in 10 times in our check")  # was '9 in 10'
        self.assertEqual(op.odds_label(0.97), "right about 19 in 20 times in our check")
        self.assertEqual(op.odds_label(0.47), "right about 4 in 10 times in our check")
        self.assertEqual(op.odds_label(0.9, low=0.78), "right about 3 in 4 times in our check")
        self.assertEqual(op.odds_label(0.06, low=0.01), "right about 1 in 20 times in our check")
        for p in (0.0, 0.03, 0.2, 0.5, 0.75, 0.97, 1.0):
            self.assertNotIn("at least", op.odds_label(p))
            self.assertNotIn("at least", op.odds_label(p, low=p / 2))

    def test_twenty_clean_predictions_do_not_read_as_a_guarantee(self) -> None:
        def label(correct, n):
            rows = [{"low": 0.0, "high": 1.0, "predictions": n, "correct": correct}]
            test = {"pictures": 60, "ground_truth_boxes": 90, "records": n}
            band = op.confidence_wording({"operating_points": {"schema": op.SCHEMA, "test": test, "bucket_precision": rows}})["bands"][0]
            return band["label"]

        self.assertEqual(label(20, 20), "right about 8 in 10 times in our check")  # Wilson 90% low end is 0.88
        self.assertEqual(label(399, 399), "right about 19 in 20 times in our check")  # low end 0.993
        self.assertEqual(label(365, 399), "right about 8 in 10 times in our check")  # 91% measured, low end 0.8926
        self.assertNotIn("at least", label(20, 20))

    def test_the_unreachable_sentence_makes_no_absolute_claim(self) -> None:
        item = self.item(reachable=False, threshold=None, validation=None, test=None, interval90=None, recommended=False)
        text = op.suggestion_sentence({**item, "target_recall": 0.9}, ceiling=0.8825, why_not="")
        self.assertEqual(text, "On the Checking pictures, no cut-off found more than 89% of the drawn boxes, so a target of 90% cannot be reached.")
        text = op.suggestion_sentence({**item, "target_recall": 0.9}, ceiling=0.0, why_not="")
        self.assertEqual(text, "On the Checking pictures, no cut-off found any of the drawn boxes, so a target of 90% cannot be reached.")
        self.assertNotIn("never", text)
        thin = op.suggestion_sentence({**item, "enough_data": False}, ceiling=0.88, why_not="Too few checking boxes.")
        self.assertTrue(thin.endswith("Too few checking boxes.") and "never" not in thin)


class UncertaintyIsNeverHiddenTests(SimpleTestCase):
    def sentence(self, **interval):
        item = HonestRoundingTests().item()
        item["interval90"] = {"basis": "x", "precision": None, "recall": None, "resamples_used": 1000, "resamples": 1000, **interval}
        return op.suggestion_sentence(item, ceiling=0.9, why_not="")

    def test_a_missing_range_says_so_with_the_number_of_redraws(self) -> None:
        text = self.sentence(recall=None, resamples_used=7)
        self.assertIn("(range not available: the target was reached in only 7 of 1000 re-draws of the pictures)", text)

    def test_a_range_states_in_how_many_redraws_the_target_was_reached(self) -> None:
        text = self.sentence(recall=[0.7, 0.8], resamples_used=724)
        self.assertIn("(90% range 70% to 80%, among the 724 of 1000 re-draws of the pictures that reached the target)", text)
        text = self.sentence(recall=[0.7, 0.8], resamples_used=1000)
        self.assertIn("(90% range 70% to 80%; the target was reached in all 1000 re-draws of the pictures)", text)

    def test_the_precision_range_is_shown(self) -> None:
        text = self.sentence(recall=[0.7, 0.8], precision=[0.41, 0.56])
        self.assertIn("52% of the boxes it drew there were right (90% range 41% to 56%)", text)

    def test_no_interval_block_at_all_still_says_the_range_is_not_available(self) -> None:
        item = HonestRoundingTests().item(interval90=None)
        self.assertIn("on the Final test pictures (range not available);", op.suggestion_sentence(item, ceiling=0.9, why_not=""))

    def test_an_old_document_without_the_total_still_reads_plainly(self) -> None:
        item = HonestRoundingTests().item()
        item["interval90"] = {"basis": "x", "precision": None, "recall": [0.7, 0.8], "resamples_used": 900}
        self.assertIn("(90% range 70% to 80%)", op.suggestion_sentence(item, ceiling=0.9, why_not=""))


class TradeOffWordingTests(SimpleTestCase):
    def test_the_eighty_percent_suggestion_in_plain_words(self) -> None:
        eighty = doc_from(checking_pictures(), final_test_pictures())["suggestions"][0]
        text = eighty["plain"]
        self.assertTrue(text.startswith("A cut-off of 0.6 found 88% of the 500 boxes on the Checking pictures and 50% of the 300 boxes on the Final test pictures ("), text)
        self.assertIn("62% of the boxes it drew there were right", text)  # 0.625 rounded DOWN
        self.assertIn("At this setting about 4 in 10 of the boxes it draws are wrong on the Final test pictures.", text)
        self.assertIn("The same cut-off applies to every label.", text)
        self.assertIn("For comparison, at the usual 0.5 setting it found 60% of the boxes, and 66% of the boxes it drew were right (Final test pictures).", text)
        self.assertEqual(text.count("A person chooses the setting."), 1)
        self.assertTrue(text.endswith(" A person chooses the setting."))

    def test_the_wrong_share_is_rounded_toward_more_wrong(self) -> None:
        item = HonestRoundingTests().item()
        item["test"] = {"precision": 0.52, "recall": 0.8, "n_gt": 402}
        self.assertIn("about 1 in 2 of the boxes it draws are wrong", op.suggestion_sentence(item, ceiling=0.9, why_not=""))
        item["test"] = {"precision": 0.93, "recall": 0.8, "n_gt": 402}
        self.assertIn("about 1 in 10 of the boxes it draws are wrong", op.suggestion_sentence(item, ceiling=0.9, why_not=""))
        item["test"] = {"precision": 0.3, "recall": 0.8, "n_gt": 402}
        self.assertIn("about 7 in 10 of the boxes it draws are wrong", op.suggestion_sentence(item, ceiling=0.9, why_not=""))

    def test_the_comparison_with_the_usual_setting_is_only_made_from_stored_numbers(self) -> None:
        doc = doc_from(checking_pictures(), final_test_pictures())
        stored = doc["suggestions"][0]["test_at_usual_cutoff"]
        # at 0.5 on the test split the 0.3 predictions drop out: 180 right of 270 drawn, of 300 boxes
        self.assertEqual(stored, {"threshold": 0.5, "precision": 0.6666, "recall": 0.6, "n_gt": 300})
        old = {**doc["suggestions"][0]}
        del old["test_at_usual_cutoff"]
        self.assertNotIn("For comparison", op.suggestion_sentence(old, ceiling=0.88, why_not=""))
        self.assertIn("at the usual 0.5 setting it found 60% of the boxes, and 66% of the boxes it drew were right",
                      doc["suggestions"][0]["plain"])


class BadScoresTests(SimpleTestCase):
    def doc_with(self, scores):
        val_gt, val_pred = checking_pictures()
        val_pred = [list(p) for p in val_pred]
        for i, score in enumerate(scores):
            val_pred[i].append(("a", ELSEWHERE, score))
        return doc_from([val_gt, val_pred], final_test_pictures(), resamples=100)

    def test_nan_inf_and_missing_scores_are_dropped_not_fatal(self) -> None:
        clean = doc_from(checking_pictures(), final_test_pictures(), resamples=100)
        bad = self.doc_with([float("nan"), float("inf"), float("-inf"), None])
        json.dumps(bad, allow_nan=False)
        self.assertEqual(bad["records"], clean["records"])
        self.assertEqual(bad["suggestions"][0]["threshold"], clean["suggestions"][0]["threshold"])

    def test_scores_outside_zero_to_one_are_clipped_and_the_cutoff_stays_readable(self) -> None:
        val = join(pictures([1.5] * 450 + [0.5] * 50))
        doc = doc_from(val, final_test_pictures(), resamples=100)
        eighty = doc["suggestions"][0]
        self.assertEqual(eighty["threshold"], 1.0)
        self.assertEqual(op.suggested_threshold({"operating_points": doc})["value"], 1.0)
        split = op.score_split(*join(pictures([-0.3, 1.5])), ["a"])
        self.assertEqual(sorted(split.score.tolist()), [0.0, 1.0])


class StoredBlockIsCheckedTests(SimpleTestCase):
    def block(self):
        return doc_from(checking_pictures(), final_test_pictures(), resamples=100)

    def test_a_clean_block_is_served(self) -> None:
        self.assertTrue(op.plain_operating_points({"operating_points": self.block()})["available"])

    def test_a_non_finite_number_anywhere_makes_it_unreadable_not_served(self) -> None:
        for path in (("suggestions", 0, "threshold"), ("suggestions", 0, "validation", "recall"),
                     ("suggestions", 0, "interval90", "recall", 0), ("recall_ceiling",), ("bucket_precision", 2, "precision")):
            block = self.block()
            node = block
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = float("nan")
            got = op.plain_operating_points({"operating_points": block})
            self.assertFalse(got["available"], path)
            self.assertIn("could not be read", got["reason"])
            self.assertFalse(op.confidence_wording({"operating_points": block})["measured"], path)
            self.assertIsNone(op.suggested_threshold({"operating_points": block})["value"], path)

    def test_a_threshold_outside_zero_to_one_is_unreadable(self) -> None:
        block = self.block()
        block["suggestions"][0]["threshold"] = 1.5
        self.assertFalse(op.plain_operating_points({"operating_points": block})["available"])


class RunControlTests(TrainingBase):
    def test_collect_asks_before_each_pass_and_a_stop_ends_it_between_passes(self) -> None:
        class Stop(Exception):
            pass

        calls, steps = [], []

        class Backend(FakeBackend):
            def predict_split(self, artifact, items, labels, thresholds):
                calls.append(len(items))
                return [[[("damaged", (0.1, 0.2, 0.3, 0.4))]], [[("damaged", (0.1, 0.2, 0.3, 0.4), 0.9)]]]

        def step(message):
            steps.append(message)
            if len(steps) == 2:
                raise Stop()

        with self.assertRaises(Stop):
            op.collect(Backend(), Path("m"), ["v"], ["t"], ["damaged"], {}, step=step)
        self.assertEqual(calls, [1])  # the Final test pass never started
        self.assertEqual(steps, ["Scoring the Checking pictures (1 of 2)", "Scoring the Final test pictures (2 of 2)"])

    def test_a_cancel_asked_for_while_thresholds_are_worked_out_ends_the_run_as_cancelled(self) -> None:
        from .models import TrainingRun
        from . import training

        seen = []

        class CancelWhileScoring(FakeBackend):
            name = "fake-backend"

            def predict_split(self, artifact, items, labels, thresholds):
                seen.append(len(items))
                TrainingRun.objects.filter(status=TrainingRun.RUNNING).update(cancel_requested=True)
                return super().predict_split(artifact, items, labels, thresholds)

        training.register_backend(CancelWhileScoring, replace=True)
        self.addCleanup(training.register_backend, FakeBackend, replace=True)
        response = self.start_run(self.make_dataset())
        self.assertEqual(response.status_code, 202, response.content)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.CANCELLED)
        self.assertEqual(finished.model_versions.count(), 0)
        self.assertEqual(len(seen), 1)  # the second pass never started
        self.assertIn("Scoring the Checking pictures", finished.logs)

    def test_a_run_past_its_time_limit_stops_between_passes(self) -> None:
        from .models import TrainingRun

        with mock.patch.object(runner, "training_settings", return_value={**runner.training_settings(), "RUN_TIMEOUT_SECONDS": 0}):
            # train() calls progress(), which already raises when the limit is 0; the point here is the checkpoint itself
            log = type("L", (), {"run": type("R", (), {"pk": 1})(), "add": lambda s, m: None, "flush": lambda s, force=False: None})()
            with self.assertRaises(runner.TrainingTimedOut):
                runner.work_out_operating_points(FakeBackend(), Path("m"), ["v"], ["t"], ["damaged"], log,
                                                 check=lambda: (_ for _ in ()).throw(runner.TrainingTimedOut("late")))
