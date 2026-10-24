"""Plain-language results (W1-B3): ``GET /api/projects/<id>/models/<id>/results/plain``.

Every evaluation dict used below is SYNTHETIC: it is produced by the product's own ``evaluate_detections``
from hand-made ground truth and predictions (so the numbers are the product's numbers), or by the real fake-
backend training path. Nothing here says anything about a real trained model on real imagery.
"""

from __future__ import annotations

import re
import uuid

from django.test import Client, SimpleTestCase

from . import tests_roles
from .models import Asset, ModelVersion, Project
from .tests import grant
from .tests_training import TrainingBase
from .training import evaluation as ev, report

MISS_KEYS = {
    "misses_available", "reason", "items", "capped_at", "total", "no_longer_in_project", "pictures", "distinct_pictures"
}
BOX = (0.1, 0.1, 0.3, 0.3)
FAR = (0.6, 0.6, 0.3, 0.3)  # overlaps BOX not at all


def synthetic_evaluation(labels: dict[str, tuple[int, int, int]], *, with_outcomes: bool = True, confidence: float = 0.5):
    """``labels`` maps label -> (test boxes, found, wrong boxes drawn). One picture per test box, one per wrong box.

    Returns (evaluation dict from the product's own function, the list of picture ids used)."""
    ground_truth, predictions, ids = [], [], []
    for label, (n, found, wrong) in labels.items():
        for index in range(n):
            ground_truth.append([(label, BOX)])
            predictions.append([(label, BOX, 0.9)] if index < found else [])
            ids.append(str(uuid.uuid4()))
        for index in range(wrong):
            ground_truth.append([])
            predictions.append([(label, FAR, round(0.95 - index * 0.001, 4))])
            ids.append(str(uuid.uuid4()))
    result = ev.evaluate_detections(
        ground_truth, predictions, list(labels), confidence=confidence, asset_ids=ids if with_outcomes else None
    )
    return result, ids


# ---------------------------------------------------------------------------------------------------
class OutcomesFromTheEvaluationCodeTests(SimpleTestCase):
    """The per-picture list is built by the same matching rule as the stored counts, and only when asked for."""

    def test_default_output_is_unchanged_without_asset_ids(self) -> None:
        result = ev.evaluate_detections([[("a", BOX)]], [[("a", BOX, 0.9)]], ["a"])
        self.assertEqual(
            sorted(result),
            ["available", "confidence_threshold", "data", "iou_threshold", "limits", "overall", "per_label", "schema", "split"],
        )

    def test_synthetic_outcomes_add_up_to_the_stored_counts(self) -> None:
        # a duplicate prediction on one box (counts as one hit + one wrong box) and one below-threshold hit
        ground_truth = [[("a", BOX), ("a", FAR)], [("b", BOX)], []]
        predictions = [
            [("a", BOX, 0.9), ("a", BOX, 0.8), ("a", FAR, 0.3)],  # FAR hit is under 0.5: so FAR is missed
            [("a", BOX, 0.7)],  # right place, wrong label -> a false alarm for "a", a miss for "b"
            [("b", FAR, 0.6)],
        ]
        ids = ["p1", "p2", "p3"]
        result = ev.evaluate_detections(ground_truth, predictions, ["a", "b"], asset_ids=ids)
        block = result["held_out_outcomes"]
        per = result["per_label"]
        self.assertEqual(block["missed_total"], sum(row["false_negatives"] for row in per.values()))
        self.assertEqual(block["false_alarm_total"], sum(row["false_positives"] for row in per.values()))
        kinds = sorted((i["kind"], i["asset_id"], i["label"]) for i in block["items"])
        self.assertEqual(
            kinds,
            [
                ("false_alarm", "p1", "a"),
                ("false_alarm", "p2", "a"),
                ("false_alarm", "p3", "b"),
                ("missed", "p1", "a"),
                ("missed", "p2", "b"),
            ],
        )
        miss = next(i for i in block["items"] if i["kind"] == "missed" and i["asset_id"] == "p1")
        self.assertEqual(miss["box"], list(FAR))  # the person-drawn box that was not found
        self.assertIsNone(miss["score"])
        alarm = next(i for i in block["items"] if i["kind"] == "false_alarm" and i["asset_id"] == "p3")
        self.assertEqual((alarm["box"], alarm["score"]), (list(FAR), 0.6))

    def test_mismatched_picture_ids_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            ev.evaluate_detections([[]], [[]], ["a"], asset_ids=["one", "two"])

    def test_stored_outcomes_are_capped_per_kind_and_keep_the_true_totals(self) -> None:
        n = ev.MAX_STORED_OUTCOMES_PER_KIND + 20
        ground_truth = [[("a", BOX)] for _ in range(n)] + [[] for _ in range(n)]
        predictions = [[] for _ in range(n)] + [[("a", FAR, 0.9)] for _ in range(n)]
        block = ev.evaluate_detections(ground_truth, predictions, ["a"], asset_ids=[str(i) for i in range(2 * n)])[
            "held_out_outcomes"
        ]
        self.assertEqual((block["missed_total"], block["false_alarm_total"]), (n, n))
        self.assertEqual(len(block["items"]), 2 * ev.MAX_STORED_OUTCOMES_PER_KIND)


# ---------------------------------------------------------------------------------------------------
class SentenceTests(SimpleTestCase):
    def sentence(self, n, found, wrong, label="dent"):
        return report.label_sentence(label, n, found, wrong)

    def test_found_some_with_extra_or_misplaced_boxes(self) -> None:
        self.assertEqual(
            self.sentence(10, 9, 2),
            "Found 9 of 10 “dent” boxes; drew 2 extra or misplaced boxes.",
        )

    def test_singular_forms(self) -> None:
        self.assertEqual(self.sentence(1, 1, 1), "Found 1 of 1 “dent” box; drew 1 extra or misplaced box.")

    def test_found_none_and_no_extra_boxes(self) -> None:
        self.assertEqual(self.sentence(10, 0, 0), "Found none of 10 “dent” boxes; drew no extra boxes.")

    def test_found_all(self) -> None:
        self.assertEqual(self.sentence(4, 4, 0), "Found all 4 “dent” boxes; drew no extra boxes.")

    def test_the_sentence_never_claims_the_extra_boxes_had_another_label(self) -> None:
        # a duplicate on a true "x" box plus a mislocated "x" box: both extra boxes ARE labelled "x"
        ground_truth = [[("x", BOX), ("x", FAR)]]
        predictions = [[("x", BOX, 0.9), ("x", BOX, 0.8), ("x", (0.0, 0.7, 0.2, 0.2), 0.7)]]
        evaluation = ev.evaluate_detections(ground_truth, predictions, ["x"], asset_ids=["p1"])
        row = evaluation["per_label"]["x"]
        self.assertEqual((row["true_positives"], row["false_positives"], row["ground_truth_boxes"]), (1, 2, 2))
        sentence = report.plain_results(evaluation)["labels"][0]["sentence"]
        self.assertEqual(sentence, "Found 1 of 2 “x” boxes; drew 2 extra or misplaced boxes.")
        self.assertNotIn("were not", sentence)
        self.assertNotIn("was not", sentence)

    def test_headline_and_reasons_do_not_call_extra_boxes_wrong_without_saying_what_that_means(self) -> None:
        for labels in ({"d": (30, 30, 30)}, {"d": (30, 20, 1)}, {"d": (30, 30, 0)}, {"d": (8, 0, 2)}):
            evaluation, _ = synthetic_evaluation(labels)
            overall = report.plain_results(evaluation)["overall"]
            text = " ".join([overall["headline"], *overall["reasons"]])
            with self.subTest(labels=labels):
                self.assertNotIn("wrong box", text)
        evaluation, _ = synthetic_evaluation({"d": (30, 30, 2)})
        self.assertIn("2 extra or misplaced boxes", report.plain_results(evaluation)["overall"]["reasons"][0])

    def test_a_label_with_no_test_boxes_says_so(self) -> None:
        self.assertEqual(
            self.sentence(0, 0, 0), "There were no “dent” boxes in the test pictures, so nothing can be said about it."
        )
        self.assertEqual(
            self.sentence(0, 0, 3),
            "There were no “dent” boxes in the test pictures, so nothing can be said about it. "
            "It still drew 3 “dent” boxes.",
        )

    def test_label_text_is_passed_through_untouched(self) -> None:
        self.assertIn("<b>x</b>", self.sentence(3, 1, 0, label="<b>x</b>"))  # the page escapes it, not this layer


