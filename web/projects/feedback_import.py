"""Turn what operators said on the check screens into pictures a person can review.

Nothing here teaches a model. A picture taken from a check screen is *copied* into the project as a new
picture, its confirmed or added boxes become **Needs review** suggestions, and a link row (``FeedbackImport``)
records where all of it came from. The boxes are not ground truth until a person accepts them through the
ordinary review controls, so datasets, readiness, health and exports (which only read accepted boxes) ignore
them. Nothing in this module reads or writes a model version, a training run, a dataset version or a check screen.

Selection rule (the same one the feedback export uses): for each shown detection the **latest** verdict wins;
a missed-object report is its own object and always counts.

* detection called right (``true_positive``) -> one Needs-review box, the model's own box;
* detection called wrong (``false_positive``) -> no box. A wrong model box is never turned into ground truth;
* missed object with a box                    -> one Needs-review box, the box the operator drew;
* missed object without a box                 -> nothing is made, but it is counted (``missed_without_box``)
  so the labeler is told something was missed there.

A whole-result decision counts too: a result whose **latest** ``ReviewDecision`` is Reject or Needs a second look is
eligible even with no per-detection feedback (a later Confirm takes it back out). Such a picture is copied with **no
boxes at all**: the model's own boxes are not trusted ground truth, and nobody said which of them are right. The
operator's note travels as data (capped), never as markup.

A label must match the project's labels exactly (capital letters count); otherwise that box is skipped and
reported (``label_not_in_project``). A label is never invented. Boxes follow the manual-box geometry rules.
"""

from __future__ import annotations

import io
import logging
import uuid
import warnings
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import SuspiciousOperation
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Q, Subquery
from PIL import Image, UnidentifiedImageError

from . import permissions, views
from .api_common import HttpError
from .datasets.manifest import opaque_user
from .models import Asset, BoxAnnotation, FeedbackImport, FeedbackRecord, ObservationRecord, Project, ReviewDecision

logger = logging.getLogger(__name__)

IMPORT_BATCH_LIMIT = 50  # most observations one request may pull in
LIST_LIMIT = 100  # most observations the list shows (counts always cover everything scanned)
SCAN_LIMIT = 2000  # most results still waiting that are looked at per request, newest first (pulled-in ones never use this)
EXIF_ORIENTATION = 0x0112  # 1 (or missing) means "upright as stored"; anything else means a viewer would turn the picture
BOX_SOURCE_NAME = "operator feedback"  # the ``model_name`` of the Needs-review boxes made here

DECISION_NOTE_LIMIT = 300  # characters of the operator's note that the list and the import answer carry

# The stored decision values that send a result to the supervisor, and the short names the API gives them.
DECISION_NAMES = {ReviewDecision.REJECT: "reject", ReviewDecision.SECOND_LOOK: "second_look"}

PENDING, IMPORTED, CANNOT_IMPORT = "pending", "imported", "cannot_import"
STORAGE_PROBLEMS = (OSError, ValueError, SuspiciousOperation)

# Plain-language text for every reason code this module can give. Codes are for programs; messages are shown.
MESSAGES = {
    "not_found": "That check-screen result was not found in this project.",
    "no_feedback": "Nobody has said whether this result was right or wrong, so there is nothing to pull in.",
    "already_imported": "This result was already pulled into the project.",
    "observation_not_finished": "This result has not finished, so it cannot be pulled in.",
    "picture_file_missing": "The picture file from the check screen is no longer in storage.",
    "picture_too_large": "The picture from the check screen is bigger than the project accepts (20 MB).",
    "picture_not_a_supported_image": "The stored file is not a JPEG, PNG or WebP picture the project can use.",
    "picture_dimensions_too_large": (
        f"The picture has more pixels than the project accepts ({views.MAX_IMAGE_PIXELS // 1_000_000} megapixels), "
        "so it was left out."
    ),
    "picture_rotated_by_camera": (
        "This picture is stored sideways (the camera rotated it), so the boxes would not line up. It was left out."
    ),
    "storage_error": (
        "The picture could not be saved in the project (the disk may be full), so it was left out. Nothing was changed."
    ),
    "picture_changed_since_check": "The stored picture is not the one the check screen looked at, so it was left out.",
    "label_not_in_project": "This label is not in the project's label list (capital letters count), so no box was made.",
    "prediction_not_found": "The detection this verdict is about is not in the stored result, so no box was made.",
    "box_not_valid": "The box has no usable size, so no box was made.",
    "box_outside_image": "The box does not stay inside the picture, so no box was made.",
}

