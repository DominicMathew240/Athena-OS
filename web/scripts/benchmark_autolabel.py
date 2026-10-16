"""Measure auto-label accuracy on COCO val2017 so thresholds are chosen from data.

    python scripts/benchmark_autolabel.py --annotations instances_val2017.json --images 30

Fetches images from images.cocodataset.org (CC BY 4.0 annotations; Flickr image licences vary,
so images are cached in a scratch folder and never added to the repo). Runs the configured
SAM + CLIP labeler once at a low confidence floor, then sweeps the confidence threshold to
report precision/recall (box IoU >= 0.5, same class). Ground truth is COCO's human boxes for the
chosen label set; objects smaller than the labeler's minimum area are excluded from recall.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")
os.environ.setdefault("DJANGO_DEBUG", "1")

import django  # noqa: E402

django.setup()

from PIL import Image  # noqa: E402

from projects.autolabel import get_autolabeler  # noqa: E402
from projects.autolabel.sam_clip import MIN_AREA_FRACTION, box_iou  # noqa: E402

DEFAULT_LABELS = ["cat", "dog", "couch", "bed", "tv", "laptop", "chair", "book"]


def load_ground_truth(path: Path, labels: list[str], count: int, seed: int):
    data = json.loads(path.read_text())
    categories = {c["id"]: c["name"] for c in data["categories"]}
    images = {i["id"]: i for i in data["images"]}
    boxes: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = defaultdict(list)
    for ann in data["annotations"]:
        name = categories[ann["category_id"]]
        image = images[ann["image_id"]]
        if name not in labels or ann.get("iscrowd"):
            continue
        x, y, w, h = ann["bbox"]
        if w * h / (image["width"] * image["height"]) < MIN_AREA_FRACTION:
            continue
        boxes[ann["image_id"]].append(
            (name, (x / image["width"], y / image["height"], (x + w) / image["width"], (y + h) / image["height"]))
        )
    chosen = sorted(boxes)
    random.Random(seed).shuffle(chosen)
    return [(images[i], boxes[i]) for i in chosen[:count]]


def fetch(image: dict, cache: Path) -> Path:
    target = cache / image["file_name"]
    if not target.exists():
        urllib.request.urlretrieve(f"http://images.cocodataset.org/val2017/{image['file_name']}", target)
    return target


def evaluate(results, threshold: float):
    tp = fp = fn = 0
    for truth, proposals in results:
        used: set[int] = set()
        for label, confidence, box in sorted(proposals, key=lambda p: -p[1]):
            if confidence < threshold:
                continue
            best, best_iou = None, 0.5
            for index, (gt_label, gt_box) in enumerate(truth):
                if index in used or gt_label != label:
                    continue
                iou = box_iou(box, gt_box)
                if iou >= best_iou:
                    best, best_iou = index, iou
            if best is None:
                fp += 1
            else:
                used.add(best)
                tp += 1
        fn += len(truth) - len(used)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return tp, fp, fn, precision, recall


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annotations", type=Path, required=True, help="COCO instances_val2017.json")
    parser.add_argument("--images", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--cache", type=Path, default=Path(os.environ.get("TMPDIR", "/tmp")) / "coco-bench")
    parser.add_argument("--output", type=Path, help="Write raw proposals as JSON for re-scoring")
    parser.add_argument("--min-mask-iou", type=float, help="Override SAM predicted-IoU floor for this run")
    parser.add_argument("--min-stability", type=float, help="Override SAM mask-stability floor for this run")
    parser.add_argument("--points", type=int, help="Override SAM grid points per side for this run")
    args = parser.parse_args()
    from projects.autolabel import sam_clip

    if args.min_mask_iou is not None:
        sam_clip.MIN_MASK_IOU = args.min_mask_iou
    if args.min_stability is not None:
        sam_clip.MIN_STABILITY = args.min_stability
    if args.points is not None:
        from django.conf import settings

        settings.AUTOLABEL["POINTS_PER_SIDE"] = args.points
    labels = [label.strip() for label in args.labels.split(",") if label.strip()]
    args.cache.mkdir(parents=True, exist_ok=True)

    sample = load_ground_truth(args.annotations, labels, args.images, args.seed)
    labeler = get_autolabeler()
    labeler.warm_up()
    print(f"Model: {labeler.name} on {labeler.device}; {len(sample)} images; labels: {', '.join(labels)}")
    results, raw = [], []
    started = time.monotonic()
    for index, (info, truth) in enumerate(sample, 1):
        with Image.open(fetch(info, args.cache)) as image:
            image.load()
            proposals = labeler.propose(image, labels, min_confidence=0.1, max_boxes=50)
        found = [(p.label, p.confidence, (p.x, p.y, p.x + p.width, p.y + p.height)) for p in proposals]
        results.append((truth, found))
        raw.append({"file": info["file_name"], "truth": truth, "proposals": found})
        print(f"  {index}/{len(sample)} {info['file_name']}: {len(truth)} truth, {len(found)} proposed", flush=True)
    print(f"Inference: {(time.monotonic() - started) / len(sample):.1f}s per image")
    if args.output:
        args.output.write_text(json.dumps(raw))
    print("\nthreshold  TP   FP   FN  precision  recall")
    for threshold in (0.1, 0.2, 0.3, 0.45, 0.6, 0.7, 0.8, 0.9):
        tp, fp, fn, precision, recall = evaluate(results, threshold)
        print(f"{threshold:9.2f} {tp:4d} {fp:4d} {fn:4d}  {precision:9.2f} {recall:7.2f}")


if __name__ == "__main__":
    main()