# ---------------------------------------------------------------------------------------------------
class VerdictRuleTests(SimpleTestCase):
    """Named, conservative thresholds; the numbers come only from a (synthetic) stored evaluation."""

    def plain(self, labels, **kw):
        evaluation, _ = synthetic_evaluation(labels, **kw)
        return report.plain_results(evaluation)

    def test_thresholds_are_named_and_ordered(self) -> None:
        self.assertEqual(report.MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT, 30)
        self.assertLess(0, report.RECALL_FLOOR_NOT_READY)
        self.assertLess(report.RECALL_FLOOR_NOT_READY, report.RECALL_FLOOR_GOOD_ENOUGH)
        self.assertLessEqual(report.RECALL_FLOOR_GOOD_ENOUGH, 1)
        self.assertEqual(report.MISSES_CAP, 24)

    def test_perfect_with_enough_boxes_is_good_enough_to_try(self) -> None:
        plain = self.plain({"dent": (30, 30, 0), "scratch": (30, 30, 0)})
        self.assertEqual(plain["overall"]["verdict"], "good_enough_to_try")
        self.assertEqual((plain["overall"]["found"], plain["overall"]["of"], plain["overall"]["wrong_boxes"]), (60, 60, 0))

    def test_zero_recall_is_not_ready_even_with_few_boxes(self) -> None:
        plain = self.plain({"dent": (8, 0, 0)})
        self.assertEqual(plain["overall"]["verdict"], "not_ready")
        self.assertIn("none", plain["overall"]["headline"].lower())

    def test_low_recall_with_enough_boxes_is_not_ready(self) -> None:
        plain = self.plain({"dent": (40, 10, 0)})  # 25%
        self.assertEqual(plain["overall"]["verdict"], "not_ready")

    def test_low_recall_with_few_boxes_cannot_be_told(self) -> None:
        plain = self.plain({"dent": (10, 3, 0)})
        self.assertEqual(plain["overall"]["verdict"], "cannot_tell")

    def test_between_the_floors_needs_more_examples(self) -> None:
        plain = self.plain({"dent": (50, 33, 0)})  # 66%
        self.assertEqual(plain["overall"]["verdict"], "needs_more_examples")

    def test_a_good_overall_number_cannot_hide_one_weak_label(self) -> None:
        plain = self.plain({"dent": (60, 60, 0), "scratch": (30, 15, 0)})  # overall 83%, scratch 50%
        self.assertEqual(plain["overall"]["verdict"], "needs_more_examples")
        self.assertTrue(any("scratch" in reason for reason in plain["overall"]["reasons"]))

    def test_many_wrong_boxes_stop_it_being_good_enough(self) -> None:
        plain = self.plain({"dent": (30, 30, 30)})  # finds everything but half its boxes are wrong
        self.assertEqual(plain["overall"]["verdict"], "needs_more_examples")

    def test_high_recall_with_too_few_boxes_cannot_be_told(self) -> None:
        plain = self.plain({"dent": (10, 10, 0)})
        self.assertEqual(plain["overall"]["verdict"], "cannot_tell")

    def test_a_label_without_test_boxes_blocks_good_enough(self) -> None:
        plain = self.plain({"dent": (30, 30, 0), "scratch": (0, 0, 0)})
        self.assertEqual(plain["overall"]["verdict"], "cannot_tell")
        self.assertTrue(any("scratch" in reason for reason in plain["overall"]["reasons"]))

    def test_boundary_exactly_on_the_upper_floor_is_good_enough(self) -> None:
        found = round(100 * report.RECALL_FLOOR_GOOD_ENOUGH)
        plain = self.plain({"dent": (100, found, 0)})
        self.assertEqual(plain["overall"]["verdict"], "good_enough_to_try")

    def test_boundary_exactly_on_the_lower_floor_is_not_not_ready(self) -> None:
        found = round(100 * report.RECALL_FLOOR_NOT_READY)
        plain = self.plain({"dent": (100, found, 0)})
        self.assertEqual(plain["overall"]["verdict"], "needs_more_examples")

    def test_forbidden_words_and_jargon_never_appear(self) -> None:
        for labels in ({"d": (30, 30, 0)}, {"d": (8, 0, 0)}, {"d": (50, 33, 2)}, {"d": (3, 3, 0)}):
            plain = self.plain(labels)
            text = " ".join([plain["overall"]["headline"], *plain["overall"]["reasons"], *[l["sentence"] for l in plain["labels"]]])
            with self.subTest(labels=labels):
                self.assertIsNone(re.search(r"\b(accurate|accuracy|certified|safe|precision|recall|IoU|AP50)\b", text, re.I), text)

    def test_every_headline_ends_with_the_not_a_final_decision_line(self) -> None:
        for labels in ({"d": (30, 30, 0)}, {"d": (8, 0, 0)}, {"d": (50, 33, 0)}, {"d": (3, 3, 0)}):
            self.assertTrue(self.plain(labels)["overall"]["headline"].endswith(report.NOT_FINAL_DECISION))
        self.assertTrue(report.plain_results({"available": False, "reason": "x"})["overall"]["headline"].endswith(report.NOT_FINAL_DECISION))

    def test_the_not_final_line_is_the_one_the_check_screen_already_shows(self) -> None:
        from pathlib import Path

        html = (Path(__file__).resolve().parent / "templates" / "projects" / "checkscreen.html").read_text()
        text = re.sub(r"<[^>]+>", "", html)
        self.assertIn(report.NOT_FINAL_DECISION, text)

    def test_malformed_stored_evaluation_is_treated_as_unavailable(self) -> None:
        plain = report.plain_results({"available": True, "data": {}, "overall": None})
        self.assertFalse(plain["evaluated"])
        self.assertEqual(plain["overall"]["verdict"], "cannot_tell")
        self.assertFalse(plain["misses"]["misses_available"])


