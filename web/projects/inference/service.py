"""App configuration, render models, observations and feedback.

Feedback is write-once evidence. Nothing in this module changes a ``ModelVersion`` or adds data to a
dataset or training run; a person must export feedback, build a new dataset version, train, evaluate
and approve a new model through the training lifecycle.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from ..datasets.manifest import opaque_user
from ..models import (
    FeedbackRecord,
    InferenceApp,
    ModelVersion,
    ObservationRecord,
    Project,
    ReviewDecision,
)
from ..training import get_backend, report, training_settings
from ..training.lifecycle import INFERENCE_WORKER_KEY, LifecycleError, backend_runnable, clean_note, worker_status
from . import schema

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$")
PREDICTION_FLOOR = 0.05
MAX_PREDICTIONS = 100
EXPORT_ROW_LIMIT = 10_000
EXPORT_SCHEMA = "athena.feedback-candidate/1"
EXPORT_NOTICE = (
    "Reviewed training candidates: a person's verdicts on one exact model's output for one exact image. "
    "They are NOT part of any dataset or training run. To use them, a person must label them into a new "
    "dataset version, train, read the evaluation, and approve the result."
)
PENDING_TEXT = "Analysing the image…"
FAILED_TEXT = "The image could not be analysed. Nothing was decided. Try again, or ask an administrator."


def _user(user):
    return user if getattr(user, "is_authenticated", False) else None


# -- apps ------------------------------------------------------------------------------------
def _unique_slug(base: str) -> str:
    base = (slugify(base)[:60] or "app").strip("-") or "app"
    candidate, counter = base, 1
    while InferenceApp.objects.filter(slug=candidate).exists():
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


def _check_model(project: Project, model: ModelVersion) -> None:
    if model.project_id != project.id:
        raise LifecycleError("That model version belongs to a different project.", 404)
    if model.stage != ModelVersion.APPROVED:
        raise LifecycleError(
            f"An inference app can only use an approved model, and model v{model.version} is {model.stage}. "
            "Review its evaluation and approve it first.",
            409,
        )
    try:
        get_backend(model.backend)
    except ValueError:
        raise LifecycleError(
            f"The backend {model.backend!r} this model needs is not installed. Install it, then try again.", 409
        ) from None


def validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 or not name.strip().isprintable():
        raise LifecycleError("name must be 1–120 characters of plain text.", 400)
    return name.strip()


def create_app(project: Project, user, *, name: Any, slug: Any, model: ModelVersion, config: dict[str, Any]) -> InferenceApp:
    name = validate_name(name)
    _check_model(project, model)
    try:
        full = schema.validate_config(schema.merged(schema.default_config(name, model.label_names), config), model.label_names)
    except schema.ConfigError as error:
        raise LifecycleError(str(error), 400) from None
    if slug is None or slug == "":
        slug = _unique_slug(name)
    elif not isinstance(slug, str) or not SLUG_PATTERN.match(slug):
        raise LifecycleError("slug must be 2–80 characters: lower-case letters, digits and hyphens.", 400)
    try:
        with transaction.atomic():
            return InferenceApp.objects.create(
                project=project, name=name, slug=slug, model_version=model, config=full, created_by=_user(user)
            )
    except IntegrityError:
        raise LifecycleError(f"The slug {slug!r} is already used by another app. Choose a different one.", 409) from None


def update_app(
    app: InferenceApp, *, name: Any = None, model: ModelVersion | None = None, config: dict[str, Any] | None = None,
    status: Any = None,
) -> InferenceApp:
    if name is not None:
        app.name = validate_name(name)
    if model is not None:
        _check_model(app.project, model)
        app.model_version = model
    if status is not None:
        if status not in (InferenceApp.ACTIVE, InferenceApp.ARCHIVED):
            raise LifecycleError("status must be active or archived.", 400)
        if status == InferenceApp.ACTIVE and app.model_version.stage != ModelVersion.APPROVED:
            raise LifecycleError("Point this app at an approved model before reactivating it.", 409)
        app.status = status
    if config is not None or model is not None:
        try:
            app.config = schema.validate_config(
                schema.merged(app.config, config or {}), app.model_version.label_names
            )
        except schema.ConfigError as error:
            raise LifecycleError(str(error), 400) from None
    app.save()
    return app


# -- render models ---------------------------------------------------------------------------
def _model_block(model: ModelVersion) -> dict[str, Any]:
    return {
        "id": str(model.id),
        "version": model.version,
        "backend": model.backend,
        "artifact_sha256": model.artifact_sha256,
        "stage_now": model.stage,
        "label_names": model.label_names,
        "evaluation": report.summary(model.evaluation),
    }


def status_text(state: str, config: dict[str, Any]) -> str:
    if state == "processing":
        return PENDING_TEXT
    if state == "failed":
        return FAILED_TEXT
    return config["status_texts"][state]


def _static(app: InferenceApp, config: dict[str, Any], model: ModelVersion) -> dict[str, Any]:
    from ..views import MAX_IMAGE_BYTES

    return {
        "app": {"id": str(app.id), "slug": app.slug, "name": app.name, "status": app.status},
        "title": config["task_title"],
        "disclaimer": schema.DISCLAIMER,
        "accepted_inputs": config["accepted_inputs"],
        "displayed_labels": config["displayed_labels"],
        "confidence_threshold": config["confidence_threshold"],
        "status_texts": config["status_texts"],
        "decision_controls": config["decision_controls"],
        "human_review_required": config["human_review_required"],
        "model": _model_block(model),
        "model_in_use": model.stage == ModelVersion.APPROVED,
        "limits": {"max_upload_bytes": MAX_IMAGE_BYTES},
    }


def render_preview(app: InferenceApp) -> dict[str, Any]:
    """Exactly what the UI renders for this app before any image is submitted, plus the three
    result states it can show (so an operator can see their wording in place)."""
    config = app.config
    base = _static(app, config, app.model_version)
    examples = []
    for state in schema.STATE_KEYS:
        examples.append(
            {
                "state": state,
                "status_text": status_text(state, config),
                "awaiting_human_decision": bool(config["human_review_required"]),
                "example_only": True,
            }
        )
    return {**base, "result": None, "examples": examples}


def _decision_block(decision: ReviewDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "id": str(decision.id),
        "decision": decision.decision,
        "note": decision.note,
        "by": opaque_user(decision.decided_by_snapshot),
        "at": decision.created_at.isoformat(),
    }


def feedback_data(record: FeedbackRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "observation_id": str(record.observation_id),
        "prediction_index": record.prediction_index,
        "verdict": record.verdict,
        "label": record.label,
        "box": record.box,
        "note": record.note,
        "by": opaque_user(record.decided_by_snapshot),
        "at": record.created_at.isoformat(),
    }


def displayed_predictions(observation: ObservationRecord) -> list[dict[str, Any]]:
    config = observation.config_snapshot
    return [
        p
        for p in observation.predictions
        if p["label"] in config["displayed_labels"] and p["score"] >= config["confidence_threshold"]
    ]


def observation_state(observation: ObservationRecord, decision: ReviewDecision | None) -> str:
    """What the screen shows. A result is never ``clear`` before a person decided, when review is required."""
    if observation.status in (ObservationRecord.QUEUED, ObservationRecord.RUNNING):
        return "processing"
    if observation.status == ObservationRecord.FAILED:
        return "failed"
    config = observation.config_snapshot
    machine = "flagged" if displayed_predictions(observation) else "clear"
    if decision is None:
        return "needs_review" if config["human_review_required"] else machine
    if decision.decision == ReviewDecision.CONFIRM:
        return machine
    return "needs_review"  # rejected, or a second look was requested


def render_observation(observation: ObservationRecord) -> dict[str, Any]:
    config = observation.config_snapshot
    decision = observation.decisions.order_by("-created_at", "-id").first()
    state = observation_state(observation, decision)
    shown = displayed_predictions(observation)
    ok = observation.status == ObservationRecord.SUCCEEDED
    base = _static(observation.app, config, observation.model_version)
    base["model_in_use"] = observation.app.model_version_id == observation.model_version_id and (
        observation.model_version.stage == ModelVersion.APPROVED
    )
    return {
        **base,
        "result": {
            "observation_id": str(observation.id),
            "status": observation.status,
            "state": state,
            "status_text": status_text(state, config),
            "awaiting_human_decision": ok and config["human_review_required"] and decision is None,
            "machine_state": ("flagged" if shown else "clear") if ok else None,
            "detections": shown if ok else [],
            "hidden_below_threshold": len([p for p in observation.predictions if p["score"] < config["confidence_threshold"]]) if ok else 0,
            "hidden_other_labels": len([p for p in observation.predictions if p["label"] not in config["displayed_labels"]]) if ok else 0,
            "decision": _decision_block(decision),
            "result_rejected": bool(decision and decision.decision == ReviewDecision.REJECT),
            "feedback": [feedback_data(f) for f in observation.feedback.all()],
            "image": {
                "sha256": observation.image_sha256,
                "width": observation.width,
                "height": observation.height,
                "content_type": observation.content_type,
                "filename": observation.filename,
            },
            "created_at": observation.created_at.isoformat(),
            "created_by": opaque_user(observation.created_by_id),
            "error": observation.error if observation.status == ObservationRecord.FAILED else "",
        },
    }


# -- observations ----------------------------------------------------------------------------
def submit_image(app: InferenceApp, user, upload) -> ObservationRecord:
    from ..views import _validate_uploaded_image

    model = app.model_version
    if app.status != InferenceApp.ACTIVE:
        raise LifecycleError("This app is archived. Reactivate it before running images.", 409)
    if model.stage != ModelVersion.APPROVED:
        raise LifecycleError(
            f"This app is set to model v{model.version}, which is {model.stage} and no longer approved, so it will not "
            "run. Point the app at the currently approved model (PATCH the app with a model_version_id).",
            409,
        )
    ok, info = backend_runnable(model.backend, INFERENCE_WORKER_KEY)
    if not ok:
        raise LifecycleError(
            f"The backend {model.backend!r} cannot run right now, so no prediction was made. "
            f"{info['message']} {info['how_to_enable']} Then start `python manage.py inference_worker` "
            "in that environment.".replace("  ", " "),
            503,
            backend=model.backend,
        )
    pending = ObservationRecord.objects.filter(
        status__in=(ObservationRecord.QUEUED, ObservationRecord.RUNNING)
    ).count()
    if pending >= training_settings()["INFERENCE_QUEUE_LIMIT"]:
        raise LifecycleError("Too many images are waiting to be analysed. Wait a moment and try again.", 429)
    try:
        meta = _validate_uploaded_image(upload)
    except ValueError as error:
        raise LifecycleError(str(error), 400) from None
    if meta["content_type"] not in app.config["accepted_inputs"]:
        raise LifecycleError(
            f"This app accepts {', '.join(app.config['accepted_inputs'])}; the file is {meta['content_type']}.", 415
        )
    observation_id = uuid.uuid4()
    key = f"inference/{app.project_id}/{app.id}/{observation_id}.{meta['extension']}"
    upload.seek(0)
    stored = default_storage.save(key, upload)
    try:
        return ObservationRecord.objects.create(
            id=observation_id,
            app=app,
            model_version=model,
            image_key=stored,
            image_sha256=meta["sha256"],
            filename=meta["filename"],
            content_type=meta["content_type"],
            size_bytes=meta["size_bytes"],
            width=meta["width"],
            height=meta["height"],
            config_snapshot=app.config,
            created_by=_user(user),
        )
    except Exception:
        default_storage.delete(stored)
        raise


def worker_hint() -> dict[str, Any]:
    status = worker_status(INFERENCE_WORKER_KEY)
    return {"worker_online": status["online"]}


# -- decisions and feedback -----------------------------------------------------------------
def _require_finished(observation: ObservationRecord) -> None:
    if observation.status != ObservationRecord.SUCCEEDED:
        raise LifecycleError("This image has no finished result yet, so nothing can be decided about it.", 409)


def add_decision(observation: ObservationRecord, user, decision: Any, note: Any) -> ReviewDecision:
    if not getattr(user, "is_authenticated", False):
        raise LifecycleError("Sign in to record a decision.", 401)
    _require_finished(observation)
    controls = observation.config_snapshot["decision_controls"]
    if decision not in schema.DECISION_OPTIONS or decision not in controls["options"]:
        raise LifecycleError(f"decision must be one of: {', '.join(controls['options'])}.", 400)
    needs_note = decision == ReviewDecision.REJECT and controls["required_note_on_override"]
    note = clean_note(note, required=needs_note, what="A note explaining why you are overriding the model")
    return ReviewDecision.objects.create(
        observation=observation, decision=decision, note=note, decided_by=user, decided_by_snapshot=user.pk
    )


def _box(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
        raise LifecycleError("box must have exactly x, y, width and height (fractions of the image, 0 to 1).", 400)
    numbers = {}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 <= item <= 1:
            raise LifecycleError("box values must be numbers from 0 to 1.", 400)
        numbers[key] = float(item)
    if numbers["width"] <= 0 or numbers["height"] <= 0 or numbers["x"] + numbers["width"] > 1 + 1e-9 or numbers["y"] + numbers["height"] > 1 + 1e-9:
        raise LifecycleError("box must have a positive size and stay inside the image.", 400)
    return numbers


def add_feedback(observation: ObservationRecord, user, payload: dict[str, Any]) -> FeedbackRecord:
    if not getattr(user, "is_authenticated", False):
        raise LifecycleError("Sign in to record feedback.", 401)
    unknown = set(payload) - {"prediction_index", "verdict", "label", "box", "note"}
    if unknown:
        raise LifecycleError(f"Unsupported field: {', '.join(sorted(unknown))}.", 400)
    _require_finished(observation)
    config = observation.config_snapshot
    index = payload.get("prediction_index")
    verdict = payload.get("verdict")
    label = ""
    box = None
    if index == "missed":
        index, verdict = None, verdict or FeedbackRecord.FALSE_NEGATIVE
        if verdict != FeedbackRecord.FALSE_NEGATIVE:
            raise LifecycleError("A missed object is recorded with verdict false_negative.", 400)
        label = payload.get("label")
        if label not in config["displayed_labels"]:
            raise LifecycleError(
                f"label is required for a missed object and must be one of: {', '.join(config['displayed_labels'])}.", 400
            )
        box = _box(payload.get("box"))
    else:
        if isinstance(index, bool) or not isinstance(index, int):
            raise LifecycleError('prediction_index must be the number of a shown detection, or "missed".', 400)
        shown = {p["index"]: p for p in displayed_predictions(observation)}
        if index not in shown:
            raise LifecycleError(
                "prediction_index does not match a detection that was shown for this image.", 400
            )
        if verdict not in (FeedbackRecord.TRUE_POSITIVE, FeedbackRecord.FALSE_POSITIVE):
            raise LifecycleError("verdict for a detection must be true_positive or false_positive.", 400)
        if payload.get("label") is not None or payload.get("box") is not None:
            raise LifecycleError("label and box apply only to a missed object.", 400)
        label = shown[index]["label"]
    override = verdict in (FeedbackRecord.FALSE_POSITIVE, FeedbackRecord.FALSE_NEGATIVE)
    note = clean_note(
        payload.get("note"),
        required=override and config["decision_controls"]["required_note_on_override"],
        what="A note explaining why you disagree with the model",
    )
    return FeedbackRecord.objects.create(
        observation=observation,
        prediction_index=index,
        label=label,
        box=box,
        verdict=verdict,
        note=note,
        decided_by=user,
        decided_by_snapshot=user.pk,
    )


# -- export ----------------------------------------------------------------------------------
def export_rows(app: InferenceApp, *, include_superseded: bool = False) -> tuple[list[dict[str, Any]], bool]:
    records = list(
        FeedbackRecord.objects.filter(observation__app=app)
        .select_related("observation__model_version__training_run__dataset_version")
        .order_by("created_at", "id")[: EXPORT_ROW_LIMIT + 1]
    )
    truncated = len(records) > EXPORT_ROW_LIMIT
    records = records[:EXPORT_ROW_LIMIT]
    latest: dict[tuple[Any, Any], FeedbackRecord] = {}
    for record in records:
        if record.prediction_index is not None:
            latest[(record.observation_id, record.prediction_index)] = record
    latest_decision: dict[Any, ReviewDecision] = {}
    for decision in ReviewDecision.objects.filter(observation__app=app).order_by("created_at", "id"):
        latest_decision[decision.observation_id] = decision
    rows = []
    for record in records:
        effective = record.prediction_index is None or latest[(record.observation_id, record.prediction_index)] is record
        if not effective and not include_superseded:
            continue
        observation = record.observation
        model = observation.model_version
        run = model.training_run
        prediction = None
        if record.prediction_index is not None:
            prediction = next((p for p in observation.predictions if p["index"] == record.prediction_index), None)
        rows.append(
            {
                "schema": EXPORT_SCHEMA,
                "feedback_id": str(record.id),
                "effective": effective,
                "verdict": record.verdict,
                "label": record.label,
                "prediction": prediction,
                "box": record.box,
                "note": record.note,
                "decided_by": opaque_user(record.decided_by_snapshot),
                "decided_at": record.created_at.isoformat(),
                "observation": {
                    "id": str(observation.id),
                    "image_sha256": observation.image_sha256,
                    "content_type": observation.content_type,
                    "width": observation.width,
                    "height": observation.height,
                    "observed_at": observation.created_at.isoformat(),
                    "review_decision": _decision_block(latest_decision.get(observation.id)),
                },
                "model_version": {
                    "id": str(model.id),
                    "version": model.version,
                    "backend": model.backend,
                    "artifact_sha256": model.artifact_sha256,
                    "training_run_id": str(model.training_run_id) if model.training_run_id else None,
                    "dataset_version": run.dataset_version.version if run else None,
                    "dataset_manifest_sha256": run.dataset_manifest_sha256 if run else None,
                    "config_sha256": run.config_sha256 if run else None,
                },
                "app": {"id": str(app.id), "slug": app.slug, "config": observation.config_snapshot},
                "status": "candidate_not_in_any_dataset",
            }
        )
    return rows, truncated
