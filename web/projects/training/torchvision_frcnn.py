"""Reference backend: torchvision Faster R-CNN (MobileNetV3-Large, 320px FPN) on CPU.

Licence: torchvision and PyTorch are BSD-3-Clause. By default (``pretrained = "none"``) the network
is built with ``weights=None, weights_backbone=None`` so **no pretrained weights are downloaded or
shipped**; the model starts from random initialisation. That keeps licensing and offline use simple,
but a model trained from scratch on a few hundred images will usually perform poorly.

Opt-in: ``pretrained = "imagenet_backbone"`` (MobileNetV3-Large ImageNet-1K backbone, ~21 MB) or
``"coco"`` (whole torchvision COCO detector, ~74 MB, only the final class layer is re-learned).
These are fetched from download.pytorch.org **only** when the operator sets
``ATHENA_TRAINING_ALLOW_DOWNLOAD=1`` in the worker's environment; otherwise the run fails with a
plain message. Files are cached under ``<data home>/models/pretrained/checkpoints``. The weights'
training-data terms are not BSD; see ``license_note``.
Read the evaluation report before believing any number.

Module import is torch-free; torch is imported inside the methods the worker calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import random
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
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
DEFAULTS = {"epochs": 5, "batch_size": 4, "learning_rate": 0.005, "seed": 0, "threads": 0, "pretrained": "none"}
PRETRAINED_MODES = ("none", "imagenet_backbone", "coco")
DOWNLOAD_ENV = "ATHENA_TRAINING_ALLOW_DOWNLOAD"
DOWNLOAD_TIMEOUT_SECONDS = 60
# Published torchvision 0.24.1 weights (a test checks these match the installed package's enums).
# hash_prefix is the first 8 hex digits of the file's sha256, as torchvision itself uses.
PRETRAINED_FILES = {
    "imagenet_backbone": {
        "enum": "MobileNet_V3_Large_Weights.IMAGENET1K_V1",
        "url": "https://download.pytorch.org/models/mobilenet_v3_large-8738ca79.pth",
        "hash_prefix": "8738ca79",
        "approx_mb": 21,
        "what": "MobileNetV3-Large backbone trained on ImageNet-1K",
    },
    "coco": {
        "enum": "FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1",
        "url": "https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth",
        "hash_prefix": "907ea3f9",
        "approx_mb": 74,
        "what": "Faster R-CNN MobileNetV3-Large 320 FPN trained on COCO",
    },
}
ARTIFACT_NAME = "model.pt"
ARTIFACT_FORMAT = "athena.torchvision-fasterrcnn/1"


def downloads_allowed() -> bool:
    return os.environ.get(DOWNLOAD_ENV, "0").strip().lower() in {"1", "true", "yes"}


def pretrained_dir() -> Path:
    """Where published weights are cached: ``<data home>/models/pretrained`` (torch-hub layout)."""
    from . import models_root

    return models_root() / "pretrained"


def _weights_path(mode: str) -> Path:
    return pretrained_dir() / "checkpoints" / PRETRAINED_FILES[mode]["url"].rsplit("/", 1)[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, hash_prefix: str) -> bool:
    return _sha256(path).startswith(hash_prefix)


def _fetch(url: str, destination: Path, hash_prefix: str) -> None:
    """Download ``url`` to ``destination`` and check the published hash prefix. Tests replace this."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, open(partial, "wb") as out:  # noqa: S310
            while chunk := response.read(1 << 20):
                out.write(chunk)
        if not _verify(partial, hash_prefix):
            raise ValueError("The downloaded weights file did not match its published checksum, so it was discarded.")
        partial.replace(destination)
    except urllib.error.URLError as error:
        raise ValueError(
            f"Could not download the pretrained weights ({getattr(error, 'reason', error)}). Check this "
            "computer's internet access, or start the run with pretrained set to 'none'."
        ) from None
    except OSError as error:
        raise ValueError(f"Could not save the pretrained weights ({error.__class__.__name__}).") from None
    finally:
        partial.unlink(missing_ok=True)