# ---------------------------------------------------------------------------------------------------
def counts_evaluation(labels: dict[str, tuple[int, int, int]]) -> dict:
    """A stored-shape evaluation built straight from counts: label -> (test boxes, found, extra or misplaced).

    Rounded ratios are produced by the product's own ``_ratio``, so a stored 0.85 may stand for 0.84996."""
    per_label, total = {}, {"gt": 0, "tp": 0, "fp": 0}
    for label, (gt, tp, fp) in labels.items():
        per_label[label] = {
            "ground_truth_boxes": gt, "predictions_at_threshold": tp + fp, "true_positives": tp,
            "false_positives": fp, "false_negatives": gt - tp, "precision": ev._ratio(tp, tp + fp),
            "recall": ev._ratio(tp, gt) if gt else None, "ap50": None,
        }
        total["gt"] += gt
        total["tp"] += tp
        total["fp"] += fp
    return {
        "schema": ev.SCHEMA, "available": total["gt"] > 0, "split": "test", "iou_threshold": 0.5,
        "confidence_threshold": 0.5,
        "data": {"images": max(1, total["gt"]), "boxes": total["gt"], "boxes_per_label": {k: v[0] for k, v in labels.items()}},
        "per_label": per_label,
        "overall": {
            "precision": ev._ratio(total["tp"], total["tp"] + total["fp"]),
            "recall": ev._ratio(total["tp"], total["gt"]) if total["gt"] else None,
            "true_positives": total["tp"], "false_positives": total["fp"], "false_negatives": total["gt"] - total["tp"],
            "map50": None,
        },
        "limits": [],
    }


# ---------------------------------------------------------------------------------------------------
class VerdictFromCountsTests(SimpleTestCase):
    """Floors are applied to the counts, never to the stored (rounded) ratios; a known failure is never hidden."""

    def verdict(self, labels) -> str:
        return report.plain_results(counts_evaluation(labels))["overall"]["verdict"]

    def test_one_failing_label_is_not_hidden_by_a_small_label_that_did_well(self) -> None:
        plain = report.plain_results(counts_evaluation({"A": (100, 0, 0), "B": (5, 5, 0)}))
        self.assertEqual(plain["overall"]["verdict"], "not_ready")
        self.assertIn("“A”", plain["overall"]["headline"])
        self.assertEqual(self.verdict({"A": (40, 10, 0), "B": (5, 5, 0)}), "not_ready")
        self.assertEqual(self.verdict({"A": (40, 10, 0), "B": (0, 0, 0)}), "not_ready")

    def test_a_small_weak_label_alone_still_cannot_be_told(self) -> None:
        self.assertEqual(self.verdict({"A": (100, 100, 0), "B": (5, 0, 0)}), "cannot_tell")
        self.assertEqual(self.verdict({"A": (10, 3, 0)}), "cannot_tell")

    def test_a_label_exactly_on_the_lower_floor_is_not_a_known_failure(self) -> None:
        self.assertEqual(self.verdict({"A": (100, 50, 0), "B": (5, 5, 0)}), "cannot_tell")

    def test_exact_boundaries_on_the_good_enough_floor(self) -> None:
        self.assertEqual(self.verdict({"d": (100, 85, 0)}), "good_enough_to_try")
        self.assertEqual(self.verdict({"d": (100, 84, 0)}), "needs_more_examples")

    def test_exact_boundaries_on_the_not_ready_floor(self) -> None:
        self.assertEqual(self.verdict({"d": (100, 50, 0)}), "needs_more_examples")
        self.assertEqual(self.verdict({"d": (100, 49, 0)}), "not_ready")

    def test_a_stored_ratio_that_rounds_up_to_the_good_enough_floor_does_not_pass(self) -> None:
        evaluation = counts_evaluation({"d": (100000, 84996, 0)})
        self.assertEqual(evaluation["overall"]["recall"], 0.85)  # what is stored: rounded up
        self.assertEqual(evaluation["per_label"]["d"]["recall"], 0.85)
        plain = report.plain_results(evaluation)
        self.assertEqual(plain["overall"]["verdict"], "needs_more_examples")
        self.assertNotIn("(85%)", plain["overall"]["headline"])  # never shows a rounded-up share as meeting the floor

    def test_a_stored_ratio_that_rounds_up_to_the_not_ready_floor_does_not_escape(self) -> None:
        evaluation = counts_evaluation({"d": (100000, 49999, 0)})
        self.assertEqual(evaluation["overall"]["recall"], 0.5)
        self.assertEqual(report.plain_results(evaluation)["overall"]["verdict"], "not_ready")

    def test_exact_boundaries_on_the_precision_floor(self) -> None:
        self.assertEqual(self.verdict({"d": (70, 70, 30)}), "good_enough_to_try")  # exactly 70% of drawn boxes right
        self.assertEqual(self.verdict({"d": (70, 70, 31)}), "needs_more_examples")
        evaluation = counts_evaluation({"d": (69999, 69999, 30001)})
        self.assertEqual(evaluation["overall"]["precision"], 0.7)  # stored rounded up
        self.assertEqual(report.plain_results(evaluation)["overall"]["verdict"], "needs_more_examples")

    def test_a_good_label_cannot_be_rescued_by_rounding_either(self) -> None:
        self.assertEqual(self.verdict({"a": (100000, 84996, 0), "b": (100000, 100000, 0)}), "needs_more_examples")


# ---------------------------------------------------------------------------------------------------
class InconsistentStoredNumbersTests(SimpleTestCase):
    """Numbers that do not add up make the evaluation unreadable; they can never produce a good verdict."""

    def corrupt(self, mutate) -> dict:
        evaluation = counts_evaluation({"dent": (40, 40, 0), "scratch": (40, 40, 0)})
        mutate(evaluation)
        return evaluation

    def assert_unreadable(self, evaluation) -> None:
        plain = report.plain_results(evaluation)
        self.assertFalse(plain["evaluated"])
        self.assertIn("could not be read", plain["reason_if_not"])
        self.assertEqual(plain["overall"]["verdict"], "cannot_tell")
        self.assertFalse(plain["misses"]["misses_available"])
        self.assertEqual(plain["labels"], [])

    def test_the_untouched_fixture_is_good_enough(self) -> None:
        plain = report.plain_results(self.corrupt(lambda e: None))
        self.assertTrue(plain["evaluated"])
        self.assertEqual(plain["overall"]["verdict"], "good_enough_to_try")

    def test_found_more_than_there_were(self) -> None:
        def mutate(e):
            e["per_label"]["dent"]["true_positives"] = 99
            e["overall"]["true_positives"] = 139

        self.assert_unreadable(self.corrupt(mutate))

    def test_found_and_missed_do_not_add_up_to_the_test_boxes(self) -> None:
        self.assert_unreadable(self.corrupt(lambda e: e["per_label"]["dent"].update(false_negatives=3)))

    def test_negative_counts(self) -> None:
        self.assert_unreadable(self.corrupt(lambda e: e["per_label"]["dent"].update(false_positives=-1)))

        def mutate(e):
            e["per_label"]["dent"].update(true_positives=-5, false_negatives=45)

        self.assert_unreadable(self.corrupt(mutate))

    def test_booleans_floats_and_strings_are_not_counts(self) -> None:
        for bad in (True, 40.0, "40", None):
            with self.subTest(bad=bad):
                self.assert_unreadable(self.corrupt(lambda e: e["per_label"]["dent"].update(true_positives=bad)))
                self.assert_unreadable(self.corrupt(lambda e: e["per_label"]["dent"].update(false_positives=bad)))
                self.assert_unreadable(self.corrupt(lambda e: e["per_label"]["dent"].update(ground_truth_boxes=bad)))

    def test_an_overall_ratio_outside_zero_to_one(self) -> None:
        self.assert_unreadable(self.corrupt(lambda e: e["overall"].update(recall=7)))
        self.assert_unreadable(self.corrupt(lambda e: e["overall"].update(precision=-0.2)))
        self.assert_unreadable(self.corrupt(lambda e: e["per_label"]["dent"].update(recall=True)))

    def test_a_stored_ratio_that_disagrees_with_its_counts(self) -> None:
        self.assert_unreadable(self.corrupt(lambda e: e["overall"].update(recall=0.5)))

    def test_overall_counts_that_are_not_the_sum_of_the_labels(self) -> None:
        self.assert_unreadable(self.corrupt(lambda e: e["overall"].update(false_positives=3)))
        self.assert_unreadable(self.corrupt(lambda e: e["data"].update(boxes=999)))
        self.assert_unreadable(self.corrupt(lambda e: e["data"].update(images=-1)))

    def test_available_but_no_test_boxes_at_all(self) -> None:
        self.assert_unreadable(
            {**counts_evaluation({"dent": (0, 0, 0)}), "available": True}
        )

    def test_a_non_dict_label_row_or_per_label(self) -> None:
        self.assert_unreadable(self.corrupt(lambda e: e["per_label"].update(dent=[1, 2])))
        self.assert_unreadable(self.corrupt(lambda e: e.update(per_label=[])))


