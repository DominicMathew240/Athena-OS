"""Operating points: which confidence cut-off to use, and how often each score was right, from measured data.

PROTOTYPE, built and checked on synthetic pictures only. Nothing here calibrates a score (no Platt, no isotonic) and
nothing changes a live model: the numbers are stored in the model's evaluation document under ``operating_points``
and a person still chooses every setting.

What is measured (numpy only; see ``docs/guides/model-quality.md``):

* **Thresholds** come from the VALIDATION ("Checking") split. Every prediction the model made on those pictures at its
  0.05 floor is matched to the person-drawn boxes by ``evaluation.match_image`` (IoU 0.5, same label, greedy by
  score). For a target share found ("recall") the suggested cut-off is the *highest* score whose recall on that split is
  at least the target (anything lower only adds wrong boxes). Recall counts ALL drawn boxes, so boxes the model never
  proposed count against it. The highest recall any cut-off can reach (``recall_ceiling``) is stored; a target above
  it is ``reachable: false`` and says so.
* **Bucket precision** (how often a score in a range was right) comes from the TEST ("Final test") split, because
  numbers fitted on the validation split drift on new pictures.
* **Uncertainty**: 90% percentile intervals from a bootstrap over PICTURES (boxes of one picture are not
  independent), deterministic (``BOOTSTRAP_SEED``) and vectorised. When a test split exists the interval is for the
  test recall at the cut-off that the resampled checking pictures would have chosen; without one it is the resampled
  checking recall (optimistic, and says so in ``basis``).
* **Bucket ranges** need at least ``MIN_PREDICTIONS_PER_BUCKET`` predictions each AND a test split of at least
  ``MIN_TEST_PICTURES`` pictures and ``MIN_TEST_BOXES`` drawn boxes; a weak model makes hundreds of low-score
  predictions from a handful of pictures, and those say little. Otherwise every range is ``enough: false``.
* **Enough data**: a cut-off is recommended only if the checking split has at least ``MIN_VALIDATION_BOXES`` boxes in
  total AND ``MIN_VALIDATION_BOXES_PER_LABEL`` for every label AND there is a test split (with drawn boxes) to check it
  on. Below that the numbers are still stored but nothing is ``recommended`` and ``enough_data`` is false (``why_not``
  says which). ``recommended`` additionally needs the target reached in at least ``MIN_SHARE_OF_REDRAWS_REACHED`` of
  the re-draws (``interval90.resamples_used`` of ``interval90.resamples``); a test recall below the target is
  worded in ``plain`` but does not by itself remove the recommendation.
* **Rounding**: nothing is rounded in the product's favour. Cut-offs, stored shares, percentages and the low end of an
  interval are rounded DOWN, the high end of an interval UP (``floor4``, ``ceil4``, ``share_down``, ``pct_down``,
  ``pct_up``). Scores that are not finite numbers are dropped and the rest clipped into 0 to 1.

Stored shape (``evaluation["operating_points"]``, all keys additive, JSON only)::

    {schema, source: "validation split", bucket_source: "test split",
     records, ground_truth_boxes, pictures, boxes_per_label, prediction_floor, iou_threshold,
     recall_ceiling, minimum_boxes, enough_data, why_not,
     test: {pictures, ground_truth_boxes, records} | None,
     bootstrap: {by: "picture", resamples, seed, level},
     suggestions: [{target_recall, reachable, threshold, enough_data, recommended,
                    validation: {precision, recall, n_gt} | None, test: {precision, recall, n_gt} | None,
                    interval90: {recall: [lo, hi] | None, precision: [lo, hi] | None, resamples_used, resamples, basis} | None,
                    test_at_usual_cutoff: {threshold: 0.5, precision, recall, n_gt} | None,
                    plain}],
     bucket_edges, bucket_precision: [{low, high, predictions, correct, precision, interval90, enough}]}

Reading side (pure Python, no numpy; safe on every old or damaged document): ``read_operating_points``,
``wording_table`` (Results page), ``confidence_wording`` and ``suggested_threshold`` (check-screen render model).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from . import evaluation as evaluation_lib

np = evaluation_lib.np  # lazy: this module is also read on installs that have no numpy

SCHEMA = "athena.operating_points/1"
SOURCE = "validation split"
BUCKET_SOURCE = "test split"
#: Target shares of the drawn boxes found. Shown in this order.
TARGET_RECALLS = (0.80, 0.90, 0.95)
#: The one target whose cut-off is offered as the check screen's suggested default (never applied automatically).
SUGGESTED_TARGET_RECALL = 0.80
#: Total checking-split boxes needed before a cut-off is recommended. The spike (docs/guides/model-quality.md) found
#: 630 boxes gave about +-4 recall points and 150 boxes about +-7-8; "at least 400 boxes" was its practical minimum.
MIN_VALIDATION_BOXES = 400
#: ...and at least this many for EVERY label: one pooled cut-off hides a label with a handful of boxes. This reuses the
#: minimum the plain verdict already asks of the test split (the spike's "~100 per class" is a looser remark that the
#: 400 total already implies on average).
MIN_VALIDATION_BOXES_PER_LABEL = evaluation_lib.MIN_BOXES_PER_LABEL_FOR_VERDICT
#: Fixed score ranges for "how often was a score in this range right". ``low`` is inclusive, ``high`` exclusive
#: except the last range, which includes 1.0. Wider where the spike found most predictions (below 0.5), 0.1 wide where
#: decisions are made; the spike's 2,722 test predictions put at least 114 in every 0.1-wide range.
BUCKET_EDGES = (0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
#: A range with fewer predictions than this is stored but marked ``enough: false`` (a single mistake moves it 5 points).
MIN_PREDICTIONS_PER_BUCKET = 20
#: The test split itself must be big enough to say anything (the bins are counts of predictions, and a weak model makes
#: hundreds of low-score predictions from a handful of pictures): at least this many pictures and drawn boxes in all,
#: the same minimums the rest of the product uses for the test side. Otherwise every range is ``enough: false``.
MIN_TEST_PICTURES = evaluation_lib.MIN_IMAGES_FOR_CONFIDENCE
MIN_TEST_BOXES = evaluation_lib.MIN_BOXES_PER_LABEL_FOR_VERDICT
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20261023
INTERVAL_LEVEL = 0.90
#: Wilson interval multiplier for 90% (two-sided).
WILSON_Z = 1.6449
#: Fewer valid resamples than this and no interval is stated.
MIN_RESAMPLES_FOR_INTERVAL = 20
#: A cut-off is recommended only if the target was reached in at least this share of the re-drawn pictures; below it
#: the cut-off depends too much on which pictures happened to be in the Checking split.
MIN_SHARE_OF_REDRAWS_REACHED = 0.90
#: The setting most people start from; the Final test split is also scored at it so the 80% suggestion can be compared.
USUAL_CUTOFF = 0.5

CHECKING = "Checking pictures"
FINAL_TEST = "Final test pictures"

NOT_MEASURED_NOTE = "Scores are the computer's own numbers; they have not been checked against pictures it has not seen."
_NOT_MEASURED_REASON = (
    "How often each score was right is not measured for this model (it was checked before this was added, or its "
    "backend could not score the pictures)."
)
_UNREADABLE_REASON = "The saved score checks could not be read, so how often each score was right is not measured."
_NO_TEST_REASON = "There were no Final test pictures to measure how often each score was right, so it is not measured."
_NO_TEST_CHECK_REASON = (
    "There were no Final test pictures, so a cut-off chosen on the Checking pictures has not been checked on pictures "
    "the model did not see."
)


# -- the maths (numpy) -------------------------------------------------------------------------------------------
@dataclass
class ScoredSplit:
    """Every prediction on one split: which picture, its score, and whether it matched a drawn box."""

    n_images: int
    gt_per_image: Any  # int array, one count per picture (only the labels being scored)
    gt_per_label: dict[str, int]
    image: Any  # int array, one per prediction
    score: Any  # float array
    hit: Any  # bool array

    @property
    def n_gt(self) -> int:
        return int(self.gt_per_image.sum()) if self.n_images else 0

    @property
    def n_records(self) -> int:
        return int(len(self.score))

    @classmethod
    def from_records(cls, image, score, hit, gt_per_image, gt_per_label: dict[str, int] | None = None) -> "ScoredSplit":
        gt = np.asarray(gt_per_image, dtype=np.int64).reshape(-1)
        return cls(
            n_images=int(len(gt)),
            gt_per_image=gt,
            gt_per_label=dict(gt_per_label or {}),
            image=np.asarray(image, dtype=np.int64).reshape(-1),
            score=np.asarray(score, dtype=float).reshape(-1),
            hit=np.asarray(hit, dtype=bool).reshape(-1),
        )


def _usable_score(value: Any) -> bool:
    """A score is usable if it is a real, finite number (a NaN, an infinity or a missing score is dropped)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def score_split(ground_truth, predictions, labels, iou: float = evaluation_lib.IOU_THRESHOLD) -> ScoredSplit:
    """Match every prediction to the drawn boxes with ``evaluation.match_image`` (the SAME function the test numbers
    use) and keep one record per prediction. Inputs are shaped like ``evaluation.evaluate_detections``'. A prediction
    whose score is not a finite number is dropped; other scores are clipped into 0 to 1 (a cut-off outside that range
    could never be read back)."""
    labels = list(labels)
    if len(ground_truth) != len(predictions):
        raise ValueError("ground_truth and predictions must describe the same images.")
    gt_per_image = np.zeros(len(ground_truth), dtype=np.int64)
    gt_per_label = {label: 0 for label in labels}
    image, score, hit = [], [], []
    for label in labels:
        for index, (gt_image, pred_image) in enumerate(zip(ground_truth, predictions)):
            gt = [box for name, box in gt_image if name == label]
            gt_per_image[index] += len(gt)
            gt_per_label[label] += len(gt)
            preds = [(box, _clip01(s)) for name, box, s in pred_image if name == label and _usable_score(s)]
            if preds:
                for s, matched in evaluation_lib.match_image([p[0] for p in preds], [p[1] for p in preds], gt, iou):
                    image.append(index)
                    score.append(s)
                    hit.append(matched)
    return ScoredSplit.from_records(image, score, hit, gt_per_image, gt_per_label)


