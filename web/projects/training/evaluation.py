"""Held-out detection metrics, computed here (numpy only) so every backend is judged identically.

Definitions (also printed in the report):
* A prediction is a true positive if its label matches an *unmatched* ground-truth box of the
  same image and label with IoU >= ``iou`` (greedy, highest score first).
* precision = TP / (TP + FP) and recall = TP / (TP + FN) at the stated confidence threshold.
* AP50 = area under the precision-recall curve over *all* predictions of that label (all-point
  interpolation), at IoU 0.5. It does not depend on the confidence threshold.
Undefined values (no predictions, or no ground truth) are ``None``, never 0.
"""

from __future__ import annotations

from typing import Any, Iterable


class _LazyNumpy:
    """``np``, imported on first use. The numbers and helpers below that need no arrays (the thresholds, the
    worst-first ordering) are read by the data-health check and the plain-language results, which also run on
    an install that has no numpy; only scoring needs it."""

    def __getattr__(self, name: str) -> Any:
        import numpy

        return getattr(numpy, name)


np = _LazyNumpy()

SCHEMA = "athena.evaluation/1"
IOU_THRESHOLD = 0.5
DEFAULT_OPERATING_CONFIDENCE = 0.5
#: Predictions are collected down to this score so a threshold can be chosen anywhere above it.
PREDICTION_FLOOR = 0.05
# The test-side numbers. This module is their one home: ``datasets/health.py`` and ``training/report.py`` import
# them (under their own public names) instead of writing the figures again.
#: Below this many held-out pictures a few mistakes can swing every figure.
MIN_IMAGES_FOR_CONFIDENCE = 30
#: Below this many held-out boxes of one label its figures are only a rough indication.
MIN_BOXES_PER_LABEL = 10
#: docs/guides/model-quality.md, "Minimum quantities": "aim for at least ~30 Final test boxes per label". With fewer,
#: one or two mistakes move every percentage (9 of 10 -> 0.60-0.98 recall, 27 of 30 -> 0.74-0.97).
MIN_BOXES_PER_LABEL_FOR_VERDICT = 30
# Per-picture outcomes (which held-out pictures were missed or got a wrong box) are kept only this many per kind,
# worst first, so a stored evaluation stays small. The true totals are stored beside them.
OUTCOMES_SCHEMA = "athena.held_out_outcomes/1"
MAX_STORED_OUTCOMES_PER_KIND = 250