ZERO_KINDS = {"true_positive": 0, "false_positive": 0, "missed": 0}


# -- whole-result decisions ------------------------------------------------------------------
def latest_decisions(observation_ids) -> dict[Any, ReviewDecision]:
    """The latest whole-result decision per observation (oldest to newest by ``(created_at, id)``), one query."""
    latest: dict[Any, ReviewDecision] = {}
    rows = ReviewDecision.objects.filter(observation_id__in=list(observation_ids)).order_by("created_at", "id")
    for decision in rows:
        latest[decision.observation_id] = decision
    return latest


def decision_fields(value: str | None, note: str | None) -> tuple[str | None, str]:
    """``(name, note)`` for a latest decision value that is a reject or a second look; ``(None, "")`` otherwise."""
    name = DECISION_NAMES.get(value or "")
    return (name, (note or "")[:DECISION_NOTE_LIMIT]) if name else (None, "")


def _latest_decision_columns(observation_ref: str) -> dict[str, Subquery]:
    """Annotations that put each observation's latest decision (and its note) on the row: no extra query per list."""
    newest = ReviewDecision.objects.filter(observation=OuterRef(observation_ref)).order_by("-created_at", "-id")
    return {"latest_decision": Subquery(newest.values("decision")[:1]), "latest_decision_note": Subquery(newest.values("note")[:1])}


# -- the selection rule ----------------------------------------------------------------------
def effective_feedback(records) -> list[FeedbackRecord]:
    """The feedback that counts: the latest verdict per shown detection, and every missed-object report.

    ``records`` may come in any order; they are read oldest to newest by ``(created_at, id)`` exactly as the
    export does, so the two always agree on what the current verdict is.
    """
    ordered = sorted(records, key=lambda record: (record.created_at, str(record.id)))
    latest: dict[int, FeedbackRecord] = {}
    for record in ordered:
        if record.prediction_index is not None:
            latest[record.prediction_index] = record
    return [r for r in ordered if r.prediction_index is None or latest[r.prediction_index] is r]


def kinds_of(effective) -> dict[str, int]:
    counts = Counter(
        "missed" if r.verdict == FeedbackRecord.FALSE_NEGATIVE else r.verdict for r in effective
    )
    return {key: counts.get(key, 0) for key in ZERO_KINDS}


# -- boxes -----------------------------------------------------------------------------------
def _geometry(raw: Any) -> tuple[dict[str, Decimal] | None, str | None]:
    """The manual-box rules (``views._validated_annotation_data``): normalised, positive size, inside [0, 1]."""
    if not isinstance(raw, dict):
        return None, "box_not_valid"
    values: dict[str, Decimal] = {}
    for name in views.BOX_COORDINATE_FIELDS:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return None, "box_not_valid"
        try:
            number = Decimal(str(value)).quantize(views.BOX_COORDINATE_QUANTUM)
        except InvalidOperation:
            return None, "box_not_valid"
        if not number.is_finite():
            return None, "box_not_valid"
        values[name] = number
    x, y, width, height = (values[name] for name in views.BOX_COORDINATE_FIELDS)
    if width <= 0 or height <= 0:
        return None, "box_not_valid"
    one = Decimal(1)
    if x < 0 or y < 0 or width > one or height > one or x + width > one or y + height > one:
        return None, "box_outside_image"
    return values, None


@dataclass
class PlannedBox:
    label: str
    geometry: dict[str, Decimal]
    confidence: float | None
    created_by_id: int | None


@dataclass
class Plan:
    kinds: dict[str, int]
    boxes: list[PlannedBox] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    missed_without_box: int = 0

    @property
    def false_alarms(self) -> int:
        return self.kinds["false_positive"]


