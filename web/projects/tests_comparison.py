"""Comparison of a newly taught model with the model in use (W8-L2).

SYNTHETIC data only: tiny generated pictures and scripted test-double backends that find exactly the pictures a test
tells them to. Nothing here says anything about how a real model behaves on real imagery.
"""

from __future__ import annotations

import copy
import json
import random
import re
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from . import training
from .datasets import manifest as dataset_manifest, service as dataset_service, splits
from .models import ModelVersion, TrainingRun
from .tests_datasets import png_bytes
from .tests_results_plain import synthetic_evaluation
from .tests_training import TrainingBase
from .training import comparison, evaluation as ev, lifecycle, report, runner
from .training.base import Availability, BoxPrediction, TrainingBackend, TrainingCancelled, TrainResult

FOUND = BoxPrediction("damaged", 0.1, 0.2, 0.3, 0.4, 0.9)  # exactly the box make_box draws
WRONG = BoxPrediction("damaged", 0.6, 0.6, 0.3, 0.3, 0.9)  # overlaps it not at all
KEYS = {
    "schema", "available", "baseline", "test_pictures", "test_boxes", "fair", "unfair_reason", "overlap_pictures",
    "baseline_result", "candidate_result", "per_label", "difference", "verdict", "plain",
}


class ScriptedBackend(TrainingBackend):
    """Finds the person-drawn box on exactly the pictures whose number is in ``finds`` (a picture's number is
    written in its top-left pixel), and also draws a wrong box on the ones in ``wrong``."""

    description = "Test double."
    license_note = "Test only; no third-party code."
    finds: frozenset = frozenset()
    wrong: frozenset = frozenset()
    fail: str | None = None
    available = True
    calls = 0

    def check_available(self) -> Availability:
        return Availability(True, "ready") if type(self).available else Availability(False, "Not installed here.", "Install it.")

    def train(self, dataset_dir, config, progress_cb, cancel_cb):
        artifact = dataset_dir.parent / "model.bin"
        artifact.write_bytes(f"{self.name}-weights".encode())
        return TrainResult(artifact, {}, (dataset_dir / "classes.txt").read_text().splitlines())

    def predict(self, artifact, image, thresholds):
        cls = type(self)
        cls.calls += 1
        if cls.fail:
            raise RuntimeError(cls.fail.replace("{path}", str(artifact)))
        red, green, _ = image.getpixel((0, 0))
        number = red + 256 * green
        found = ([FOUND] if number in cls.finds else []) + ([WRONG] if number in cls.wrong else [])
        return [b for b in found if b.score >= thresholds.get("confidence", 0)]


class ScriptA(ScriptedBackend):
    name = "script-a"


class ScriptB(ScriptedBackend):
    name = "script-b"


def make_block(evaluation: dict, *, baseline_found: dict[str, int], baseline_wrong: int, b: int, c: int,
               fair: bool = True, overlap: int = 0, trained_on: int = 120) -> dict:
    """A comparison block of the stored shape for ``evaluation`` (built by hand, as the product would write it)."""
    labels = list(evaluation["per_label"])
    new_found = {label: evaluation["per_label"][label]["true_positives"] for label in labels}
    boxes = evaluation["data"]["boxes"]
    assert sum(new_found.values()) - sum(baseline_found.values()) == b - c, "b - c must be the difference in found"
    block = {
        "schema": comparison.SCHEMA, "available": True,
        "baseline": {"model_id": "0f6f5d44-2f5a-4c9c-9f55-5a3c1f0b7a11", "version": 3, "artifact_sha256": "ab" * 32,
                     "backend": "script-a", "trained_on_pictures": trained_on},
        "test_pictures": evaluation["data"]["images"], "test_boxes": boxes, "fair": fair,
        "unfair_reason": "" if fair else comparison.overlap_reason(overlap, evaluation["data"]["images"], trained_on),
        "overlap_pictures": overlap,
        "baseline_result": comparison._result(sum(baseline_found.values()), boxes, baseline_wrong),
        "candidate_result": comparison._result(evaluation["overall"]["true_positives"], boxes,
                                               evaluation["overall"]["false_positives"]),
        "per_label": [{"label": l, "baseline_found": baseline_found[l], "candidate_found": new_found[l],
                       "of": evaluation["per_label"][l]["ground_truth_boxes"]} for l in labels],
        "difference": {"found": b - c, "wrong_boxes": evaluation["overall"]["false_positives"] - baseline_wrong,
                       "only_candidate_found": b, "only_baseline_found": c},
    }
    verdict, why = comparison.decide(
        fair=fair, boxes_per_label={l: evaluation["per_label"][l]["ground_truth_boxes"] for l in labels},
        test_pictures=block["test_pictures"], only_candidate=b, only_baseline=c)
    block["verdict"], block["plain"] = verdict, comparison.sentence(block, verdict, why)
    return block


def version_of(*entries, **over) -> SimpleNamespace:
    """A dataset version as the database holds it: a manifest, the checksum of that manifest and the image count."""
    document = {"images": [{"asset_id": a, "sha256": sha, "split": split} for a, sha, split in entries]}
    fields = {"manifest": document, "manifest_sha256": dataset_manifest.sha256_hex(document), "image_count": len(entries)}
    return SimpleNamespace(**{**fields, **over})


def baseline_of(version) -> SimpleNamespace:
    return SimpleNamespace(training_run=SimpleNamespace(dataset_version=version))


# ---------------------------------------------------------------------------------------------------------
class DecisionRuleTests(SimpleTestCase):
    """The verdict rule on counts alone: exact boundaries."""

    def decide(self, *, b, c, boxes=None, fair=True, pictures=40):
        return comparison.decide(fair=fair, boxes_per_label={"a": 40} if boxes is None else boxes, test_pictures=pictures,
                                 only_candidate=b, only_baseline=c)

    def test_the_margin_is_three_boxes_or_ten_percent_whichever_is_larger_rounded_up(self) -> None:
        self.assertEqual([comparison.margin_needed(n) for n in (1, 30, 31, 40, 41, 100, 101, 250)],
                         [3, 3, 4, 4, 5, 10, 11, 25])

    def test_exactly_at_the_margin_is_better_and_one_below_is_the_same(self) -> None:
        for boxes, need in ((30, 3), (31, 4), (100, 10), (101, 11)):
            with self.subTest(boxes=boxes):
                kw = {"boxes": {"a": boxes}, "pictures": boxes}
                self.assertEqual(self.decide(b=need, c=0, **kw)[0], "better")
                self.assertEqual(self.decide(b=need - 1, c=0, **kw)[0], "about_the_same")
                self.assertEqual(self.decide(b=0, c=need, **kw)[0], "worse")
                self.assertEqual(self.decide(b=0, c=need - 1, **kw)[0], "about_the_same")
                # it is the DIFFERENCE b - c that counts, not b alone
                self.assertEqual(self.decide(b=need + 5, c=6, **kw)[0], "about_the_same")
                self.assertEqual(self.decide(b=need + 5, c=5, **kw)[0], "better")
                self.assertEqual(self.decide(b=5, c=need + 5, **kw)[0], "worse")

    def test_the_same_boxes_found_is_the_same(self) -> None:
        self.assertEqual(self.decide(b=0, c=0), ("about_the_same", "same"))
        self.assertEqual(self.decide(b=20, c=20), ("about_the_same", "same"))

    def test_an_unfair_comparison_cannot_tell_however_large_the_difference(self) -> None:
        self.assertEqual(self.decide(b=40, c=0, fair=False), ("cannot_tell", "unfair"))
        self.assertEqual(self.decide(b=0, c=40, fair=False), ("cannot_tell", "unfair"))

    def test_no_test_boxes_or_pictures_cannot_tell(self) -> None:
        self.assertEqual(self.decide(b=0, c=0, boxes={"a": 0}), ("cannot_tell", "no_test"))
        self.assertEqual(self.decide(b=3, c=0, pictures=0), ("cannot_tell", "no_test"))
        self.assertEqual(self.decide(b=0, c=0, boxes={}), ("cannot_tell", "no_test"))

    def test_a_label_with_boxes_but_fewer_than_the_shared_minimum_cannot_tell(self) -> None:
        minimum = ev.MIN_BOXES_PER_LABEL_FOR_VERDICT
        self.assertEqual(self.decide(b=9, c=0, boxes={"a": minimum, "b": minimum - 1}), ("cannot_tell", "too_few"))
        self.assertEqual(self.decide(b=9, c=0, boxes={"a": minimum, "b": minimum})[0], "better")
        self.assertEqual(self.decide(b=0, c=0, boxes={"a": minimum - 1})[0], "cannot_tell")

    def test_a_label_with_no_test_boxes_at_all_cannot_tell_either_like_the_results_verdict(self) -> None:
        """A4: ``report._verdict`` calls a label with 0 test boxes 'too few'; the comparison must not be more daring."""
        minimum = ev.MIN_BOXES_PER_LABEL_FOR_VERDICT
        self.assertEqual(self.decide(b=9, c=0, boxes={"a": minimum, "b": 0}), ("cannot_tell", "too_few"))
        self.assertEqual(self.decide(b=0, c=9, boxes={"a": minimum, "b": 0}), ("cannot_tell", "too_few"))
        self.assertEqual(self.decide(b=0, c=0, boxes={"a": 0, "b": 0}), ("cannot_tell", "no_test"))  # nothing at all
        # the same answer as report._verdict for every combination of the two labels
        for a in (0, 1, minimum - 1, minimum, minimum + 5):
            for b in (0, 1, minimum - 1, minimum, minimum + 5):
                with self.subTest(a=a, b=b):
                    rows = [{"label": "a", "test_boxes": a, "found": a}, {"label": "b", "test_boxes": b, "found": b}]
                    results_say_cannot_tell = report._verdict(rows, {"found": a + b, "of": a + b, "wrong_boxes": 0})[0] == "cannot_tell"
                    ours = self.decide(b=0, c=0, boxes={"a": a, "b": b}, pictures=1)[0] == "cannot_tell"
                    if a + b:  # with nothing found at all the results page says "not_ready" first; ours says no test boxes
                        self.assertEqual(ours, results_say_cannot_tell)

    def test_the_minimum_is_the_shared_constant_not_a_copy(self) -> None:
        with mock.patch.object(ev, "MIN_BOXES_PER_LABEL_FOR_VERDICT", 50):
            self.assertEqual(self.decide(b=9, c=0, boxes={"a": 40}), ("cannot_tell", "too_few"))