# ---------------------------------------------------------------------------------------------------
class VerdictPropertyTests(SimpleTestCase):
    """Seeded fuzz over synthetic evaluations: whatever the numbers, a good verdict needs the real evidence."""

    FORBIDDEN = re.compile(r"\b(accurate|accuracy|certif\w*|safe|safely|guarantee\w*|precision|recall|IoU|AP50)\b", re.I)

    @staticmethod
    def exact_ok(plain_labels, overall) -> bool:
        from fractions import Fraction

        if overall["of"] == 0 or Fraction(overall["found"], overall["of"]) < Fraction(85, 100):
            return False
        return all(row["test_boxes"] >= 30 and row["found"] > 0 and Fraction(row["found"], row["test_boxes"]) >= Fraction(85, 100)
                   for row in plain_labels)

    def random_labels(self, rng) -> dict:
        labels = {}
        for index in range(rng.randint(1, 3)):
            gt = rng.choice([0, 1, 5, 29, 30, 31, 45, 80, rng.randint(0, 120)])
            mode = rng.random()
            tp = gt if mode < 0.3 else rng.randint(0, gt) if mode < 0.55 else max(0, gt - rng.randint(0, 6))
            fp = rng.choice([0, 0, 1, rng.randint(0, 60)])
            labels[f"l{index}"] = (gt, tp, fp)
        return labels

    def corrupt(self, rng, evaluation) -> None:
        row = evaluation["per_label"][rng.choice(list(evaluation["per_label"]))]
        field = rng.choice(["ground_truth_boxes", "true_positives", "false_positives", "false_negatives", "recall", "precision"])
        row[field] = rng.choice([-3, 10**6, True, 2.5, "9", None, 7, row[field] if isinstance(row[field], int) else 0])
        if rng.random() < 0.4:
            evaluation["overall"][rng.choice(["recall", "precision", "true_positives", "false_negatives", "false_positives"])] = rng.choice([7, -1, True, 99999, None, 0.99])

    def test_good_enough_only_with_the_real_evidence_and_no_forbidden_words(self) -> None:
        import copy
        import random

        rng = random.Random(20261023)
        good = 0
        for case in range(4000):
            evaluation = counts_evaluation(self.random_labels(rng))
            damaged = rng.random() < 0.35
            if damaged:
                self.corrupt(rng, evaluation)
            pristine = copy.deepcopy(evaluation)
            plain = report.plain_results(evaluation)
            self.assertEqual(evaluation, pristine)  # the stored document is never touched
            overall = plain["overall"]
            self.assertIn(overall["verdict"], report.VERDICTS)
            text = " ".join([overall["headline"], *overall["reasons"], *[row["sentence"] for row in plain["labels"]]])
            self.assertIsNone(self.FORBIDDEN.search(text), text)
            if overall["verdict"] != "good_enough_to_try":
                continue
            good += 1
            with self.subTest(case=case):
                self.assertTrue(plain["evaluated"])
                self.assertTrue(self.exact_ok(plain["labels"], overall), evaluation)
                self.assertGreaterEqual(overall["found"] * 10, (overall["found"] + overall["wrong_boxes"]) * 7)  # >= 70% right
                if damaged:  # damaged numbers may only pass if the damage left every identity intact
                    for name, row in evaluation["per_label"].items():
                        for key in ("ground_truth_boxes", "true_positives", "false_positives", "false_negatives"):
                            self.assertIs(type(row[key]), int, (name, key))
                        self.assertEqual(row["true_positives"] + row["false_negatives"], row["ground_truth_boxes"])
        self.assertGreater(good, 100)  # the property is not vacuous

    def test_every_damaged_evaluation_that_breaks_an_identity_is_unreadable(self) -> None:
        import random

        rng = random.Random(7)
        for case in range(1500):
            evaluation = counts_evaluation({"a": (40, rng.randint(0, 40), rng.randint(0, 5)), "b": (35, 35, 0)})
            row = evaluation["per_label"]["a"]
            kind = rng.choice(["tp_over", "negative", "bool", "sum", "ratio"])
            if kind == "tp_over":
                row["true_positives"] = row["ground_truth_boxes"] + rng.randint(1, 50)
            elif kind == "negative":
                row["false_positives"] = -rng.randint(1, 9)
            elif kind == "bool":
                row["true_positives"] = bool(rng.randint(0, 1))
            elif kind == "sum":
                row["false_negatives"] = row["ground_truth_boxes"] - row["true_positives"] + rng.randint(1, 5)
            else:
                evaluation["overall"]["recall"] = rng.choice([7, 1.5, -0.1])
            with self.subTest(case=case, kind=kind):
                plain = report.plain_results(evaluation)
                self.assertFalse(plain["evaluated"])
                self.assertEqual(plain["overall"]["verdict"], "cannot_tell")


# ---------------------------------------------------------------------------------------------------
def picture_id(index: int) -> str:
    return str(uuid.UUID(int=index + 1))


def picture_name(asset_id: str) -> str:
    return f"p{uuid.UUID(asset_id).int - 1:02d}"


def multi_box_evaluation(pictures: list[dict], labels=("a",)):
    """One evaluation from hand-made pictures. Each picture: {"gt": [(label, box)], "pred": [(label, box, score)]}.

    Picture ids are picture_id(0), picture_id(1), ... (shown as p00, p01, ...) in order. Returns (evaluation, ids)."""
    ids = [picture_id(index) for index in range(len(pictures))]
    result = ev.evaluate_detections(
        [list(pic.get("gt", [])) for pic in pictures],
        [list(pic.get("pred", [])) for pic in pictures],
        list(labels),
        asset_ids=ids,
    )
    return result, ids


def distinct_runs(items) -> list[str]:
    runs: list[str] = []
    for item in items:
        if not runs or runs[-1] != picture_name(item["asset_id"]):
            runs.append(picture_name(item["asset_id"]))
    return runs