def build_plan(project: Project, observation: ObservationRecord, records) -> Plan:
    effective = effective_feedback(records)
    plan = Plan(kinds=kinds_of(effective))
    shown = {p.get("index"): p for p in observation.predictions if isinstance(p, dict)}
    for record in effective:
        if record.verdict == FeedbackRecord.FALSE_POSITIVE:
            continue  # a wrong model box is never ground truth
        confidence = None
        prediction = None
        if record.verdict == FeedbackRecord.TRUE_POSITIVE:
            kind, prediction = "true_positive", shown.get(record.prediction_index)
            raw = prediction.get("box") if prediction else None
            confidence = prediction.get("score") if prediction else None
        else:
            kind, raw = "missed", record.box
            if raw is None:
                plan.missed_without_box += 1
                continue
        problem = None
        geometry: dict[str, Decimal] | None = None
        if record.label not in project.labels:
            problem = "label_not_in_project"
        elif kind == "true_positive" and prediction is None:
            problem = "prediction_not_found"
        else:
            geometry, problem = _geometry(raw)
        if problem or geometry is None:
            problem = problem or "box_not_valid"
            plan.skipped.append(
                {
                    "reason": problem,
                    "kind": kind,
                    "label": record.label,
                    "prediction_index": record.prediction_index,
                    "feedback_id": str(record.id),
                }
            )
            continue
        plan.boxes.append(
            PlannedBox(record.label, geometry, confidence if isinstance(confidence, (int, float)) else None, record.decided_by_id)
        )
    return plan


# -- the picture -----------------------------------------------------------------------------
def _cheap_picture_problem(observation: ObservationRecord) -> str | None:
    """What can be seen without opening the file: used by the list, so it never decodes a picture."""
    if observation.size_bytes > views.MAX_IMAGE_BYTES:
        return "picture_too_large"
    if observation.content_type not in {content_type for _, content_type in views.IMAGE_FORMATS.values()}:
        return "picture_not_a_supported_image"
    try:
        if not default_storage.exists(observation.image_key):
            return "picture_file_missing"
    except STORAGE_PROBLEMS:  # includes a key that points outside the storage folder (SuspiciousFileOperation)
        return "picture_file_missing"
    return None


def _has_too_many_pixels(data: bytes) -> bool:
    """The same pixel ceiling the upload gate has, read from the header only (so it can be named on its own)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return True
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return False  # not a picture at all: the upload gate says so in its own words
    return width * height > views.MAX_IMAGE_PIXELS


def _camera_orientation(data: bytes) -> int | None:
    """The EXIF orientation of the stored bytes, or None when there is none (or it cannot be read)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            value = image.getexif().get(EXIF_ORIENTATION)
    except Exception:  # a damaged EXIF block must not decide the outcome; the picture itself already passed the gate
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _discard(storage_key: str) -> None:
    """Remove a file we may have written; never raises (it runs while another problem is being handled)."""
    try:
        default_storage.delete(storage_key)
    except STORAGE_PROBLEMS:
        logger.warning("Could not remove %s after a failed import.", storage_key)


def _load_picture(observation: ObservationRecord) -> tuple[bytes, dict[str, Any]] | str:
    """The stored bytes and what the project's image gate says about them, or a reason code."""
    problem = _cheap_picture_problem(observation)
    if problem:
        return problem
    try:
        with default_storage.open(observation.image_key, "rb") as handle:
            data = handle.read(views.MAX_IMAGE_BYTES + 1)
    except STORAGE_PROBLEMS:
        return "picture_file_missing"
    # Size and pixel ceilings are checked here, by number, so the reason never depends on the upload gate's wording.
    if len(data) > views.MAX_IMAGE_BYTES:
        return "picture_too_large"
    if _has_too_many_pixels(data):
        return "picture_dimensions_too_large"
    upload = SimpleUploadedFile(observation.filename or "image", data)
    try:
        meta = views._validate_uploaded_image(upload)  # the very check an upload goes through
    except ValueError:
        return "picture_not_a_supported_image"
    if meta["sha256"] != observation.image_sha256:
        return "picture_changed_since_check"
    # Boxes from the model are in the stored pixels, boxes drawn on the check screen are in the picture as shown
    # (turned upright). They only agree when nothing needs turning, so a picture that does is left out.
    orientation = _camera_orientation(data)
    if orientation is not None and orientation != 1:
        return "picture_rotated_by_camera"
    return data, meta


# -- one observation -------------------------------------------------------------------------
@dataclass
class Outcome:
    observation_id: str
    imported: bool = False
    reason: str | None = None
    asset_id: str | None = None
    boxes_created: int = 0
    missed_without_box: int = 0
    false_alarms: int = 0
    skipped_boxes: list[dict[str, Any]] = field(default_factory=list)
    decision: str | None = None  # "reject" or "second_look" when that is the latest whole-result decision
    decision_note: str = ""
    decision_only: bool = False  # the picture exists because of that decision alone: no per-detection feedback

    @property
    def message(self) -> str:
        return MESSAGES.get(self.reason or "", "")

    def imported_json(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "asset_id": self.asset_id,
            "boxes_created": self.boxes_created,
            "missed_without_box": self.missed_without_box,
            "false_alarms": self.false_alarms,
            "skipped_boxes": [{**item, "message": MESSAGES[item["reason"]]} for item in self.skipped_boxes],
            "decision": self.decision,
            "decision_note": self.decision_note,
        }

    def skipped_json(self) -> dict[str, Any]:
        data = {"observation_id": self.observation_id, "reason": self.reason, "message": self.message}
        if self.asset_id:
            data["asset_id"] = self.asset_id
        return data


