"""Compare a newly taught model with the model currently in use, on the SAME held-out pictures.

PROTOTYPE, checked on synthetic pictures only. Nothing here changes a live model: the result is stored under
``evaluation["comparison"]`` of the new candidate, as information for the person who decides.

What is measured
----------------
* The model in use (the project's ``approved`` model) is run through ITS OWN backend on the new model's Final test
  pictures, and scored with the very code and settings the new model was scored with (``evaluation.predict_items`` /
  ``evaluate_detections`` / ``match_image_detail``, the same confidence and IoU), so both are judged identically.
* **Paired on the same boxes.** For every person-drawn test box we know whether each model found it. ``b`` is the
  number of boxes ONLY the new model found, ``c`` the number ONLY the model in use found. ``b - c`` is exactly the
  difference in boxes found. Identical boxes on one picture are grouped and counted in the way that shows the
  fewest disagreements (never more difference than the data proves).
* **Fairness.** The model in use may have been taught from, or checked on, some of the new model's test pictures
  (the same bytes, found by SHA-256 in the dataset manifest of the run that made it). Then it has seen part of the
  test, the comparison is marked ``fair: false`` and the verdict is ``cannot_tell``. A model whose training pictures
  are unknown (no run or manifest, e.g. imported) is unfair for the same reason, and so is one whose stored manifest
  is damaged (its checksum or picture count no longer matches, the same test as ``datasets.service.verify_integrity``)
  or lists no Training or Checking picture at all: "saw nothing" is never read from a list that may have lost
  entries. Near-duplicates are not detected. ``trained_on_pictures`` counts DIFFERENT pictures (unique SHA-256).

Verdict (conservative; every rule below is applied to counts, never to rounded shares)::

    cannot_tell    not fair, or no test boxes, or ANY label of the model has fewer than
                   evaluation.MIN_BOXES_PER_LABEL_FOR_VERDICT test boxes (none at all counts: the same rule as
                   ``report._verdict``)
    better         b - c >= max(MIN_DIFFERENCE_BOXES, MIN_DIFFERENCE_SHARE of the test boxes)
    worse          c - b >= the same margin
    about_the_same anything else

The number of wrong boxes is reported but never changes the verdict; the sentence says so when the new model drew
many more of them.

Reading side (pure Python, no numpy; safe on every old or damaged document): ``plain_comparison``. The reader never
trusts a stored verdict or sentence: both are derived again from the stored counts, and no stored free text reaches
the page: the unfair sentence is rebuilt from the overlap counts and fixed wording (a stored reason is only matched
against the fixed variants), an "unavailable" reason is shown only when it is one of ours, and labels, ids and
backend names must have the shape of the real thing.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable

from . import evaluation as evaluation_lib

logger = logging.getLogger(__name__)

SCHEMA = "athena.comparison/1"
VERDICTS = ("better", "about_the_same", "worse", "cannot_tell")

#: A model is called better (or worse) only when it found at least this many MORE boxes the other missed than the
#: other way round, AND at least ``MIN_DIFFERENCE_SHARE`` of all the test boxes, whichever is larger. A handful of
#: boxes is what one lucky or unlucky picture can swing.
MIN_DIFFERENCE_BOXES = 3
#: 10% of the test boxes (exact fraction; the margin is rounded UP to a whole box).
MIN_DIFFERENCE_SHARE = Fraction(1, 10)
#: Wrong boxes: "many more" is judged against the same margin as the found boxes. Reported, never a verdict input.
#: (The minimum test boxes per label is evaluation.MIN_BOXES_PER_LABEL_FOR_VERDICT, read when needed.)

NOT_SAVED_REASON = (
    "No comparison with a model in use was saved for this model (none was in use when it was taught, it was checked "
    "before comparisons existed, or the model in use could not be run)."
)
NO_RESULTS_REASON = "There are no held-out test results for this model, so there is nothing to compare."
UNREADABLE_REASON = "The saved comparison with the model in use could not be read, so it is not shown."
BASELINE_FILE_REASON = (
    "The model in use was not compared: its saved file is missing or no longer matches the checksum recorded when "
    "it was approved, so it was not run."
)
NO_COMPARISON_REASON = "No comparison is available for this model."
#: The only "not compared" reasons the writer stores. The reader shows one of these or ``NO_COMPARISON_REASON``.
KNOWN_UNAVAILABLE_REASONS = (BASELINE_FILE_REASON,)

_CANNOT_RULE_OUT = "so it cannot be ruled out that it has already seen the test pictures."
UNFAIR_UNKNOWN_REASON = f"It is not known which pictures the model in use was taught from, {_CANNOT_RULE_OUT}"
UNFAIR_EMPTY_REASON = (
    f"The list of pictures the model in use was taught from is missing or empty, {_CANNOT_RULE_OUT}"
)
UNFAIR_DAMAGED_REASON = (
    "The saved list of pictures the model in use was taught from has changed since it was made (it no longer matches "
    f"its checksum or picture count), {_CANNOT_RULE_OUT}"
)
UNFAIR_TEST_UNKNOWN_REASON = (
    "It is not known which pictures the new model was tested on, so it cannot be ruled out that the model in use "
    "has already seen them."
)
UNFAIR_GENERIC_REASON = "It cannot be ruled out that the model in use has already seen the test pictures."
#: Every fixed unfair sentence (the "N of the M test pictures were also used" one is built from the counts).
KNOWN_UNFAIR_REASONS = (
    UNFAIR_UNKNOWN_REASON, UNFAIR_EMPTY_REASON, UNFAIR_DAMAGED_REASON, UNFAIR_TEST_UNKNOWN_REASON, UNFAIR_GENERIC_REASON,
)

_BACKEND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")
_MODEL_ID = re.compile(r"[0-9a-fA-F][0-9a-fA-F\-]{15,63}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def overlap_reason(overlap: int, test_pictures: int, trained_on: int) -> str:
    """The unfair sentence when the model in use has seen some test pictures; only counts go into it."""
    verb = "was" if overlap == 1 else "were"
    text = (
        f"{overlap} of the {test_pictures} test {'picture' if test_pictures == 1 else 'pictures'} {verb} also used to "
        "teach or check the model in use, so it has already seen part of this test."
    )
    if trained_on:
        text += f" The model in use was taught from or checked on {trained_on} different {'picture' if trained_on == 1 else 'pictures'}."
    return text


@dataclass
class Candidate:
    """What the worker knows about the model that was just taught (it is not yet a ``ModelVersion`` row)."""

    backend: Any  # the TrainingBackend instance that trained it
    artifact: Any  # path of its artifact in the work directory
    evaluation: dict  # its own held-out evaluation (from ``backend.evaluate``)
    labels: list[str]
    manifest: dict | None = None  # frozen manifest of the dataset version it was taught from
    extra: dict = field(default_factory=dict)


# -- the rules (pure) ----------------------------------------------------------------------------------------
def margin_needed(test_boxes: int) -> int:
    """Boxes of difference needed (``b - c``) before one model is called better: the larger of the fixed minimum
    and 10% of the test boxes, rounded UP to a whole box."""
    return max(MIN_DIFFERENCE_BOXES, math.ceil(MIN_DIFFERENCE_SHARE * test_boxes))


def decide(
    *, fair: bool, boxes_per_label: dict[str, int], test_pictures: int, only_candidate: int, only_baseline: int
) -> tuple[str, str]:
    """``(verdict, reason)``; ``reason`` is ``unfair``, ``no_test``, ``too_few``, ``better``, ``worse`` or ``same``."""
    total = sum(boxes_per_label.values())
    if not fair:
        return "cannot_tell", "unfair"
    if test_pictures <= 0 or total <= 0:
        return "cannot_tell", "no_test"
    minimum = evaluation_lib.MIN_BOXES_PER_LABEL_FOR_VERDICT
    # Every label of the model counts, one with no test box at all too: the same rule as ``report._verdict``.
    if any(count < minimum for count in boxes_per_label.values()):
        return "cannot_tell", "too_few"
    gap = only_candidate - only_baseline
    need = margin_needed(total)
    if gap >= need:
        return "better", "better"
    if -gap >= need:
        return "worse", "worse"
    return "about_the_same", "same"


def _pct(found: int, total: int) -> str:
    from . import report

    return report._pct_down(found, total)  # noqa: SLF001 - the one whole-percent-rounded-DOWN helper


def sentence(block: dict[str, Any], verdict: str, reason: str) -> str:
    """The plain sentence for a comparison (all numbers from ``block``); always ends with the not-final line."""
    from . import report

    plural = report._plural  # noqa: SLF001
    n = block["test_boxes"]
    new, old = block["candidate_result"], block["baseline_result"]
    diff = block["difference"]
    b, c = diff["only_candidate_found"], diff["only_baseline_found"]
    common = (
        f"the new model found {new['found']} of {plural(n, 'test box', 'test boxes')} ({_pct(new['found'], n)}) "
        f"and the model in use found {old['found']} ({_pct(old['found'], n)})"
    )
    need = margin_needed(n)
    more_wrong = new["wrong_boxes"] - old["wrong_boxes"]
    wrong_note = ""
    if more_wrong >= need:
        wrong_note = (
            f" The new model also drew {more_wrong} more extra or misplaced boxes than the model in use "
            f"({new['wrong_boxes']} against {old['wrong_boxes']}); extra boxes do not change this verdict."
        )
    if verdict == "cannot_tell":
        if reason == "unfair":
            text = f"Cannot tell, and this comparison is not fair: {block['unfair_reason']}"
        elif reason == "no_test":
            text = "Cannot tell: there are no held-out test boxes to compare the two models on."
        else:
            minimum = evaluation_lib.MIN_BOXES_PER_LABEL_FOR_VERDICT
            short = [
                f"“{row['label']}” has " + ("no test boxes" if row["of"] == 0 else plural(row["of"], "test box", "test boxes"))
                for row in block["per_label"]
                if row["of"] < minimum
            ]
            text = (
                f"Cannot tell: {'; '.join(short)}. About {minimum} per label are needed before two models can be "
                "compared."
            )
        return f"{text} For information only: {common}.{wrong_note} {report.NOT_FINAL_DECISION}"
    disagreed = b + c
    if disagreed == 0:
        split = "The two models found exactly the same boxes."
    else:
        split = (
            f"The two models disagreed on {plural(disagreed, 'box', 'boxes')}: the new model alone found {b}, "
            f"the model in use alone found {c}."
        )
    if verdict == "better":
        text = f"Better on these test pictures: {common}. {split}"
    elif verdict == "worse":
        text = f"Worse on these test pictures: {common}. {split}"
    else:
        text = (
            f"About the same on these test pictures: {common}. {split} "
            f"One model is called better only when it found at least {plural(need, 'box', 'boxes')} more that the "
            "other missed."
        )
    return f"{text}{wrong_note} {report.NOT_FINAL_DECISION}"


# -- numpy side: running the model in use ----------------------------------------------------------------------
def _share(found: int, total: int) -> float | None:
    from .operating_points import share_down

    return None if total == 0 else share_down(found, total)


def _result(found: int, of: int, wrong: int) -> dict[str, Any]:
    """Counts plus shares rounded DOWN (``recall`` of the boxes drawn by people, ``precision`` of the boxes drawn)."""
    return {
        "found": found,
        "of": of,
        "wrong_boxes": wrong,
        "recall": _share(found, of),
        "precision": _share(found, found + wrong),
    }


def _key(item: Any, index: int, label: str, box) -> tuple:
    asset = getattr(item, "asset_id", None)
    return (str(asset) if asset else f"#{index}", label, tuple(evaluation_lib._round_box(box)))  # noqa: SLF001


def _group_counts(items, ground_truth, predictions, labels, confidence: float, iou: float) -> tuple[Counter, Counter]:
    """Per person-drawn box (grouped by picture, label and exact box): how many there are and how many were found.

    The matching rule is ``evaluation.match_image_detail`` (the rule behind every stored count): all predictions of
    a label are matched greedily, highest score first, and a box counts as found when the prediction that took it
    scored at or above ``confidence``."""
    total: Counter = Counter()
    found: Counter = Counter()
    for index, (item, gt_image, pred_image) in enumerate(zip(items, ground_truth, predictions)):
        for label in labels:
            gt = [box for name, box in gt_image if name == label]
            preds = [(box, score) for name, box, score in pred_image if name == label]
            matched = {
                g
                for _, score, g in evaluation_lib.match_image_detail(
                    [p[0] for p in preds], [p[1] for p in preds], gt, iou
                )
                if g is not None and score >= confidence
            }
            for g, box in enumerate(gt):
                key = _key(item, index, label, box)
                total[key] += 1
                if g in matched:
                    found[key] += 1
    return total, found


def _candidate_found_from_outcomes(evaluation: dict, total: Counter, items) -> Counter | None:
    """The new model's found boxes from the per-picture list its evaluation already stored, so it needs no second
    pass. ``None`` unless that list is complete (not cut short), readable and agrees with the stored counts."""
    if not all(getattr(item, "asset_id", None) for item in items):
        return None
    block = evaluation.get("held_out_outcomes")
    if not isinstance(block, dict) or not isinstance(block.get("items"), list):
        return None
    missed_total = block.get("missed_total")
    if isinstance(missed_total, bool) or not isinstance(missed_total, int):
        return None
    try:
        missed = Counter(
            (str(row["asset_id"]), row["label"], tuple(round(float(v), 6) for v in row["box"]))
            for row in block["items"]
            if row["kind"] == "missed"
        )
    except (KeyError, TypeError, ValueError):
        return None
    if sum(missed.values()) != missed_total or missed_total != evaluation["overall"]["false_negatives"]:
        return None
    if any(count > total.get(key, 0) for key, count in missed.items()):
        return None
    return Counter({key: count - missed.get(key, 0) for key, count in total.items()})


def _say(log, message: str) -> None:
    logger.info("Comparison with the model in use: %s", message)
    if log is not None:
        log.add(message)
        log.flush(force=True)


def _seen_hashes(manifest: Any) -> set[str] | None:
    """SHA-256 of every picture a model saw while learning (its Training and Checking pictures), from the frozen
    manifest of the dataset it was made from. ``None`` when the manifest is not readable."""
    images = manifest.get("images") if isinstance(manifest, dict) else None
    if not isinstance(images, list):
        return None
    seen: set[str] = set()
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("sha256"), str):
            return None
        if image.get("split") in ("train", "val"):
            seen.add(image["sha256"])
    return seen


def _test_hashes(manifest: Any) -> list[str] | None:
    images = manifest.get("images") if isinstance(manifest, dict) else None
    if not isinstance(images, list):
        return None
    out = []
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("sha256"), str):
            return None
        if image.get("split") == "test":
            out.append(image["sha256"])
    return out


def _manifest_intact(version: Any) -> bool:
    """Is the stored manifest of the dataset version the model in use was made from still what was recorded when the
    version was created? The same two tests as ``datasets.service.verify_integrity``: the checksum of the manifest
    and the number of pictures. Anything unreadable or missing is "not intact"."""
    from ..datasets import manifest as dataset_manifest

    stored = getattr(version, "manifest", None)
    expected_sum, expected_count = getattr(version, "manifest_sha256", None), getattr(version, "image_count", None)
    if not isinstance(stored, dict) or not isinstance(expected_sum, str) or isinstance(expected_count, bool):
        return False
    try:
        if dataset_manifest.sha256_hex(stored) != expected_sum:
            return False
    except (TypeError, ValueError):
        return False
    images = stored.get("images")
    return isinstance(images, list) and expected_count == len(images)


def _fairness(baseline_model, candidate_manifest: Any) -> tuple[bool, str, int, int]:
    """``(fair, reason, overlap_pictures, trained_on_pictures)``; ``trained_on_pictures`` counts DIFFERENT pictures.

    Fair only when the model in use is known to have been made from an intact manifest that lists at least one
    Training or Checking picture and none of the new model's test pictures is among them."""
    run = baseline_model.training_run
    version = getattr(run, "dataset_version", None) if run is not None else None
    if version is None:
        return False, UNFAIR_UNKNOWN_REASON, 0, 0
    if not _manifest_intact(version):
        return False, UNFAIR_DAMAGED_REASON, 0, 0
    seen = _seen_hashes(version.manifest)
    if seen is None:
        return False, UNFAIR_UNKNOWN_REASON, 0, 0
    if not seen:  # "it saw nothing" is never concluded from a list with nothing in it
        return False, UNFAIR_EMPTY_REASON, 0, 0
    test = _test_hashes(candidate_manifest)
    if test is None:
        return False, UNFAIR_TEST_UNKNOWN_REASON, 0, len(seen)
    overlap = sum(1 for sha in test if sha in seen)
    if overlap:
        return False, overlap_reason(overlap, len(test), len(seen)), overlap, len(seen)
    return True, "", 0, len(seen)