Box = tuple[float, float, float, float]  # normalized x, y, width, height


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between every box in ``a`` (n,4) and ``b`` (m,4); boxes are (x, y, w, h)."""
    a = np.asarray(a, dtype=float).reshape(-1, 4)
    b = np.asarray(b, dtype=float).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    ax2, ay2 = a[:, 0] + a[:, 2], a[:, 1] + a[:, 3]
    bx2, by2 = b[:, 0] + b[:, 2], b[:, 1] + b[:, 3]
    iw = np.clip(np.minimum(ax2[:, None], bx2[None]) - np.maximum(a[:, 0][:, None], b[:, 0][None]), 0, None)
    ih = np.clip(np.minimum(ay2[:, None], by2[None]) - np.maximum(a[:, 1][:, None], b[:, 1][None]), 0, None)
    inter = iw * ih
    union = (a[:, 2] * a[:, 3])[:, None] + (b[:, 2] * b[:, 3])[None] - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1), 0.0)


def match_image(pred_boxes, pred_scores, gt_boxes, iou: float = IOU_THRESHOLD) -> list[tuple[float, bool]]:
    """(score, is_true_positive) for each prediction of one label in one image; greedy by score."""
    pred_boxes = np.asarray(pred_boxes, dtype=float).reshape(-1, 4)
    gt_boxes = np.asarray(gt_boxes, dtype=float).reshape(-1, 4)
    scores = np.asarray(pred_scores, dtype=float).reshape(-1)
    order = np.argsort(-scores, kind="stable")
    overlaps = iou_matrix(pred_boxes, gt_boxes)
    taken = np.zeros(len(gt_boxes), dtype=bool)
    out: list[tuple[float, bool]] = []
    for index in order:
        hit = False
        if len(gt_boxes):
            candidates = np.where(taken, -1.0, overlaps[index])
            best = int(np.argmax(candidates))
            if candidates[best] >= iou:
                taken[best] = True
                hit = True
        out.append((float(scores[index]), hit))
    return out


def match_image_detail(pred_boxes, pred_scores, gt_boxes, iou: float = IOU_THRESHOLD) -> list[tuple[int, float, int | None]]:
    """Like ``match_image`` (same greedy rule) but says *which* ground-truth box each prediction matched.

    Returns ``(prediction index, score, ground-truth index or None)`` in matching (highest score first) order.
    """
    pred_boxes = np.asarray(pred_boxes, dtype=float).reshape(-1, 4)
    gt_boxes = np.asarray(gt_boxes, dtype=float).reshape(-1, 4)
    scores = np.asarray(pred_scores, dtype=float).reshape(-1)
    order = np.argsort(-scores, kind="stable")
    overlaps = iou_matrix(pred_boxes, gt_boxes)
    taken = np.zeros(len(gt_boxes), dtype=bool)
    out: list[tuple[int, float, int | None]] = []
    for index in order:
        matched: int | None = None
        if len(gt_boxes):
            candidates = np.where(taken, -1.0, overlaps[index])
            best = int(np.argmax(candidates))
            if candidates[best] >= iou:
                taken[best] = True
                matched = best
        out.append((int(index), float(scores[index]), matched))
    return out


def average_precision(flags: list[tuple[float, bool]], n_ground_truth: int) -> float | None:
    """All-point interpolated AP. ``None`` when there is no ground truth to measure against."""
    if n_ground_truth <= 0:
        return None
    if not flags:
        return 0.0
    ordered = sorted(flags, key=lambda item: -item[0])
    hits = np.array([1.0 if hit else 0.0 for _, hit in ordered])
    tp = np.cumsum(hits)
    fp = np.cumsum(1.0 - hits)
    recall = tp / n_ground_truth
    precision = tp / np.maximum(tp + fp, 1e-12)
    recall = np.concatenate(([0.0], recall, [1.0]))
    precision = np.concatenate(([0.0], precision, [0.0]))
    for index in range(len(precision) - 2, -1, -1):  # make precision monotonically non-increasing
        precision[index] = max(precision[index], precision[index + 1])
    changes = np.where(recall[1:] != recall[:-1])[0]
    return float(np.sum((recall[changes + 1] - recall[changes]) * precision[changes + 1]))


def share_found(found: int, test_boxes: int) -> tuple[float, int]:
    """Worst-first sort key of a label: the share of its test boxes found (1.0 when it has none), then the count."""
    return (found / test_boxes if test_boxes else 1.0, found)


def sort_worst_first(missed: list[dict[str, Any]], alarms: list[dict[str, Any]], share_of) -> None:
    """Put per-picture outcome items in worst-first order, in place. The ONE place this order is written.

    * ``missed``: labels with the lowest share found first (``share_of(label)`` -> ``share_found(...)``), then
      label, then picture id.
    * ``alarms`` (false alarms): highest score first, then label, then picture id.

    The sort is stable, so items equal on these keys keep the order they came in.
    """
    missed.sort(key=lambda item: (*share_of(item["label"]), item["label"], item["asset_id"]))
    alarms.sort(key=lambda item: (-(item["score"] or 0.0), item["label"], item["asset_id"]))


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def evaluate_detections(
    ground_truth: list[list[tuple[str, Box]]],
    predictions: list[list[tuple[str, Box, float]]],
    labels: Iterable[str],
    *,
    confidence: float = DEFAULT_OPERATING_CONFIDENCE,
    iou: float = IOU_THRESHOLD,
    asset_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Score per-image predictions against ground truth.

    ``ground_truth[i]`` is ``[(label, (x, y, w, h)), ...]`` and ``predictions[i]`` is
    ``[(label, (x, y, w, h), score), ...]`` for image ``i`` (same order). Returns the evaluation
    document (no free text; see ``report.build_report``).

    When ``asset_ids`` (one id per image, same order) is given, the document also carries
    ``held_out_outcomes``: which pictures were missed or got a wrong box (see ``held_out_outcomes``). Without
    it the output is exactly what it always was.
    """
    labels = list(labels)
    if len(ground_truth) != len(predictions):
        raise ValueError("ground_truth and predictions must describe the same images.")
    if asset_ids is not None and len(asset_ids) != len(ground_truth):
        raise ValueError("asset_ids must name exactly the images being scored, in the same order.")
    n_images = len(ground_truth)
    per_label: dict[str, dict[str, Any]] = {}
    totals = {"tp": 0, "fp": 0, "fn": 0, "gt": 0}
    for label in labels:
        flags: list[tuple[float, bool]] = []
        gt_total = 0
        for gt_image, pred_image in zip(ground_truth, predictions):
            gt = [box for name, box in gt_image if name == label]
            gt_total += len(gt)
            preds = [(box, score) for name, box, score in pred_image if name == label]
            if preds:
                flags.extend(
                    match_image([p[0] for p in preds], [p[1] for p in preds], gt, iou)
                )
        kept = [(score, hit) for score, hit in flags if score >= confidence]
        tp = sum(1 for _, hit in kept if hit)
        fp = len(kept) - tp
        fn = gt_total - tp
        ap = average_precision(flags, gt_total)
        per_label[label] = {
            "ground_truth_boxes": gt_total,
            "predictions_at_threshold": len(kept),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn) if gt_total else None,
            "ap50": None if ap is None else round(ap, 4),
        }
        totals["tp"] += tp
        totals["fp"] += fp
        totals["fn"] += fn
        totals["gt"] += gt_total
    aps = [row["ap50"] for row in per_label.values() if row["ap50"] is not None]
    boxes_per_label = {label: per_label[label]["ground_truth_boxes"] for label in labels}
    result = {
        "schema": SCHEMA,
        "available": n_images > 0 and totals["gt"] > 0,
        "split": "test",
        "iou_threshold": iou,
        "confidence_threshold": confidence,
        "data": {"images": n_images, "boxes": totals["gt"], "boxes_per_label": boxes_per_label},
        "per_label": per_label,
        "overall": {
            "precision": _ratio(totals["tp"], totals["tp"] + totals["fp"]),
            "recall": _ratio(totals["tp"], totals["tp"] + totals["fn"]) if totals["gt"] else None,
            "true_positives": totals["tp"],
            "false_positives": totals["fp"],
            "false_negatives": totals["fn"],
            "map50": round(float(np.mean(aps)), 4) if aps else None,
        },
        "limits": data_limits(n_images, boxes_per_label),
    }
    if asset_ids is not None:
        result["held_out_outcomes"] = held_out_outcomes(
            ground_truth, predictions, asset_ids, labels, per_label, confidence=confidence, iou=iou
        )
    return result