class _AlreadyImported(Exception):
    """Raised inside the import transaction when another request got there first."""


def _link_exists(observation_id) -> bool:
    return FeedbackImport.objects.filter(observation_id=observation_id).exists()


def _already(observation_id: str, observation_pk) -> Outcome:
    asset_id = FeedbackImport.objects.filter(observation_id=observation_pk).values_list("asset_ref", flat=True).first()
    return Outcome(observation_id, reason="already_imported", asset_id=asset_id or None)


def import_observation(project: Project, observation_id, user) -> Outcome:
    """Pull one observation into ``project``. Its own transaction: one bad file never loses the others.

    Returns an ``Outcome``; it raises only on a genuine fault (disk, database), after removing any file it saved.
    """
    key = str(observation_id)
    observation = (
        ObservationRecord.objects.select_related("app", "model_version")
        .filter(pk=observation_id, app__project=project)
        .first()
    )
    if observation is None:
        return Outcome(key, reason="not_found")
    if _link_exists(observation.pk):
        return _already(key, observation.pk)
    if observation.status != ObservationRecord.SUCCEEDED:
        return Outcome(key, reason="observation_not_finished")
    records = list(FeedbackRecord.objects.filter(observation=observation))
    newest = latest_decisions([observation.pk]).get(observation.pk)
    decision, decision_note = decision_fields(newest and newest.decision, newest and newest.note)
    if not records and decision is None:  # nothing per detection, and no reject / second look (or a later confirm)
        return Outcome(key, reason="no_feedback")
    plan = build_plan(project, observation, records)
    loaded = _load_picture(observation)
    if isinstance(loaded, str):
        return Outcome(key, reason=loaded)
    data, meta = loaded

    person = user if getattr(user, "is_authenticated", False) else None
    asset_id = uuid.uuid4()
    wanted_key = f"projects/{project.id}/assets/{asset_id}.{meta['extension']}"
    try:
        storage_key = default_storage.save(wanted_key, ContentFile(data))
    except OSError:  # disk full, not writable...: this picture is skipped, the rest of the batch goes on
        logger.exception("Could not store the picture for observation %s", observation.pk)
        _discard(wanted_key)  # a half-written file must not stay behind
        return Outcome(key, reason="storage_error")
    try:
        with transaction.atomic():
            # Take the observation's row first: a second importer waits here, then sees the link and stops.
            ObservationRecord.objects.select_for_update().filter(pk=observation.pk).first()
            if _link_exists(observation.pk):
                raise _AlreadyImported
            asset = Asset.objects.create(
                id=asset_id,
                project=project,
                storage_key=storage_key,
                **{name: value for name, value in meta.items() if name != "extension"},
            )
            model = observation.model_version
            rows = [
                BoxAnnotation(
                    asset=asset,
                    label=box.label,
                    origin=BoxAnnotation.ORIGIN_MODEL,  # a Needs-review suggestion; the review controls know this shape
                    status=BoxAnnotation.STATUS_SUGGESTED,
                    confidence=box.confidence,
                    model_name=f"{BOX_SOURCE_NAME} (model v{model.version})",
                    created_by_id=box.created_by_id,
                    **box.geometry,
                )
                for box in plan.boxes
            ]
            BoxAnnotation.objects.bulk_create(rows)
            FeedbackImport.objects.create(
                project=project,
                observation=observation,
                observation_ref=str(observation.pk),
                asset=asset,
                asset_ref=str(asset.pk),
                model_version_ref=str(model.pk),
                model_version_number=model.version,
                artifact_sha256=model.artifact_sha256,
                image_sha256=observation.image_sha256,
                kinds=plan.kinds,
                boxes_created=len(rows),
                missed_without_box=plan.missed_without_box,
                skipped=plan.skipped,
                annotation_ids=[str(row.pk) for row in rows],
                imported_by=person,
                imported_by_ref=opaque_user(person.pk) or "" if person else "",
                imported_by_role=(permissions.role_of(person, project) or "") if person else "",
            )
    except (_AlreadyImported, IntegrityError) as error:
        _discard(storage_key)
        if isinstance(error, IntegrityError) and not _row_exists(observation.pk):
            raise  # a different integrity problem: do not hide it behind "already imported"
        return _already(key, observation.pk)
    except Exception:
        _discard(storage_key)
        raise
    return Outcome(
        key,
        imported=True,
        asset_id=str(asset_id),
        boxes_created=len(plan.boxes),
        missed_without_box=plan.missed_without_box,
        false_alarms=plan.false_alarms,
        skipped_boxes=plan.skipped,
        decision=decision,
        decision_note=decision_note,
        decision_only=not records,
    )


