"""Measure the reference training backend on a deterministic SYNTHETIC detection dataset.

    python scripts/benchmark_training.py --seed 7 --epochs 12 --modes none,imagenet_backbone,coco

What it does
* Draws ~200 train / 60 validation / 60 test pictures of three shape classes (circle, square,
  triangle) with real difficulty: scale 28-130 px, random rotation, random colours (colour is NOT a
  cue), gradient + blotch + noise backgrounds, hollow and filled shapes, similar-looking
  distractors that are NOT labelled (ellipses, rectangles, hexagons, crosses, thin lines), partial
  occlusion by thin strokes, 1-4 objects per picture.
* Writes it as a YOLO export (the format the product hands to the backend), trains the backend for
  each mode, and scores the held-out ``test`` split with ``backend.evaluate`` - the SAME function
  the product's worker uses (projects/training/evaluation.py, confidence = runner.EVAL_CONFIDENCE).
* Prints per-label AP50 / precision / recall plus wall time, and writes ``results.json`` and
  ``results.md`` into --workdir.

SYNTHETIC SHAPES SAY NOTHING ABOUT REAL SCANNER IMAGERY. This measures whether the training code and
the optional pretrained starts learn at all, nothing more. Pretrained modes need
ATHENA_TRAINING_ALLOW_DOWNLOAD=1 (or weights already cached under <data home>/models/pretrained);
without it that mode is reported as skipped with the product's own message. Needs torch +
torchvision (run it in the worker environment). Runs on CPU only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")
os.environ.setdefault("DJANGO_DEBUG", "1")

import django  # noqa: E402

django.setup()

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

CLASSES = ["circle", "square", "triangle"]
SIZE = 320  # model input size of the 320 FPN network
SS = 2  # supersampling for anti-aliased edges
SPLITS = {"train": 200, "val": 60, "test": 60}
SPLIT_OFFSET = {"train": 0, "val": 1_000_003, "test": 2_000_029}


# ----------------------------------------------------------------------------- synthetic data
def luminance(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def random_colour(rng: random.Random):
    return tuple(rng.randint(15, 240) for _ in range(3))


def polygon(cx, cy, radius, sides, angle):
    return [
        (cx + radius * math.cos(angle + 2 * math.pi * i / sides), cy + radius * math.sin(angle + 2 * math.pi * i / sides))
        for i in range(sides)
    ]


def rotated_rect(cx, cy, w, h, angle):
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def background(rng: random.Random, np_rng: np.random.RandomState) -> Image.Image:
    a, b = random_colour(rng), random_colour(rng)
    t = np.linspace(0, 1, SIZE * SS)
    angle = rng.uniform(0, 2 * math.pi)
    gx, gy = np.meshgrid(t, t)
    mix = np.clip((gx * math.cos(angle) + gy * math.sin(angle)) * 0.7 + 0.15, 0, 1)[..., None]
    base = (np.array(a)[None, None] * (1 - mix) + np.array(b)[None, None] * mix).astype(np.uint8)
    image = Image.fromarray(base, "RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(rng.randint(2, 6)):  # soft blotches
        r = rng.randint(30, 160) * SS
        x, y = rng.randint(0, SIZE * SS), rng.randint(0, SIZE * SS)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=random_colour(rng) + (rng.randint(20, 60),))
    return image.filter(ImageFilter.GaussianBlur(6 * SS))


def draw_distractor(draw: ImageDraw.ImageDraw, rng: random.Random, thin_only: bool) -> None:
    cx, cy = rng.uniform(0, SIZE * SS), rng.uniform(0, SIZE * SS)
    size = rng.uniform(18, 110) * SS
    colour = random_colour(rng) + (rng.randint(170, 255),)
    kind = rng.choice(["line", "line"] if thin_only else ["ellipse", "rectangle", "hexagon", "cross", "line", "pentagon"])
    angle = rng.uniform(0, 2 * math.pi)
    if kind == "line":
        dx, dy = math.cos(angle) * size, math.sin(angle) * size
        draw.line([cx - dx, cy - dy, cx + dx, cy + dy], fill=colour, width=rng.randint(2, 6) * SS)
    elif kind == "ellipse":
        draw.ellipse([cx - size, cy - size / rng.uniform(1.6, 2.6), cx + size, cy + size / rng.uniform(1.6, 2.6)], fill=colour)
    elif kind == "rectangle":
        draw.polygon(rotated_rect(cx, cy, size * 1.4, size * rng.uniform(0.35, 0.6), angle), fill=colour)
    elif kind == "hexagon":
        draw.polygon(polygon(cx, cy, size / 2, 6, angle), fill=colour)
    elif kind == "pentagon":
        draw.polygon(polygon(cx, cy, size / 2, 5, angle), fill=colour)
    else:  # cross
        draw.polygon(rotated_rect(cx, cy, size, size / 4, angle), fill=colour)
        draw.polygon(rotated_rect(cx, cy, size / 4, size, angle), fill=colour)


def place_objects(rng: random.Random, bg: Image.Image):
    """Returns [(class_index, polygon-or-ellipse spec, colour, hollow)] with boxes in pixels (1x)."""
    objects, boxes = [], []
    for _ in range(rng.randint(1, 4)):
        for _attempt in range(40):
            side = math.exp(rng.uniform(math.log(28), math.log(130)))  # varying scale
            cls = rng.randrange(len(CLASSES))
            cx, cy = rng.uniform(0, SIZE), rng.uniform(0, SIZE)
            angle = rng.uniform(0, 2 * math.pi)
            if cls == 0:
                pts, kind = [(cx - side / 2, cy - side / 2), (cx + side / 2, cy + side / 2)], "ellipse"
            elif cls == 1:
                pts, kind = rotated_rect(cx, cy, side, side, angle), "polygon"
            else:
                pts, kind = polygon(cx, cy, side * 0.62, 3, angle), "polygon"
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            full = (x2 - x1) * (y2 - y1)
            cx1, cy1, cx2, cy2 = max(0, x1), max(0, y1), min(SIZE, x2), min(SIZE, y2)
            if cx2 <= cx1 or cy2 <= cy1 or (cx2 - cx1) * (cy2 - cy1) / full < 0.8:
                continue
            if any(min(cx2, b[2]) - max(cx1, b[0]) > 0 and min(cy2, b[3]) - max(cy1, b[1]) > 0 and
                   (min(cx2, b[2]) - max(cx1, b[0])) * (min(cy2, b[3]) - max(cy1, b[1])) > 0.25 * min((cx2 - cx1) * (cy2 - cy1), (b[2] - b[0]) * (b[3] - b[1]))
                   for b in boxes):
                continue
            local = np.asarray(bg.crop((int(cx1 * SS), int(cy1 * SS), int(cx2 * SS), int(cy2 * SS))).resize((1, 1))).reshape(3)
            colour = random_colour(rng)
            for _try in range(12):
                if abs(luminance(colour) - luminance(local)) >= 35:
                    break
                colour = random_colour(rng)
            objects.append((cls, kind, pts, colour, rng.random() < 0.25))
            boxes.append((cx1, cy1, cx2, cy2))
            break
    return objects, boxes


def make_image(seed: int):
    rng = random.Random(seed)
    np_rng = np.random.RandomState(seed % (2**32))
    image = background(rng, np_rng)
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(rng.randint(3, 8)):
        draw_distractor(draw, rng, thin_only=False)
    objects, boxes = place_objects(rng, image)
    for cls, kind, pts, colour, hollow in objects:
        scaled = [(x * SS, y * SS) for x, y in pts]
        outline = tuple(max(0, c - 70) for c in colour)
        if kind == "ellipse":
            box = [scaled[0][0], scaled[0][1], scaled[1][0], scaled[1][1]]
            draw.ellipse(box, fill=None if hollow else colour, outline=colour if hollow else outline, width=(5 if hollow else 2) * SS)
        else:
            draw.polygon(scaled, fill=None if hollow else colour)
            draw.line(scaled + [scaled[0]], fill=colour if hollow else outline, width=(5 if hollow else 2) * SS, joint="curve")
    for _ in range(rng.randint(0, 3)):  # thin strokes over the objects = partial occlusion
        draw_distractor(draw, rng, thin_only=True)
    image = image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    if rng.random() < 0.5:
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.2)))
    pixels = np.asarray(image).astype(np.float32)
    pixels *= rng.uniform(0.75, 1.15)  # exposure jitter
    pixels += np_rng.normal(0, rng.uniform(3, 18), pixels.shape)  # sensor-like noise
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")
    labels = []
    for (cls, *_), (x1, y1, x2, y2) in zip(objects, boxes):
        labels.append((cls, (x1 + x2) / 2 / SIZE, (y1 + y2) / 2 / SIZE, (x2 - x1) / SIZE, (y2 - y1) / SIZE))
    return image, labels


def build_dataset(root: Path, seed: int, counts: dict[str, int]) -> dict:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
    summary = {}
    for split, count in counts.items():
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
        per_class = [0] * len(CLASSES)
        for index in range(count):
            image, labels = make_image(seed * 7919 + SPLIT_OFFSET[split] + index)
            image.save(root / "images" / split / f"{split}_{index:04d}.png")
            lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in labels]
            (root / "labels" / split / f"{split}_{index:04d}.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
            for c, *_ in labels:
                per_class[c] += 1
        summary[split] = {"images": count, "boxes": sum(per_class), "per_class": dict(zip(CLASSES, per_class))}
    return summary


# ----------------------------------------------------------------------------- training + scoring
def hardware() -> str:
    chip = ""
    if sys.platform == "darwin":
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    return f"{chip or platform.processor() or platform.machine()}; {os.cpu_count()} logical CPUs; {platform.platform()}; CPU only"


def run_mode(backend, dataset: Path, mode: str, args) -> dict:
    from projects.training.runner import EVAL_CONFIDENCE

    config = {"epochs": args.epochs, "batch_size": args.batch_size, "seed": args.seed, "threads": args.threads, "pretrained": mode}
    started = time.monotonic()
    try:
        last_printed = [0.0]

        def progress(fraction: float, message: str) -> None:
            if fraction - last_printed[0] >= 0.1 or fraction >= 1:
                last_printed[0] = fraction
                print(f"    {mode}: {message}", flush=True)

        result = backend.train(dataset, config, progress, lambda: False)
    except ValueError as error:  # e.g. downloads switched off: the product's own plain message
        return {"mode": mode, "skipped": True, "reason": str(error)}
    train_seconds = time.monotonic() - started
    items = backend.eval_items(dataset, "test")
    evaluated = time.monotonic()
    evaluation = backend.evaluate(Path(result.artifact_path), items, CLASSES, {"confidence": EVAL_CONFIDENCE, "max_boxes": 100})
    return {
        "mode": mode,
        "skipped": False,
        "config": config,
        "train_wall_seconds": round(train_seconds, 1),
        "eval_wall_seconds": round(time.monotonic() - evaluated, 1),
        "mean_loss_first_last": [result.metrics["epochs"][0]["mean_loss"], result.metrics["epochs"][-1]["mean_loss"]],
        "pretrained": result.metrics.get("pretrained"),
        "evaluation": evaluation,
    }


def fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def markdown(results: list[dict], meta: dict) -> str:
    lines = [
        f"Command: `{meta['command']}`  ",
        f"Seed {meta['seed']}, {meta['epochs']} epochs, batch {meta['batch_size']}, confidence threshold {meta['confidence']} (the product's `EVAL_CONFIDENCE`), IoU 0.5.  ",
        f"Hardware: {meta['hardware']}.  Library versions: {meta['runtime']}.  ",
        "Data (SYNTHETIC shapes): " + "; ".join(f"{s} {d['images']} pictures / {d['boxes']} boxes ({', '.join(f'{k} {v}' for k, v in d['per_class'].items())})" for s, d in meta["data"].items()) + ".",
        "",
    ]
    for result in results:
        if result["skipped"]:
            lines += [f"### `pretrained = {result['mode']}`: SKIPPED", "", f"> {result['reason']}", ""]
            continue
        ev = result["evaluation"]
        lines += [
            f"### `pretrained = {result['mode']}` - train {result['train_wall_seconds']} s, evaluate {result['eval_wall_seconds']} s, "
            f"mean loss {result['mean_loss_first_last'][0]} -> {result['mean_loss_first_last'][1]} (epoch 1 -> last)",
            "",
            "| class | test boxes | AP50 | precision | recall | TP | FP | FN |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for label, row in ev["per_label"].items():
            lines.append(
                f"| {label} | {row['ground_truth_boxes']} | {fmt(row['ap50'])} | {fmt(row['precision'])} | {fmt(row['recall'])} | "
                f"{row['true_positives']} | {row['false_positives']} | {row['false_negatives']} |"
            )
        o = ev["overall"]
        lines += [f"| **all (mAP50 = mean of class AP50)** | {ev['data']['boxes']} | {fmt(o['map50'])} | {fmt(o['precision'])} | {fmt(o['recall'])} | {o['true_positives']} | {o['false_positives']} | {o['false_negatives']} |", ""]
    lines.append("SYNTHETIC shapes: these numbers say nothing about real scanner imagery.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--threads", type=int, default=0, help="torch threads; 0 = torch default")
    parser.add_argument("--modes", default="none,imagenet_backbone,coco")
    parser.add_argument("--train", type=int, default=SPLITS["train"])
    parser.add_argument("--val", type=int, default=SPLITS["val"])
    parser.add_argument("--test", type=int, default=SPLITS["test"])
    parser.add_argument("--workdir", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "athena-benchmark-training"))
    args = parser.parse_args()

    from projects.training import get_backend
    from projects.training.runner import EVAL_CONFIDENCE

    backend = get_backend("torchvision-fasterrcnn")
    available = backend.check_available()
    if not available.ok:
        raise SystemExit(f"{available.message} {available.how_to_enable}")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    dataset = workdir / "dataset"
    print(f"Drawing synthetic pictures into {dataset} (seed {args.seed}) ...", flush=True)
    data = build_dataset(dataset, args.seed, {"train": args.train, "val": args.val, "test": args.test})
    print(json.dumps(data), flush=True)
    results = []
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        print(f"== training pretrained={mode} ==", flush=True)
        results.append(run_mode(backend, dataset, mode, args))
        if not results[-1]["skipped"]:
            print(f"   done in {results[-1]['train_wall_seconds']} s", flush=True)
    meta = {
        "command": "python scripts/benchmark_training.py " + " ".join(sys.argv[1:]),
        "seed": args.seed,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "confidence": EVAL_CONFIDENCE,
        "hardware": hardware(),
        "runtime": ", ".join(f"{k} {v}" for k, v in backend.runtime_info().items()),
        "data": data,
        "downloads_enabled": os.environ.get("ATHENA_TRAINING_ALLOW_DOWNLOAD", "0"),
    }
    (workdir / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=1), encoding="utf-8")
    text = markdown(results, meta)
    (workdir / "results.md").write_text(text, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    main()
