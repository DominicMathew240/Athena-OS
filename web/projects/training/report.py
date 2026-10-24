"""Plain-language evaluation report. Every sentence is derived from the stored numbers only."""

from __future__ import annotations

import uuid
from fractions import Fraction
from typing import Any

from . import comparison as comparison_lib, evaluation as evaluation_lib, operating_points


def _pct(value: float | None) -> str:
    return "not measurable" if value is None else f"{value * 100:.0f}%"


def _as_list(value: Any) -> list:
    """A stored list, or ``[]`` when what was stored is not a list (a damaged document must not break a page)."""
    return list(value) if isinstance(value, list) else []


def _unavailable_report(evaluation: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "text": (
            f"Evaluation not available. {reason} Without a held-out evaluation this model cannot be staged "
            "or approved, and nothing can be said about how well it works."
        ),
        "numbers": evaluation or {},
        "limits": _as_list((evaluation or {}).get("limits", [])),
    }


def build_report(evaluation: dict[str, Any]) -> dict[str, Any]:
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    if not evaluation or not evaluation.get("available"):
        return _unavailable_report(evaluation, evaluation.get("reason", "No held-out evaluation exists for this model."))
    try:
        return _available_report(evaluation)
    except (KeyError, TypeError, ValueError, AttributeError):
        return _unavailable_report({}, _UNREADABLE_EVALUATION_REASON)


def _available_report(evaluation: dict[str, Any]) -> dict[str, Any]:
    data = evaluation["data"]
    overall = evaluation["overall"]
    threshold = evaluation["confidence_threshold"]
    lines = [
        f"This model was checked on {data['images']} held-out test image(s) containing {data['boxes']} "
        "labelled box(es). Those images were set aside before training and were not used to train the model.",
        f"At a confidence threshold of {threshold:g}, a detection counts as correct when it has the right "
        f"label and overlaps a person-drawn box by at least {evaluation['iou_threshold']:g} (IoU).",
        f"Overall, {_pct(overall['precision'])} of the model's detections were correct (precision) and it "
        f"found {_pct(overall['recall'])} of the boxes people had drawn (recall). "
        f"That is {overall['true_positives']} correct, {overall['false_positives']} wrong and "
        f"{overall['false_negatives']} missed.",
    ]
    if overall["map50"] is not None:
        lines.append(
            f"Averaged over labels, AP50 (a threshold-free ranking score, 0–100%) is {_pct(overall['map50'])}."
        )
    for label, row in evaluation["per_label"].items():
        if row["ground_truth_boxes"] == 0:
            lines.append(f"{label}: no test boxes, so nothing can be said about it.")
            continue
        lines.append(
            f"{label}: {row['ground_truth_boxes']} test box(es); precision {_pct(row['precision'])}, "
            f"recall {_pct(row['recall'])}, AP50 {_pct(row['ap50'])} "
            f"({row['true_positives']} correct, {row['false_positives']} wrong, {row['false_negatives']} missed)."
        )
    lines.append("These figures describe the test images only. Do not read them as accuracy in the field.")
    return {
        "available": True,
        "text": "\n".join(lines),
        "numbers": evaluation,
        "limits": _as_list(evaluation.get("limits", [])),
    }


def summary(evaluation: dict[str, Any] | None) -> dict[str, Any]:
    """The few numbers shown next to a model in lists; the full report has the rest."""
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    if not evaluation.get("available"):
        return {"available": False, "reason": evaluation.get("reason", "No held-out evaluation exists.")}
    try:
        return {
            "available": True,
            "test_images": evaluation["data"]["images"],
            "test_boxes": evaluation["data"]["boxes"],
            "precision": evaluation["overall"]["precision"],
            "recall": evaluation["overall"]["recall"],
            "map50": evaluation["overall"]["map50"],
            "confidence_threshold": evaluation["confidence_threshold"],
        }
    except (KeyError, TypeError, AttributeError):
        return {"available": False, "reason": _UNREADABLE_EVALUATION_REASON}