def _row_exists(observation_pk) -> bool:
    """Unlike ``_link_exists`` this is always the real answer (tests stub the early check to stress the constraint)."""
    return FeedbackImport.objects.filter(observation_id=observation_pk).exists()


# -- the list --------------------------------------------------------------------------------
def _with_feedback(project: Project):
    """Results that have something to say: per-detection feedback, or a latest decision of reject / second look."""
    ids = FeedbackRecord.objects.filter(observation__app__project=project).values("observation_id")
    return (
        ObservationRecord.objects.filter(app__project=project)
        .annotate(**_latest_decision_columns("pk"))
        .filter(Q(pk__in=ids) | Q(latest_decision__in=list(DECISION_NAMES)))
    )


def _waiting(project: Project) -> tuple[list[tuple[ObservationRecord, str | None]], bool, int]:
    """Results with feedback that are not pulled in yet: (newest ``SCAN_LIMIT`` with why-not or None, truncated, all).

    The window is over what is *waiting*: pulled-in results never use it up, so older ones stay reachable.
    Whether a file is really there needs a look at storage, so that part is only known for the results looked at.
    """
    waiting = _with_feedback(project).filter(feedback_imports__isnull=True)
    total = waiting.count()
    window = list(waiting.select_related("app", "model_version").order_by("-created_at", "-id")[:SCAN_LIMIT])
    return [(observation, _cheap_picture_problem(observation)) for observation in window], total > len(window), total


def _skipped_summary(skipped: Any) -> list[dict[str, Any]]:
    reasons = Counter(entry.get("reason", "") for entry in skipped if isinstance(entry, dict))
    return [{"reason": reason, "count": count, "message": MESSAGES.get(reason, "")} for reason, count in sorted(reasons.items())]


def _item(
    observation: ObservationRecord, feedback, state: str, reason: str | None, link: FeedbackImport | None, decision=None
) -> dict[str, Any]:
    """``decision`` is ``(value, note)`` of the latest whole-result decision, read with the row (no extra query)."""
    decision, decision_note = decision_fields(*(decision or (None, "")))
    item = {
        "observation_id": str(observation.pk),
        "app_id": str(observation.app_id),
        "app_name": observation.app.name,
        "taken_at": observation.created_at.isoformat(),
        "model_version": {"id": str(observation.model_version_id), "version": observation.model_version.version},
        "kinds": kinds_of(effective_feedback(feedback.get(observation.pk, []))),
        "state": state,
        "reason": reason,
        "message": MESSAGES.get(reason, "") if reason else "",
        # The latest whole-result decision when it is a reject or a second look (else None / ""); the note is data.
        # For a picture already pulled in this is the decision as it stands now.
        "decision": decision,
        "decision_note": decision_note,
    }
    if link is not None:
        item.update(
            asset_id=link.asset_id and str(link.asset_id),
            imported_at=link.created_at.isoformat(),
            imported_by=link.imported_by_ref or None,
            boxes_created=link.boxes_created,
            missed_without_box=link.missed_without_box,
            # Boxes that were not made (a label that is not in the project, a box outside the picture...): never silent.
            boxes_skipped=len(link.skipped or []),
            skipped_reasons=_skipped_summary(link.skipped or []),
        )
    return item