class MissesByPictureTests(SimpleTestCase):
    """The list counts and orders PICTURES honestly: boxes of one picture sit together, false alarms are not all last."""

    def scenario(self):
        pictures = [{"gt": [("a", BOX)] * 3} for _ in range(5)]  # p00-p04: three missed boxes each
        pictures += [{"gt": [("a", BOX)]}, {"gt": [("a", BOX)]}]  # p05, p06: one missed box each
        pictures += [{"pred": [("a", FAR, 0.9)]}]  # p07: one false alarm
        pictures += [{"pred": [("a", FAR, 0.8), ("a", BOX, 0.7)]}]  # p08: two false alarms
        pictures += [{"gt": [("a", BOX)], "pred": [("a", FAR, 0.95)]}]  # p09: a missed box AND a false alarm
        return multi_box_evaluation(pictures)

    def misses(self, evaluation, known=None):
        plain = report.plain_results(evaluation, known_asset_ids=known)
        self.assertTrue(plain["evaluated"], plain)
        return plain["misses"]

    def test_pictures_are_counted_as_pictures_not_boxes(self) -> None:
        evaluation, ids = self.scenario()
        misses = self.misses(evaluation)
        self.assertEqual(misses["total"], 22)  # boxes: 18 missed + 4 false alarms
        self.assertEqual(len(misses["items"]), 22)
        self.assertEqual(misses["pictures"], {"total": 10, "shown": 10, "exact": True})
        self.assertEqual(misses["distinct_pictures"], 10)
        self.assertEqual(len({i["asset_id"] for i in misses["items"]}), 10)

    def test_a_picture_is_listed_once_with_its_boxes_together(self) -> None:
        evaluation, ids = self.scenario()
        items = self.misses(evaluation)["items"]
        runs = distinct_runs(items)
        self.assertEqual(len(runs), len(set(runs)), runs)  # no picture comes back later
        both = [i for i in items if picture_name(i["asset_id"]) == "p09"]
        self.assertEqual([i["kind"] for i in both], ["missed", "false_alarm"])  # misses first inside a picture

    def test_false_alarms_are_interleaved_by_picture_not_listed_last(self) -> None:
        evaluation, ids = self.scenario()
        items = self.misses(evaluation)["items"]
        # worst missed picture, worst false-alarm picture, next missed picture, next false-alarm picture, ...
        self.assertEqual(distinct_runs(items), ["p00", "p09", "p01", "p07", "p02", "p08", "p03", "p04", "p05", "p06"])
        kinds = [i["kind"] for i in items]
        self.assertLess(kinds.index("false_alarm"), len(kinds) - kinds[::-1].index("missed") - 1)

    def test_within_each_kind_the_worst_first_order_is_unchanged(self) -> None:
        """The order inside misses and inside false alarms is the one the evaluation stored (one shared sort)."""
        evaluation, ids = synthetic_evaluation({"dent": (60, 50, 20), "scratch": (60, 20, 20)})
        stored = evaluation["held_out_outcomes"]["items"]
        listed = report.plain_results(evaluation)["misses"]["items"]
        for kind in ("missed", "false_alarm"):
            from_store = [(i["asset_id"], i["label"], i["score"]) for i in stored if i["kind"] == kind]
            shown = [(i["asset_id"], i["label"], i["score"]) for i in listed if i["kind"] == kind]
            self.assertEqual(shown, from_store[: len(shown)], kind)
            self.assertEqual(len(shown), 12)

    def test_the_cap_is_on_boxes_and_the_shown_pictures_are_counted_honestly(self) -> None:
        pictures = [{"gt": [("a", BOX)] * 3} for _ in range(20)]  # 60 missed boxes on 20 pictures
        pictures += [{"pred": [("a", FAR, 0.9 - 0.001 * n)] * 2} for n in range(20)]  # 40 false alarms on 20 pictures
        evaluation, ids = multi_box_evaluation(pictures)
        misses = self.misses(evaluation)
        self.assertEqual((misses["capped_at"], len(misses["items"]), misses["total"]), (24, 24, 100))
        kinds = [i["kind"] for i in misses["items"]]
        self.assertEqual((kinds.count("missed"), kinds.count("false_alarm")), (12, 12))  # the 12 / 12 floor holds
        self.assertEqual(misses["pictures"], {"total": 40, "shown": len({i["asset_id"] for i in misses["items"]}), "exact": True})
        self.assertLessEqual(misses["pictures"]["shown"], 24)
        self.assertLess(misses["pictures"]["shown"], misses["pictures"]["total"])

    def test_pictures_that_left_the_project_are_not_counted(self) -> None:
        evaluation, ids = self.scenario()
        misses = self.misses(evaluation, known={picture_id(0), picture_id(9), picture_id(7)})
        self.assertEqual(misses["pictures"], {"total": 3, "shown": 3, "exact": True})
        self.assertEqual(misses["distinct_pictures"], 3)
        self.assertEqual(misses["no_longer_in_project"], 22 - len(misses["items"]))

    def test_a_stored_list_that_was_cut_short_says_the_picture_total_is_a_lower_bound(self) -> None:
        n = ev.MAX_STORED_OUTCOMES_PER_KIND + 30
        evaluation, ids = multi_box_evaluation([{"gt": [("a", BOX)]} for _ in range(n)])
        misses = self.misses(evaluation)
        self.assertEqual(misses["total"], n)
        self.assertEqual(misses["pictures"]["total"], ev.MAX_STORED_OUTCOMES_PER_KIND)
        self.assertFalse(misses["pictures"]["exact"])

    def test_nothing_to_list_has_zero_pictures(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 30, 0)})
        misses = report.plain_results(evaluation)["misses"]
        self.assertEqual(
            (misses["items"], misses["pictures"], misses["distinct_pictures"], misses["total"]),
            ([], {"total": 0, "shown": 0, "exact": True}, 0, 0),
        )


# ---------------------------------------------------------------------------------------------------
class SharedNumbersTests(SimpleTestCase):
    def test_the_verdict_number_is_the_one_in_evaluation(self) -> None:
        self.assertEqual(report.MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT, ev.MIN_BOXES_PER_LABEL_FOR_VERDICT)

    def test_the_verdict_number_is_not_written_a_second_time(self) -> None:
        from pathlib import Path

        text = (Path(__file__).resolve().parent / "training" / "report.py").read_text()
        self.assertIsNone(re.search(r"^MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT = \d+", text, re.M))

    def test_the_worst_first_order_is_written_once(self) -> None:
        from pathlib import Path

        here = Path(__file__).resolve().parent / "training"
        for name in ("report.py", "evaluation.py"):
            text = (here / name).read_text()
            self.assertEqual(text.count("-(item[\"score\"] or 0.0)") + text.count("-(i[\"score\"] or 0.0)"), 1 if name == "evaluation.py" else 0, name)
        self.assertTrue(callable(ev.sort_worst_first))

    def test_the_results_route_reuses_the_project_scoped_model_lookup(self) -> None:
        from . import results_views, training_views

        self.assertIs(results_views._get_model, training_views._get_model)