# -- plain-language results (what a person who is not a data scientist can act on) ---------------------------
# Everything below is derived from the stored evaluation only. Thresholds are named, documented and deliberately
# conservative: a "good" verdict has to clear every one of them, any doubt falls to a weaker verdict, and no
# sentence calls a model accurate, certified or safe.

#: docs/guides/model-quality.md: "aim for at least ~30 Final test boxes per label". Below this for any label the
#: verdict is "cannot_tell" (unless the model found none of the boxes at all, which is informative on its own).
#: The number lives in training/evaluation.py (one source for every test-side threshold); this is its public name here.
MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT = evaluation_lib.MIN_BOXES_PER_LABEL_FOR_VERDICT
#: Share of test boxes found, at the stored operating point, below which a model is "not_ready" (enough data).
RECALL_FLOOR_NOT_READY = 0.5
#: "good_enough_to_try" needs overall AND every label's share found at or above this...
RECALL_FLOOR_GOOD_ENOUGH = 0.85
#: ...and at least this share of the boxes it drew to be right (when it drew any).
PRECISION_FLOOR_GOOD_ENOUGH = 0.7
#: At most this many pictures in the list of worst misses and false alarms.
MISSES_CAP = 24

VERDICTS = ("good_enough_to_try", "needs_more_examples", "not_ready", "cannot_tell")
#: Same words as the check screen's ``cs-not-final`` line (templates/projects/checkscreen.html); a test pins that.
NOT_FINAL_DECISION = "Not a final decision. This is the computer’s opinion. A person decides."

_NO_OUTCOMES_REASON = (
    "The pictures this model got wrong were not saved when it was checked, so they cannot be listed. "
    "The counts are still correct."
)
_UNREADABLE_OUTCOMES_REASON = "The saved list of wrong pictures could not be read, so it is not shown."
_MISMATCHED_OUTCOMES_REASON = (
    "The saved list of wrong pictures and the saved counts do not match, so the list is not shown."
)
_UNREADABLE_EVALUATION_REASON = "The saved evaluation could not be read."
_ALL_PICTURES_GONE_REASON = (
    "The pictures these results were measured on are no longer in the project, so they cannot be listed. "
    "The counts are still correct."
)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _extra_text(count: int) -> str:
    """``count`` boxes the model drew that matched none of the person-drawn boxes: a duplicate on a box it had
    already found, or a box in the wrong place. (They carry the label they are counted under; this does not say
    they had a different label.)"""
    return "no extra boxes" if count == 0 else _plural(count, "extra or misplaced box", "extra or misplaced boxes")


def _floor(value: float) -> Fraction:
    """A named floor as an exact fraction (0.85 -> 17/20), so a floor is compared against counts, not floats."""
    return Fraction(str(value))


def _share(found: int, total: int) -> Fraction | None:
    return Fraction(found, total) if total else None


def _pct_down(found: int, total: int) -> str:
    """Whole percent, rounded DOWN: a share just under a floor is never shown as the floor."""
    return "not measurable" if total == 0 else f"{found * 100 // total}%"


def label_sentence(label: str, test_boxes: int, found: int, extra_boxes: int) -> str:
    """One plain sentence for one label. ``label`` is data: it is inserted as given (the page escapes it).

    ``extra_boxes`` is the stored count of boxes drawn that matched no person-drawn box (duplicates and boxes in
    the wrong place), so the sentence calls them "extra or misplaced", never a different label."""
    name = f"“{label}”"
    if test_boxes == 0:
        text = f"There were no {name} boxes in the test pictures, so nothing can be said about it."
        if extra_boxes:
            text += f" It still drew {_plural(extra_boxes, f'{name} box', f'{name} boxes')}."
        return text
    noun = f"{name} box" if test_boxes == 1 else f"{name} boxes"
    if found == 0:
        first = f"Found none of {test_boxes} {noun}"
    elif found == test_boxes and test_boxes > 1:
        first = f"Found all {test_boxes} {noun}"
    else:
        first = f"Found {found} of {test_boxes} {noun}"
    return f"{first}; drew {_extra_text(extra_boxes)}."


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _ratio_matches(stored: Any, numerator: int, denominator: int) -> bool:
    """A stored, rounded ratio must be None exactly when there is nothing to divide, else within rounding of the counts."""
    if stored is None:
        return denominator == 0
    if isinstance(stored, bool) or not isinstance(stored, (int, float)) or denominator == 0:
        return False
    return 0 <= stored <= 1 and abs(stored - numerator / denominator) <= 0.0005