def ensure_pretrained_weights(mode: str) -> dict[str, Any]:
    """Make sure the weights for ``mode`` are cached; returns provenance for the run record.

    Nothing is fetched unless ``ATHENA_TRAINING_ALLOW_DOWNLOAD`` is switched on. A file that is already
    in the cache and matches its published checksum is used without the switch (no download happens).
    """
    if mode == "none":
        return {"mode": "none"}
    info = PRETRAINED_FILES[mode]
    path = _weights_path(mode)
    if path.is_file() and _verify(path, info["hash_prefix"]):
        source = "cache"
    elif not downloads_allowed():
        raise ValueError(
            f"This run asked for pretrained weights ('{mode}': {info['what']}, about {info['approx_mb']} MB), but "
            f"downloading is switched off on this computer. Either set {DOWNLOAD_ENV}=1 in the environment of "
            "the training helper (manage.py train_worker) and run it again - the file is fetched once from "
            "download.pytorch.org and kept in your Athena data folder under models/pretrained - or start the run "
            "with pretrained set to 'none', which downloads nothing. Read the licence note first: the pretrained "
            "weights come with their own training-data terms."
        )
    else:
        path.unlink(missing_ok=True)  # a stale or corrupt file must not survive
        _fetch(info["url"], path, info["hash_prefix"])
        source = "download"
    return {
        "mode": mode,
        "what": info["what"],
        "torchvision_weights": info["enum"],
        "source_url": info["url"],
        "sha256": _sha256(path) if path.is_file() else None,
        "from": source,
    }


_VERIFIED: dict[tuple[str, int, int, str], bool] = {}


def cached_weights_ok(mode: str) -> bool:
    """True when the published file for ``mode`` is in the cache and matches its published checksum.

    Hashing 75 MB takes a moment, so the answer is remembered per (path, size, modified time)."""
    info = PRETRAINED_FILES[mode]
    path = _weights_path(mode)
    try:
        stat = path.stat()
    except OSError:
        return False
    key = (str(path), stat.st_size, stat.st_mtime_ns, info["hash_prefix"])
    if key not in _VERIFIED:
        _VERIFIED[key] = _verify(path, info["hash_prefix"])
    return _VERIFIED[key]


def _downloads_for(worker: dict[str, Any]) -> tuple[bool, str]:
    """(allowed, who said so). The helper that trains is the one that downloads, so its own report wins; with
    no helper running the console's environment is the best guess, and the page says it is a guess."""
    reported = worker.get("downloads_allowed")
    if reported is not None:
        return bool(reported), "helper"
    return downloads_allowed(), "console"


ENABLE_STEPS = (
    "Ask your administrator to switch downloads on: set ATHENA_TRAINING_ALLOW_DOWNLOAD=1 in the environment of the "
    "teaching helper (manage.py train_worker), restart the helper, then press “Check again” here."
)
LAWYER = "Ask a lawyer before commercial use."
GENERAL_TERMS = (
    "Ready-made models come with their own terms, separate from the free software that trains them. The makers of the "
    "software say it is your responsibility to decide whether you may use them."
)
START_OPTION_TEXT = {
    "none": {
        "title": "Start from scratch",
        "summary": (
            "The computer starts knowing nothing and learns only from your own pictures. Nothing is downloaded and there are "
            "no extra licence terms, so this is the safest choice. But learning from nothing needs many labelled pictures: "
            "with only a few it often learns almost nothing, so read the results before you trust it."
        ),
    },
    "imagenet_backbone": {
        "title": "Start from a general picture model (ImageNet)",
        "summary": (
            "The computer starts with the “eyes” of a ready-made model that was taught on a huge collection of everyday photos "
            "(ImageNet), then learns your items on top. It usually needs far fewer pictures than starting from scratch."
        ),
        "licence_plain": (
            f"{GENERAL_TERMS} The ImageNet collection says it may be used only for non-commercial research and education. "
            f"Nobody has confirmed whether that also covers a model that was taught from it. {LAWYER}"
        ),
    },
    "coco": {
        "title": "Start from a general object model (COCO)",
        "summary": (
            "The computer starts with a ready-made model that already finds everyday objects in photos (taught on the COCO "
            "collection), and only learns your own kinds of item on top. It usually needs far fewer pictures than starting "
            "from scratch."
        ),
        "licence_plain": (
            f"{GENERAL_TERMS} The photos in the COCO collection come from many owners with mixed terms, and the collection says "
            f"it does not own them. Nobody has confirmed whether those terms reach a model that was taught from them. {LAWYER}"
        ),
    },
}