class SentenceTests(SimpleTestCase):
    """Pinned wording. Shares are rounded DOWN; never 'accurate', 'certified' or 'safe'; always the not-final line."""

    def block(self, *, new_found, base_found, b, c, new_wrong=2, base_wrong=2, fair=True, overlap=0, boxes=(60, 40)):
        evaluation, _ = synthetic_evaluation({"dent": (boxes[0], new_found[0], new_wrong), "scratch": (boxes[1], new_found[1], 0)})
        return make_block(evaluation, baseline_found={"dent": base_found[0], "scratch": base_found[1]},
                          baseline_wrong=base_wrong, b=b, c=c, fair=fair, overlap=overlap)

    def test_better(self) -> None:
        block = self.block(new_found=(55, 35), base_found=(50, 30), b=11, c=1)
        self.assertEqual(block["verdict"], "better")
        self.assertEqual(
            block["plain"],
            "Better on these test pictures: the new model found 90 of 100 test boxes (90%) and the model in use found "
            "80 (80%). The two models disagreed on 12 boxes: the new model alone found 11, the model in use alone "
            f"found 1. {report.NOT_FINAL_DECISION}",
        )

    def test_about_the_same(self) -> None:
        block = self.block(new_found=(51, 31), base_found=(50, 30), b=3, c=1)
        self.assertEqual(block["verdict"], "about_the_same")
        self.assertEqual(
            block["plain"],
            "About the same on these test pictures: the new model found 82 of 100 test boxes (82%) and the model in "
            "use found 80 (80%). The two models disagreed on 4 boxes: the new model alone found 3, the model in use "
            "alone found 1. One model is called better only when it found at least 10 boxes more that the other "
            f"missed. {report.NOT_FINAL_DECISION}",
        )

    def test_worse(self) -> None:
        block = self.block(new_found=(45, 29), base_found=(50, 35), b=0, c=11)
        self.assertEqual(block["verdict"], "worse")
        self.assertTrue(block["plain"].startswith("Worse on these test pictures: the new model found 74 of 100"))
        self.assertIn("The two models disagreed on 11 boxes: the new model alone found 0, the model in use alone found 11.", block["plain"])
        self.assertNotIn("Only one of the two", block["plain"])

    def test_exactly_the_same_boxes(self) -> None:
        block = self.block(new_found=(50, 30), base_found=(50, 30), b=0, c=0)
        self.assertIn("The two models found exactly the same boxes.", block["plain"])
        self.assertEqual(block["difference"]["found"], 0)

    def test_cannot_tell_because_unfair_says_how_many_pictures(self) -> None:
        block = self.block(new_found=(55, 35), base_found=(50, 30), b=10, c=0, fair=False, overlap=7)
        self.assertEqual(block["verdict"], "cannot_tell")
        self.assertEqual(
            block["plain"],
            "Cannot tell, and this comparison is not fair: 7 of the 102 test pictures were also used to teach or "
            "check the model in use, so it has already seen part of this test. The model in use was taught from or "
            "checked on 120 different pictures. For information only: the new model "
            "found 90 of 100 test boxes (90%) and the model in use found 80 (80%). "
            f"{report.NOT_FINAL_DECISION}",
        )

    def test_cannot_tell_because_a_label_has_too_few_boxes(self) -> None:
        block = self.block(new_found=(25, 10), base_found=(20, 10), b=5, c=0, boxes=(30, 12))
        self.assertEqual(block["verdict"], "cannot_tell")
        self.assertIn("Cannot tell: “scratch” has 12 test boxes. About 30 per label are needed before two models can be compared.", block["plain"])

    def test_cannot_tell_names_a_label_with_no_test_boxes_at_all(self) -> None:
        block = self.block(new_found=(30, 0), base_found=(25, 0), b=5, c=0, boxes=(30, 0))
        self.assertEqual(block["verdict"], "cannot_tell")
        self.assertIn("Cannot tell: “scratch” has no test boxes. About 30 per label are needed before two models can "
                      "be compared.", block["plain"])
        both = self.block(new_found=(10, 0), base_found=(10, 0), b=0, c=0, boxes=(12, 0))
        self.assertIn("Cannot tell: “dent” has 12 test boxes; “scratch” has no test boxes. About 30 per label", both["plain"])

    def test_cannot_tell_also_mentions_many_more_wrong_boxes_like_the_other_verdicts(self) -> None:
        wrong = "The new model also drew 22 more extra or misplaced boxes than the model in use (25 against 3); " \
                "extra boxes do not change this verdict."
        for kw in ({"boxes": (30, 12)}, {"boxes": (60, 40), "fair": False, "overlap": 4}):
            with self.subTest(kw=kw):
                block = self.block(new_found=(25, 10), base_found=(20, 10), b=5, c=0, new_wrong=25, base_wrong=3, **kw)
                self.assertEqual(block["verdict"], "cannot_tell")
                self.assertIn(f" {wrong} {report.NOT_FINAL_DECISION}", block["plain"])
                self.assertIn("For information only: the new model found", block["plain"])
                few = self.block(new_found=(25, 10), base_found=(20, 10), b=5, c=0, new_wrong=5, base_wrong=3, **kw)
                self.assertNotIn("more extra or misplaced", few["plain"])

    def test_many_more_wrong_boxes_are_mentioned_and_do_not_change_the_verdict(self) -> None:
        block = self.block(new_found=(55, 35), base_found=(50, 30), b=10, c=0, new_wrong=25, base_wrong=3)
        self.assertEqual(block["verdict"], "better")
        self.assertEqual(block["difference"]["wrong_boxes"], 22)
        self.assertIn("The new model also drew 22 more extra or misplaced boxes than the model in use (25 against 3); "
                      "extra boxes do not change this verdict.", block["plain"])
        few = self.block(new_found=(55, 35), base_found=(50, 30), b=10, c=0, new_wrong=5, base_wrong=3)
        self.assertNotIn("more extra or misplaced", few["plain"])

    def test_shares_are_rounded_down_and_no_banned_word_appears(self) -> None:
        block = self.block(new_found=(59, 40), base_found=(50, 40), b=9, c=0, boxes=(60, 40))
        self.assertIn("found 99 of 100 test boxes (99%)", block["plain"])
        evaluation, _ = synthetic_evaluation({"a": (3, 2, 0)})  # 2 of 3 is 66%, never 67%
        block = make_block(evaluation, baseline_found={"a": 1}, baseline_wrong=0, b=1, c=0)
        self.assertIn("found 2 of 3 test boxes (66%)", block["plain"])
        for verdict_block in (self.block(new_found=(55, 35), base_found=(50, 30), b=11, c=1), block):
            text = verdict_block["plain"].lower()
            for word in ("accurate", "certified", "safe"):
                self.assertNotIn(word, text)
            self.assertTrue(verdict_block["plain"].endswith(report.NOT_FINAL_DECISION))
            self.assertIn("a person decides", text)