def _label_rows(per_label: Any) -> list[dict[str, Any]]:
    """Per-label counts, checked: ints (not bools), 0 <= found <= test boxes, found + missed == test boxes, extra >= 0.
    ValueError if anything does not add up."""
    if not isinstance(per_label, dict):
        raise ValueError("per_label")
    rows = []
    for label, row in per_label.items():
        if not isinstance(label, str) or not isinstance(row, dict):
            raise ValueError("label row")
        test_boxes, found = row["ground_truth_boxes"], row["true_positives"]
        missed, extra = row["false_negatives"], row["false_positives"]
        if not all(_count(v) for v in (test_boxes, found, missed, extra)):
            raise ValueError("counts")
        if not (0 <= found <= test_boxes and found + missed == test_boxes and extra >= 0):
            raise ValueError("counts do not add up")
        if not (
            _ratio_matches(row.get("recall"), found, test_boxes)
            and _ratio_matches(row.get("precision"), found, found + extra)
        ):
            raise ValueError("ratio does not match counts")
        rows.append(
            {
                "label": label,
                "test_boxes": test_boxes,
                "found": found,
                "missed": missed,
                "wrong_boxes": extra,
                "sentence": label_sentence(label, test_boxes, found, extra),
            }
        )
    return rows


def _read_totals(evaluation: dict[str, Any], labels: list[dict[str, Any]]) -> dict[str, int]:
    """Overall counts and picture/box counts, checked against the per-label rows. ValueError if they disagree."""
    overall, data = evaluation["overall"], evaluation["data"]
    if not isinstance(overall, dict) or not isinstance(data, dict):
        raise ValueError("overall")
    found, missed, extra = overall["true_positives"], overall["false_negatives"], overall["false_positives"]
    pictures, boxes = data["images"], data["boxes"]
    if not all(_count(v) for v in (found, missed, extra, pictures, boxes)) or pictures < 0:
        raise ValueError("overall counts")
    if (
        found != sum(r["found"] for r in labels)
        or missed != sum(r["missed"] for r in labels)
        or extra != sum(r["wrong_boxes"] for r in labels)
        or boxes != found + missed
        or boxes == 0
    ):
        raise ValueError("overall counts do not match the labels")
    if not (
        _ratio_matches(overall.get("recall"), found, found + missed)
        and _ratio_matches(overall.get("precision"), found, found + extra)
    ):
        raise ValueError("overall ratio does not match counts")
    return {"found": found, "of": found + missed, "wrong_boxes": extra, "pictures": pictures, "boxes": boxes}


