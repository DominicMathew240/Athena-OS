"""Plain-language evaluation report. Every sentence is derived from the stored numbers only."""

from __future__ import annotations

from typing import Any


def _pct(value: float | None) -> str:
    return "not measurable" if value is None else f"{value * 100:.0f}%"


def build_report(evaluation: dict[str, Any]) -> dict[str, Any]:
    if not evaluation or not evaluation.get("available"):
        reason = (evaluation or {}).get(
            "reason", "No held-out evaluation exists for this model."
        )
        return {
            "available": False,
            "text": (
                f"Evaluation not available. {reason} Without a held-out evaluation this model cannot be staged "
                "or approved, and nothing can be said about how well it works."
            ),
            "numbers": evaluation or {},
            "limits": (evaluation or {}).get("limits", []),
        }
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
        "limits": evaluation.get("limits", []),
    }


def summary(evaluation: dict[str, Any] | None) -> dict[str, Any]:
    """The few numbers shown next to a model in lists; the full report has the rest."""
    evaluation = evaluation or {}
    if not evaluation.get("available"):
        return {"available": False, "reason": evaluation.get("reason", "No held-out evaluation exists.")}
    return {
        "available": True,
        "test_images": evaluation["data"]["images"],
        "test_boxes": evaluation["data"]["boxes"],
        "precision": evaluation["overall"]["precision"],
        "recall": evaluation["overall"]["recall"],
        "map50": evaluation["overall"]["map50"],
        "confidence_threshold": evaluation["confidence_threshold"],
    }