def _order(split: ScoredSplit):
    """Highest score first; ties keep their recorded order (stable), so the result never depends on dict order."""
    return np.argsort(-split.score, kind="stable")


def _need(target: float, n_gt):
    """Correct boxes needed to reach ``target`` of ``n_gt`` (exact integer; no float edge at e.g. 0.8 * 630)."""
    return np.ceil(np.asarray(target, dtype=float) * n_gt - 1e-9)


def pick_threshold(split: ScoredSplit, target: float) -> float | None:
    """Highest score ``t`` such that the predictions scoring ``>= t`` find at least ``target`` of ALL drawn boxes;
    ``None`` when even every prediction does not (the target is above the model's ceiling) or there are no boxes."""
    n_gt = split.n_gt
    if n_gt <= 0 or split.n_records == 0:
        return None
    order = _order(split)
    scores, tp = split.score[order], np.cumsum(split.hit[order])
    group_end = np.append(scores[1:] != scores[:-1], True)  # take whole groups of tied scores
    ok = np.nonzero(group_end & (tp >= _need(target, n_gt)))[0]
    return None if ok.size == 0 else float(scores[ok[0]])


def recall_ceiling(split: ScoredSplit) -> float | None:
    """The most boxes any cut-off can find: every prediction counted, over all drawn boxes."""
    return None if split.n_gt <= 0 else float(split.hit.sum()) / split.n_gt