def _round_box(box) -> list[float]:
    return [round(float(value), 6) for value in box]


def held_out_outcomes(
    ground_truth: list[list[tuple[str, Box]]],
    predictions: list[list[tuple[str, Box, float]]],
    asset_ids: list[str],
    labels: Iterable[str],
    per_label: dict[str, dict[str, Any]],
    *,
    confidence: float = DEFAULT_OPERATING_CONFIDENCE,
    iou: float = IOU_THRESHOLD,
) -> dict[str, Any]:
    """Which held-out pictures were wrong, by the same rule as the counts in ``per_label``.

    * ``missed``: a person-drawn box that no prediction at or above ``confidence`` matched (its ``box`` is the
      person-drawn one, ``score`` is ``None``).
    * ``false_alarm``: a prediction at or above ``confidence`` that matched no person-drawn box.

    Totals therefore equal the summed ``false_negatives`` / ``false_positives``. Items are worst first (labels
    with the lowest share found first; false alarms by score) and kept to ``MAX_STORED_OUTCOMES_PER_KIND`` each;
    the totals are the uncapped counts. Only ids and normalized boxes are stored, never paths.
    """
    labels = list(labels)
    missed: list[dict[str, Any]] = []
    alarms: list[dict[str, Any]] = []
    for label in labels:
        for image_index, (gt_image, pred_image) in enumerate(zip(ground_truth, predictions)):
            gt = [box for name, box in gt_image if name == label]
            preds = [(box, score) for name, box, score in pred_image if name == label]
            matched_gt: set[int] = set()
            for pred_index, score, gt_index in match_image_detail(
                [p[0] for p in preds], [p[1] for p in preds], gt, iou
            ):
                if score < confidence:
                    continue
                if gt_index is not None:
                    matched_gt.add(gt_index)
                else:
                    alarms.append(
                        {
                            "asset_id": str(asset_ids[image_index]),
                            "kind": "false_alarm",
                            "label": label,
                            "score": round(score, 4),
                            "box": _round_box(preds[pred_index][0]),
                        }
                    )
            for gt_index, box in enumerate(gt):
                if gt_index not in matched_gt:
                    missed.append(
                        {
                            "asset_id": str(asset_ids[image_index]),
                            "kind": "missed",
                            "label": label,
                            "score": None,
                            "box": _round_box(box),
                        }
                    )

    def share_of(label: str) -> tuple[float, int]:
        row = per_label[label]
        return share_found(row["true_positives"], row["ground_truth_boxes"])

    # The person-drawn / predicted box is the last tie-break: sort by it first, then let the (stable) shared
    # worst-first sort put the rest in order.
    missed.sort(key=lambda item: item["box"])
    alarms.sort(key=lambda item: item["box"])
    sort_worst_first(missed, alarms, share_of)
    return {
        "schema": OUTCOMES_SCHEMA,
        "confidence_threshold": confidence,
        "iou_threshold": iou,
        "missed_total": len(missed),
        "false_alarm_total": len(alarms),
        "items": missed[:MAX_STORED_OUTCOMES_PER_KIND] + alarms[:MAX_STORED_OUTCOMES_PER_KIND],
    }


