"""The replaceable training-backend interface.

A backend turns an exported dataset directory into a model artifact and can run that artifact
on one image. Importing this module (or a backend's own module) must never import torch or any
other heavy framework: the web process lists and describes backends, and only the worker calls
``train`` / ``predict``. Import frameworks lazily inside those methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

ProgressCallback = Callable[[float, str], None]
"""``progress(fraction_0_to_1, message)``; may raise to stop training (timeout/cancel)."""
CancelCallback = Callable[[], bool]


class TrainingCancelled(Exception):
    """Raised by a backend when ``cancel_cb()`` returns true."""


class BackendUnavailable(Exception):
    """The backend cannot run here; the message says how to enable it."""


@dataclass(frozen=True)
class Availability:
    ok: bool
    message: str = ""
    how_to_enable: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "how_to_enable": self.how_to_enable}


@dataclass(frozen=True)
class BoxPrediction:
    """One detected box in normalized (0-1) coordinates, top-left origin."""

    label: str
    x: float
    y: float
    width: float
    height: float
    score: float


@dataclass
class TrainResult:
    artifact_path: Path  # a file inside the scratch work dir; the runner moves and hashes it
    metrics: dict[str, Any] = field(default_factory=dict)
    label_names: list[str] = field(default_factory=list)


@dataclass
class EvalItem:
    """One held-out image: where it is and its ground-truth boxes (normalized x, y, w, h + label)."""

    path: Path
    boxes: list[tuple[str, float, float, float, float]]
    width: int
    height: int


class TrainingBackend:
    """Subclass this and register it (see ``projects.training.register_backend`` or the
    ``athena.training_backends`` entry-point group)."""

    #: Stable identifier stored on runs and models. Lower-case letters, digits, "-" and "_".
    name: str = ""
    description: str = ""
    #: Plain-language licence of the backend code *and* of any weights it downloads or ships.
    license_note: str = ""
    #: Which export the backend reads from ``dataset_dir``: "yolo" or "coco".
    dataset_format: str = "yolo"
    #: Documented, user-visible limits (hardware, runtime, expected quality).
    constraints: tuple[str, ...] = ()

    def check_available(self) -> Availability:
        """Cheap check that never imports the heavy framework (use ``importlib.util.find_spec``)."""
        raise NotImplementedError

    def default_config(self) -> dict[str, Any]:
        return {}

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return the normalized config or raise ``ValueError`` with a plain-language message."""
        if config:
            raise ValueError("This backend takes no settings.")
        return {}

    def runtime_info(self) -> dict[str, Any]:
        """Framework versions and device, captured when a run starts. No paths, no host names."""
        return {}

    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        raise NotImplementedError

    def predict(self, artifact: Path, image: Image.Image, thresholds: dict[str, Any]) -> list[BoxPrediction]:
        """Run the model. ``thresholds`` carries ``confidence`` (0-1) and ``max_boxes``."""
        raise NotImplementedError

    def evaluate(
        self, artifact: Path, items: list[EvalItem], labels: list[str], thresholds: dict[str, Any]
    ) -> dict[str, Any]:
        """Held-out evaluation. The default runs ``predict`` and scores it with Athena's own metric,
        so every backend is judged by the same code. Override only to speed up inference."""
        from .evaluation import evaluate_items

        return evaluate_items(self, artifact, items, labels, thresholds)