def compare_with_approved(
    project,
    candidate: Candidate,
    test_items,
    *,
    thresholds: dict[str, Any] | None = None,
    log=None,
    check: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    """Run the project's approved model over the new model's test pictures and compare, honestly.

    Returns the block to store under ``evaluation["comparison"]``; ``{schema, available: False, reason}`` when the
    model in use is known but its file cannot be trusted; ``None`` (nothing stored, a line in the log) when there is
    nothing to compare with or the model in use cannot run here. Raises only what ``check`` raises (cancel, time
    limit) or on an unexpected fault: the caller isolates those (see ``runner.work_out_comparison``).

    ``check`` is called before AND after each pass over the pictures (a pass cannot be interrupted part-way, so a
    limit that ran out during it ends the run before anything is stored). A model that hangs inside a pass is not
    caught here: that needs a mechanism outside this function (a separate process or thread with a deadline)."""
    from .. import models as db
    from . import get_backend
    from .lifecycle import LifecycleError, verify_artifact

    evaluation = candidate.evaluation
    if not test_items or not isinstance(evaluation, dict) or not evaluation.get("available"):
        _say(log, "No comparison with the model in use: there are no held-out test results.")
        return None
    baseline = (
        db.ModelVersion.objects.filter(project=project, stage=db.ModelVersion.APPROVED)
        .select_related("training_run__dataset_version")
        .first()
    )
    if baseline is None:
        _say(log, "No comparison with the model in use: no model is in use yet.")
        return None
    try:
        artifact = verify_artifact(baseline)
    except LifecycleError:
        _say(log, f"Could not compare with version {baseline.version}, the model in use: its file cannot be trusted.")
        # Documented deviation: here the evaluation gets ``{available: false, reason}`` and not an absent key (every
        # other "nothing to compare" case stores nothing), so the page can say WHY the model in use was not run.
        return {"schema": SCHEMA, "available": False, "reason": BASELINE_FILE_REASON}
    backend = None
    try:
        backend = get_backend(baseline.backend)
        availability = backend.check_available()
    except Exception:  # noqa: BLE001 - an unknown or broken backend means "cannot run here", never a failed run
        availability = None
    if backend is None or availability is None or not availability.ok:
        _say(log, f"No comparison with the model in use: its backend ({baseline.backend}) cannot run here.")
        return None

    labels = list(candidate.labels)
    confidence = float(evaluation["confidence_threshold"])
    iou = float(evaluation["iou_threshold"])
    thresholds = dict(thresholds or {"confidence": confidence, "max_boxes": 100})
    thresholds["confidence"] = confidence

    if check:
        check()
    _say(log, f"Comparing with version {baseline.version}, the model in use, on the same {len(test_items)} test pictures")
    split = backend.predict_split(artifact, list(test_items), labels, thresholds)
    if check:
        check()  # the pass may have run past the time limit, or been cancelled meanwhile
    if split is None:
        _say(log, "No comparison with the model in use: its backend cannot score these pictures.")
        return None
    ground_truth, base_predictions = split
    base_eval = evaluation_lib.evaluate_detections(ground_truth, base_predictions, labels, confidence=confidence, iou=iou)
    total, base_found = _group_counts(test_items, ground_truth, base_predictions, labels, confidence, iou)

    cand_found = _candidate_found_from_outcomes(evaluation, total, test_items)
    if cand_found is None:  # the stored list of misses is absent or cut short: one more pass of the NEW model
        if check:
            check()
        _say(log, "Scoring the new model again, box by box, so the two can be paired")
        again = candidate.backend.predict_split(candidate.artifact, list(test_items), labels, thresholds)
        if check:
            check()
        if again is None:
            _say(log, "No comparison with the model in use: the new model cannot be scored box by box.")
            return None
        cand_found = _group_counts(test_items, again[0], again[1], labels, confidence, iou)[1]

    overall_new, overall_old = evaluation["overall"], base_eval["overall"]
    n_boxes = sum(total.values())
    # The paired counts must agree with the counts both evaluations report; if not, something is wrong: store nothing.
    if (
        n_boxes != evaluation["data"]["boxes"]
        or sum(cand_found.values()) != overall_new["true_positives"]
        or sum(base_found.values()) != overall_old["true_positives"]
    ):
        raise ValueError("The paired counts do not agree with the evaluations.")
    only_new = sum(max(0, cand_found[k] - base_found[k]) for k in total)
    only_old = sum(max(0, base_found[k] - cand_found[k]) for k in total)

    fair, reason, overlap, trained_on = _fairness(baseline, candidate.manifest)
    if overlap:  # the reader rebuilds this sentence from ``test_pictures``: say the very same thing
        reason = overlap_reason(overlap, len(test_items), trained_on)
    boxes_per_label = {label: evaluation["per_label"][label]["ground_truth_boxes"] for label in labels}
    block: dict[str, Any] = {
        "schema": SCHEMA,
        "available": True,
        "baseline": {
            "model_id": str(baseline.id),
            "version": baseline.version,
            "artifact_sha256": baseline.artifact_sha256,
            "backend": baseline.backend,
            "trained_on_pictures": trained_on,
        },
        "test_pictures": len(test_items),
        "test_boxes": n_boxes,
        "fair": fair,
        "unfair_reason": reason,
        "overlap_pictures": overlap,
        "baseline_result": _result(
            overall_old["true_positives"], n_boxes, overall_old["false_positives"]
        ),
        "candidate_result": _result(
            overall_new["true_positives"], n_boxes, overall_new["false_positives"]
        ),
        "per_label": [
            {
                "label": label,
                "baseline_found": base_eval["per_label"][label]["true_positives"],
                "candidate_found": evaluation["per_label"][label]["true_positives"],
                "of": boxes_per_label[label],
            }
            for label in labels
        ],
        "difference": {
            "found": overall_new["true_positives"] - overall_old["true_positives"],
            "wrong_boxes": overall_new["false_positives"] - overall_old["false_positives"],
            "only_candidate_found": only_new,
            "only_baseline_found": only_old,
        },
    }
    verdict, why = decide(
        fair=fair, boxes_per_label=boxes_per_label, test_pictures=len(test_items),
        only_candidate=only_new, only_baseline=only_old,
    )
    block["verdict"] = verdict
    block["plain"] = sentence(block, verdict, why)
    return block


# -- reading side (pure Python) ----------------------------------------------------------------------------------
def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number_or_none(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1
    )


def _read_result(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("result")
    found, of, wrong = row["found"], row["of"], row["wrong_boxes"]
    if not (_count(found) and _count(of) and _count(wrong)) or found > of:
        raise ValueError("result counts")
    # The shares are derived again from the counts (rounded DOWN); a stored share is not trusted.
    return _result(found, of, wrong)


def _validate(block: Any, evaluation: dict[str, Any]) -> dict[str, Any]:
    """A checked copy of a stored comparison with its verdict and sentence derived again from its counts.

    ``ValueError``/``KeyError``/``TypeError`` when anything is malformed or disagrees with the evaluation it sits in."""
    if not isinstance(block, dict) or block.get("schema") != SCHEMA or block.get("available") is not True:
        raise ValueError("schema")
    base = block["baseline"]
    if not isinstance(base, dict):
        raise ValueError("baseline")
    if not (
        isinstance(base["model_id"], str)
        and _MODEL_ID.fullmatch(base["model_id"])
        and _count(base["version"])
        and isinstance(base["artifact_sha256"], str)
        and _SHA256_HEX.fullmatch(base["artifact_sha256"])
        and isinstance(base["backend"], str)
        and _BACKEND_NAME.fullmatch(base["backend"])
        and _count(base["trained_on_pictures"])
    ):
        raise ValueError("baseline fields")
    pictures, boxes, overlap = block["test_pictures"], block["test_boxes"], block["overlap_pictures"]
    fair, unfair_reason = block["fair"], block["unfair_reason"]
    if not (_count(pictures) and _count(boxes) and _count(overlap)) or not isinstance(fair, bool):
        raise ValueError("counts")
    if not isinstance(unfair_reason, str) or len(unfair_reason) > 2000:
        raise ValueError("reason")
    if overlap > pictures or (overlap and fair) or (not fair and not unfair_reason):
        raise ValueError("fairness")
    # No stored sentence is shown: the overlap one is rebuilt from the counts, any other must be one of ours.
    if fair:
        unfair_reason = ""
    elif overlap:
        unfair_reason = overlap_reason(overlap, pictures, base["trained_on_pictures"])
    elif unfair_reason not in KNOWN_UNFAIR_REASONS:
        unfair_reason = UNFAIR_GENERIC_REASON
    old, new = _read_result(block["baseline_result"]), _read_result(block["candidate_result"])
    if old["of"] != boxes or new["of"] != boxes:
        raise ValueError("boxes")
    rows = block["per_label"]
    if not isinstance(rows, list):
        raise ValueError("per_label")
    known_labels = evaluation["per_label"]  # a label is shown only if the evaluation it sits in has it too
    if not isinstance(known_labels, dict):
        raise ValueError("evaluation labels")
    per_label = []
    for row in rows:
        label, bf, cf, of = row["label"], row["baseline_found"], row["candidate_found"], row["of"]
        if not isinstance(label, str) or not (_count(bf) and _count(cf) and _count(of)) or bf > of or cf > of:
            raise ValueError("label row")
        if label not in known_labels or any(label == other["label"] for other in per_label):
            raise ValueError("label")
        per_label.append({"label": label, "baseline_found": bf, "candidate_found": cf, "of": of})
    if (
        sum(r["of"] for r in per_label) != boxes
        or sum(r["baseline_found"] for r in per_label) != old["found"]
        or sum(r["candidate_found"] for r in per_label) != new["found"]
    ):
        raise ValueError("labels do not add up")
    diff = block["difference"]
    only_new, only_old = diff["only_candidate_found"], diff["only_baseline_found"]
    if not (_count(only_new) and _count(only_old)):
        raise ValueError("pairs")
    if (
        diff["found"] != new["found"] - old["found"]
        or diff["wrong_boxes"] != new["wrong_boxes"] - old["wrong_boxes"]
        or only_new - only_old != diff["found"]
        or only_new > new["found"]
        or only_old > old["found"]
        or only_new + only_old > boxes
    ):
        raise ValueError("difference")
    # It must be about the evaluation it is stored in: the same test pictures, boxes and counts.
    data, overall = evaluation["data"], evaluation["overall"]
    if (
        data["images"] != pictures
        or data["boxes"] != boxes
        or overall["true_positives"] != new["found"]
        or overall["false_positives"] != new["wrong_boxes"]
    ):
        raise ValueError("not this evaluation")
    out = {
        "schema": SCHEMA,
        "baseline": {key: base[key] for key in ("model_id", "version", "artifact_sha256", "backend", "trained_on_pictures")},
        "test_pictures": pictures,
        "test_boxes": boxes,
        "fair": fair,
        "unfair_reason": unfair_reason,
        "overlap_pictures": overlap,
        "baseline_result": old,
        "candidate_result": new,
        "per_label": per_label,
        "difference": {
            "found": diff["found"],
            "wrong_boxes": diff["wrong_boxes"],
            "only_candidate_found": only_new,
            "only_baseline_found": only_old,
        },
    }
    verdict, why = decide(
        fair=fair,
        boxes_per_label={r["label"]: r["of"] for r in per_label},
        test_pictures=pictures,
        only_candidate=only_new,
        only_baseline=only_old,
    )
    out["verdict"] = verdict
    out["plain"] = sentence(out, verdict, why)
    return out


def plain_comparison(evaluation: Any, *, no_results: bool = False) -> dict[str, Any]:
    """The stored comparison for the Results page: ``{available: True, **checked block}`` or
    ``{available: False, reason}``. Never raises."""
    if no_results:
        return {"available": False, "reason": NO_RESULTS_REASON}
    block = evaluation.get("comparison") if isinstance(evaluation, dict) else None
    if block is None:
        return {"available": False, "reason": NOT_SAVED_REASON}
    try:
        if isinstance(block, dict) and block.get("schema") == SCHEMA and block.get("available") is False:
            reason = block.get("reason")
            known = isinstance(reason, str) and reason in KNOWN_UNAVAILABLE_REASONS
            return {"available": False, "reason": reason if known else NO_COMPARISON_REASON}
        return {"available": True, **_validate(block, evaluation)}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, ZeroDivisionError, IndexError):
        return {"available": False, "reason": UNREADABLE_REASON}