def precision_recall_at(split: ScoredSplit, threshold: float) -> dict[str, Any]:
    """Precision (correct / drawn, ``None`` when nothing was drawn) and recall at ``score >= threshold``."""
    keep = split.score >= threshold
    drawn, correct = int(keep.sum()), int(split.hit[keep].sum())
    return {
        "precision": None if drawn == 0 else share_down(correct, drawn),
        "recall": None if split.n_gt == 0 else share_down(correct, split.n_gt),
        "n_gt": split.n_gt,
    }


def round_threshold_down(value: float) -> float:
    """4 decimals, rounded DOWN so the prediction that set the cut-off is still included."""
    return math.floor(value * 1e4 + 1e-6) / 1e4


floor4 = round_threshold_down


def ceil4(value: float) -> float:
    """4 decimals, rounded UP (the high end of a range)."""
    return math.ceil(value * 1e4 - 1e-6) / 1e4


def share_down(correct: int, total: int) -> float:
    """``correct / total`` to 4 decimals, rounded DOWN (exact integer arithmetic): a stored share never flatters."""
    return correct * 10000 // total / 10000


def wilson_interval(correct: int, n: int, z: float = WILSON_Z) -> list[float] | None:
    """Wilson score interval for a share of ``n`` (90% by default). ``None`` for ``n == 0``."""
    if n <= 0:
        return None
    p = correct / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def test_split_is_adequate(pictures: Any, boxes: Any) -> bool:
    """Whether a test split of this size can back any score-range statement at all."""
    return _is_count(pictures) and _is_count(boxes) and pictures >= MIN_TEST_PICTURES and boxes >= MIN_TEST_BOXES


def bucket_precision(
    split: ScoredSplit, edges: tuple[float, ...] = BUCKET_EDGES, *, adequate: bool = True
) -> list[dict[str, Any]]:
    """How often a prediction scoring in each range was right (counts, share, Wilson 90% interval, enough).

    ``enough`` needs ``MIN_PREDICTIONS_PER_BUCKET`` predictions in the range AND an ``adequate`` test split."""
    rows = []
    last = len(edges) - 2
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        inside = (split.score >= low) & ((split.score <= high) if index == last else (split.score < high))
        n, correct = int(inside.sum()), int(split.hit[inside].sum())
        rows.append(
            {
                "low": low,
                "high": high,
                "predictions": n,
                "correct": correct,
                "precision": None if n == 0 else share_down(correct, n),
                "interval90": wilson_interval(correct, n),
                "enough": adequate and n >= MIN_PREDICTIONS_PER_BUCKET,
            }
        )
    return rows


def interval_ends(values, low: float, high: float) -> list[float] | None:
    """The ``low`` and ``high`` percentiles of the resampled values, rounded OUTWARD to 4 decimals (low down, high
    up); ``None`` when fewer than ``MIN_RESAMPLES_FOR_INTERVAL`` resamples are left."""
    if len(values) < MIN_RESAMPLES_FOR_INTERVAL:
        return None
    return [floor4(float(np.percentile(values, low))), ceil4(float(np.percentile(values, high)))]