def _verdict(labels: list[dict[str, Any]], totals: dict[str, int]) -> tuple[str, str, list[str]]:
    """(verdict, headline without the closing line, reasons). Every floor is applied to the COUNTS (exact
    fractions), never to a stored rounded ratio. Rules, in order:

    1. nothing found at all  -> ``not_ready`` (a negative result needs no minimum number of boxes)
    2. a label with at least ``MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT`` test boxes whose share found is below
       ``RECALL_FLOOR_NOT_READY`` -> ``not_ready`` (a known failure is not hidden by a label with too few boxes)
    3. some label has fewer than ``MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT`` test boxes -> ``cannot_tell``
    4. total share found below ``RECALL_FLOOR_NOT_READY`` -> ``not_ready``
    5. every label and the total at or above ``RECALL_FLOOR_GOOD_ENOUGH`` and the boxes it drew right at
       least ``PRECISION_FLOOR_GOOD_ENOUGH`` of the time -> ``good_enough_to_try``
    6. otherwise ``needs_more_examples``
    """
    found, total, wrong = totals["found"], totals["of"], totals["wrong_boxes"]
    not_ready_floor, good_floor = _floor(RECALL_FLOOR_NOT_READY), _floor(RECALL_FLOOR_GOOD_ENOUGH)
    boxes_text = _plural(total, "test box", "test boxes")
    pct = _pct_down(found, total)
    extra_text = _extra_text(wrong)
    reasons = [f"It found {found} of {boxes_text} ({pct}) and drew {extra_text}."]
    if found + wrong:
        reasons.append(f"Of the boxes it drew, {_pct_down(found, found + wrong)} were right.")
    short = [row for row in labels if row["test_boxes"] < MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT]
    weak = [row for row in labels if row["test_boxes"] and _share(row["found"], row["test_boxes"]) < good_floor]
    failing = [
        row
        for row in labels
        if row["test_boxes"] >= MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT
        and _share(row["found"], row["test_boxes"]) < not_ready_floor
    ]
    for row in short:
        reasons.append(
            f"Only {_plural(row['test_boxes'], 'test box', 'test boxes')} for “{row['label']}”; "
            f"about {MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT} per label are needed before a result means much."
        )
    for row in weak:
        reasons.append(
            f"“{row['label']}”: found {row['found']} of {row['test_boxes']} ({_pct_down(row['found'], row['test_boxes'])}), "
            f"below the {RECALL_FLOOR_GOOD_ENOUGH * 100:.0f}% this tool asks for."
        )

    if found == 0:
        return "not_ready", f"Not ready: it found none of the {boxes_text}.", reasons
    if failing:
        worst = min(failing, key=lambda row: (_share(row["found"], row["test_boxes"]), row["label"]))
        of_text = _plural(worst["test_boxes"], "test box", "test boxes")
        how = "none" if worst["found"] == 0 else f"only {worst['found']}"
        return (
            "not_ready",
            f"Not ready: for “{worst['label']}” it found {how} of {of_text} "
            f"({_pct_down(worst['found'], worst['test_boxes'])}).",
            reasons,
        )
    if short:
        return (
            "cannot_tell",
            f"Cannot tell yet: it found {found} of {boxes_text}, but some labels have "
            "too few test boxes to say how well it works.",
            reasons,
        )
    if _share(found, total) < not_ready_floor:
        return "not_ready", f"Not ready: it found only {found} of {boxes_text} ({pct}).", reasons
    precise = found + wrong == 0 or Fraction(found, found + wrong) >= _floor(PRECISION_FLOOR_GOOD_ENOUGH)
    if not weak and _share(found, total) >= good_floor and precise:
        return (
            "good_enough_to_try",
            f"Good enough to try: it found {found} of {boxes_text} ({pct}) "
            f"and drew {extra_text}. Try it on a few real pictures and look at what it gets wrong.",
            reasons,
        )
    if not precise:
        reasons.append(
            f"Too many of the boxes it drew were extra or misplaced: fewer than "
            f"{PRECISION_FLOOR_GOOD_ENOUGH * 100:.0f}% of them were right."
        )
    return (
        "needs_more_examples",
        f"Needs more examples: it found {found} of {boxes_text} ({pct}) "
        f"and drew {extra_text}. Add pictures of what it missed, then teach it again.",
        reasons,
    )


def _asset_key(value: Any) -> str:
    return str(uuid.UUID(str(value)))


def _read_outcomes(block: Any) -> list[dict[str, Any]]:
    """Validated copies of the stored per-picture items; ValueError/TypeError/KeyError when malformed."""
    if not isinstance(block, dict) or not isinstance(block.get("items"), list):
        raise ValueError("malformed")
    items = []
    for item in block["items"]:
        kind, score, box = item["kind"], item["score"], item["box"]
        if kind not in ("missed", "false_alarm") or not isinstance(item["label"], str):
            raise ValueError("malformed")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))):
            raise ValueError("malformed")
        if box is not None and (
            not isinstance(box, list) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in box)
        ):
            raise ValueError("malformed")
        items.append({"asset_id": _asset_key(item["asset_id"]), "kind": kind, "label": item["label"], "score": score, "box": box})
    return items