@contextmanager
def _torch_hub_dir(path: Path):
    """Point torch.hub at our cache for the duration of a model build, then restore it."""
    import torch

    previous = torch.hub.get_dir()
    torch.hub.set_dir(str(path))
    try:
        yield
    finally:
        torch.hub.set_dir(previous)


def _int(config: dict[str, Any], key: str, low: int, high: int) -> int:
    value = config.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be a whole number between {low} and {high}.")
    return value


class TorchvisionFasterRCNN(TrainingBackend):
    name = "torchvision-fasterrcnn"
    description = (
        "Faster R-CNN (MobileNetV3-Large 320 FPN) on CPU. Starts from random weights unless you opt in to "
        "published pretrained weights (setting 'pretrained')."
    )
    license_note = (
        "Code: torchvision and PyTorch, BSD-3-Clause. Default (pretrained='none'): No pretrained weights are "
        "downloaded or bundled (weights=None, weights_backbone=None), so there is no separate model-weight "
        "licence. Opt-in (pretrained='imagenet_backbone' or 'coco', and only if ATHENA_TRAINING_ALLOW_DOWNLOAD=1): "
        "torchvision's published weights are fetched from download.pytorch.org. Those weights are NOT covered by "
        "torchvision's BSD-3 code licence alone. Checked: torchvision's README says its pre-trained models 'may "
        "have their own licenses or terms and conditions derived from the dataset used for training' and that "
        "you must decide whether you may use them; the weight metadata shipped in torchvision 0.24.1 has no "
        "licence field. ImageNet (backbone): image-net.org's terms of access say the Database may be used 'only "
        "for non-commercial research and educational purposes'; whether that reaches weights trained on it is "
        "unverified, ask a lawyer before commercial use. COCO (full detector): cocodataset.org says the "
        "annotations are CC BY 4.0, the COCO Consortium does not own the images and their use must follow the "
        "Flickr terms (mixed per-image licences); whether that reaches weights trained on them is unverified, "
        "ask a lawyer. Models you train are derived from your own images; check the licence of those images."
    )
    dataset_format = "yolo"
    constraints = (
        "Needs torch==2.9.1 and torchvision==0.24.1 in the worker environment (web/requirements-autolabel.txt).",
        "CPU only. Expect minutes per epoch for a few hundred images on a laptop; no GPU is used.",
        "Starts from random weights by default, so small datasets give weak models. Check the evaluation report.",
        "Optional pretrained start (setting 'pretrained'; needs ATHENA_TRAINING_ALLOW_DOWNLOAD=1 for the first "
        "download, about 21 MB for 'imagenet_backbone' or 74 MB for 'coco'). See the licence note.",
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
        pretrained = config.get("pretrained", DEFAULTS["pretrained"])
        if not isinstance(pretrained, str) or pretrained not in PRETRAINED_MODES:
            raise ValueError("pretrained must be one of: " + ", ".join(f"'{m}'" for m in PRETRAINED_MODES) + ".")
        return {
            "pretrained": pretrained,
            "epochs": _int(config, "epochs", 1, 100),
            "batch_size": _int(config, "batch_size", 1, 16),
            "learning_rate": float(rate),
            "seed": _int(config, "seed", 0, 2**31 - 1),
            "threads": _int(config, "threads", 0, 64),
        }

    # -- the Teach panel's "start from" choices ------------------------------------------------
    def _unavailable(self, mode: str, worker: dict[str, Any]) -> tuple[str, str]:
        """(reason, how to fix) for a ready-made start that cannot be used right now; ("", "") when it can."""
        if cached_weights_ok(mode):
            return "", ""
        allowed, who = _downloads_for(worker)
        if allowed:
            return "", ""
        mb = PRETRAINED_FILES[mode]["approx_mb"]
        reason = (
            f"Downloading ready-made models is switched off on this computer, and this one (about {mb} MB) has not been "
            "downloaded before, so it cannot be used. Only an administrator can change this."
        )
        if who == "console" and not worker.get("online"):
            reason += (
                " The teaching helper is not running, so this is the setting of the console itself; the helper must have "
                "the same setting."
            )
        return reason, ENABLE_STEPS

    def start_options(self, worker: dict[str, Any]) -> list[dict[str, Any]]:
        options = []
        for value in PRETRAINED_MODES:
            text = START_OPTION_TEXT[value]
            if value == "none":
                options.append({
                    "value": value, "title": text["title"], "summary": text["summary"], "licence_plain": "",
                    "available": True, "ready": True, "needs_acknowledgement": False, "unavailable_reason": "",
                    "how_to_enable": "", "size_note": "",
                })
                continue
            ready = cached_weights_ok(value)
            reason, how = self._unavailable(value, worker)
            mb = PRETRAINED_FILES[value]["approx_mb"]
            options.append({
                "value": value, "title": text["title"], "summary": text["summary"], "licence_plain": text["licence_plain"],
                "available": not reason, "ready": ready, "needs_acknowledgement": True,
                "unavailable_reason": reason, "how_to_enable": how,
                "size_note": (
                    f"Already on this computer (about {mb} MB, verified). Nothing will be downloaded."
                    if ready else f"About {mb} MB. It is downloaded once, from download.pytorch.org, and then kept in Athena’s data folder."
                ),
            })
        return options

    def start_check(self, config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
        mode = config.get("pretrained", "none")
        if mode == "none":
            return {"needs_acknowledgement": False, "blocked": ""}
        reason, how = self._unavailable(mode, worker)
        return {"needs_acknowledgement": True, "blocked": f"{reason} {how}".strip()}

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
    def build_model(num_classes: int, pretrained: str = "none"):
        """Build the network. ``pretrained`` other than 'none' needs the weights already cached
        (see ``ensure_pretrained_weights``); torchvision then reads them from our cache folder."""
        from torchvision.models import MobileNet_V3_Large_Weights
        from torchvision.models.detection import (
            FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
            fasterrcnn_mobilenet_v3_large_320_fpn,
        )

        if pretrained == "none":  # Nothing is downloaded: both weight arguments are None.
            return fasterrcnn_mobilenet_v3_large_320_fpn(weights=None, weights_backbone=None, num_classes=num_classes)
        with _torch_hub_dir(pretrained_dir()):
            if pretrained == "imagenet_backbone":
                return fasterrcnn_mobilenet_v3_large_320_fpn(
                    weights=None,
                    weights_backbone=MobileNet_V3_Large_Weights.IMAGENET1K_V1,
                    num_classes=num_classes,
                )
            from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

            model = fasterrcnn_mobilenet_v3_large_320_fpn(
                weights=FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1, weights_backbone=None
            )
        model.roi_heads.box_predictor = FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features, num_classes)
        return model

    @classmethod
    def build_structure(cls, num_classes: int, frozen_bn: bool):
        """The same network without any weights, for loading a saved artifact (never downloads).
        Pretrained starts use frozen batch-norm layers, which have a different state-dict layout."""
        from torch import nn
        from torchvision.ops.misc import FrozenBatchNorm2d

        model = cls.build_model(num_classes)
        if frozen_bn:

            def freeze(module):
                for name, child in module.named_children():
                    if isinstance(child, nn.BatchNorm2d):
                        setattr(module, name, FrozenBatchNorm2d(child.num_features, eps=child.eps))
                    else:
                        freeze(child)

            freeze(model)
        return model

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
        provenance = ensure_pretrained_weights(cfg["pretrained"])  # fails fast, before any work
        labels = self.read_classes(dataset_dir)
        samples = self.read_split(dataset_dir, "train")
        if not samples:
            raise ValueError("The train split has no images, so there is nothing to learn from.")
        if cfg["threads"]:
            torch.set_num_threads(cfg["threads"])
        torch.manual_seed(cfg["seed"])
        shuffler = random.Random(cfg["seed"])

        model = self.build_model(len(labels) + 1, cfg["pretrained"])
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
                "pretrained": cfg["pretrained"],
                "frozen_bn": cfg["pretrained"] != "none",
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
                "pretrained": provenance,
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
        model = self.build_structure(len(labels) + 1, bool(checkpoint.get("frozen_bn", False)))
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
