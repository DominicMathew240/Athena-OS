"""Compatible detector inference adapters for recorded operation sources (T21).

The adapter takes what an execution already has — the pinned model binding's exact
``ModelVersion`` (T05), the recording's reproducible frame timeline (T20) — and runs
REAL detector inference on reference frames taken from the recording's stored bytes:

* **Compatibility is re-validated, never trusted.** The binding's pin was verified
  when it was made; by run time the model may have been retired, its artifact may
  have changed, or its labels may no longer satisfy the recipe. The adapter checks:
  the model is still approved, its task is a detection task (the vision-detection
  recipe or a pre-recipe image model), its label set covers every label the pinned
  people-counting recipe expects, and its artifact file still exists and matches the
  recorded checksum. A pin that no longer passes is a refusal with a plain message.
* **Reference frames come from the timeline.** Frames are sampled deterministically
  from the timeline's on-time frames (evenly spaced, always including the first and
  last), extracted with ffmpeg from a re-digested private copy of the recording —
  the same forced-demeruxer, local-file-protocol, digest-first pattern as
  :mod:`projects.source_time`.
* **Outputs are normalized.** One record per detection: the timeline frame's index
  and PTS, the frame's session, the label, the score, and a top-left-origin box in
  [0,1] coordinates. A failed adapter call raises :class:`InferenceAdapterError`
  with a plain-language message; it never returns zero or fabricated detections.

The adapter runs the bound backend's ``predict`` path through the same
:func:`projects.training.get_backend` registry the training and inference workers
use, with the product's prediction floor (a low confidence threshold so execution
outcomes can report what the model found, not what a screen chose to display).
Tracking (T22) and crossing episodes (T25) are out of scope: the adapter produces
per-frame detections only.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage

from ..source_time import FrameTimeline

#: The product's prediction floor, as the inference worker uses: record what the
#: model actually found, not what a display threshold would hide.
PREDICTION_FLOOR = 0.05
#: At most this many detections are kept per frame, matching the inference worker.
MAX_PREDICTIONS_PER_FRAME = 100
#: How many reference frames an execution samples from the timeline.
DEFAULT_SAMPLE_FRAMES = 6
#: Explicit frame-processing policies (T24):
#: - ``recorded_sampled``: deterministic evenly spaced reference sampling; never claims full coverage.
#: - ``recorded_full``: recorded full-frame analysis; decodes every on-time frame and refuses to drop frames under queue limits.
#: - ``live_frame_drop``: live bounded-queue backpressure policy; drops intermediate frames when arrival exceeds ``max_in_flight_frames``,
#:   accounts every dropped frame and overload gap, starts a new tracking session across drop gaps, and never claims full coverage.
FRAME_POLICY_RECORDED_SAMPLED = "recorded_sampled"
FRAME_POLICY_RECORDED_FULL = "recorded_full"
FRAME_POLICY_LIVE_DROP = "live_frame_drop"
SUPPORTED_FRAME_POLICIES = (
    FRAME_POLICY_RECORDED_SAMPLED,
    FRAME_POLICY_RECORDED_FULL,
    FRAME_POLICY_LIVE_DROP,
)
#: Extracting one frame must finish within this budget (the same order as the
#: timeline build's decode budget).
_FRAME_TIMEOUT_SECONDS = 120

#: The recipe keys whose model must detect boxes. ``None`` (a pre-recipe image
#: model) is a detection task too: every image model the console trained before
#: recipes existed was a box detector.
DETECTION_TASKS = (None, "vision-detection")

#: The one recipe the operation domain pins today, and the labels it expects a
#: compatible detector to cover. A recipe that is not in this table has no
#: adapter contract yet and is refused rather than guessed.
RECIPE_EXPECTED_LABELS = {"people_counting": ("person",)}


class InferenceAdapterError(ValueError):
    """Inference could not run, with a plain-language reason. Never a silent zero."""

    def __init__(self, message, *, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


@dataclass(frozen=True)
class FrameDetections:
    """What the bound model found on one reference frame.

    ``frame_index``/``pts``/``session`` come from the timeline; ``detections`` is
    a list of ``{"label", "x", "y", "width", "height", "score"}`` dicts with
    top-left-origin boxes in [0,1] coordinates.
    """

    frame_index: int
    pts: float
    session: int
    wall_clock: str | None
    detections: tuple[dict, ...]


@dataclass(frozen=True)
class FramePolicyPlan:
    """Explicit frame-selection plan and drop/skip accounting for one timeline (T24)."""

    policy: str
    total_on_time_frames: int
    selected_indices: tuple[int, ...]
    sampled_skip_count: int
    dropped_frame_indices: tuple[int, ...]
    overload_gaps: tuple[dict, ...]
    session_overrides: dict[int, int]
    sampled_analysis: bool
    overload_dropped: bool
    full_frame_coverage: bool


@dataclass(frozen=True)
class AdapterReport:
    """The normalized outcome of one adapter run, recorded on the execution row."""

    model_version_id: str
    model_version_number: int
    backend: str
    artifact_sha256: str
    recipe_key: str
    expected_labels: tuple[str, ...]
    model_labels: tuple[str, ...]
    frame_count: int
    frames: tuple[FrameDetections, ...]
    frame_policy: str = FRAME_POLICY_RECORDED_SAMPLED
    policy_plan: FramePolicyPlan | None = None

    def summary(self) -> dict:
        """A plain JSON-serializable summary for execution outcomes."""
        return {
            "model_version_id": self.model_version_id,
            "model_version_number": self.model_version_number,
            "backend": self.backend,
            "artifact_sha256": self.artifact_sha256,
            "recipe_key": self.recipe_key,
            "expected_labels": list(self.expected_labels),
            "model_labels": list(self.model_labels),
            "frame_count": self.frame_count,
            "total_detections": sum(len(frame.detections) for frame in self.frames),
            "frame_policy": self.frame_policy,
        }


# -- compatibility: re-validated at inference time, the pin is never trusted --------------------
def validate_compatibility(model_version, recipe_key: str) -> dict:
    """Re-run the compatibility checks at inference time. Returns the checks dict.

    ``model_version`` is the exact ``ModelVersion`` the binding pinned. A model
    that was compatible when the pin was made can have become incompatible since:
    retired, its artifact replaced, or — for a recipe that declares expected
    labels — its label set no longer covering them. Each failure is a refusal
    with a plain message, never a downgrade to a different model.
    """
    from ..models import ModelVersion

    checks = {
        "compatible": True,
        "reason": None,
        "model_version_id": str(model_version.id),
        "model_version_number": model_version.version,
        "backend": model_version.backend,
        "task": model_version.task,
        "artifact_sha256": model_version.artifact_sha256,
        "stage": model_version.stage,
        "recipe_key": recipe_key,
        "expected_labels": list(RECIPE_EXPECTED_LABELS.get(recipe_key, ())),
        "model_labels": list(model_version.label_names or []),
    }
    if recipe_key not in RECIPE_EXPECTED_LABELS:
        checks.update(
            compatible=False,
            reason=(
                f"The recipe '{recipe_key}' has no inference adapter yet, so no model can run for it. "
                "This execution is refused rather than run with an unverified model."
            ),
        )
        return checks
    if model_version.task not in DETECTION_TASKS:
        checks.update(
            compatible=False,
            reason=(
                f"The bound model's task is '{model_version.task or 'unknown'}', which is not a detection "
                "task; it cannot produce the boxes this operation needs. Bind a detection model."
            ),
        )
        return checks
    if model_version.stage != ModelVersion.APPROVED:
        checks.update(
            compatible=False,
            reason=(
                f"The bound model version is no longer approved (it is {model_version.stage}), so it will not "
                "run. Bind the currently approved model version and start a new run."
            ),
        )
        return checks
    missing = [label for label in RECIPE_EXPECTED_LABELS[recipe_key] if label not in checks["model_labels"]]
    if missing:
        checks.update(
            compatible=False,
            reason=(
                f"The bound model was not taught to find {', '.join(missing)}, which the "
                f"'{recipe_key}' recipe expects. Bind a model whose labels cover the recipe's needs."
            ),
        )
        return checks
    return checks


def _verify_artifact(model_version) -> Path:
    """The model's artifact path, re-digested. Raises with a plain message on mismatch."""
    from ..training.lifecycle import LifecycleError, artifact_file, sha256_file

    try:
        path = artifact_file(model_version)
        if not path.is_file():
            raise LifecycleError("missing", 409)
        if sha256_file(path) != model_version.artifact_sha256:
            raise LifecycleError("mismatch", 409)
    except LifecycleError:
        raise InferenceAdapterError(
            "The bound model's file no longer matches the checksum recorded when it was trained, so it will "
            "not be run. Re-train the model or bind the current approved model version."
        ) from None
    return path