def stored_outcome_asset_ids(evaluation: dict[str, Any] | None) -> set[str]:
    """Picture ids named by the stored per-picture list (for the view to check against the project)."""
    try:
        return {item["asset_id"] for item in _read_outcomes((evaluation or {}).get("held_out_outcomes"))}
    except (KeyError, TypeError, ValueError, AttributeError):
        return set()


def _no_misses(reason: str, no_longer_in_project: int = 0) -> dict[str, Any]:
    return {
        "misses_available": False,
        "reason": reason,
        "items": [],
        "capped_at": MISSES_CAP,
        "total": 0,
        "no_longer_in_project": no_longer_in_project,
        "pictures": {"total": 0, "shown": 0, "exact": True},
        "distinct_pictures": 0,
    }


def _picture_order(missed: list[dict[str, Any]], alarms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The listed items grouped by picture. Both inputs are already worst first.

    Rule: a picture appears once, with all of its listed items next to each other (missed boxes first, then false
    alarms, each in worst-first order). Pictures with a missed box are ranked 0, 1, 2... by their worst missed box;
    pictures with a false alarm likewise by their worst false alarm; a picture with both takes the better of its two
    ranks. Pictures are then listed by rank, missed first on a tie: the page alternates between the worst missed
    picture and the worst false-alarm picture instead of listing every false alarm last.
    """
    best: dict[str, tuple[int, int]] = {}
    for kind_order, group in enumerate((missed, alarms)):
        seen: list[str] = []
        for item in group:
            if item["asset_id"] not in seen:
                seen.append(item["asset_id"])
        for rank, asset_id in enumerate(seen):
            best[asset_id] = min(best.get(asset_id, (rank, kind_order)), (rank, kind_order))
    ordered: list[dict[str, Any]] = []
    for asset_id in sorted(best, key=best.__getitem__):
        ordered += [item for item in missed if item["asset_id"] == asset_id]
        ordered += [item for item in alarms if item["asset_id"] == asset_id]
    return ordered


def _plain_misses(
    evaluation: dict[str, Any], labels: list[dict[str, Any]], known_asset_ids: set[str] | None
) -> dict[str, Any]:
    """Worst held-out pictures, only from per-picture outcomes the evaluation stored; never recomputed or guessed.

    ``items`` has one entry per BOX (a missed box or a false alarm), capped at ``MISSES_CAP``: up to half of the
    room for each kind, room one kind does not use goes to the other (worst first within each kind, see
    ``evaluation.sort_worst_first``). The chosen items are then grouped by picture (see ``_picture_order``).
    ``total`` counts boxes. ``pictures`` counts distinct pictures: ``total`` among all the stored items, ``shown``
    among the listed ones; ``exact`` is False when the stored list was itself cut short, so ``total`` is then a
    lower bound. ``distinct_pictures`` repeats ``pictures.total``.
    """
    block = evaluation.get("held_out_outcomes")
    if block is None:
        return _no_misses(_NO_OUTCOMES_REASON)
    try:
        items = _read_outcomes(block)
        missed_total, alarm_total = block["missed_total"], block["false_alarm_total"]
        if not (_count(missed_total) and _count(alarm_total)):
            raise ValueError("totals")
    except (KeyError, TypeError, ValueError, AttributeError):
        return _no_misses(_UNREADABLE_OUTCOMES_REASON)
    if (
        missed_total != sum(row["missed"] for row in labels)
        or alarm_total != sum(row["wrong_boxes"] for row in labels)
    ):
        return _no_misses(_MISMATCHED_OUTCOMES_REASON)
    total = missed_total + alarm_total
    dropped = 0
    if known_asset_ids is not None:
        kept = [item for item in items if item["asset_id"] in known_asset_ids]
        dropped = len(items) - len(kept)
        items = kept
        total -= dropped
    if dropped and not items:
        return _no_misses(_ALL_PICTURES_GONE_REASON, dropped)
    share = {row["label"]: evaluation_lib.share_found(row["found"], row["test_boxes"]) for row in labels}
    missed = [item for item in items if item["kind"] == "missed"]
    alarms = [item for item in items if item["kind"] == "false_alarm"]
    evaluation_lib.sort_worst_first(missed, alarms, lambda label: share.get(label, (1.0, 0)))
    # Fair split: up to half of the room for each kind; room one kind does not use goes to the other.
    take_missed = min(len(missed), max(MISSES_CAP // 2, MISSES_CAP - len(alarms)))
    take_alarms = min(len(alarms), MISSES_CAP - take_missed)
    listed = _picture_order(missed[:take_missed], alarms[:take_alarms])
    distinct = len({item["asset_id"] for item in items})
    return {
        "misses_available": True,
        "reason": "",
        "items": listed,
        "capped_at": MISSES_CAP,
        "total": total,
        "no_longer_in_project": dropped,
        "pictures": {
            "total": distinct,
            "shown": len({item["asset_id"] for item in listed}),
            "exact": len(items) == total,
        },
        "distinct_pictures": distinct,
    }


def _plain_unavailable(evaluation: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    reason = reason or evaluation.get("reason") or "No held-out evaluation exists for this model."
    data = evaluation.get("data") if isinstance(evaluation.get("data"), dict) else {}
    pictures, boxes = data.get("images", 0), data.get("boxes", 0)
    return {
        "evaluated": False,
        "reason_if_not": reason,
        "counts": {
            "test_pictures": pictures if isinstance(pictures, int) else 0,
            "test_boxes": boxes if isinstance(boxes, int) else 0,
        },
        "labels": [],
        "overall": {
            "found": 0,
            "of": 0,
            "wrong_boxes": 0,
            "verdict": "cannot_tell",
            "headline": f"Cannot tell yet: there are no held-out test results for this model. {NOT_FINAL_DECISION}",
            "reasons": [build_report({**evaluation, "reason": reason, "available": False})["text"]],
        },
        "misses": _no_misses("There are no held-out test results, so there are no pictures to list."),
        "confidence_used": None,
        "iou_used": None,
        "limits": _as_list(evaluation.get("limits")),
        "operating_points": operating_points.plain_operating_points(evaluation),
        "wording": operating_points.wording_table(evaluation),
        # Additive (W8-L2): no results means nothing to compare.
        "comparison": comparison_lib.plain_comparison(evaluation, no_results=True),
    }


def plain_results(evaluation: dict[str, Any] | None, *, known_asset_ids: set[str] | None = None) -> dict[str, Any]:
    """The operator's view of one stored evaluation: found X of Y per label, a conservative verdict, and (only
    if the evaluation stored them) the worst held-out pictures. Pure: no database, no files, no model.

    ``known_asset_ids`` (when given) are the picture ids that still belong to the project; others are not listed.
    """
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    if not evaluation.get("available"):
        return _plain_unavailable(evaluation)
    try:
        labels = _label_rows(evaluation["per_label"])
        totals = _read_totals(evaluation, labels)
        verdict, headline, reasons = _verdict(labels, totals)
        misses = _plain_misses(evaluation, labels, known_asset_ids)
        result = {
            "evaluated": True,
            "reason_if_not": "",
            "counts": {"test_pictures": totals["pictures"], "test_boxes": totals["boxes"]},
            "labels": labels,
            "overall": {
                "found": totals["found"],
                "of": totals["of"],
                "wrong_boxes": totals["wrong_boxes"],
                "verdict": verdict,
                "headline": f"{headline} {NOT_FINAL_DECISION}",
                "reasons": reasons,
            },
            "misses": misses,
            "confidence_used": evaluation["confidence_threshold"],
            "iou_used": evaluation["iou_threshold"],
            "limits": _as_list(evaluation.get("limits")),
            # Additive (W4-T): thresholds chosen on the Checking pictures, and how often each score range was right
            # on the Final test pictures. Both say "not measured" for models evaluated before this existed.
            "operating_points": operating_points.plain_operating_points(evaluation),
            "wording": operating_points.wording_table(evaluation),
            # Additive (W8-L2): how the model compares with the model in use on the same test pictures, or why
            # that is not known. Checked against this evaluation; the verdict is derived again from the counts.
            "comparison": comparison_lib.plain_comparison(evaluation),
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return _plain_unavailable({}, _UNREADABLE_EVALUATION_REASON)
    return result