def bootstrap_intervals(
    val: ScoredSplit,
    test: ScoredSplit | None,
    targets: tuple[float, ...] = TARGET_RECALLS,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = INTERVAL_LEVEL,
) -> dict[float, dict[str, Any]]:
    """Percentile intervals for recall and precision at the cut-off each target would choose, resampling PICTURES.

    Each resample draws checking pictures (and test pictures) with replacement, re-picks the cut-off from the
    resampled checking pictures and measures it on the resampled test pictures (or, with no test split, on the same
    checking resample: optimistic). Resampled pictures are turned into integer weights, so a resample costs one
    cumulative sum rather than a re-sort. Resamples where a target cannot be reached are left out
    (``resamples_used`` counts the rest). The cut-off used in each resample is rounded DOWN to 4 decimals exactly as the
    stored cut-off is, so the interval describes the setting that is stored. The ends of an interval are rounded
    outward (low down, high up). Deterministic for a given ``seed``.
    """
    out: dict[float, dict[str, Any]] = {}
    basis = (
        "checking and Final test pictures resampled; cut-off re-chosen each time"
        if test is not None
        else "checking pictures resampled (no Final test pictures, so optimistic)"
    )
    if val.n_images == 0 or val.n_records == 0 or val.n_gt == 0:
        return {
            t: {"recall": None, "precision": None, "resamples_used": 0, "resamples": resamples, "basis": basis}
            for t in targets
        }
    rng = np.random.default_rng(seed)
    t_img = t_hit = t_neg = tp_test = n_test = gt_test = n_val = None  # set below when they apply
    v_order = _order(val)
    v_score, v_hit, v_img = val.score[v_order], val.hit[v_order].astype(float), val.image[v_order]
    v_end = np.append(v_score[1:] != v_score[:-1], True)
    v_neg = -v_score  # ascending, for searchsorted
    if test is not None:
        t_order = _order(test)
        t_score, t_hit, t_img = test.score[t_order], test.hit[t_order].astype(float), test.image[t_order]
        t_neg = -t_score  # ascending, for searchsorted
    n_records = val.n_records + (test.n_records if test is not None else 0)
    block = max(1, min(resamples, 1_000_000 // max(1, n_records)))
    found: dict[float, dict[str, list]] = {t: {"recall": [], "precision": []} for t in targets}

    def counts(n_images: int, rows: int):
        picks = rng.integers(0, n_images, size=(rows, n_images)) + (np.arange(rows) * n_images)[:, None]
        return np.bincount(picks.ravel(), minlength=rows * n_images).reshape(rows, n_images)

    done = 0
    while done < resamples:
        rows = min(block, resamples - done)
        done += rows
        r = np.arange(rows)
        c_val = counts(val.n_images, rows)
        gt_val = c_val @ val.gt_per_image
        w_val = c_val[:, v_img]
        tp_val = np.cumsum(w_val * v_hit, axis=1)
        if test is not None:
            c_test = counts(test.n_images, rows) if test.n_images else np.zeros((rows, 0), dtype=np.int64)
            gt_test = c_test @ test.gt_per_image
            w_test = c_test[:, t_img]
            tp_test, n_test = np.cumsum(w_test * t_hit, axis=1), np.cumsum(w_test, axis=1)
        else:
            n_val = np.cumsum(w_val, axis=1)
        for target in targets:
            ok = (tp_val >= _need(target, gt_val)[:, None]) & v_end[None, :]
            first = ok.argmax(axis=1)
            reach = ok[r, first] & (gt_val > 0)
            chosen = np.floor(v_score[first] * 1e4 + 1e-6) / 1e4  # the stored (rounded-down) cut-off
            if test is not None:
                k = np.searchsorted(t_neg, -chosen, side="right")  # test predictions scoring >= the cut-off
                at = np.maximum(k - 1, 0)
                correct = np.where(k > 0, tp_test[r, at] if tp_test.shape[1] else 0, 0)
                drawn = np.where(k > 0, n_test[r, at] if n_test.shape[1] else 0, 0)
                gt_for = gt_test
            else:
                k = np.searchsorted(v_neg, -chosen, side="right")  # checking predictions scoring >= the cut-off
                at = np.maximum(k - 1, 0)
                correct, drawn, gt_for = np.where(k > 0, tp_val[r, at], 0), np.where(k > 0, n_val[r, at], 0), gt_val
            good = reach & (gt_for > 0)
            found[target]["recall"].append((correct / np.where(gt_for > 0, gt_for, 1))[good])
            has_drawn = reach & (drawn > 0)
            found[target]["precision"].append((correct / np.where(drawn > 0, drawn, 1))[has_drawn])
    low, high = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    for target in targets:
        recall = np.concatenate(found[target]["recall"]) if found[target]["recall"] else np.array([])
        precision = np.concatenate(found[target]["precision"]) if found[target]["precision"] else np.array([])

        def band(values):
            return interval_ends(values, low, high)

        out[target] = {
            "recall": band(recall),
            "precision": band(precision),
            "resamples_used": int(len(recall)),
            "resamples": resamples,
            "basis": basis,
        }
    return out


# -- plain wording (pure Python; derived from the stored numbers only) -------------------------------------------
def pct_down(value: float | None) -> str:
    """Whole percent, rounded DOWN (the one helper for every share in a sentence, like ``report._pct_down``): a share
    just under a target is never shown as the target."""
    return "not measurable" if value is None else f"{math.floor(value * 100 + 1e-6)}%"


def pct_up(value: float) -> str:
    """Whole percent, rounded UP: for the high end of a range and for a ceiling ('no more than')."""
    return f"{math.ceil(value * 100 - 1e-6)}%"


_pct = pct_down


def _boxes(count: int) -> str:
    return "1 box" if count == 1 else f"{count} boxes"


def _why_not_enough(per_label: dict[str, int], total: int) -> str:
    short = [f"“{label}” has {count}" for label, count in per_label.items() if count < MIN_VALIDATION_BOXES_PER_LABEL]
    text = (
        "Too few checking boxes to choose a threshold you can trust: "
        f"{total} drawn boxes on the Checking pictures, and at least {MIN_VALIDATION_BOXES} are needed"
    )
    if short:
        text += f", with at least {MIN_VALIDATION_BOXES_PER_LABEL} for every label ({', '.join(short)})"
    return text + "."


def _range_text(ival: dict[str, Any]) -> str:
    """'(90% range 74% to 83%; the target was reached in all 1000 re-draws of the pictures)' - never silent when the
    range is missing, and always says in how many re-draws of the pictures the target was reached."""
    used, total = ival.get("resamples_used"), ival.get("resamples")
    counted = _is_count(used) and _is_count(total) and total > 0
    band = ival.get("recall")
    if _is_band(band):
        text = f"90% range {pct_down(band[0])} to {pct_up(band[1])}"
        if not counted:
            return f" ({text})"
        if used >= total:
            return f" ({text}; the target was reached in all {total} re-draws of the pictures)"
        return f" ({text}, among the {used} of {total} re-draws of the pictures that reached the target)"
    if counted:
        return f" (range not available: the target was reached in only {used} of {total} re-draws of the pictures)"
    return " (range not available)"


def _precision_range_text(ival: dict[str, Any]) -> str:
    band = ival.get("precision")
    return f" (90% range {pct_down(band[0])} to {pct_up(band[1])})" if _is_band(band) else ""


def _is_band(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_number(v) for v in value)


def _wrong_sentence(precision: float | None, split: str) -> str:
    """'At this setting about 4 in 10 of the boxes it draws are wrong on the Final test pictures.' The wrong share is
    worked out from the percent shown (rounded down) and read as the next fraction UP, so it never flatters."""
    if precision is None:
        return ""
    wrong = (100 - math.floor(precision * 100 + 1e-6)) / 100
    if wrong <= 0:
        return ""
    if wrong > _ODDS[-1][0]:
        return f" At this setting nearly all of the boxes it draws are wrong on the {split}."
    fraction = next(label for value, label in _ODDS if value >= wrong - 1e-9)
    return f" At this setting about {fraction} of the boxes it draws are wrong on the {split}."


def suggestion_sentence(
    item: dict[str, Any], *, ceiling: float | None, why_not: str
) -> str:
    """One plain sentence for one stored suggestion. Never says accurate, certified or safe."""
    target = pct_down(item["target_recall"])
    if not item["reachable"]:
        if ceiling is None:
            text = f"No cut-off can be chosen to find {target} of the boxes: there are no drawn boxes to measure against."
        else:
            best = "any of the drawn boxes" if ceiling == 0 else f"more than {pct_up(ceiling)} of the drawn boxes"
            text = f"On the {CHECKING}, no cut-off found {best}, so a target of {target} cannot be reached."
        return text if item["enough_data"] else f"{text} {why_not}"
    val, test, ival = item["validation"], item.get("test"), item.get("interval90") or {}
    text = (
        f"A cut-off of {item['threshold']:g} found {pct_down(val['recall'])} of the {_boxes(val['n_gt'])} on the "
        f"{CHECKING}"
    )
    on_test = bool(test) and test.get("recall") is not None
    if on_test:
        text += f" and {pct_down(test['recall'])} of the {_boxes(test['n_gt'])} on the {FINAL_TEST}"
        text += _range_text(ival)
        if test.get("precision") is not None:
            text += f"; {pct_down(test['precision'])} of the boxes it drew there were right"
            text += _precision_range_text(ival)
    elif val.get("precision") is not None:
        text += f"; {pct_down(val['precision'])} of the boxes it drew there were right"
    text += "."
    if on_test and test["recall"] < item["target_recall"] - 1e-9:
        text += f" On the {FINAL_TEST} it fell short of the {target} target."
    if item["enough_data"] and not item["recommended"]:
        text += " This cut-off is not recommended: the target was missed in many re-draws of the pictures."
    if on_test:
        text += _wrong_sentence(test.get("precision"), FINAL_TEST)
    else:
        text += _wrong_sentence(val.get("precision"), CHECKING)
    text += " The same cut-off applies to every label."
    usual = item.get("test_at_usual_cutoff")
    if (
        isinstance(usual, dict)
        and usual.get("threshold") != item["threshold"]
        and usual.get("recall") is not None
        and usual.get("precision") is not None
    ):
        text += (
            f" For comparison, at the usual {usual['threshold']:g} setting it found {pct_down(usual['recall'])} of the "
            f"boxes, and {pct_down(usual['precision'])} of the boxes it drew were right ({FINAL_TEST})."
        )
    text += " A person chooses the setting."
    return text if item["enough_data"] else f"{why_not} {text}"


#: Everyday fractions a measured share is described by. A share is read DOWN to the next one (a good result is never
#: rounded up to a better fraction); a wrong share is read UP.
_ODDS = (
    (0.05, "1 in 20"), (0.1, "1 in 10"), (0.2, "2 in 10"), (0.25, "1 in 4"), (0.3, "3 in 10"), (0.4, "4 in 10"),
    (0.5, "1 in 2"), (0.6, "6 in 10"), (0.7, "7 in 10"), (0.75, "3 in 4"), (0.8, "8 in 10"), (0.9, "9 in 10"),
    (0.95, "19 in 20"),
)


def odds_label(precision: float, low: float | None = None) -> str:
    """Neutral, quantitative wording for a measured share right: 'right about 3 in 4 times in our check'.

    The fraction is the largest one at or below the share - and, when the 90% interval's ``low`` end is given, at or
    below that, so a share measured on few predictions reads cautiously. Never 'at least'."""
    if precision < 0.05:
        return "right fewer than 1 in 20 times in our check"
    basis = precision if low is None else min(precision, low)
    fraction = next((label for value, label in reversed(_ODDS) if value <= basis + 1e-9), _ODDS[0][1])
    return f"right about {fraction} times in our check"


def bucket_sentence(row: dict[str, Any]) -> str:
    """'Scores between 0.8 and 0.9 were right 76% of the time on the Final test pictures (96 boxes).'"""
    span = f"Scores between {row['low']:g} and {row['high']:g}"
    if not row["enough"] and row["predictions"] >= MIN_PREDICTIONS_PER_BUCKET:  # the test split itself was too small
        return f"{span}: too few Final test pictures to say how often they were right."
    if not row["enough"]:
        count = "no boxes" if row["predictions"] == 0 else f"only {_boxes(row['predictions'])}"
        return f"{span}: {count} on the {FINAL_TEST}, too few to say how often they were right."
    return f"{span} were right {pct_down(row['precision'])} of the time on the {FINAL_TEST} ({_boxes(row['predictions'])})."


# -- build the stored document -----------------------------------------------------------------------------------
def build_operating_points(
    val_ground_truth,
    val_predictions,
    test_ground_truth,
    test_predictions,
    labels,
    *,
    iou: float = evaluation_lib.IOU_THRESHOLD,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """The document stored under ``evaluation["operating_points"]``. ``test_*`` may be ``None`` (no test pictures)."""
    labels = list(labels)
    val = score_split(val_ground_truth, val_predictions, labels, iou)
    test = None
    if test_ground_truth is not None and len(test_ground_truth):
        test = score_split(test_ground_truth, test_predictions, labels, iou)
    short = [label for label, count in val.gt_per_label.items() if count < MIN_VALIDATION_BOXES_PER_LABEL]
    has_test = test is not None and test.n_gt > 0  # a test split with no drawn boxes cannot check anything
    enough_boxes = val.n_gt >= MIN_VALIDATION_BOXES and not short
    enough = enough_boxes and has_test
    reasons = ([] if enough_boxes else [_why_not_enough(val.gt_per_label, val.n_gt)]) + ([] if has_test else [_NO_TEST_CHECK_REASON])
    why_not = " ".join(reasons)
    ceiling = recall_ceiling(val)
    intervals = bootstrap_intervals(val, test, TARGET_RECALLS, resamples=resamples, seed=seed)
    suggestions = []
    for target in TARGET_RECALLS:
        threshold = pick_threshold(val, target)
        item: dict[str, Any] = {
            "target_recall": target,
            "reachable": threshold is not None,
            "threshold": None,
            "enough_data": enough,
            "recommended": False,
            "validation": None,
            "test": None,
            "interval90": None,
        }
        if threshold is not None:
            threshold = round_threshold_down(threshold)
            item["threshold"] = threshold
            item["validation"] = precision_recall_at(val, threshold)
            item["test"] = None if test is None else precision_recall_at(test, threshold)
            item["test_at_usual_cutoff"] = (
                None if test is None else {"threshold": USUAL_CUTOFF, **precision_recall_at(test, USUAL_CUTOFF)}
            )
            item["interval90"] = intervals[target]
            # enough boxes on the Checking pictures, a Final test split to check on, and the target reached in nearly
            # every re-draw of the pictures
            item["recommended"] = enough and intervals[target]["resamples_used"] >= MIN_SHARE_OF_REDRAWS_REACHED * resamples
        item["plain"] = suggestion_sentence(item, ceiling=ceiling, why_not=why_not)
        suggestions.append(item)
    return {
        "schema": SCHEMA,
        "source": SOURCE,
        "bucket_source": BUCKET_SOURCE,
        "records": val.n_records,
        "ground_truth_boxes": val.n_gt,
        "pictures": val.n_images,
        "boxes_per_label": val.gt_per_label,
        "prediction_floor": evaluation_lib.PREDICTION_FLOOR,
        "iou_threshold": iou,
        "recall_ceiling": None if ceiling is None else round(ceiling, 4),
        "minimum_boxes": {"total": MIN_VALIDATION_BOXES, "per_label": MIN_VALIDATION_BOXES_PER_LABEL},
        "enough_data": enough,
        "why_not": why_not,
        "test": None
        if test is None
        else {"pictures": test.n_images, "ground_truth_boxes": test.n_gt, "records": test.n_records},
        "bootstrap": {"by": "picture", "resamples": resamples, "seed": seed, "level": INTERVAL_LEVEL},
        "suggestions": suggestions,
        "bucket_edges": list(BUCKET_EDGES),
        "bucket_precision": []
        if test is None
        else bucket_precision(test, adequate=test_split_is_adequate(test.n_images, test.n_gt)),
    }


def collect(backend, artifact, val_items, test_items, labels, thresholds, *, step=None) -> dict[str, Any] | None:
    """Run the backend over the validation (and test) pictures and build the document.

    ``None`` when the backend cannot predict on a split (``backend.predict_split`` returns ``None``) or there are no
    checking pictures: the key is then simply absent from the evaluation, as for every model made before this existed.
    ``step(message)`` is called before each pass (it may log, and may raise to stop: the run's cancel and time limit).
    """
    if not val_items:
        return None
    total = 2 if test_items else 1
    if step:
        step(f"Scoring the {CHECKING} (1 of {total})")
    val = backend.predict_split(artifact, val_items, labels, thresholds)
    if val is None:
        return None
    test = None
    if test_items:
        if step:
            step(f"Scoring the {FINAL_TEST} (2 of {total})")
        test = backend.predict_split(artifact, test_items, labels, thresholds)
    test_gt, test_pred = test if test is not None else (None, None)
    return build_operating_points(val[0], val[1], test_gt, test_pred, labels)


# -- reading a stored document (pure Python) ---------------------------------------------------------------------
def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _all_finite(node: Any) -> bool:
    if isinstance(node, float):
        return math.isfinite(node)
    if isinstance(node, dict):
        return all(_all_finite(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return all(_all_finite(v) for v in node)
    return True


def _sound(block: dict[str, Any]) -> bool:
    """No NaN or infinity anywhere, and every stored cut-off a real number from 0 to 1 (or null)."""
    if not _all_finite(block):
        return False
    suggestions = block.get("suggestions")
    for item in suggestions if isinstance(suggestions, list) else []:
        value = item.get("threshold") if isinstance(item, dict) else None
        if value is not None and not (_is_number(value) and 0 <= value <= 1):
            return False
    return True


def read_operating_points(evaluation: Any) -> dict[str, Any] | None:
    """The stored block if it is present, of the known schema and holds only finite numbers, else ``None`` (old or
    damaged documents)."""
    block = evaluation.get("operating_points") if isinstance(evaluation, dict) else None
    if not isinstance(block, dict) or block.get("schema") != SCHEMA or not _sound(block):
        return None
    return block


def read_buckets(evaluation: Any) -> list[dict[str, Any]] | None:
    """Checked bucket rows, re-derived from their stored counts (precision and ``enough`` are not trusted as stored).
    ``None`` when absent or damaged; ``[]`` when there was no test split."""
    block = read_operating_points(evaluation)
    if block is None or not isinstance(block.get("bucket_precision"), list):
        return None
    test = block.get("test")
    adequate = isinstance(test, dict) and test_split_is_adequate(test.get("pictures"), test.get("ground_truth_boxes"))
    rows = []
    try:
        for raw in block["bucket_precision"]:
            low, high, n, correct = raw["low"], raw["high"], raw["predictions"], raw["correct"]
            if not (_is_number(low) and _is_number(high) and 0 <= low < high <= 1):
                return None
            if not (_is_count(n) and _is_count(correct) and correct <= n):
                return None
            rows.append(
                {
                    "low": float(low),
                    "high": float(high),
                    "predictions": n,
                    "correct": correct,
                    "precision": None if n == 0 else share_down(correct, n),
                    "interval90": wilson_interval(correct, n),
                    "enough": adequate and n >= MIN_PREDICTIONS_PER_BUCKET,
                }
            )
    except (KeyError, TypeError):
        return None
    rows.sort(key=lambda row: row["low"])
    if any(a["high"] > b["low"] for a, b in zip(rows, rows[1:])):  # overlapping ranges: do not guess
        return None
    return rows


def unavailable_reason(evaluation: Any) -> str:
    """Why nothing is measured: never stored (older model, backend could not score) or stored but unreadable."""
    block = evaluation.get("operating_points") if isinstance(evaluation, dict) else None
    return _NOT_MEASURED_REASON if block is None else _UNREADABLE_REASON


def wording_table(evaluation: Any) -> dict[str, Any]:
    """For the Results page: one measured sentence per score range, or ``{available: False, reason}``."""
    rows = read_buckets(evaluation)
    if rows is None:
        return {"available": False, "reason": unavailable_reason(evaluation), "split": FINAL_TEST, "buckets": []}
    if not rows:
        return {"available": False, "reason": _NO_TEST_REASON, "split": FINAL_TEST, "buckets": []}
    return {
        "available": True,
        "reason": "",
        "split": FINAL_TEST,
        "buckets": [{**row, "sentence": bucket_sentence(row)} for row in rows],
    }


def confidence_wording(evaluation: Any) -> dict[str, Any]:
    """The check screen's score bands from measured precision.

    Contract (the screen reads this instead of fixed 0.9 / 0.75 / 0.5 edges)::

        {measured: bool, bands: [{low, high, label, precision, n, enough}], note: str}

    * ``measured`` is true only if at least one band has enough predictions to say anything.
    * ``bands`` run from the lowest score upward, ``low`` inclusive and ``high`` exclusive (the last band includes
      1.0). A detection belongs to the band holding its score. ``label`` is neutral and quantitative ('right about
      3 in 4 times in our check'), or 'too few examples to say' when ``enough`` is false (then ``precision`` is still
      given but must not be worded as a result). ``n`` is how many predictions the band was measured on.
    * Not measured (an older model, a backend that could not score, too few predictions): ``measured: false``,
      ``bands: []`` and ``note`` is ``NOT_MEASURED_NOTE``; the screen must then show the raw score as the computer's
      own number and nothing stronger.
    """
    rows = read_buckets(evaluation)
    if not rows or not any(row["enough"] for row in rows):
        return {"measured": False, "bands": [], "note": NOT_MEASURED_NOTE}
    bands = [
        {
            "low": row["low"],
            "high": row["high"],
            "label": odds_label(row["precision"], (row["interval90"] or [None])[0]) if row["enough"] else "too few examples to say",
            "precision": row["precision"],
            "n": row["predictions"],
            "enough": row["enough"],
        }
        for row in rows
    ]
    return {
        "measured": True,
        "bands": bands,
        "note": (
            f"Measured on the {FINAL_TEST} only, not on new cameras, lighting or products. "
            "The score is the computer's own number, not a probability. A person decides."
        ),
    }


def suggested_threshold(evaluation: Any) -> dict[str, Any]:
    """Read-only hint for the check-screen builder (the person still sets the value; nothing is applied).

    ``{value, target_recall, basis, enough_data, plain}``. ``value`` is ``None`` unless a cut-off for
    ``SUGGESTED_TARGET_RECALL`` was reachable AND it was ``recommended`` (enough checking boxes, a test split, and the
    target reached in nearly every re-draw). The builder's own 0 to 1 validation is unchanged.
    """
    block = read_operating_points(evaluation)
    none = {"value": None, "target_recall": None, "basis": "not measured", "enough_data": False}
    if block is None:
        return {**none, "plain": "No cut-off is suggested: " + unavailable_reason(evaluation)}
    try:
        item = next(s for s in block["suggestions"] if s["target_recall"] == SUGGESTED_TARGET_RECALL)
        value = item["threshold"]
        usable = bool(item["recommended"]) and bool(item["enough_data"]) and item["reachable"] and _is_number(value)
        if value is not None and not (_is_number(value) and 0 <= value <= 1):
            raise ValueError("threshold")
        return {
            "value": float(value) if usable and value is not None else None,
            "target_recall": SUGGESTED_TARGET_RECALL,
            "basis": SOURCE,
            "enough_data": bool(item["enough_data"]),
            "plain": str(item["plain"]),
        }
    except (KeyError, TypeError, StopIteration, ValueError):
        return {**none, "plain": "No cut-off is suggested: " + _UNREADABLE_REASON}


def plain_operating_points(evaluation: Any) -> dict[str, Any]:
    """The stored block for the Results page: ``{available: True, **stored}``, or ``{available: False, reason}``."""
    block = read_operating_points(evaluation)
    if block is None:
        return {"available": False, "reason": unavailable_reason(evaluation)}
    return {"available": True, **block}