class ReadingSideTests(SimpleTestCase):
    """What a page gets: a checked copy, or ``{available: False, reason}``. Never an exception, never a stored
    verdict or sentence that the counts do not support."""

    def pair(self):
        evaluation, _ = synthetic_evaluation({"dent": (60, 55, 4), "scratch": (40, 31, 2)})
        block = make_block(evaluation, baseline_found={"dent": 50, "scratch": 30}, baseline_wrong=3, b=7, c=1)
        return {**evaluation, "comparison": block}, block

    def test_a_valid_block_is_passed_through_with_every_stored_key(self) -> None:
        evaluation, block = self.pair()
        plain = comparison.plain_comparison(evaluation)
        self.assertTrue(plain["available"])
        self.assertEqual({k: v for k, v in plain.items() if k != "available"}, {k: block[k] for k in KEYS - {"available"}})
        self.assertEqual(set(plain), KEYS)

    def test_the_report_carries_it_and_every_old_key_is_unchanged(self) -> None:
        evaluation, block = self.pair()
        old = report.plain_results({k: v for k, v in evaluation.items() if k != "comparison"})
        new = report.plain_results(evaluation)
        self.assertEqual({k: v for k, v in new.items() if k != "comparison"}, {k: v for k, v in old.items() if k != "comparison"})
        self.assertEqual(old["comparison"], {"available": False, "reason": comparison.NOT_SAVED_REASON})
        self.assertEqual(new["comparison"]["verdict"], block["verdict"])
        self.assertEqual(new["comparison"]["plain"], block["plain"])

    def test_the_unavailable_result_keeps_the_key(self) -> None:
        plain = report.plain_results({"available": False, "reason": "no test pictures"})
        self.assertEqual(plain["comparison"], {"available": False, "reason": comparison.NO_RESULTS_REASON})
        self.assertEqual(report.plain_results(None)["comparison"]["available"], False)

    def test_a_stored_verdict_or_sentence_is_never_trusted(self) -> None:
        evaluation, block = self.pair()
        self.assertEqual(block["verdict"], "about_the_same")
        forged = {**evaluation, "comparison": {**block, "verdict": "better", "plain": "Better. Totally accurate and safe."}}
        plain = comparison.plain_comparison(forged)
        self.assertEqual((plain["verdict"], plain["plain"]), (block["verdict"], block["plain"]))

    def test_an_unfair_block_that_claims_a_verdict_is_read_as_cannot_tell(self) -> None:
        evaluation, block = self.pair()
        for forged in ({**block, "fair": False, "unfair_reason": "x", "overlap_pictures": 3, "verdict": "better"},):
            plain = comparison.plain_comparison({**evaluation, "comparison": forged})
            self.assertEqual(plain["verdict"], "cannot_tell")

    def test_counts_that_do_not_add_up_or_belong_to_another_evaluation_are_unreadable(self) -> None:
        evaluation, block = self.pair()
        unreadable = {"available": False, "reason": comparison.UNREADABLE_REASON}
        wrong = [
            lambda b: b["difference"].update(found=99),
            lambda b: b["difference"].update(only_candidate_found=9),
            lambda b: b["candidate_result"].update(found=b["candidate_result"]["found"] + 1),
            lambda b: b["per_label"][0].update(baseline_found=b["per_label"][0]["baseline_found"] + 1),
            lambda b: b.update(test_boxes=b["test_boxes"] + 1),
            lambda b: b.update(test_pictures=b["test_pictures"] + 1),
            lambda b: b["baseline_result"].update(found=b["baseline_result"]["of"] + 1),
            lambda b: b.update(overlap_pictures=b["test_pictures"] + 1),
            lambda b: b.update(overlap_pictures=2),  # overlap while claiming fair
            lambda b: b.update(fair=False, unfair_reason=""),
            lambda b: b.update(schema="other"),
            lambda b: b["baseline"].update(version="3"),
        ]
        for index, mutate in enumerate(wrong):
            with self.subTest(case=index):
                changed = copy.deepcopy(block)
                mutate(changed)
                self.assertEqual(comparison.plain_comparison({**evaluation, "comparison": changed}), unreadable)

    def test_an_explicit_unavailable_block_gives_its_reason(self) -> None:
        stored = {"schema": comparison.SCHEMA, "available": False, "reason": comparison.BASELINE_FILE_REASON}
        self.assertEqual(comparison.plain_comparison({"comparison": stored}),
                         {"available": False, "reason": comparison.BASELINE_FILE_REASON})
        self.assertEqual(comparison.plain_comparison({"comparison": {**stored, "reason": ""}})["reason"], comparison.NO_COMPARISON_REASON)

    def unfair_pair(self, overlap: int = 3):
        evaluation, _ = synthetic_evaluation({"dent": (60, 55, 4), "scratch": (40, 31, 2)})
        block = make_block(evaluation, baseline_found={"dent": 50, "scratch": 30}, baseline_wrong=3, b=7, c=1,
                           fair=False, overlap=overlap)
        return {**evaluation, "comparison": block}, block

    def test_a_stored_unfair_reason_is_never_shown_the_sentence_is_rebuilt_from_the_counts(self) -> None:
        """A2: free text in ``unfair_reason`` must not reach the page."""
        evaluation, block = self.unfair_pair(overlap=3)
        evil = "Totally accurate and safe <script>alert(1)</script>"
        plain = comparison.plain_comparison({**evaluation, "comparison": {**block, "unfair_reason": evil}})
        self.assertEqual(plain["unfair_reason"], block["unfair_reason"])
        self.assertEqual(plain["plain"], block["plain"])
        self.assertNotIn("<script", json.dumps(plain))
        self.assertNotIn("accurate", json.dumps(plain))
        self.assertTrue(plain["plain"].startswith(
            f"Cannot tell, and this comparison is not fair: 3 of the {block['test_pictures']} test pictures were also used "
            "to teach or check the model in use"))
        # when nothing overlaps and the text is not one of ours, a generic sentence is shown
        quiet = {**block, "overlap_pictures": 0, "unfair_reason": evil}
        plain = comparison.plain_comparison({**evaluation, "comparison": quiet})
        self.assertEqual(plain["unfair_reason"], comparison.UNFAIR_GENERIC_REASON)
        self.assertEqual(plain["verdict"], "cannot_tell")
        self.assertNotIn("<script", json.dumps(plain))

    def test_every_known_unfair_reason_is_shown_and_a_fair_block_never_carries_one(self) -> None:
        evaluation, block = self.unfair_pair(overlap=0)
        self.assertGreaterEqual(len(comparison.KNOWN_UNFAIR_REASONS), 4)
        for reason in comparison.KNOWN_UNFAIR_REASONS:
            with self.subTest(reason=reason[:40]):
                plain = comparison.plain_comparison({**evaluation, "comparison": {**block, "unfair_reason": reason}})
                self.assertEqual(plain["unfair_reason"], reason)
                self.assertIn(reason, plain["plain"])
        fair_evaluation, fair_block = self.pair()
        plain = comparison.plain_comparison({**fair_evaluation, "comparison": {**fair_block, "unfair_reason": "<b>x</b>"}})
        self.assertEqual((plain["fair"], plain["unfair_reason"]), (True, ""))

    def test_an_unavailable_reason_is_accepted_only_when_it_is_one_of_ours(self) -> None:
        """A2: the stored ``reason`` of an ``available: false`` block is not shown unless we wrote it."""
        for reason in comparison.KNOWN_UNAVAILABLE_REASONS:
            stored = {"schema": comparison.SCHEMA, "available": False, "reason": reason}
            self.assertEqual(comparison.plain_comparison({"comparison": stored}), {"available": False, "reason": reason})
        self.assertIn(comparison.BASELINE_FILE_REASON, comparison.KNOWN_UNAVAILABLE_REASONS)
        for junk in ("Totally accurate and safe <script>alert(1)</script>", comparison.BASELINE_FILE_REASON + " ", "x" * 900, 5, None, ["a"]):
            stored = {"schema": comparison.SCHEMA, "available": False, "reason": junk}
            plain = comparison.plain_comparison({"comparison": stored})
            self.assertEqual(plain, {"available": False, "reason": "No comparison is available for this model."})

    def test_fuzz_no_stored_string_ever_reaches_the_output(self) -> None:
        """A2: put a marker into every string of a stored block, one place at a time and several at once; it must
        never come back out, in the sentence or in any field."""
        marker = "ZQ<script>7</script>"
        variants = [self.pair(), self.unfair_pair(3), self.unfair_pair(0)]
        unavailable = {"schema": comparison.SCHEMA, "available": False, "reason": comparison.BASELINE_FILE_REASON}
        rng = random.Random(9)

        def string_paths(node, path=()):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from string_paths(value, path + (key,))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    yield from string_paths(value, path + (index,))
            elif isinstance(node, str):
                yield path

        def put(root, path, value):
            holder = root
            for step in path[:-1]:
                holder = holder[step]
            holder[path[-1]] = value

        shown = 0
        for evaluation, block in variants:
            paths = list(string_paths(block))
            self.assertGreater(len(paths), 6)
            attempts = [[path] for path in paths] + [rng.sample(paths, 3) for _ in range(40)]
            for chosen in attempts:
                for style in ("replace", "append"):
                    changed = copy.deepcopy(block)
                    for path in chosen:
                        old = changed
                        for step in path:
                            old = old[step]
                        put(changed, path, marker if style == "replace" else old + marker)
                    plain = comparison.plain_comparison({**evaluation, "comparison": changed})
                    shown += plain["available"]
                    with self.subTest(paths=chosen, style=style):
                        self.assertNotIn("ZQ", json.dumps(plain))
                        self.assertNotIn("<script>", json.dumps(plain))
        self.assertGreater(shown, 10)  # some stay readable: the free-text ones are replaced, not refused
        for style in (marker, comparison.BASELINE_FILE_REASON + marker):
            plain = comparison.plain_comparison({"comparison": {**unavailable, "reason": style}})
            self.assertNotIn("ZQ", json.dumps(plain))

    def test_labels_and_ids_that_are_not_ours_are_unreadable(self) -> None:
        evaluation, block = self.pair()
        unreadable = {"available": False, "reason": comparison.UNREADABLE_REASON}
        for mutate in (
            lambda b: b["per_label"][0].update(label="<b>x</b>"),                      # not a label of this evaluation
            lambda b: b["baseline"].update(backend="<script>"),
            lambda b: b["baseline"].update(model_id="Totally accurate and safe"),
            lambda b: b["baseline"].update(artifact_sha256="not hex"),
        ):
            changed = copy.deepcopy(block)
            mutate(changed)
            self.assertEqual(comparison.plain_comparison({**evaluation, "comparison": changed}), unreadable)

    def test_non_finite_numbers_are_unreadable(self) -> None:
        evaluation, block = self.pair()
        for junk in (float("nan"), float("inf"), -1, True, "7", None, 2.5):
            changed = copy.deepcopy(block)
            changed["difference"]["only_candidate_found"] = junk
            self.assertEqual(comparison.plain_comparison({**evaluation, "comparison": changed})["available"], False, junk)

    def test_fuzz_never_raises_and_a_readable_answer_is_always_consistent(self) -> None:
        evaluation, block = self.pair()
        junk_values = [None, 5, -3, 2.5, float("nan"), True, "text", {}, {"a": 1}, [], [1, "two", None], [[]], {"found": "x"}]
        rng = random.Random(82)
        paths = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    paths.append(path + [key])
                    walk(value, path + [key])
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    paths.append(path + [index])
                    walk(value, path + [index])

        walk(block, [])
        readable = 0
        for case in range(1500):
            changed = copy.deepcopy(block)
            for _ in range(rng.choice([1, 1, 2, 3])):
                path = rng.choice(paths)
                try:
                    holder = changed
                    for step in path[:-1]:
                        holder = holder[step]
                    if rng.random() < 0.15:
                        del holder[path[-1]]
                    else:
                        holder[path[-1]] = rng.choice(junk_values)
                except (KeyError, IndexError, TypeError):
                    pass
            stored = rng.choice([changed, changed, changed, rng.choice(junk_values)])
            with self.subTest(case=case):
                plain = report.plain_results({**evaluation, "comparison": stored})["comparison"]
                self.assertIsInstance(plain["available"], bool)
                if plain["available"]:
                    readable += 1
                    self.assertIn(plain["verdict"], comparison.VERDICTS)
                    self.assertEqual(plain["difference"]["found"], plain["candidate_result"]["found"] - plain["baseline_result"]["found"])
                    self.assertEqual(plain["plain"], comparison.sentence(plain, *comparison.decide(
                        fair=plain["fair"], boxes_per_label={r["label"]: r["of"] for r in plain["per_label"]},
                        test_pictures=plain["test_pictures"], only_candidate=plain["difference"]["only_candidate_found"],
                        only_baseline=plain["difference"]["only_baseline_found"])))
                else:
                    self.assertIsInstance(plain["reason"], str)
                json.dumps(plain, allow_nan=False)
        self.assertGreater(readable, 50)  # the fuzz did reach the readable path too