# ---------------------------------------------------------------------------------------------------
class DamagedShapesTests(SimpleTestCase):
    """Whatever was stored, a page gets an answer: lists are lists or they are treated as empty."""

    JUNK = [None, 5, 2.5, True, "text", {}, {"a": 1}, [], [1, "two", None], [[]], ["ok", 3]]

    def test_unavailable_evaluation_with_a_non_list_limits_does_not_raise(self) -> None:
        for junk in self.JUNK:
            with self.subTest(limits=junk):
                plain = report.plain_results({"available": False, "limits": junk})
                self.assertFalse(plain["evaluated"])
                self.assertIsInstance(plain["limits"], list)
                self.assertIsInstance(report.build_report({"available": False, "limits": junk})["limits"], list)

    def test_the_regression_from_the_review(self) -> None:
        self.assertEqual(report.plain_results({"available": False, "limits": 5})["limits"], [])

    def test_fuzz_of_every_stored_list(self) -> None:
        import copy
        import random

        rng = random.Random(23)
        base, ids = synthetic_evaluation({"dent": (30, 20, 3), "scratch": (31, 31, 0)})
        for case in range(600):
            evaluation = copy.deepcopy(base)
            where = rng.choice(["limits", "per_label", "items", "outcomes", "data", "overall", "whole", "unavailable"])
            junk = rng.choice(self.JUNK)
            if where == "limits":
                evaluation["limits"] = junk
            elif where == "per_label":
                evaluation["per_label"] = junk
            elif where == "items":
                evaluation["held_out_outcomes"]["items"] = junk
            elif where == "outcomes":
                evaluation["held_out_outcomes"] = junk
            elif where == "data":
                evaluation["data"] = junk
            elif where == "overall":
                evaluation["overall"] = junk
            elif where == "whole":
                evaluation = junk
            else:
                evaluation = {"available": False, "reason": junk, "limits": rng.choice(self.JUNK), "data": rng.choice(self.JUNK)}
            known = rng.choice([None, set(), set(ids[:5])])
            with self.subTest(case=case, where=where, junk=junk):
                plain = report.plain_results(evaluation, known_asset_ids=known)
                self.assertIn(plain["overall"]["verdict"], report.VERDICTS)
                self.assertIsInstance(plain["limits"], list)
                self.assertIsInstance(plain["misses"]["items"], list)
                self.assertIsInstance(plain["misses"]["pictures"]["total"], int)
                report.stored_outcome_asset_ids(evaluation if isinstance(evaluation, dict) else None)
                report.summary(evaluation if isinstance(evaluation, dict) else None)
                self.assertIsInstance(report.build_report(evaluation if not where == "whole" else junk)["limits"], list) if where in ("limits", "unavailable", "whole") else None