def data_limits(n_images: int, boxes_per_label: dict[str, int]) -> list[str]:
    limits = [
        "These numbers describe only the held-out test images listed above. They say nothing certain "
        "about other cameras, lighting, products or time periods.",
        "Matching uses IoU 0.5 and byte-identical duplicates are kept in one split, but near-duplicate "
        "images (for example, consecutive frames) are not detected and may make results look better than they are.",
    ]
    if n_images < MIN_IMAGES_FOR_CONFIDENCE:
        limits.append(
            f"Only {n_images} test image(s): fewer than {MIN_IMAGES_FOR_CONFIDENCE}, so a few mistakes can swing every figure."
        )
    for label, count in boxes_per_label.items():
        if count == 0:
            limits.append(f"No test boxes for {label!r}: nothing about this label could be measured.")
        elif count < MIN_BOXES_PER_LABEL:
            limits.append(f"Only {count} test box(es) for {label!r}: treat its figures as a rough indication.")
    return limits


def not_available(reason: str, n_images: int = 0, boxes_per_label: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "available": False,
        "reason": reason,
        "split": "test",
        "data": {"images": n_images, "boxes": sum((boxes_per_label or {}).values()), "boxes_per_label": boxes_per_label or {}},
        "limits": [],
    }


def predict_items(backend, artifact, items, labels, thresholds):
    """Run ``backend.predict`` over items at the product's prediction floor.

    Returns ``(ground_truth, predictions)`` in the shapes ``evaluate_detections`` takes. This is the one place the
    floor and the "only known labels" rule live; the test evaluation and the validation-split operating points both
    use it, so they see predictions made exactly the same way.
    """
    from PIL import Image

    floor = {"confidence": PREDICTION_FLOOR, "max_boxes": int(thresholds.get("max_boxes", 100))}
    ground_truth, predictions = [], []
    for item in items:
        with Image.open(item.path) as opened:
            image = opened.convert("RGB")
        found = backend.predict(artifact, image, floor)
        predictions.append(
            [(p.label, (p.x, p.y, p.width, p.height), p.score) for p in found if p.label in labels]
        )
        ground_truth.append([(label, (x, y, w, h)) for label, x, y, w, h in item.boxes])
    return ground_truth, predictions


def evaluate_items(backend, artifact, items, labels, thresholds) -> dict[str, Any]:
    """Run ``backend.predict`` over held-out items and score the result."""
    ids = [getattr(item, "asset_id", None) for item in items]
    ground_truth, predictions = predict_items(backend, artifact, items, labels, thresholds)
    return evaluate_detections(
        ground_truth,
        predictions,
        labels,
        confidence=float(thresholds.get("confidence", DEFAULT_OPERATING_CONFIDENCE)),
        # Per-picture outcomes are kept only when every held-out item says which picture it is.
        asset_ids=[str(i) for i in ids] if ids and all(ids) else None,
    )