# ---------------------------------------------------------------------------------------------------------
class ComparisonBase(TrainingBase):
    """A project of 240 numbered pictures (one drawn box each) and two scripted backends."""

    PICTURES = 240

    def setUp(self) -> None:
        super().setUp()
        for backend in (ScriptA, ScriptB):
            backend.finds, backend.wrong, backend.fail, backend.available, backend.calls = frozenset(), frozenset(), None, True, 0
            training.register_backend(backend, replace=True)
            self.addCleanup(training.unregister_backend, backend.name)
        self.project.labels = ["damaged"]  # every label of the project has boxes (a label without any cannot tell, A4)
        self.project.save()
        self.number_of = {}
        for number in range(self.PICTURES):
            asset = self.make_asset(data=png_bytes((number % 256, number // 256, 5)))
            self.make_box(asset)
            self.number_of[str(asset.id)] = number

    def freeze(self, seed: int = 0, shares=(0.6, 0.2, 0.2)):
        analysis = dataset_service.analyse(self.project, splits.SplitConfig(seed, *shares))
        return dataset_service.create_version(analysis, self.user)

    def numbers(self, dataset, split: str) -> list[int]:
        return sorted(self.number_of[i["asset_id"]] for i in dataset.manifest["images"] if i["split"] == split)

    def teach(self, backend: type[ScriptedBackend], dataset) -> ModelVersion:
        response = self.post(self.api("/training/runs"), {"backend": backend.name, "dataset_id": str(dataset.id)})
        self.assertEqual(response.status_code, 202, response.content)
        finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.SUCCEEDED, finished.error)
        self.last_run = finished
        return finished.model_versions.get()

    def put_in_use(self, model: ModelVersion) -> ModelVersion:
        self.assertEqual(self.post(self.api(f"/models/{model.id}/stage"), {}).status_code, 200)
        done = self.post(self.api(f"/models/{model.id}/approve"),
                         {"note": "Prototype check, synthetic pictures.", "acknowledge_weak_results": True})
        self.assertEqual(done.status_code, 200, done.content)
        model.refresh_from_db()
        self.assertEqual(model.stage, "approved")
        return model

    def baseline_in_use(self, dataset, finds=()):
        ScriptA.finds = frozenset(finds)
        model = self.put_in_use(self.teach(ScriptA, dataset))
        ScriptA.calls = 0
        return model

    def block(self, model: ModelVersion) -> dict:
        self.assertIn("comparison", model.evaluation, model.training_run.logs)
        return model.evaluation["comparison"]


class PipelineTests(ComparisonBase):
    def setUp(self) -> None:
        super().setUp()
        self.dataset = self.freeze()
        self.test_numbers = self.numbers(self.dataset, "test")
        self.assertGreaterEqual(len(self.test_numbers), 31, "the fixture needs enough test boxes for a verdict")
        self.need = comparison.margin_needed(len(self.test_numbers))

    def run_pair(self, base: list[int], new: list[int], *, new_wrong=()) -> dict:
        self.baseline_in_use(self.dataset, base)
        ScriptB.finds, ScriptB.wrong = frozenset(new), frozenset(new_wrong)
        return self.block(self.teach(ScriptB, self.dataset))

    def test_the_stored_block_has_every_key_and_the_numbers_of_the_two_models(self) -> None:
        t = self.test_numbers
        baseline = self.baseline_in_use(self.dataset, t[:15])
        ScriptB.finds = frozenset(t[:15] + t[15:15 + self.need])
        model = self.teach(ScriptB, self.dataset)
        block = self.block(model)
        self.assertEqual(set(block), KEYS)
        self.assertEqual(block["schema"], "athena.comparison/1")
        self.assertEqual(block["baseline"], {
            "model_id": str(baseline.id), "version": baseline.version, "artifact_sha256": baseline.artifact_sha256,
            "backend": "script-a",
            "trained_on_pictures": len(self.numbers(self.dataset, "train")) + len(self.numbers(self.dataset, "val")),
        })
        self.assertEqual((block["test_pictures"], block["test_boxes"]), (len(t), len(t)))
        self.assertEqual((block["fair"], block["unfair_reason"], block["overlap_pictures"]), (True, "", 0))
        self.assertEqual({k: block["baseline_result"][k] for k in ("found", "of", "wrong_boxes")},
                         {"found": 15, "of": len(t), "wrong_boxes": 0})
        self.assertEqual(block["candidate_result"]["found"], 15 + self.need)
        self.assertEqual(block["candidate_result"]["recall"], (15 + self.need) * 10000 // len(t) / 10000)
        self.assertEqual(block["per_label"], [
            {"label": "damaged", "baseline_found": 15, "candidate_found": 15 + self.need, "of": len(t)},
        ])
        self.assertEqual(block["difference"], {"found": self.need, "wrong_boxes": 0,
                                               "only_candidate_found": self.need, "only_baseline_found": 0})
        self.assertEqual(block["verdict"], "better")
        # the candidate's own evaluation is untouched, and still the same keys plus the additive ones
        for key in ("schema", "available", "split", "iou_threshold", "confidence_threshold", "data", "per_label",
                    "overall", "limits", "held_out_outcomes", "dataset"):
            self.assertIn(key, model.evaluation)
        self.assertEqual(model.evaluation["overall"]["true_positives"], 15 + self.need)
        self.assertEqual(ModelVersion.objects.get(pk=baseline.pk).stage, "approved")  # nothing else moved

    def test_better_about_the_same_and_worse_at_the_exact_margin(self) -> None:
        t, need = self.test_numbers, self.need
        self.baseline_in_use(self.dataset, t[:15])
        cases = {
            "better": (t[:15] + t[15:15 + need], (need, 0)),
            "about_the_same": (t[:15] + t[15:14 + need], (need - 1, 0)),
            "about_the_same ": (t[2:15] + t[15:15 + need + 1], (need + 1, 2)),  # b - c = need - 1
            "better ": (t[2:15] + t[15:15 + need + 2], (need + 2, 2)),  # b - c = need
            "worse": (t[need:15], (0, need)),
            "about_the_same  ": (t[need - 1:15], (0, need - 1)),
        }
        for expected, (found, (b, c)) in cases.items():
            with self.subTest(expected=expected):
                ScriptB.finds = frozenset(found)
                diff = self.block(self.teach(ScriptB, self.dataset))["difference"]
                self.assertEqual((diff["only_candidate_found"], diff["only_baseline_found"]), (b, c))
                self.assertEqual(self.last_run.model_versions.get().evaluation["comparison"]["verdict"], expected.strip())

    def test_the_candidate_equal_to_the_model_in_use_is_about_the_same_with_no_difference(self) -> None:
        t = self.test_numbers
        self.baseline_in_use(self.dataset, t[:20])
        ScriptA.finds = frozenset(t[:20])
        block = self.block(self.teach(ScriptA, self.dataset))
        self.assertEqual(block["verdict"], "about_the_same")
        self.assertEqual(block["difference"], {"found": 0, "wrong_boxes": 0, "only_candidate_found": 0, "only_baseline_found": 0})
        self.assertEqual(block["baseline_result"], block["candidate_result"])
        self.assertIn("exactly the same boxes", block["plain"])

    def test_wrong_boxes_are_counted_and_do_not_flip_the_verdict(self) -> None:
        t = self.test_numbers
        block = self.run_pair(t[:15], t[:15 + self.need], new_wrong=t[20:20 + 3 * self.need])
        self.assertEqual(block["verdict"], "better")
        self.assertEqual(block["difference"]["wrong_boxes"], 3 * self.need)
        self.assertEqual(block["candidate_result"]["wrong_boxes"], 3 * self.need)
        self.assertEqual(block["baseline_result"]["wrong_boxes"], 0)
        self.assertIn(f"The new model also drew {3 * self.need} more extra or misplaced boxes", block["plain"])

    def test_the_model_in_use_is_scored_by_its_own_backend_in_one_pass_over_the_test_pictures(self) -> None:
        t = self.test_numbers
        self.baseline_in_use(self.dataset, t[:15])
        ScriptB.finds = frozenset(t[:15])
        ScriptA.calls = ScriptB.calls = 0
        model = self.teach(ScriptB, self.dataset)
        self.assertEqual(self.block(model)["baseline"]["backend"], "script-a")
        self.assertEqual(ScriptA.calls, len(t), "exactly one prediction pass of the model in use over the test split")
        self.assertIn("Comparing with version 1, the model in use", self.last_run.logs)

    def test_a_candidate_without_a_complete_miss_list_is_scored_again_and_gives_the_same_block(self) -> None:
        t = self.test_numbers
        self.baseline_in_use(self.dataset, t[:15])
        ScriptB.finds = frozenset(t[:15 + self.need])
        first = self.block(self.teach(ScriptB, self.dataset))
        ScriptB.calls = 0
        with mock.patch.object(comparison, "_candidate_found_from_outcomes", return_value=None):
            second = self.block(self.teach(ScriptB, self.dataset))
        self.assertGreaterEqual(ScriptB.calls, 2 * len(t))  # its own evaluation plus the extra pass
        self.assertEqual(first, second)
        self.assertIn("Scoring the new model again", self.last_run.logs)

    def test_two_runs_give_the_same_block(self) -> None:
        t = self.test_numbers
        self.baseline_in_use(self.dataset, t[:15])
        ScriptB.finds, ScriptB.wrong = frozenset(t[3:30]), frozenset(t[:4])
        first, second = (self.block(self.teach(ScriptB, self.dataset)) for _ in range(2))
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_swapping_the_two_models_flips_better_and_worse_exactly(self) -> None:
        t = self.test_numbers
        better = self.run_pair(t[:15], t[:15 + self.need])
        self.assertEqual(better["verdict"], "better")
        # now the model in use is the one that finds more
        ScriptA.finds = frozenset(t[:15 + self.need])
        ScriptB.finds = frozenset(t[:15])
        worse = self.block(self.teach(ScriptB, self.dataset))
        self.assertEqual(worse["verdict"], "worse")
        self.assertEqual(worse["difference"]["found"], -self.need)
        self.assertEqual(worse["difference"]["only_baseline_found"], self.need)

    def test_the_comparison_reaches_the_plain_results_endpoint(self) -> None:
        t = self.test_numbers
        self.baseline_in_use(self.dataset, t[:15])
        ScriptB.finds = frozenset(t[:15 + self.need])
        model = self.teach(ScriptB, self.dataset)
        body = self.client.get(self.api(f"/models/{model.id}/results/plain")).json()
        self.assertTrue(body["comparison"]["available"])
        self.assertEqual(body["comparison"]["verdict"], "better")
        self.assertEqual(body["comparison"]["plain"], model.evaluation["comparison"]["plain"])
        for key in ("evaluated", "counts", "labels", "overall", "misses", "operating_points", "wording"):
            self.assertIn(key, body)
        baseline_body = self.client.get(self.api(f"/models/{self.put_in_use(model).id}/results/plain")).json()
        self.assertTrue(baseline_body["comparison"]["available"])


class FairnessTests(ComparisonBase):
    def test_test_pictures_the_model_in_use_saw_make_it_unfair_and_cannot_tell(self) -> None:
        first = self.freeze(seed=0)
        self.baseline_in_use(first, self.numbers(first, "test")[:5])
        second = self.freeze(seed=7)
        saw = {self.number_of[i["asset_id"]] for i in first.manifest["images"] if i["split"] in ("train", "val")}
        test_numbers = self.numbers(second, "test")
        expected = sum(1 for n in test_numbers if n in saw)
        self.assertGreater(expected, 0, "the fixture must make the two splits overlap")
        ScriptB.finds = frozenset(test_numbers)  # finds everything: still cannot be called better
        model = self.teach(ScriptB, second)
        block = self.block(model)
        self.assertEqual(block["overlap_pictures"], expected)
        self.assertFalse(block["fair"])
        self.assertEqual(block["verdict"], "cannot_tell")
        self.assertIn(f"{expected} of the {len(test_numbers)} test pictures were also used to teach or check the model in use",
                      block["unfair_reason"])
        self.assertTrue(block["plain"].startswith("Cannot tell, and this comparison is not fair: " + block["unfair_reason"]))
        self.assertEqual(block["candidate_result"]["found"], len(test_numbers))  # the numbers are still there
        plain = self.client.get(self.api(f"/models/{model.id}/results/plain")).json()["comparison"]
        self.assertEqual((plain["fair"], plain["verdict"], plain["overlap_pictures"]), (False, "cannot_tell", expected))

    def test_identical_bytes_under_another_picture_id_still_count_and_held_out_pictures_do_not(self) -> None:
        def manifest(*entries):
            return {"images": [{"asset_id": a, "sha256": sha, "split": split} for a, sha, split in entries]}

        baseline = baseline_of(version_of(("a1", "aa", "train"), ("a2", "bb", "val"), ("a3", "cc", "test")))
        candidate = manifest(("n1", "aa", "test"), ("n2", "cc", "test"), ("n3", "dd", "test"), ("n4", "bb", "train"))
        fair, reason, overlap, trained_on = comparison._fairness(baseline, candidate)
        # "aa" (a copy of a training picture) and nothing else; "cc" was only ever HELD OUT by the model in use
        self.assertEqual((fair, overlap, trained_on), (False, 1, 2))
        self.assertIn("1 of the 3 test pictures was also used", reason)
        self.assertIn("taught from or checked on 2 different pictures", reason)
        fair, reason, overlap, trained_on = comparison._fairness(baseline, manifest(("n2", "cc", "test"), ("n3", "dd", "test")))
        self.assertEqual((fair, reason, overlap, trained_on), (True, "", 0, 2))
        # unreadable manifests are never fair (their checksums are right: the content is what is wrong)
        for junk in (None, {}, {"images": 5}, {"images": [{"split": "test"}]}, {"images": [None]}):
            self.assertFalse(comparison._fairness(baseline, junk)[0])
            damaged = version_of(manifest=junk)
            damaged.manifest_sha256 = dataset_manifest.sha256_hex(junk)
            self.assertFalse(comparison._fairness(baseline_of(damaged), candidate)[0])
        self.assertFalse(comparison._fairness(SimpleNamespace(training_run=None), candidate)[0])

    def test_the_same_picture_taught_twice_counts_once_in_the_different_pictures(self) -> None:
        baseline = baseline_of(version_of(("a1", "aa", "train"), ("a2", "aa", "val"), ("a3", "bb", "train")))
        fair, reason, overlap, trained_on = comparison._fairness(baseline, {"images": [{"asset_id": "n", "sha256": "zz", "split": "test"}]})
        self.assertEqual((fair, overlap, trained_on), (True, 0, 2))  # two different pictures, not three entries

    def test_a_baseline_manifest_with_no_train_or_val_picture_is_unknown_not_fair(self) -> None:
        """A1: nothing seen by the model in use is NOT the same as 'seen nothing'."""
        candidate = {"images": [{"asset_id": "n", "sha256": "zz", "split": "test"}]}
        for entries in ((), (("a", "aa", "test"),)):  # empty, or only held-out pictures: the checksum is right
            with self.subTest(entries=entries):
                fair, reason, overlap, trained_on = comparison._fairness(baseline_of(version_of(*entries)), candidate)
                self.assertEqual((fair, overlap, trained_on), (False, 0, 0))
                self.assertEqual(reason, comparison.UNFAIR_EMPTY_REASON)
                self.assertIn("the list of pictures the model in use was taught from is missing or empty", reason.lower())

    def test_a_baseline_manifest_that_no_longer_matches_its_checksum_or_count_is_not_trusted(self) -> None:
        """A1: the same integrity rule as ``datasets.service.verify_integrity`` (checksum of the manifest, image count)."""
        entries = (("a1", "aa", "train"), ("a2", "bb", "val"), ("a3", "cc", "test"))
        candidate = {"images": [{"asset_id": "n", "sha256": "zz", "split": "test"}]}
        good = version_of(*entries)
        self.assertEqual(comparison._fairness(baseline_of(good), candidate), (True, "", 0, 2))
        damaged = {
            "train and val entries removed": version_of(*entries, manifest={"images": [good.manifest["images"][2]]}),
            "one entry removed": version_of(*entries, manifest={"images": good.manifest["images"][:2]}),
            "a sha changed": version_of(*entries, manifest={"images": [{**good.manifest["images"][0], "sha256": "zz"}, *good.manifest["images"][1:]]}),
            "the checksum is wrong": version_of(*entries, manifest_sha256="0" * 64),
            "the checksum is missing": SimpleNamespace(manifest=good.manifest, image_count=3),
            "the image count is wrong": version_of(*entries, image_count=4),
            "the image count is missing": SimpleNamespace(manifest=good.manifest, manifest_sha256=good.manifest_sha256),
        }
        for name, version in damaged.items():
            with self.subTest(name):
                fair, reason, overlap, trained_on = comparison._fairness(baseline_of(version), candidate)
                self.assertEqual((fair, overlap, trained_on, reason), (False, 0, 0, comparison.UNFAIR_DAMAGED_REASON))
        # a manifest whose checksum was recomputed but whose count is not (and the other way round) is also refused
        self.assertFalse(comparison._fairness(baseline_of(version_of(*entries, image_count=2)), candidate)[0])

    def test_a_model_in_use_with_no_known_training_pictures_is_unfair(self) -> None:
        dataset = self.freeze()
        baseline = self.put_in_use(self.teach(ScriptA, dataset))
        TrainingRun.objects.filter(pk=baseline.training_run_id)  # the run row stays; the link is what an import lacks
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {ModelVersion._meta.db_table} SET training_run_id = NULL WHERE id = %s",
                           [baseline.pk.hex])
        block = self.block(self.teach(ScriptB, dataset))
        self.assertEqual((block["fair"], block["overlap_pictures"], block["baseline"]["trained_on_pictures"]), (False, 0, 0))
        self.assertIn("not known which pictures the model in use was taught from", block["unfair_reason"])
        self.assertEqual(block["verdict"], "cannot_tell")


class DamagedBaselineManifestTests(ComparisonBase):
    """A1: the model in use is compared fairly only if the stored list of pictures it was taught from is intact. The
    damage is done with raw SQL, the way the reviewer did it: the model rows and the files are untouched."""

    def setUp(self) -> None:
        super().setUp()
        self.first = self.freeze(seed=0)
        self.baseline = self.baseline_in_use(self.first, self.numbers(self.first, "test")[:5])
        self.second = self.freeze(seed=7)  # its test pictures partly overlap what the model in use was taught from
        self.second_test = self.numbers(self.second, "test")
        self.saw = {self.number_of[i["asset_id"]] for i in self.first.manifest["images"] if i["split"] in ("train", "val")}
        self.assertTrue(any(n in self.saw for n in self.second_test), "the fixture must make the two splits overlap")

    def damage(self, mutate, *, fix_checksum=False, fix_count=False) -> None:
        from django.db import connection

        document = copy.deepcopy(self.first.manifest)
        mutate(document)
        sets, params = ["manifest = %s"], [json.dumps(document)]
        if fix_checksum:
            sets.append("manifest_sha256 = %s")
            params.append(dataset_manifest.sha256_hex(document))
        if fix_count:
            sets.append("image_count = %s")
            params.append(len(document["images"]))
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {type(self.first)._meta.db_table} SET {', '.join(sets)} WHERE id = %s", [*params, self.first.pk.hex])

    def compare_second(self) -> dict:
        ScriptB.finds = frozenset(self.second_test)  # finds every test box: would be "better" if the comparison were fair
        return self.block(self.teach(ScriptB, self.second))

    def assert_unfair(self, block, reason) -> None:
        self.assertEqual((block["fair"], block["overlap_pictures"], block["verdict"]), (False, 0, "cannot_tell"))
        self.assertEqual(block["unfair_reason"], reason)
        self.assertEqual(block["baseline"]["trained_on_pictures"], 0)
        self.assertTrue(block["plain"].startswith("Cannot tell, and this comparison is not fair: " + reason))
        plain = self.client.get(self.api(f"/models/{self.last_run.model_versions.get().id}/results/plain")).json()["comparison"]
        self.assertEqual((plain["fair"], plain["verdict"], plain["unfair_reason"]), (False, "cannot_tell", reason))

    def test_before_any_damage_it_is_unfair_only_because_of_the_real_overlap(self) -> None:
        block = self.compare_second()
        self.assertGreater(block["overlap_pictures"], 0)
        self.assertEqual(block["unfair_reason"], comparison.overlap_reason(
            block["overlap_pictures"], len(self.second_test), block["baseline"]["trained_on_pictures"]))

    def test_removing_every_train_and_val_entry_from_the_stored_manifest_makes_it_unfair_not_fair(self) -> None:
        self.damage(lambda d: d.update(images=[i for i in d["images"] if i["split"] == "test"]))
        self.assert_unfair(self.compare_second(), comparison.UNFAIR_DAMAGED_REASON)

    def test_removing_one_entry_or_changing_one_sha_is_also_caught(self) -> None:
        for name, mutate in (("one entry removed", lambda d: d["images"].pop(0)),
                             ("one sha changed", lambda d: d["images"][0].update(sha256="0" * 64))):
            with self.subTest(name):
                self.damage(mutate)
                self.assert_unfair(self.compare_second(), comparison.UNFAIR_DAMAGED_REASON)

    def test_a_wrong_image_count_alone_is_caught(self) -> None:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {type(self.first)._meta.db_table} SET image_count = image_count + 1 WHERE id = %s", [self.first.pk.hex])
        self.assert_unfair(self.compare_second(), comparison.UNFAIR_DAMAGED_REASON)

    def test_a_consistent_manifest_with_no_train_or_val_picture_is_unknown_not_fair(self) -> None:
        """Checksum and count recomputed by whoever damaged it: the list is intact but says nothing was taught."""
        self.damage(lambda d: d.update(images=[i for i in d["images"] if i["split"] == "test"]), fix_checksum=True, fix_count=True)
        self.assert_unfair(self.compare_second(), comparison.UNFAIR_EMPTY_REASON)
        self.damage(lambda d: d.update(images=[]), fix_checksum=True, fix_count=True)
        self.assert_unfair(self.compare_second(), comparison.UNFAIR_EMPTY_REASON)


class EveryLabelNeedsBoxesTests(ComparisonBase):
    def test_a_project_label_with_no_test_box_makes_the_verdict_cannot_tell(self) -> None:
        """A4, end to end: the project has a second label that nobody drew a box for."""
        self.project.labels = ["damaged", "normal"]
        self.project.save()
        dataset = self.freeze()
        test_numbers = self.numbers(dataset, "test")
        self.assertGreaterEqual(len(test_numbers), 31)
        self.baseline_in_use(dataset, test_numbers[:15])
        ScriptB.finds = frozenset(test_numbers)  # every damaged box found: far more than the margin
        block = self.block(self.teach(ScriptB, dataset))
        self.assertEqual([r["of"] for r in block["per_label"]], [len(test_numbers), 0])
        self.assertTrue(block["fair"])
        self.assertEqual(block["verdict"], "cannot_tell")
        self.assertIn("Cannot tell: “normal” has no test boxes.", block["plain"])


class SkipAndFailureTests(ComparisonBase):
    def setUp(self) -> None:
        super().setUp()
        self.dataset = self.freeze()
        self.t = self.numbers(self.dataset, "test")

    def assert_run_fine_without_comparison(self, model: ModelVersion, *, log_contains: str) -> None:
        self.assertNotIn("comparison", model.evaluation)
        self.assertTrue(model.evaluation["available"])
        self.assertIn(log_contains, self.last_run.logs)
        self.assertNotIn(self.home.name, self.last_run.logs)
        self.assertEqual(report.plain_results(model.evaluation)["comparison"],
                         {"available": False, "reason": comparison.NOT_SAVED_REASON})

    def test_no_model_in_use_means_no_key_and_a_log_line(self) -> None:
        ScriptB.finds = frozenset(self.t)
        model = self.teach(ScriptB, self.dataset)
        self.assert_run_fine_without_comparison(model, log_contains="no model is in use yet")

    def test_a_staged_or_retired_model_is_not_the_model_in_use(self) -> None:
        old = self.baseline_in_use(self.dataset, self.t[:5])
        newer = self.put_in_use(self.teach(ScriptB, self.dataset))  # retires `old`
        self.assertEqual(ModelVersion.objects.get(pk=old.pk).stage, "retired")
        ScriptA.calls = 0
        third = self.teach(ScriptB, self.dataset)
        self.assertEqual(third.evaluation["comparison"]["baseline"]["model_id"], str(newer.id))  # the approved one only
        self.assertEqual(ScriptA.calls, 0)  # the retired one was not run

    def test_an_evaluation_with_no_test_split_stores_nothing(self) -> None:
        dataset = self.freeze(seed=0, shares=(0.7, 0.3, 0.0))
        self.assertEqual(self.numbers(dataset, "test"), [])
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptB.finds = frozenset(self.t)
        model = self.teach(ScriptB, dataset)
        self.assertFalse(model.evaluation["available"])
        self.assertNotIn("comparison", model.evaluation)
        self.assertEqual(report.plain_results(model.evaluation)["comparison"]["reason"], comparison.NO_RESULTS_REASON)

    def test_a_changed_file_of_the_model_in_use_stores_an_unavailable_block_rather_than_an_absent_key_and_is_not_run(self) -> None:
        baseline = self.baseline_in_use(self.dataset, self.t[:5])
        lifecycle.artifact_file(baseline).write_bytes(b"tampered")
        ScriptB.finds = frozenset(self.t)
        model = self.teach(ScriptB, self.dataset)
        self.assertEqual(model.evaluation["comparison"],
                         {"schema": comparison.SCHEMA, "available": False, "reason": comparison.BASELINE_FILE_REASON})
        self.assertEqual(ScriptA.calls, 0, "a file that does not match its checksum is never run")
        self.assertEqual(report.plain_results(model.evaluation)["comparison"],
                         {"available": False, "reason": comparison.BASELINE_FILE_REASON})
        self.assertTrue(model.evaluation["available"])
        self.assertNotIn(self.home.name, json.dumps(model.evaluation["comparison"]))

    def test_a_missing_file_of_the_model_in_use_stores_an_unavailable_block_rather_than_an_absent_key(self) -> None:
        baseline = self.baseline_in_use(self.dataset, self.t[:5])
        lifecycle.artifact_file(baseline).unlink()
        model = self.teach(ScriptB, self.dataset)
        self.assertFalse(model.evaluation["comparison"]["available"])

    def test_a_backend_that_raises_on_the_model_in_use_leaves_the_run_succeeded_and_the_key_absent(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptA.fail = "boom while reading {path}"
        ScriptB.finds = frozenset(self.t)
        model = self.teach(ScriptB, self.dataset)
        self.assert_run_fine_without_comparison(model, log_contains="Could not compare with the model in use")
        self.assertEqual(model.evaluation["overall"]["true_positives"], len(self.t))

    def test_a_backend_that_cannot_run_here_is_skipped_silently(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptA.available = False
        model = self.teach(ScriptB, self.dataset)
        self.assert_run_fine_without_comparison(model, log_contains="its backend (script-a) cannot run here")

    def test_a_backend_that_is_no_longer_installed_is_skipped_silently(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        training.unregister_backend(ScriptA.name)
        model = self.teach(ScriptB, self.dataset)
        self.assert_run_fine_without_comparison(model, log_contains="its backend (script-a) cannot run here")

    def test_a_backend_that_cannot_score_other_pictures_is_skipped(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        with mock.patch.object(ScriptA, "predict_split", return_value=None):
            model = self.teach(ScriptB, self.dataset)
        self.assert_run_fine_without_comparison(model, log_contains="cannot score these pictures")

    def test_a_damaged_candidate_evaluation_is_isolated_like_any_other_fault(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        with mock.patch.object(comparison, "_group_counts", side_effect=ValueError("bad \u00e7 /tmp/secret/x")):
            model = self.teach(ScriptB, self.dataset)
        self.assertNotIn("comparison", model.evaluation)
        self.assertIn("Could not compare with the model in use", self.last_run.logs)

    def test_cancel_is_honoured_between_the_passes(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        original = runner.work_out_operating_points

        def then_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            TrainingRun.objects.filter(status="running").update(cancel_requested=True)
            return result

        ScriptA.calls = 0
        with mock.patch.object(runner, "work_out_operating_points", side_effect=then_cancel):
            response = self.post(self.api("/training/runs"), {"backend": "script-b", "dataset_id": str(self.dataset.id)})
            self.assertEqual(response.status_code, 202)
            finished = runner.run_training(runner.claim_next_run())
        self.assertEqual(finished.status, TrainingRun.CANCELLED)
        self.assertEqual(ScriptA.calls, 0, "the model in use was not run after the cancel")
        self.assertEqual(ModelVersion.objects.filter(stage="candidate").count(), 0)

    def run_with_slow_pass(self, *, slow_class, limit: float = 5.0, cancel: bool = False, no_miss_list: bool = False):
        """Run a new model while ``slow_class.predict_split`` seems to take 100 seconds when the comparison calls it
        (a clock the test controls; the real run takes well under ``limit``)."""
        import inspect
        from django.test import override_settings

        clock = [0.0]
        real = runner.time.monotonic
        original = slow_class.predict_split

        def slow(self_, *args, **kwargs):
            result = original(self_, *args, **kwargs)
            if any(frame.function == "compare_with_approved" for frame in inspect.stack()):
                clock[0] += 100.0
                if cancel:
                    TrainingRun.objects.filter(status="running").update(cancel_requested=True)
            return result

        patches = [mock.patch.object(runner.time, "monotonic", lambda: real() + clock[0]),
                   mock.patch.object(slow_class, "predict_split", slow)]
        if no_miss_list:
            patches.append(mock.patch.object(comparison, "_candidate_found_from_outcomes", return_value=None))
        response = self.post(self.api("/training/runs"), {"backend": "script-b", "dataset_id": str(self.dataset.id)})
        self.assertEqual(response.status_code, 202)
        claimed = runner.claim_next_run()
        with override_settings(TRAINING={"RUN_TIMEOUT_SECONDS": limit}):
            for patch in patches:
                patch.start()
            try:
                return runner.run_training(claimed)
            finally:
                for patch in reversed(patches):
                    patch.stop()

    def test_the_time_limit_is_honoured_after_the_pass_over_the_model_in_use(self) -> None:
        """A3: a pass that ran past the limit ends the run as timed out; nothing is saved, like the other passes."""
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptB.finds = frozenset(self.t)
        ScriptA.calls = 0
        finished = self.run_with_slow_pass(slow_class=ScriptA)
        self.assertEqual(finished.status, TrainingRun.FAILED)
        self.assertIn("time limit", finished.error)
        self.assertEqual(ModelVersion.objects.filter(stage="candidate").count(), 0)
        self.assertEqual(ScriptA.calls, len(self.t), "the pass itself did run once")

    def test_the_time_limit_is_honoured_after_the_extra_pass_over_the_new_model(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptB.finds = frozenset(self.t)
        ScriptB.calls = 0
        # slow only the SECOND use of the new model's backend: its own evaluation comes first, then the extra pass
        finished = self.run_with_slow_pass(slow_class=ScriptB, no_miss_list=True)
        self.assertEqual(finished.status, TrainingRun.FAILED)
        self.assertIn("time limit", finished.error)
        self.assertEqual(ModelVersion.objects.filter(stage="candidate").count(), 0)

    def test_a_cancel_during_the_pass_over_the_model_in_use_ends_the_run_cancelled(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptB.finds = frozenset(self.t)
        ScriptA.calls = 0
        finished = self.run_with_slow_pass(slow_class=ScriptA, cancel=True, limit=10_000.0)
        self.assertEqual(finished.status, TrainingRun.CANCELLED)
        self.assertEqual(ModelVersion.objects.filter(stage="candidate").count(), 0)

    def test_a_pass_that_finishes_inside_the_limit_still_succeeds(self) -> None:
        self.baseline_in_use(self.dataset, self.t[:5])
        ScriptB.finds = frozenset(self.t)
        model = self.teach(ScriptB, self.dataset)
        self.assertIn("comparison", model.evaluation)

    def test_cancel_and_time_limit_are_not_swallowed_by_the_isolation(self) -> None:
        run = TrainingRun.objects.create(project=self.project, dataset_version=self.dataset, backend="script-b",
                                         config={}, config_sha256="x", dataset_manifest_sha256=self.dataset.manifest_sha256)
        log = runner.RunLog(run)
        for error in (TrainingCancelled("stop"), runner.TrainingTimedOut("late")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(comparison, "compare_with_approved", side_effect=error):
                    with self.assertRaises(type(error)):
                        runner.work_out_comparison(run, ScriptB(), None, [object()], ["damaged"], {"available": True}, log)
        with mock.patch.object(comparison, "compare_with_approved", side_effect=RuntimeError("x")):
            self.assertIsNone(runner.work_out_comparison(run, ScriptB(), None, [object()], ["damaged"], {"available": True}, log))

    def test_the_check_runs_before_the_pass_over_the_model_in_use(self) -> None:
        baseline = self.baseline_in_use(self.dataset, self.t[:5])
        evaluation, _ = synthetic_evaluation({"damaged": (40, 20, 0)})
        candidate = comparison.Candidate(ScriptB(), None, evaluation, ["damaged"], self.dataset.manifest)
        calls = []
        ScriptA.calls = 0
        with self.assertRaises(TrainingCancelled):
            comparison.compare_with_approved(self.project, candidate, [object()], check=lambda: (calls.append(1), (_ for _ in ()).throw(TrainingCancelled("x"))))
        self.assertEqual((calls, ScriptA.calls), ([1], 0))
        self.assertEqual(ModelVersion.objects.get(pk=baseline.pk).stage, "approved")