# ---------------------------------------------------------------------------------------------------
class ResultsPlainEndpointTests(TrainingBase):
    def make_model(self, evaluation, *, project=None, labels=None, version=1) -> ModelVersion:
        project = project or self.project
        return ModelVersion.objects.create(
            project=project,
            version=version,
            backend="fake-backend",
            artifact_path=f"models/{project.id}/synthetic/model.bin",
            artifact_sha256="0" * 64,
            artifact_size_bytes=1,
            label_names=labels or list(evaluation.get("per_label", {})),
            evaluation=evaluation,
        )

    def url(self, model, project=None) -> str:
        return self.api(f"/models/{model.id}/results/plain", project)

    def register_pictures(self, ids) -> None:
        """Give every picture id in a synthetic evaluation a real (file-less) picture row in this project."""
        Asset.objects.bulk_create(
            Asset(
                id=pk, project=self.project, filename=f"{pk}.png", storage_key=f"synthetic/{pk}.png",
                content_type="image/png", sha256="0" * 64, size_bytes=1, width=40, height=30,
            )
            for pk in ids
        )

    def fetch(self, evaluation, ids=()) -> dict:
        self.register_pictures(ids)
        model = self.make_model(evaluation)
        response = self.client.get(self.url(model))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["model_id"], str(model.id))
        return body

    # -- shapes -----------------------------------------------------------------------------------
    def test_partial_synthetic_evaluation_gives_the_whole_contract(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 27, 2), "scratch": (40, 24, 5)})
        body = self.fetch(evaluation, ids)
        self.assertEqual(
            sorted(body),
            # the W4-T keys (operating_points, wording) and the W8-L2 key (comparison) are the only additions to
            # the earlier contract
            ["comparison", "confidence_used", "counts", "evaluated", "iou_used", "labels", "limits", "misses",
             "model_id", "operating_points", "overall", "reason_if_not", "wording"],
        )
        self.assertEqual(body["comparison"]["available"], False)  # hand-made: no comparison was saved
        self.assertEqual(body["operating_points"]["available"], False)  # this hand-made evaluation predates the key
        self.assertTrue(body["evaluated"])
        self.assertEqual(body["reason_if_not"], "")
        self.assertEqual(body["counts"], {"test_pictures": len(ids), "test_boxes": 70})
        self.assertEqual((body["confidence_used"], body["iou_used"]), (0.5, 0.5))
        self.assertEqual(body["limits"], evaluation["limits"])
        by_label = {row["label"]: row for row in body["labels"]}
        self.assertEqual(
            {k: by_label["dent"][k] for k in ("test_boxes", "found", "missed", "wrong_boxes")},
            {"test_boxes": 30, "found": 27, "missed": 3, "wrong_boxes": 2},
        )
        self.assertEqual(by_label["scratch"]["sentence"], "Found 24 of 40 “scratch” boxes; drew 5 extra or misplaced boxes.")
        overall = body["overall"]
        self.assertEqual((overall["found"], overall["of"], overall["wrong_boxes"]), (51, 70, 7))
        self.assertEqual(overall["verdict"], "needs_more_examples")
        self.assertEqual(set(body["misses"]), MISS_KEYS)
        self.assertEqual(body["misses"]["no_longer_in_project"], 0)
        self.assertTrue(body["misses"]["misses_available"])
        self.assertEqual(body["misses"]["total"], 19 + 7)
        for item in body["misses"]["items"]:
            self.assertEqual(set(item), {"asset_id", "kind", "label", "score", "box"})
            self.assertIn(item["kind"], {"missed", "false_alarm"})

    def test_perfect_synthetic_evaluation(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 30, 0)})
        body = self.fetch(evaluation, ids)
        self.assertEqual(body["overall"]["verdict"], "good_enough_to_try")
        self.assertEqual(body["misses"]["items"], [])
        self.assertTrue(body["misses"]["misses_available"])
        self.assertEqual(body["misses"]["total"], 0)

    def test_zero_recall_synthetic_evaluation(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (36, 0, 0)})
        body = self.fetch(evaluation, ids)
        self.assertEqual(body["overall"]["verdict"], "not_ready")
        self.assertEqual(body["labels"][0]["found"], 0)
        self.assertEqual(body["labels"][0]["sentence"], "Found none of 36 “dent” boxes; drew no extra boxes.")
        self.assertEqual({i["kind"] for i in body["misses"]["items"]}, {"missed"})

    def test_a_label_with_no_test_boxes_synthetic_evaluation(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 30, 0), "scratch": (0, 0, 2)})
        body = self.fetch(evaluation, ids)
        row = next(r for r in body["labels"] if r["label"] == "scratch")
        self.assertEqual((row["test_boxes"], row["found"], row["missed"], row["wrong_boxes"]), (0, 0, 0, 2))
        self.assertIn("no “scratch” boxes", row["sentence"])
        self.assertEqual(body["overall"]["verdict"], "cannot_tell")

    # -- no held-out pictures -----------------------------------------------------------------------
    def test_a_real_run_with_no_test_group_is_not_evaluated(self) -> None:
        model = self.train_candidate(self.make_dataset(with_test=False))
        body = self.client.get(self.url(model)).json()
        self.assertFalse(body["evaluated"])
        self.assertIn("test split has no images", body["reason_if_not"])
        self.assertEqual(body["overall"]["verdict"], "cannot_tell")
        self.assertEqual(body["labels"], [])
        self.assertEqual(body["counts"], {"test_pictures": 0, "test_boxes": 0})
        self.assertIsNone(body["confidence_used"])
        self.assertFalse(body["misses"]["misses_available"])
        self.assertEqual(body["misses"]["items"], [])
        self.assertEqual(body["overall"]["found"], 0)
        self.assertEqual(body["overall"]["of"], 0)

    def test_a_model_with_an_empty_evaluation_is_not_evaluated(self) -> None:
        body = self.fetch({})
        self.assertFalse(body["evaluated"])
        self.assertTrue(body["reason_if_not"])
        self.assertEqual(body["overall"]["verdict"], "cannot_tell")

    # -- honesty about per-picture misses -------------------------------------------------------------
    def test_the_real_training_path_stores_per_picture_outcomes_so_misses_are_available(self) -> None:
        # (what exactly is stored is pinned in tests_training_outcomes; this only checks the endpoint's reading)
        model = self.train_candidate(self.make_dataset(with_test=True))
        self.assertIn("held_out_outcomes", model.evaluation)
        body = self.client.get(self.url(model)).json()
        self.assertTrue(body["evaluated"])
        self.assertTrue(body["misses"]["misses_available"])
        self.assertEqual(body["misses"]["reason"], "")
        self.assertEqual(body["misses"]["capped_at"], report.MISSES_CAP)
        # the numbers still come straight from the stored evaluation
        stored = model.evaluation["overall"]
        self.assertEqual(body["overall"]["found"], stored["true_positives"])
        self.assertEqual(body["overall"]["wrong_boxes"], stored["false_positives"])

    def test_a_legacy_evaluation_without_the_block_says_per_picture_results_were_not_saved(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)}, with_outcomes=False)
        self.assertNotIn("held_out_outcomes", evaluation)
        body = self.fetch(evaluation, ids)
        self.assertFalse(body["misses"]["misses_available"])
        self.assertIn("not saved", body["misses"]["reason"])
        self.assertEqual((body["misses"]["items"], body["misses"]["total"]), ([], 0))
        self.assertEqual(body["misses"]["no_longer_in_project"], 0)
        self.assertEqual(body["labels"][0]["found"], 20)  # per-label numbers are still given

    def test_outcomes_that_disagree_with_the_stored_counts_are_not_shown(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        evaluation["held_out_outcomes"]["missed_total"] += 1
        body = self.fetch(evaluation, ids)
        self.assertFalse(body["misses"]["misses_available"])
        self.assertIn("do not match", body["misses"]["reason"])
        self.assertEqual(body["misses"]["items"], [])

    def test_malformed_outcomes_are_not_shown(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        evaluation["held_out_outcomes"] = {"items": "nope"}
        self.assertFalse(self.fetch(evaluation, ids)["misses"]["misses_available"])

    def test_huge_number_of_outcomes_is_capped_worst_first(self) -> None:
        # dent: 100 boxes, 90 found (90%); scratch: 100 boxes, 20 found (20%) -> scratch misses come first
        evaluation, ids = synthetic_evaluation({"dent": (100, 90, 40), "scratch": (100, 20, 40)})
        body = self.fetch(evaluation, ids)
        misses = body["misses"]
        self.assertEqual(misses["capped_at"], 24)
        self.assertEqual(len(misses["items"]), 24)
        self.assertEqual(misses["total"], 10 + 80 + 40 + 40)
        kinds = [i["kind"] for i in misses["items"]]
        self.assertEqual(kinds, ["missed", "false_alarm"] * 12)  # one box per picture here: they alternate
        missed = [i for i in misses["items"] if i["kind"] == "missed"]
        alarms = [i for i in misses["items"] if i["kind"] == "false_alarm"]
        self.assertEqual((len(missed), len(alarms)), (12, 12))  # still the fair 12 / 12 split
        self.assertTrue(all(i["label"] == "scratch" for i in missed))
        scores = [i["score"] for i in alarms]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(i["score"] is None for i in missed))
        self.assertEqual(misses["pictures"], {"total": 10 + 80 + 40 + 40, "shown": 24, "exact": True})
        self.assertTrue(all(len(i["box"]) == 4 for i in misses["items"]))

    def test_when_there_are_few_false_alarms_misses_use_the_spare_room(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (100, 20, 2)})
        items = self.fetch(evaluation, ids)["misses"]["items"]
        kinds = [i["kind"] for i in items]
        self.assertEqual((kinds.count("missed"), kinds.count("false_alarm")), (22, 2))  # misses use the spare room
        self.assertEqual(kinds[:4], ["missed", "false_alarm", "missed", "false_alarm"])
        self.assertEqual(kinds[4:], ["missed"] * 20)

    def test_pictures_not_in_this_project_are_never_listed(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 0)})
        mine = [self.make_asset() for _ in range(3)]  # only these three exist in this project
        keep = {str(a.id) for a in mine}
        replaced = 0
        for item in evaluation["held_out_outcomes"]["items"]:
            if replaced < 3:
                item["asset_id"] = str(mine[replaced].id)
                replaced += 1
        body = self.fetch(evaluation, [])
        shown = {i["asset_id"] for i in body["misses"]["items"]}
        self.assertEqual(shown, keep)
        self.assertEqual(body["misses"]["total"], 3)
        self.assertEqual(body["misses"]["no_longer_in_project"], 7)

    def test_when_every_stored_miss_belongs_to_a_deleted_picture_misses_are_not_available(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (40, 0, 40)})  # 80 stored outcomes: 40 missed, 40 extra
        body = self.fetch(evaluation, [])  # none of those pictures exist in the project any more
        misses = body["misses"]
        self.assertEqual((body["overall"]["found"], body["overall"]["of"], body["overall"]["wrong_boxes"]), (0, 40, 40))
        self.assertFalse(misses["misses_available"])
        self.assertEqual(misses["items"], [])
        self.assertEqual(misses["total"], 0)
        self.assertEqual(misses["no_longer_in_project"], 80)
        self.assertIn("no longer in the project", misses["reason"])
        self.assertEqual(set(misses), MISS_KEYS)
        self.assertEqual(misses["pictures"], {"total": 0, "shown": 0, "exact": True})
        self.assertEqual(misses["distinct_pictures"], 0)

    def test_no_longer_in_project_is_always_present_and_zero_when_nothing_was_dropped(self) -> None:
        for evaluation in (
            synthetic_evaluation({"dent": (30, 20, 1)})[0],  # available
            synthetic_evaluation({"dent": (30, 20, 1)}, with_outcomes=False)[0],  # legacy
            {},  # not evaluated
            {"available": True, "data": {}, "overall": None},  # unreadable
        ):
            misses = report.plain_results(evaluation, known_asset_ids=None)["misses"]
            self.assertEqual(misses["no_longer_in_project"], 0)
            self.assertEqual(set(misses), MISS_KEYS)

    # -- access -----------------------------------------------------------------------------------
    def test_a_viewer_can_read_it_and_it_is_not_cached(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        model = self.make_model(evaluation)
        self.user.groups.clear()
        grant(self.user, "viewer")
        response = self.client.get(self.url(model))
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])

    def test_anonymous_callers_are_refused_with_no_data(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        model = self.make_model(evaluation)
        response = Client().get(self.url(model))
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("dent", response.content.decode())

    def test_a_person_with_no_role_is_refused_like_the_evaluation_route(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        model = self.make_model(evaluation)
        self.user.groups.clear()
        same = self.client.get(self.api(f"/models/{model.id}/evaluation")).status_code
        self.assertEqual(self.client.get(self.url(model)).status_code, same)

    def test_a_model_from_another_project_is_a_plain_404(self) -> None:
        other = Project.objects.create(name="Other", modality="image", task="Object detection", labels=["dent"])
        evaluation, ids = synthetic_evaluation({"secretlabel": (30, 20, 1)})
        foreign = self.make_model(evaluation, project=other)
        response = self.client.get(self.url(foreign))  # this project's URL, the other project's model
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("secretlabel", response.content.decode())
        self.assertEqual(self.client.get(self.url(foreign, other)).status_code, 200)  # its own project can read it
        gone = self.client.get(self.api(f"/models/{uuid.uuid4()}/results/plain"))
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(gone.content, response.content)

    def test_it_is_read_only(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        model = self.make_model(evaluation)
        before = ModelVersion.objects.filter(pk=model.pk).values().get()
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)(self.url(model), data="{}", content_type="application/json")
            self.assertEqual(response.status_code, 405, method)
        self.assertEqual(ModelVersion.objects.filter(pk=model.pk).values().get(), before)

    def test_it_changes_no_stored_row(self) -> None:
        evaluation, ids = synthetic_evaluation({"dent": (30, 20, 1)})
        model = self.make_model(evaluation)
        before = ModelVersion.objects.filter(pk=model.pk).values().get()
        self.client.get(self.url(model))
        self.assertEqual(ModelVersion.objects.filter(pk=model.pk).values().get(), before)

    def test_the_route_is_declared_view_only(self) -> None:
        from django.urls import resolve

        match = resolve(self.url(self.make_model(synthetic_evaluation({"dent": (1, 1, 0)})[0])))
        self.assertEqual(match.func.athena_access, {"GET": "view"})
        self.assertIn(
            "api/projects/<uuid:project_id>/models/<uuid:model_id>/results/plain", tests_roles.EveryRouteDeclaresItsRole.DECLARED
        )


# ---------------------------------------------------------------------------------------------------
# W4-T: operating points and measured wording, added to the plain results (additive keys only).
PLAIN_KEYS_BEFORE = {
    "evaluated", "reason_if_not", "counts", "labels", "overall", "misses", "confidence_used", "iou_used", "limits",
}
NEW_PLAIN_KEYS = {"operating_points", "wording", "comparison"}  # "comparison": W8-L2


class PlainOperatingPointsTests(SimpleTestCase):
    def points(self) -> dict:
        from .tests_operating_points import synthetic_operating_points

        return synthetic_operating_points()

    def test_an_old_evaluation_gets_not_measured_and_every_old_key_is_unchanged(self) -> None:
        evaluation, _ = synthetic_evaluation({"a": (40, 38, 2)})
        plain = report.plain_results(evaluation)
        self.assertEqual(set(plain), PLAIN_KEYS_BEFORE | NEW_PLAIN_KEYS)
        self.assertEqual(plain["operating_points"]["available"], False)
        self.assertIn("not measured", plain["operating_points"]["reason"])
        self.assertEqual(set(plain["operating_points"]), {"available", "reason"})
        self.assertEqual(plain["wording"], {"available": False, "reason": plain["operating_points"]["reason"], "split": "Final test pictures", "buckets": []})
        with_points = report.plain_results({**evaluation, "operating_points": self.points()})
        self.assertEqual({k: v for k, v in with_points.items() if k not in NEW_PLAIN_KEYS},
                         {k: v for k, v in plain.items() if k not in NEW_PLAIN_KEYS})  # nothing else moves

    def test_stored_points_are_passed_through_and_each_range_gets_a_measured_sentence(self) -> None:
        evaluation, _ = synthetic_evaluation({"a": (40, 38, 2)})
        doc = self.points()
        plain = report.plain_results({**evaluation, "operating_points": doc})
        self.assertEqual(plain["operating_points"], {"available": True, **doc})
        wording = plain["wording"]
        self.assertTrue(wording["available"])
        self.assertEqual(len(wording["buckets"]), len(doc["bucket_precision"]))
        for stored, row in zip(doc["bucket_precision"], wording["buckets"]):
            self.assertEqual((row["low"], row["high"], row["predictions"], row["correct"]), (stored["low"], stored["high"], stored["predictions"], stored["correct"]))
            self.assertEqual(row["enough"], stored["predictions"] >= 20)
            self.assertTrue(row["sentence"].startswith(f"Scores between {stored['low']:g} and {stored['high']:g}"))
            if row["enough"]:
                self.assertIn(f"were right {stored['correct'] * 100 // stored['predictions']}% of the time on the Final test pictures ({stored['predictions']} boxes)", row["sentence"])
        text = " ".join(r["sentence"] for r in wording["buckets"]).lower()
        for word in ("accurate", "certified", "safe"):
            self.assertNotIn(word, text)

    def test_the_unavailable_result_has_the_new_keys_too(self) -> None:
        plain = report.plain_results({"available": False, "reason": "no test pictures"})
        self.assertEqual(set(plain), PLAIN_KEYS_BEFORE | NEW_PLAIN_KEYS)
        self.assertFalse(plain["operating_points"]["available"])
        self.assertFalse(plain["wording"]["available"])

    def test_a_damaged_block_says_it_could_not_be_read_and_never_raises(self) -> None:
        evaluation, _ = synthetic_evaluation({"a": (40, 38, 2)})
        for junk in (None, 7, "x", [], {}, {"schema": "other"}, {"schema": "athena.operating_points/1", "bucket_precision": 5},
                     {"schema": "athena.operating_points/1", "bucket_precision": [{"low": "x"}]}):
            plain = report.plain_results({**evaluation, "operating_points": junk})
            self.assertTrue(plain["evaluated"])
            self.assertFalse(plain["wording"]["available"], junk)
            if junk is not None:
                self.assertIn("could not be read", plain["wording"]["reason"])

    def test_with_no_final_test_pictures_the_wording_is_not_measured(self) -> None:
        from .tests_operating_points import synthetic_split
        from .training import operating_points as op

        gt, pred = synthetic_split(1, 120)
        doc = op.build_operating_points(gt, pred, None, None, ["a", "b"])
        evaluation, _ = synthetic_evaluation({"a": (40, 38, 2)})
        plain = report.plain_results({**evaluation, "operating_points": doc})
        self.assertTrue(plain["operating_points"]["available"])
        self.assertFalse(plain["wording"]["available"])
        self.assertIn("not measured", plain["wording"]["reason"])


class PlainOperatingPointsEndpointTests(TrainingBase):
    def test_the_endpoint_carries_the_new_keys_for_a_trained_and_an_old_model(self) -> None:
        model = self.train_candidate(self.make_dataset(), solid=False)
        url = self.api(f"/models/{model.id}/results/plain")
        body = self.client.get(url).json()
        self.assertEqual(body["operating_points"]["schema"], "athena.operating_points/1")
        self.assertFalse(body["operating_points"]["enough_data"])  # a 14-picture project is never enough
        self.assertTrue(body["wording"]["available"])
        self.assertTrue(all(not row["enough"] for row in body["wording"]["buckets"]))
        old = {k: v for k, v in model.evaluation.items() if k != "operating_points"}
        model = self.set_evaluation(model, old)
        body = self.client.get(url).json()
        self.assertEqual(body["operating_points"]["available"], False)
        self.assertIn("not measured", body["wording"]["reason"])
        self.assertEqual(body["overall"]["verdict"], "cannot_tell")
