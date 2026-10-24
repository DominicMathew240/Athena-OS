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


class StartBlocked(Exception):
    """A run cannot be started right now for a reason only an administrator can fix (HTTP 409).

    ``str(error)`` is the plain-language reason; ``details`` is extra JSON the page can render."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


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
    """One held-out image: where it is and its ground-truth boxes (normalized x, y, w, h + label).

    ``asset_id`` is the project picture it came from. It is optional: when every item has one, the evaluation
    also records which pictures were missed or got a wrong box."""

    path: Path
    boxes: list[tuple[str, float, float, float, float]]
    width: int
    height: int
    asset_id: str | None = None


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

    def start_options(self, worker: dict[str, Any]) -> list[dict[str, Any]]:
        """Optional "how should it start?" choices the Teach panel can offer, in plain words.

        ``worker`` is ``{"online": bool, "downloads_allowed": bool | None}`` (what the running helper reported).
        Each choice is a dict with ``value`` (the config value), ``title``, ``summary``, ``licence_plain``,
        ``available``, ``ready``, ``needs_acknowledgement``, ``unavailable_reason``, ``how_to_enable`` and
        ``size_note``. The default is no choices, so the panel shows nothing extra."""
        return []

    def start_check(self, config: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
        """Server-side check before a run is queued (the page's tick-box is only a convenience).

        Returns ``{"needs_acknowledgement": bool, "blocked": str}``: ``blocked`` is a plain-language reason the
        run cannot start (empty when it can), ``needs_acknowledgement`` says the person must have ticked the
        box about the terms of what is being used."""
        return {"needs_acknowledgement": False, "blocked": ""}

    def resolve_start(self, worker: dict[str, Any]) -> dict[str, Any] | None:
        """What an automatic "start from the best ready-made model" would pick right now.

        ``None`` (the default) means this backend has no ready-made starts and does not understand ``"auto"``.
        Otherwise ``{"resolved": value | None, "available": [start_options entries], "reason_if_none": str,
        "how_to_enable": str}``. It never resolves to a from-scratch start."""
        return None

    def prepare_config(self, config: dict[str, Any], *, train_pictures: int, worker: dict[str, Any]) -> dict[str, Any]:
        """Turn the caller's config into the one that is stored, before ``validate_config``.

        Resolve ``"auto"`` choices and fill derived settings. Raise ``ValueError`` (HTTP 400) for a bad request and
        ``StartBlocked`` (HTTP 409) when it cannot run here. The default changes nothing."""
        return config

    def teach_preview(self, train_pictures: int | None, start: str | None) -> dict[str, Any] | None:
        """Settings the one-click start would derive for each quality choice, with a time estimate, shown before
        starting. ``None`` (the default) means this backend does not derive settings."""
        return None

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

    def predict_split(self, artifact: Path, items: list[EvalItem], labels: list[str], thresholds: dict[str, Any]):
        """Predictions on one split, for choosing a confidence threshold (the ``operating_points`` block).

        Returns ``(ground_truth, predictions)`` as ``evaluation.predict_items`` does, or ``None`` when this backend
        cannot predict on pictures outside the test split; the evaluation then simply has no ``operating_points``.
        Optional and additive: a backend that overrides ``evaluate`` keeps working unchanged. The default runs
        ``predict`` at the product's prediction floor, so it needs nothing extra from a backend.
        """
        from .evaluation import predict_items

        try:
            return predict_items(self, artifact, items, labels, thresholds)
        except NotImplementedError:
            return None
