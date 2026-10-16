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

import numpy as np

SCHEMA = "athena.evaluation/1"
IOU_THRESHOLD = 0.5
DEFAULT_OPERATING_CONFIDENCE = 0.5
MIN_IMAGES_FOR_CONFIDENCE = 30
MIN_BOXES_PER_LABEL = 10

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


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def evaluate_detections(
    ground_truth: list[list[tuple[str, Box]]],
    predictions: list[list[tuple[str, Box, float]]],
    labels: Iterable[str],
    *,
    confidence: float = DEFAULT_OPERATING_CONFIDENCE,
    iou: float = IOU_THRESHOLD,
) -> dict[str, Any]:
    """Score per-image predictions against ground truth.

    ``ground_truth[i]`` is ``[(label, (x, y, w, h)), ...]`` and ``predictions[i]`` is
    ``[(label, (x, y, w, h), score), ...]`` for image ``i`` (same order). Returns the evaluation
    document (no free text; see ``report.build_report``).
    """
    labels = list(labels)
    if len(ground_truth) != len(predictions):
        raise ValueError("ground_truth and predictions must describe the same images.")
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
    return {
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


def evaluate_items(backend, artifact, items, labels, thresholds) -> dict[str, Any]:
    """Run ``backend.predict`` over held-out items and score the result."""
    from PIL import Image

    floor = {"confidence": 0.05, "max_boxes": int(thresholds.get("max_boxes", 100))}
    ground_truth, predictions = [], []
    for item in items:
        with Image.open(item.path) as opened:
            image = opened.convert("RGB")
        found = backend.predict(artifact, image, floor)
        predictions.append(
            [(p.label, (p.x, p.y, p.width, p.height), p.score) for p in found if p.label in labels]
        )
        ground_truth.append([(label, (x, y, w, h)) for label, x, y, w, h in item.boxes])
    return evaluate_detections(
        ground_truth,
        predictions,
        labels,
        confidence=float(thresholds.get("confidence", DEFAULT_OPERATING_CONFIDENCE)),
    )