# -- reference frames: deterministic sampling and extraction -----------------------------------
def sample_frame_indices(timeline: FrameTimeline, count: int = DEFAULT_SAMPLE_FRAMES) -> list[int]:
    """Which timeline frames an execution analyses: evenly spaced, first and last always in.

    Only on-time frames are candidates (late frames are excluded from the
    monotonic processing order by T20 and reported there). The choice is a pure
    function of the timeline, so the same timeline digest always analyses the
    same frames.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise InferenceAdapterError("The number of frames to analyse must be a positive whole number.")
    candidates = [frame.index for frame in timeline.frames if frame.on_time]
    if not candidates:
        raise InferenceAdapterError("The recording has no usable frames to analyse.")
    if len(candidates) <= count:
        return candidates
    if count == 1:
        return [candidates[0]]
    chosen: list[int] = []
    for position in range(count):
        index = round(position * (len(candidates) - 1) / (count - 1))
        if index not in chosen:
            chosen.append(index)
    return [candidates[i] for i in chosen]


def plan_frame_selection(
    timeline: FrameTimeline,
    *,
    policy: str = FRAME_POLICY_RECORDED_SAMPLED,
    sample_frames: int = DEFAULT_SAMPLE_FRAMES,
    max_in_flight_frames: int | None = None,
) -> FramePolicyPlan:
    """Plan which frames to decode/analyse under an explicit T24 processing policy.

    * ``recorded_sampled``: deterministic evenly spaced reference frames; never claims
      full-frame coverage.
    * ``recorded_full``: recorded full-frame analysis; selects every on-time frame in
      monotonic PTS order and refuses to drop frames when ``max_in_flight_frames`` is
      exceeded. Reports ``full_frame_coverage=True`` only when every frame in the
      timeline is selected and the timeline has zero gaps and zero late frames.
    * ``live_frame_drop``: bounded-queue live backpressure policy; when on-time frames
      exceed ``max_in_flight_frames`` (default ``sample_frames``), sheds intermediate
      frames, records every dropped frame and contiguous overload gap, advances tracking
      session numbers across each drop interval so tracks never bridge dropped frames,
      and always sets ``full_frame_coverage=False``.
    """
    if policy not in SUPPORTED_FRAME_POLICIES:
        raise InferenceAdapterError(
            f"Unsupported frame processing policy '{policy}'. Choose one of: {', '.join(SUPPORTED_FRAME_POLICIES)}."
        )
    if max_in_flight_frames is not None and (
        isinstance(max_in_flight_frames, bool)
        or not isinstance(max_in_flight_frames, int)
        or max_in_flight_frames < 1
    ):
        raise InferenceAdapterError("The maximum in-flight frame queue limit must be a positive whole number.")
    on_time = [frame for frame in timeline.frames if frame.on_time]
    if not on_time:
        raise InferenceAdapterError("The recording has no usable frames to analyse.")

    if policy == FRAME_POLICY_RECORDED_SAMPLED:
        selected = tuple(sample_frame_indices(timeline, sample_frames))
        return FramePolicyPlan(
            policy=policy,
            total_on_time_frames=len(on_time),
            selected_indices=selected,
            sampled_skip_count=max(0, len(on_time) - len(selected)),
            dropped_frame_indices=(),
            overload_gaps=(),
            session_overrides={},
            sampled_analysis=True,
            overload_dropped=False,
            full_frame_coverage=False,
        )

    if policy == FRAME_POLICY_RECORDED_FULL:
        if max_in_flight_frames is not None and len(on_time) > max_in_flight_frames:
            raise InferenceAdapterError(
                f"Recorded full-frame analysis refuses to drop frames when the recording ({len(on_time)} frames) "
                f"exceeds the configured in-flight queue limit ({max_in_flight_frames}). Raise the queue limit or "
                "use the explicit 'live_frame_drop' policy."
            )
        selected = tuple(frame.index for frame in on_time)
        full_coverage = (
            len(selected) == len(timeline.frames) > 0
            and len(timeline.gaps) == 0
            and len(timeline.late_frames) == 0
        )
        return FramePolicyPlan(
            policy=policy,
            total_on_time_frames=len(on_time),
            selected_indices=selected,
            sampled_skip_count=0,
            dropped_frame_indices=(),
            overload_gaps=(),
            session_overrides={},
            sampled_analysis=False,
            overload_dropped=False,
            full_frame_coverage=full_coverage,
        )

    # FRAME_POLICY_LIVE_DROP
    budget = max_in_flight_frames if max_in_flight_frames is not None else sample_frames
    selected = tuple(sample_frame_indices(timeline, budget))
    selected_set = set(selected)
    dropped = tuple(frame.index for frame in on_time if frame.index not in selected_set)
    overload_gaps: list[dict] = []
    session_overrides: dict[int, int] = {}
    session_offset = 0
    prev_kept = None
    pending_dropped: list = []
    for frame in on_time:
        if frame.index not in selected_set:
            pending_dropped.append(frame)
            continue
        if pending_dropped and prev_kept is not None:
            start_pts = round(prev_kept.pts + prev_kept.duration, 6)
            end_pts = round(frame.pts, 6)
            overload_gaps.append(
                {
                    "kind": "live_overload_drop",
                    "after_frame": prev_kept.index,
                    "before_frame": frame.index,
                    "start_pts": start_pts,
                    "end_pts": end_pts,
                    "length_seconds": round(max(0.0, end_pts - start_pts), 6),
                    "dropped_frame_count": len(pending_dropped),
                    "dropped_frame_indices": [f.index for f in pending_dropped],
                }
            )
            session_offset += 1
            pending_dropped = []
        elif pending_dropped:
            pending_dropped = []
        session_overrides[frame.index] = frame.session + session_offset
        prev_kept = frame

    return FramePolicyPlan(
        policy=policy,
        total_on_time_frames=len(on_time),
        selected_indices=selected,
        sampled_skip_count=0,
        dropped_frame_indices=dropped,
        overload_gaps=tuple(overload_gaps),
        session_overrides=session_overrides,
        sampled_analysis=False,
        overload_dropped=len(dropped) > 0,
        full_frame_coverage=False,
    )


@dataclass(frozen=True)
class ExtractedFrame:
    """One decoded reference frame: its bytes on disk and its timeline entry."""

    path: Path
    index: int
    pts: float
    session: int
    wall_clock: str | None


def _extract_frames(recording, timeline: FrameTimeline, indices: list[int]):
    """Context manager: yields the chosen reference frames decoded next to a re-digested copy.

    Follows :func:`projects.source_time.build_timeline`'s pattern exactly: the
    stored bytes are streamed to a private temporary copy while being re-digested
    (a changed file is a refusal), and ffmpeg runs with the forced MP4 demuxer
    and the local file protocol only. Frames are extracted by exact PTS position
    into PNG stills, so the same recording always yields the same pictures. The
    scratch directory (and every frame in it) is removed when the block exits.
    """
    from contextlib import contextmanager

    @contextmanager
    def _frames():
        directory = Path(settings.DATA_HOME) / "inference-frames"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        wanted = {frame.index: frame for frame in timeline.frames}
        with tempfile.TemporaryDirectory(prefix="adapter-", dir=directory) as work:
            source = Path(work) / "recording.mp4"
            try:
                with default_storage.open(recording.storage_key, "rb") as stored, open(source, "wb") as handle:
                    for chunk in iter(lambda: stored.read(1024 * 1024), b""):
                        digest.update(chunk)
                        handle.write(chunk)
            except (OSError, ValueError) as error:
                raise InferenceAdapterError(
                    "The recording could not be read from storage. Ask an administrator to check it."
                ) from error
            if digest.hexdigest() != recording.sha256:
                raise InferenceAdapterError(
                    "The recording changed on disk since the run was queued. Start a new run from the new file."
                )
            extracted: list[ExtractedFrame] = []
            for index in indices:
                frame = wanted.get(index)
                if frame is None:
                    raise InferenceAdapterError("The frame timeline does not contain a frame the run asked for.")
                target = Path(work) / f"frame-{index:06d}.png"
                try:
                    result = subprocess.run(
                        [
                            "ffmpeg",
                            "-v",
                            "error",
                            "-nostdin",
                            "-protocol_whitelist",
                            "file",
                            "-f",
                            "mov",
                            "-enable_drefs",
                            "0",
                            "-use_absolute_path",
                            "0",
                            "-ss",
                            f"{frame.pts:.6f}",
                            "-i",
                            str(source),
                            "-frames:v",
                            "1",
                            "-f",
                            "image2",
                            "-y",
                            str(target),
                        ],
                        capture_output=True,
                        timeout=_FRAME_TIMEOUT_SECONDS,
                    )
                except FileNotFoundError as error:
                    raise InferenceAdapterError(
                        "Frame extraction is unavailable. Ask an administrator to install ffmpeg.",
                        unavailable=True,
                    ) from error
                except subprocess.TimeoutExpired as error:
                    raise InferenceAdapterError("The recording's frames could not be extracted in time.") from error
                if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                    raise InferenceAdapterError(
                        "A reference frame could not be decoded from the recording. The file may be damaged; "
                        "re-import it."
                    )
                extracted.append(
                    ExtractedFrame(
                        path=target,
                        index=frame.index,
                        pts=frame.pts,
                        session=frame.session,
                        wall_clock=frame.wall_clock,
                    )
                )
            yield extracted

    return _frames()


# -- the adapter --------------------------------------------------------------------------------
def run_detector(
    binding,
    recording,
    timeline: FrameTimeline,
    recipe_key: str,
    *,
    sample_frames: int = DEFAULT_SAMPLE_FRAMES,
    frame_policy: str = FRAME_POLICY_RECORDED_SAMPLED,
    max_in_flight_frames: int | None = None,
) -> AdapterReport:
    """Run the bound model under the selected frame-processing policy. Raises InferenceAdapterError; never fakes.

    ``binding`` is the ``OperationModelBinding`` the execution's bundle pinned;
    ``recording`` is the ``OperationSourceRecording`` the execution runs against;
    ``timeline`` is the T20 timeline of that recording (the caller has already
    cross-checked the recording digest); ``recipe_key`` is the recipe the bundle's
    snapshot pinned, whose expected labels the model must cover. The bound
    ``ModelVersion``'s compatibility is re-validated here — the pin recorded
    checks at binding time, and inference does not trust them.
    """
    from ..training import get_backend
    from ..training.base import BoxPrediction

    model = binding.model_version
    checks = validate_compatibility(model, recipe_key)
    if not checks["compatible"]:
        raise InferenceAdapterError(checks["reason"])
    artifact = _verify_artifact(model)

    try:
        backend = get_backend(model.backend)
    except ValueError:
        raise InferenceAdapterError(
            f"The model's backend ('{model.backend}') is not installed in this worker, so no inference "
            "can run. Ask an administrator to install it in the operation worker's environment."
        ) from None

    plan = plan_frame_selection(
        timeline,
        policy=frame_policy,
        sample_frames=sample_frames,
        max_in_flight_frames=max_in_flight_frames,
    )
    indices = list(plan.selected_indices)
    results: list[FrameDetections] = []
    with _extract_frames(recording, timeline, indices) as frames:
        for frame in frames:
            from PIL import Image

            try:
                with Image.open(frame.path) as opened:
                    opened.load()
                    image = opened.convert("RGB")
                found = backend.predict(
                    artifact, image, {"confidence": PREDICTION_FLOOR, "max_boxes": MAX_PREDICTIONS_PER_FRAME}
                )
            except Exception as error:  # noqa: BLE001 - the model failed; that is an explicit failure
                raise InferenceAdapterError(
                    f"The bound model failed while analysing frame {frame.index}: {error or error.__class__.__name__}"
                ) from error
            ordered = sorted(
                (p for p in found if isinstance(p, BoxPrediction) and p.label in checks["model_labels"]),
                key=lambda p: -p.score,
            )[:MAX_PREDICTIONS_PER_FRAME]
            detections = tuple(
                {
                    "label": p.label,
                    "x": round(float(p.x), 6),
                    "y": round(float(p.y), 6),
                    "width": round(float(p.width), 6),
                    "height": round(float(p.height), 6),
                    "score": round(float(p.score), 6),
                }
                for p in ordered
                if _is_normalized_box(p)
            )
            results.append(
                FrameDetections(
                    frame_index=frame.index,
                    pts=round(frame.pts, 6),
                    session=plan.session_overrides.get(frame.index, frame.session),
                    wall_clock=frame.wall_clock,
                    detections=detections,
                )
            )

    return AdapterReport(
        model_version_id=str(model.id),
        model_version_number=model.version,
        backend=model.backend,
        artifact_sha256=model.artifact_sha256,
        recipe_key=recipe_key,
        expected_labels=tuple(checks["expected_labels"]),
        model_labels=tuple(checks["model_labels"]),
        frame_count=len(results),
        frames=tuple(results),
        frame_policy=plan.policy,
        policy_plan=plan,
    )


def _is_normalized_box(prediction) -> bool:
    """A prediction counts only with finite [0,1] coordinates, positive size and score."""
    try:
        values = (
            float(prediction.x),
            float(prediction.y),
            float(prediction.width),
            float(prediction.height),
            float(prediction.score),
        )
    except (TypeError, ValueError, OverflowError):
        return False
    x, y, width, height, score = values
    return (
        math.isfinite(x)
        and math.isfinite(y)
        and math.isfinite(width)
        and math.isfinite(height)
        and 0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and width > 0.0
        and height > 0.0
        and x + width <= 1.0 + 1e-9
        and y + height <= 1.0 + 1e-9
        and 0.0 <= score <= 1.0
    )


# -- recording verification: the T20 gap this task closes ---------------------------------------
def crosscheck_duration(recording, timeline: FrameTimeline) -> dict:
    """Cross-check the stored duration against the timeline. Returns the check record.

    T20 derived the reproducible timeline but left ``duration_seconds`` (the
    import-time container duration) unverified against it. The timeline's own
    span — the last on-time frame's end minus the first frame's start — must
    match the stored duration within one frame's cadence. A recording whose
    stored duration disagrees with its actual frames has drifted metadata: the
    run is refused rather than analysed against a wrong clock.
    """
    if not timeline.frames:
        raise InferenceAdapterError("The recording has no frames, so its duration cannot be verified.")
    last = timeline.frames[-1]
    span = last.pts + last.duration
    stored = float(recording.duration_seconds)
    tolerance = max(float(last.duration or 0.0), 0.0) + 1e-6
    record = {
        "stored_duration_seconds": round(stored, 6),
        "timeline_span_seconds": round(span, 6),
        "tolerance_seconds": round(tolerance, 6),
        "matches": True,
    }
    if not math.isfinite(stored) or stored <= 0 or abs(span - stored) > tolerance:
        record["matches"] = False
        raise InferenceAdapterError(
            "The recording's stored duration no longer matches its actual frames, so its timeline "
            "cannot be trusted for analysis. Re-import the recording and start a new run."
        )
    return record


__all__ = [
    "AdapterReport",
    "DEFAULT_SAMPLE_FRAMES",
    "DETECTION_TASKS",
    "FRAME_POLICY_LIVE_DROP",
    "FRAME_POLICY_RECORDED_FULL",
    "FRAME_POLICY_RECORDED_SAMPLED",
    "FrameDetections",
    "FramePolicyPlan",
    "InferenceAdapterError",
    "RECIPE_EXPECTED_LABELS",
    "SUPPORTED_FRAME_POLICIES",
    "crosscheck_duration",
    "plan_frame_selection",
    "run_detector",
    "sample_frame_indices",
    "validate_compatibility",
]
