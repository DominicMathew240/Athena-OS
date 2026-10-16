"""Reference backend: torchvision Faster R-CNN (MobileNetV3-Large, 320px FPN) on CPU.

Licence: torchvision and PyTorch are BSD-3-Clause. This backend builds the network with
``weights=None, weights_backbone=None`` so **no pretrained weights are downloaded or shipped**; the
model starts from random initialisation. That keeps licensing and offline use simple, but a model
trained from scratch on a few hundred images will usually perform poorly - this backend exists to
prove the train -> evaluate -> stage -> approve lifecycle end to end, not to be a production detector.
Read the evaluation report before believing any number.

Module import is torch-free; torch is imported inside the methods the worker calls.
"""

from __future__ import annotations

import importlib.util
import random
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .base import (
    Availability,
    BoxPrediction,
    CancelCallback,
    ProgressCallback,
    TrainingBackend,
    TrainingCancelled,
    TrainResult,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_BOX_PIXELS = 1.0
DEFAULTS = {"epochs": 5, "batch_size": 4, "learning_rate": 0.005, "seed": 0, "threads": 0}
ARTIFACT_NAME = "model.pt"
ARTIFACT_FORMAT = "athena.torchvision-fasterrcnn/1"


def _int(config: dict[str, Any], key: str, low: int, high: int) -> int:
    value = config.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be a whole number between {low} and {high}.")
    return value


class TorchvisionFasterRCNN(TrainingBackend):
    name = "torchvision-fasterrcnn"
    description = "Faster R-CNN (MobileNetV3-Large 320 FPN) trained from random weights on CPU."
    license_note = (
        "Code: torchvision and PyTorch, BSD-3-Clause. No pretrained weights are downloaded or "
        "bundled (weights=None, weights_backbone=None), so there is no separate model-weight licence. "
        "Models you train are yours; check the licence of your own training images."
    )
    dataset_format = "yolo"
    constraints = (
        "Needs torch==2.9.1 and torchvision==0.24.1 in the worker environment (web/requirements-autolabel.txt).",
        "CPU only. Expect minutes per epoch for a few hundred images on a laptop; no GPU is used.",
        "Starts from random weights, so small datasets give weak models. Check the evaluation report.",
        "Uses the train split only; the validation split is not used by this backend.",
    )

    def __init__(self) -> None:
        self._cache: tuple[tuple[str, int, int], Any, list[str]] | None = None

    # -- availability / config ---------------------------------------------------------
    def check_available(self) -> Availability:
        missing = [name for name in ("torch", "torchvision") if importlib.util.find_spec(name) is None]
        if not missing:
            return Availability(True, "torch and torchvision are installed.")
        return Availability(
            False,
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed in this environment.",
            "Install the worker requirements (pip install -r web/requirements-autolabel.txt) in the "
            "environment that runs `manage.py train_worker`, then start the worker.",
        )

    def default_config(self) -> dict[str, Any]:
        return dict(DEFAULTS)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        rate = config.get("learning_rate", DEFAULTS["learning_rate"])
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < float(rate) <= 1:
            raise ValueError("learning_rate must be a number above 0 and at most 1.")
        return {
            "epochs": _int(config, "epochs", 1, 100),
            "batch_size": _int(config, "batch_size", 1, 16),
            "learning_rate": float(rate),
            "seed": _int(config, "seed", 0, 2**31 - 1),
            "threads": _int(config, "threads", 0, 64),
        }

    def runtime_info(self) -> dict[str, Any]:
        import torch
        import torchvision

        return {
            "torch": torch.__version__,
            "torchvision": torchvision.__version__,
            "device": "cpu",
            "threads": torch.get_num_threads(),
        }

    # -- dataset -----------------------------------------------------------------------
    @staticmethod
    def read_classes(dataset_dir: Path) -> list[str]:
        text = (dataset_dir / "classes.txt").read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line]

    @staticmethod
    def read_split(dataset_dir: Path, split: str) -> list[tuple[Path, list[tuple[int, float, float, float, float]], int, int]]:
        """(image path, [(class, x, y, w, h) normalized top-left], width, height) per image."""
        folder = dataset_dir / "images" / split
        samples = []
        if not folder.is_dir():
            return samples
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            with Image.open(path) as image:
                width, height = image.size
            label_file = dataset_dir / "labels" / split / f"{path.stem}.txt"
            boxes = []
            if label_file.is_file():
                for line in label_file.read_text(encoding="ascii").splitlines():
                    parts = line.split()
                    if len(parts) != 5:
                        continue
                    cls, cx, cy, w, h = int(parts[0]), *map(float, parts[1:])
                    boxes.append((cls, cx - w / 2, cy - h / 2, w, h))
            samples.append((path, boxes, width, height))
        return samples

    def eval_items(self, dataset_dir: Path, split: str = "test"):
        from .base import EvalItem

        labels = self.read_classes(dataset_dir)
        return [
            EvalItem(path, [(labels[c], x, y, w, h) for c, x, y, w, h in boxes], width, height)
            for path, boxes, width, height in self.read_split(dataset_dir, split)
        ]

    # -- model -------------------------------------------------------------------------
    @staticmethod
    def build_model(num_classes: int):
        from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn

        # Nothing is downloaded: both weight arguments are None.
        return fasterrcnn_mobilenet_v3_large_320_fpn(
            weights=None, weights_backbone=None, num_classes=num_classes
        )

    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import torch
        from torchvision.transforms.functional import pil_to_tensor

        cfg = self.validate_config(config)
        labels = self.read_classes(dataset_dir)
        samples = self.read_split(dataset_dir, "train")
        if not samples:
            raise ValueError("The train split has no images, so there is nothing to learn from.")
        if cfg["threads"]:
            torch.set_num_threads(cfg["threads"])
        torch.manual_seed(cfg["seed"])
        shuffler = random.Random(cfg["seed"])

        model = self.build_model(len(labels) + 1)
        model.train()
        parameters = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.SGD(parameters, lr=cfg["learning_rate"], momentum=0.9, weight_decay=1e-4)

        def load(sample, flip: bool):
            path, boxes, width, height = sample
            with Image.open(path) as opened:
                tensor = pil_to_tensor(opened.convert("RGB")).float() / 255.0
            xyxy, classes = [], []
            for cls, x, y, w, h in boxes:
                x1, y1 = x * width, y * height
                x2, y2 = max(x1 + MIN_BOX_PIXELS, (x + w) * width), max(y1 + MIN_BOX_PIXELS, (y + h) * height)
                if flip:
                    x1, x2 = width - x2, width - x1
                xyxy.append([x1, y1, x2, y2])
                classes.append(cls + 1)  # 0 is background
            if flip:
                tensor = tensor.flip(-1)
            return tensor, {
                "boxes": torch.tensor(xyxy, dtype=torch.float32).reshape(-1, 4),
                "labels": torch.tensor(classes, dtype=torch.int64),
            }

        batches_per_epoch = -(-len(samples) // cfg["batch_size"])
        total_steps = cfg["epochs"] * batches_per_epoch
        step = 0
        history = []
        started = time.monotonic()
        for epoch in range(1, cfg["epochs"] + 1):
            order = list(range(len(samples)))
            shuffler.shuffle(order)
            running, count = 0.0, 0
            for start in range(0, len(order), cfg["batch_size"]):
                if cancel_cb():
                    raise TrainingCancelled("Training was cancelled.")
                batch = [load(samples[i], shuffler.random() < 0.5) for i in order[start : start + cfg["batch_size"]]]
                losses = model([item[0] for item in batch], [item[1] for item in batch])
                loss = sum(losses.values())
                if not torch.isfinite(loss):
                    raise ValueError(
                        "Training diverged (the loss became infinite). Lower learning_rate and try again."
                    )
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                running += float(loss.detach())
                count += 1
                step += 1
                progress_cb(step / total_steps, f"epoch {epoch}/{cfg['epochs']} step {step}/{total_steps} loss {float(loss.detach()):.4f}")
            history.append({"epoch": epoch, "mean_loss": round(running / max(count, 1), 5)})

        artifact = dataset_dir.parent / ARTIFACT_NAME
        torch.save(
            {
                "format": ARTIFACT_FORMAT,
                "labels": labels,
                "state_dict": model.state_dict(),
            },
            artifact,
        )
        return TrainResult(
            artifact_path=artifact,
            metrics={
                "train_images": len(samples),
                "train_boxes": sum(len(s[1]) for s in samples),
                "epochs": history,
                "train_seconds": round(time.monotonic() - started, 1),
                "note": "Training loss only; see the held-out evaluation for quality.",
            },
            label_names=labels,
        )

    # -- inference ---------------------------------------------------------------------
    def _load(self, artifact: Path):
        import torch

        stat = Path(artifact).stat()
        key = (str(artifact), stat.st_mtime_ns, stat.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        checkpoint = torch.load(artifact, map_location="cpu", weights_only=True)
        if checkpoint.get("format") != ARTIFACT_FORMAT:
            raise ValueError("This file is not a model saved by the torchvision-fasterrcnn backend.")
        labels = list(checkpoint["labels"])
        model = self.build_model(len(labels) + 1)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self._cache = (key, model, labels)
        return model, labels

    def predict(self, artifact: Path, image: Image.Image, thresholds: dict[str, Any]) -> list[BoxPrediction]:
        import torch
        from torchvision.transforms.functional import pil_to_tensor

        model, labels = self._load(artifact)
        confidence = float(thresholds.get("confidence", 0.5))
        max_boxes = int(thresholds.get("max_boxes", 100))
        model.roi_heads.score_thresh = confidence
        model.roi_heads.detections_per_img = max_boxes
        rgb = image.convert("RGB")
        width, height = rgb.size
        with torch.no_grad():
            output = model([pil_to_tensor(rgb).float() / 255.0])[0]
        found = []
        for box, label, score in zip(output["boxes"].tolist(), output["labels"].tolist(), output["scores"].tolist()):
            x1, y1 = max(0.0, box[0] / width), max(0.0, box[1] / height)
            x2, y2 = min(1.0, box[2] / width), min(1.0, box[3] / height)
            if x2 <= x1 or y2 <= y1 or not 1 <= label <= len(labels):
                continue
            found.append(BoxPrediction(labels[label - 1], x1, y1, x2 - x1, y2 - y1, float(score)))
        return found
