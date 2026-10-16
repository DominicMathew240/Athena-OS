"""SAM + CLIP auto-labeler.

Segment Anything proposes class-agnostic object regions from a grid of point prompts;
CLIP then scores each region against the project's label names (plus "background"
prompts so scenery is dropped). Heavy imports happen lazily so the web process never
loads torch.

Quality depends on label names that describe a visible object ("knife", "laptop").
State-style labels ("damaged", "normal") cannot be judged from a crop by CLIP; for those
use the boxes as candidates and expect to relabel them during review.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
from PIL import Image

from .base import Proposal

logger = logging.getLogger(__name__)

BACKGROUND_PROMPTS = (
    "a photo of the background",
    "a photo of a table, floor or wall",
    "a photo of empty space",
    "a blurry photo of part of a scene",
)
MIN_MASK_IOU = 0.82
MIN_STABILITY = 0.88
MIN_AREA_FRACTION = 0.004
MAX_AREA_FRACTION = 0.85
MAX_BOX_FRACTION = 0.9  # a box this large is almost always the table or backdrop
NMS_IOU = 0.6
CONTAINED_FRACTION = 0.8  # same-label boxes mostly inside a bigger one are parts, not objects
CROP_MARGIN = 0.08
POINT_BATCH = 32


class AutoLabelerUnavailable(RuntimeError):
    """The model stack cannot run here (missing packages, weights, or network)."""


def box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    inter_h = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = inter_w * inter_h
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def non_max_suppression(
    boxes: list[tuple[float, float, float, float]], scores: list[float], iou_threshold: float
) -> list[int]:
    order = sorted(range(len(boxes)), key=lambda index: scores[index], reverse=True)
    kept: list[int] = []
    for index in order:
        if all(box_iou(boxes[index], boxes[other]) < iou_threshold for other in kept):
            kept.append(index)
    return kept


def suppress_contained(
    boxes: list[tuple[float, float, float, float]], labels: list[int], scores: list[float]
) -> list[int]:
    """Drop a box when >= CONTAINED_FRACTION of it lies inside a larger box with the same label.

    SAM also segments parts (a book's title, half of its cover). For object labelling the whole
    object is the useful box; the part would only add review work.
    """

    def area(box: tuple[float, float, float, float]) -> float:
        return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])

    kept: list[int] = []
    for index in sorted(range(len(boxes)), key=lambda i: area(boxes[i]), reverse=True):
        small = boxes[index]
        contained = False
        for other in kept:
            if labels[other] != labels[index]:
                continue
            big = boxes[other]
            inter_w = max(0.0, min(small[2], big[2]) - max(small[0], big[0]))
            inter_h = max(0.0, min(small[3], big[3]) - max(small[1], big[1]))
            if area(small) > 0 and inter_w * inter_h / area(small) >= CONTAINED_FRACTION:
                contained = True
                break
        if not contained:
            kept.append(index)
    return kept


def mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    columns = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or columns.size == 0:
        return None
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


class SamClipAutoLabeler:
    def __init__(self, config: dict[str, Any]) -> None:
        self.sam_id: str = config["SAM_MODEL"]
        self.clip_id: str = config["CLASSIFIER_MODEL"]
        self.points_per_side: int = int(config["POINTS_PER_SIDE"])
        self.local_only: bool = not config.get("ALLOW_DOWNLOAD", True)
        self.requested_device: str = config.get("DEVICE", "auto")
        self._loaded = False

    @property
    def name(self) -> str:
        return f"{self.sam_id} + {self.clip_id}"

    def warm_up(self) -> None:
        """Load the weights now so the first job does not pay for it."""
        self._load()

    # -- model loading -------------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        if self.local_only:
            # Without this transformers still probes the Hub for safetensors conversions.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor, SamModel, SamProcessor
        except ImportError as error:  # pragma: no cover - depends on the install
            raise AutoLabelerUnavailable(
                "The model packages (torch, transformers) are not installed in the worker. "
                "Install web/requirements-autolabel.txt."
            ) from error
        self._torch = torch
        self.device = self._pick_device(torch)
        kwargs = {"local_files_only": self.local_only}
        try:
            self.sam = SamModel.from_pretrained(self.sam_id, **kwargs).eval()
            self.sam_processor = SamProcessor.from_pretrained(self.sam_id, **kwargs)
            self.clip = CLIPModel.from_pretrained(self.clip_id, **kwargs).eval()
            self.clip_processor = CLIPProcessor.from_pretrained(self.clip_id, **kwargs)
        except OSError as error:
            raise AutoLabelerUnavailable(
                f"Could not load model weights ({self.sam_id}, {self.clip_id}). "
                "Check the worker's network access or pre-download the weights."
            ) from error
        self._to_device(self.device)
        self._loaded = True
        logger.info("Auto-label models loaded on %s", self.device)

    def _pick_device(self, torch) -> str:
        if self.requested_device != "auto":
            return self.requested_device
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _to_device(self, device: str) -> None:
        self.sam.to(device)
        self.clip.to(device)
        self.device = device

    # -- proposal pipeline ---------------------------------------------------------
    def propose(
        self,
        image: Image.Image,
        labels: list[str],
        *,
        min_confidence: float,
        max_boxes: int,
    ) -> list[Proposal]:
        self._load()
        image = image.convert("RGB")
        try:
            return self._propose(image, labels, min_confidence, max_boxes)
        except (RuntimeError, TypeError) as error:
            if self.device == "cpu":
                raise
            logger.warning("Falling back to CPU after %s device error: %s", self.device, error)
            self._to_device("cpu")
            return self._propose(image, labels, min_confidence, max_boxes)

    def _propose(
        self, image: Image.Image, labels: list[str], min_confidence: float, max_boxes: int
    ) -> list[Proposal]:
        regions = self._segment(image)
        if not regions:
            return []
        width, height = image.size
        crops = [self._crop(image, box) for box, _ in regions]
        probabilities = self._classify(crops, labels)
        candidates: list[tuple[tuple[float, float, float, float], int, float]] = []
        for (box, quality), probs in zip(regions, probabilities):
            best = int(np.argmax(probs))
            if best >= len(labels):  # background prompt won
                continue
            box_fraction = (box[2] - box[0]) * (box[3] - box[1]) / (width * height)
            if box_fraction > MAX_BOX_FRACTION:
                continue
            # SAM's predicted IoU is not bounded to 1, so clamp before combining scores.
            confidence = float(probs[best]) * min(1.0, max(0.0, float(quality)))
            if confidence < min_confidence:
                continue
            candidates.append((box, best, confidence))
        kept = non_max_suppression(
            [candidate[0] for candidate in candidates],
            [candidate[2] for candidate in candidates],
            NMS_IOU,
        )
        kept_boxes = [candidates[i] for i in kept]
        kept = [
            kept[j]
            for j in suppress_contained(
                [c[0] for c in kept_boxes], [c[1] for c in kept_boxes], [c[2] for c in kept_boxes]
            )
        ]
        proposals = [
            Proposal(
                x=candidates[i][0][0] / width,
                y=candidates[i][0][1] / height,
                width=(candidates[i][0][2] - candidates[i][0][0]) / width,
                height=(candidates[i][0][3] - candidates[i][0][1]) / height,
                label=labels[candidates[i][1]],
                confidence=round(candidates[i][2], 4),
            )
            for i in kept
        ]
        proposals.sort(key=lambda proposal: proposal.confidence, reverse=True)
        return proposals[:max_boxes]

    def _grid(self, width: int, height: int) -> list[list[float]]:
        steps = np.linspace(0.5 / self.points_per_side, 1 - 0.5 / self.points_per_side, self.points_per_side)
        return [[float(x * width), float(y * height)] for y in steps for x in steps]

    def _segment(self, image: Image.Image) -> list[tuple[tuple[int, int, int, int], float]]:
        """Return ``(pixel_box, mask_quality)`` for each distinct SAM region."""
        torch = self._torch
        width, height = image.size
        area = width * height
        inputs = self.sam_processor(image, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(self.device)
        with torch.no_grad():
            embeddings = self.sam.get_image_embeddings(pixel_values)
        points = self._grid(width, height)
        found: list[tuple[tuple[int, int, int, int], float]] = []
        for start in range(0, len(points), POINT_BATCH):
            batch = points[start : start + POINT_BATCH]
            prompt = self.sam_processor(
                image, input_points=[[[point] for point in batch]], return_tensors="pt"
            )
            with torch.no_grad():
                outputs = self.sam(
                    image_embeddings=embeddings,
                    input_points=prompt["input_points"].to(self.device, dtype=torch.float32),
                    multimask_output=True,
                )
            masks = self.sam_processor.image_processor.post_process_masks(
                outputs.pred_masks.cpu(),
                prompt["original_sizes"].cpu(),
                prompt["reshaped_input_sizes"].cpu(),
                binarize=False,
            )[0]  # (points, 3, H, W) logits
            scores = outputs.iou_scores.cpu()[0]  # (points, 3)
            for point_masks, point_scores in zip(masks, scores):
                for logits, score in zip(point_masks, point_scores):
                    if float(score) < MIN_MASK_IOU:
                        continue
                    mask = (logits > 0).numpy()
                    fraction = float(mask.sum()) / area
                    if not MIN_AREA_FRACTION <= fraction <= MAX_AREA_FRACTION:
                        continue
                    if self._stability(logits.numpy()) < MIN_STABILITY:
                        continue
                    bounds = mask_bounds(mask)
                    if bounds:
                        found.append((bounds, float(score)))
        if not found:
            return []
        kept = non_max_suppression([box for box, _ in found], [score for _, score in found], 0.9)
        return [found[index] for index in kept]

    @staticmethod
    def _stability(logits: np.ndarray, offset: float = 1.0) -> float:
        high = float((logits > offset).sum())
        low = float((logits > -offset).sum())
        return high / low if low > 0 else 0.0

    @staticmethod
    def _crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
        x0, y0, x1, y1 = box
        margin_x = int((x1 - x0) * CROP_MARGIN)
        margin_y = int((y1 - y0) * CROP_MARGIN)
        return image.crop(
            (max(0, x0 - margin_x), max(0, y0 - margin_y), min(image.width, x1 + margin_x), min(image.height, y1 + margin_y))
        )

    def _classify(self, crops: list[Image.Image], labels: list[str]) -> np.ndarray:
        """Softmax over ``labels`` followed by the background prompts, one row per crop."""
        torch = self._torch
        prompts = [f"a photo of a {label}" for label in labels] + list(BACKGROUND_PROMPTS)
        rows: list[np.ndarray] = []
        for start in range(0, len(crops), 16):
            inputs = self.clip_processor(
                text=prompts, images=crops[start : start + 16], return_tensors="pt", padding=True
            ).to(self.device)
            with torch.no_grad():
                logits = self.clip(**inputs).logits_per_image
            probabilities = logits.softmax(dim=-1)
            # Background prompts compete as one class: sum their mass.
            label_probs = probabilities[:, : len(labels)]
            background = probabilities[:, len(labels) :].sum(dim=-1, keepdim=True)
            rows.append(torch.cat([label_probs, background], dim=-1).cpu().numpy())
        return np.concatenate(rows, axis=0)