def corrections(project: Project) -> dict[str, Any]:
    """Counts for everything, and at most ``LIST_LIMIT`` entries (what still needs doing first); constant query count."""
    waiting, truncated, waiting_total = _waiting(project)
    imported_total = FeedbackImport.objects.filter(project=project).count()
    shown_waiting = waiting[:LIST_LIMIT]
    links = list(
        FeedbackImport.objects.filter(project=project)
        .annotate(**_latest_decision_columns("observation_id"))
        .select_related("observation__app", "observation__model_version")
        .order_by("-observation__created_at", "-observation_id")[: LIST_LIMIT - len(shown_waiting)]
    )
    pks = [observation.pk for observation, _ in shown_waiting] + [link.observation_id for link in links]
    feedback: dict[Any, list[FeedbackRecord]] = {}
    for record in FeedbackRecord.objects.filter(observation_id__in=pks):
        feedback.setdefault(record.observation_id, []).append(record)
    entries = [
        (
            observation,
            _item(
                observation, feedback, CANNOT_IMPORT if reason else PENDING, reason, None,
                (observation.latest_decision, observation.latest_decision_note),
            ),
        )
        for observation, reason in shown_waiting
    ] + [
        (link.observation, _item(link.observation, feedback, IMPORTED, None, link, (link.latest_decision, link.latest_decision_note)))
        for link in links
    ]
    entries.sort(key=lambda entry: (entry[0].created_at, str(entry[0].pk)), reverse=True)
    cannot = sum(1 for _, reason in waiting if reason)
    return {
        # Pending and cannot_import are counted among the results looked at (the newest ``SCAN_LIMIT`` still waiting).
        # ``scanned`` (and its older name ``cannot_import_scanned``, the same number) says how many that was:
        # it is NOT "the newest N shown" (``items`` is capped separately at ``LIST_LIMIT``) and NOT ``total``.
        # ``imported`` and ``total`` (pulled in + all still waiting) are exact.
        "pending": len(waiting) - cannot,
        "imported": imported_total,
        "cannot_import": cannot,
        "cannot_import_scanned": len(waiting),
        "scanned": len(waiting),
        "total": imported_total + waiting_total,
        "truncated": truncated,
        "items": [item for _, item in entries],
        "batch_limit": IMPORT_BATCH_LIMIT,
        "notice": (
            "Pulling corrections in only adds pictures with Needs-review boxes. Nothing is used for teaching "
            "until a person accepts the boxes, and the model in use is not changed."
        ),
    }


# -- the batch -------------------------------------------------------------------------------
def parse_observation_ids(value: Any) -> list[uuid.UUID]:
    if not isinstance(value, list):
        raise HttpError("observation_ids must be a list of check-screen result ids.")
    if len(value) > IMPORT_BATCH_LIMIT:
        raise HttpError(f"Name at most {IMPORT_BATCH_LIMIT} results at a time; the rest can be pulled in next.")
    parsed: list[uuid.UUID] = []
    for item in value:
        if not isinstance(item, str):
            raise HttpError("observation_ids must be a list of check-screen result ids.")
        try:
            parsed.append(uuid.UUID(item))
        except ValueError:
            raise HttpError("observation_ids contains an id that is not valid.") from None
    return list(dict.fromkeys(parsed))


def import_corrections(project: Project, user, observation_ids: list[uuid.UUID] | None = None) -> dict[str, Any]:
    unimportable: list[Outcome] = []
    if observation_ids is None:
        waiting, _, _ = _waiting(project)
        observation_ids = [observation.pk for observation, reason in waiting if reason is None][:IMPORT_BATCH_LIMIT]
        # Name every result that cannot be pulled in and why (the upload rule), without spending the batch on it.
        unimportable = [Outcome(str(observation.pk), reason=reason) for observation, reason in waiting if reason]
    outcomes = [import_observation(project, observation_id, user) for observation_id in observation_ids]
    imported = [outcome for outcome in outcomes if outcome.imported]
    left_out = [outcome for outcome in outcomes if not outcome.imported] + unimportable
    remaining = sum(1 for _, reason in _waiting(project)[0] if reason is None)
    return {
        "imported": [outcome.imported_json() for outcome in imported],
        "skipped": [outcome.skipped_json() for outcome in left_out],
        "totals": {
            "requested": len(outcomes) + len(unimportable),
            "imported": len(imported),
            "skipped": len(left_out),
            "boxes_created": sum(outcome.boxes_created for outcome in imported),
            # Pictures copied only because of a reject / second look (nothing per detection, so no boxes): nothing to teach from yet.
            "pictures_from_decisions": sum(1 for outcome in imported if outcome.decision_only),
            "missed_without_box": sum(outcome.missed_without_box for outcome in imported),
            "remaining_pending": remaining,
        },
        "notice": "These boxes say Needs review. They are not used for teaching until a person accepts them.",
    }
