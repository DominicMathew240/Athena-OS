from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

MODALITY_CHOICES = [
    ("image", "Image"),
    ("video", "Video"),
    ("audio", "Audio"),
    ("speech", "Speech"),
    ("text", "Text / documents"),
    ("table", "Tables"),
    ("time_series", "Time series"),
    ("depth", "Depth"),
    ("point_cloud", "Point cloud"),
    ("custom", "Custom modality"),
]


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    modality = models.CharField(max_length=32, choices=MODALITY_CHOICES)
    custom_modality = models.CharField(max_length=80, blank=True)
    task = models.CharField(max_length=120)
    labels = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return self.name


class Asset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="assets")
    filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500, unique=True)
    content_type = models.CharField(max_length=32)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return self.filename


class AutoLabelJob(models.Model):
    """A queued request to propose boxes for one image or a whole project."""

    QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED = (
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    )
    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (SUCCEEDED, "Finished"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]
    ACTIVE_STATUSES = (QUEUED, RUNNING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="autolabel_jobs")
    asset = models.ForeignKey(
        Asset, null=True, blank=True, on_delete=models.CASCADE, related_name="autolabel_jobs"
    )
    only_unlabeled = models.BooleanField(default=True)
    min_confidence = models.FloatField(default=0.45)
    max_boxes = models.PositiveSmallIntegerField(default=20)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    cancel_requested = models.BooleanField(default=False)
    model_name = models.CharField(max_length=200, blank=True)
    total_assets = models.PositiveIntegerField(default=0)
    processed_assets = models.PositiveIntegerField(default=0)
    boxes_proposed = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = (
            models.UniqueConstraint(
                fields=("project",),
                condition=models.Q(status__in=("queued", "running")),
                name="autolabeljob_one_active_per_project",
            ),
        )

    def __str__(self) -> str:
        return f"{self.project_id}:{self.status}"


class BoxAnnotation(models.Model):
    ORIGIN_MANUAL, ORIGIN_MODEL = "manual", "model"
    ORIGIN_CHOICES = [(ORIGIN_MANUAL, "Drawn by a person"), (ORIGIN_MODEL, "Proposed by a model")]
    STATUS_ACCEPTED, STATUS_SUGGESTED = "accepted", "suggested"
    STATUS_CHOICES = [(STATUS_ACCEPTED, "Accepted"), (STATUS_SUGGESTED, "Needs review")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="annotations")
    label = models.CharField(max_length=120)
    origin = models.CharField(max_length=16, choices=ORIGIN_CHOICES, default=ORIGIN_MANUAL)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACCEPTED)
    confidence = models.FloatField(null=True, blank=True)
    model_name = models.CharField(max_length=200, blank=True)
    job = models.ForeignKey(
        AutoLabelJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="annotations"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Who drew a manual box. Never exposed in the annotation API; exports carry it only as an opaque id.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    x = models.DecimalField(max_digits=12, decimal_places=8)
    y = models.DecimalField(max_digits=12, decimal_places=8)
    width = models.DecimalField(max_digits=12, decimal_places=8)
    height = models.DecimalField(max_digits=12, decimal_places=8)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(origin="model") | models.Q(status="accepted"),
                name="boxannotation_manual_is_accepted",
            ),
            models.CheckConstraint(
                condition=models.Q(x__gte=0) & models.Q(x__lte=1 - models.F("width")),
                name="boxannotation_x_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(y__gte=0) & models.Q(y__lte=1 - models.F("height")),
                name="boxannotation_y_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(width__gt=0) & models.Q(width__lte=1),
                name="boxannotation_positive_width",
            ),
            models.CheckConstraint(
                condition=models.Q(height__gt=0) & models.Q(height__lte=1),
                name="boxannotation_positive_height",
            ),
        )

    def __str__(self) -> str:
        return self.label


class _FrozenQuerySet(models.QuerySet):
    """Dataset snapshots are write-once: bulk updates are refused as well as ``save()``."""

    def update(self, **kwargs):
        raise ValueError("Dataset versions are immutable; create a new version instead.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Dataset versions are immutable; create a new version instead.")

    def delete(self):
        raise ValueError("Dataset versions cannot be deleted one by one; delete the project instead.")


class _FrozenModel(models.Model):
    objects = _FrozenQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Dataset versions are immutable; create a new version instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Dataset versions cannot be deleted one by one; delete the project instead.")


class DatasetVersion(_FrozenModel):
    """An immutable, numbered snapshot of the reviewed (accepted) boxes in a project."""

    READY = "ready"
    STATUS_CHOICES = [(READY, "Ready")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="dataset_versions")
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Set explicitly (not auto_now_add) because the timestamp is part of the hashed manifest.
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    label_names = models.JSONField(default=list)
    image_count = models.PositiveIntegerField(default=0)
    box_count = models.PositiveIntegerField(default=0)
    excluded_suggested_count = models.PositiveIntegerField(default=0)
    excluded_image_count = models.PositiveIntegerField(default=0)
    split_config = models.JSONField(default=dict)
    manifest = models.JSONField(default=dict)
    manifest_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=READY)
    # The ONE field of a frozen version that may change after creation (see ``mark_incomplete``).
    # Empty means complete. Once non-empty it is never cleared: it records, append-only, that
    # pictures were permanently deleted from the project after this version was frozen.
    # It is not part of the manifest, so the manifest stays verifiable against its checksum.
    incomplete_reason = models.TextField(blank=True, default="")

    MUTABLE_FIELDS = ("incomplete_reason",)

    class Meta:
        ordering = ("-version",)
        constraints = (
            models.UniqueConstraint(fields=("project", "version"), name="datasetversion_unique_number"),
        )

    def __str__(self) -> str:
        return f"{self.project_id} v{self.version}"

    @property
    def is_incomplete(self) -> bool:
        return bool(self.incomplete_reason)

    @classmethod
    def mark_incomplete(cls, version_id, note: str) -> None:
        """Append ``note`` to the version's incomplete reason. The only sanctioned write to a frozen row.

        It goes through a plain ``QuerySet`` on purpose (the model's own manager refuses every
        update) and touches only ``incomplete_reason``; nothing can clear or rewrite earlier text.
        """
        from django.db import transaction

        plain = models.QuerySet(model=cls)
        with transaction.atomic():
            current = plain.select_for_update().filter(pk=version_id).values_list("incomplete_reason", flat=True).get()
            plain.filter(pk=version_id).update(incomplete_reason=f"{current}\n{note}" if current else note)


class DatasetImage(_FrozenModel):
    """One image frozen into a version. ``asset`` may go null if the source image is deleted later."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="images")
    asset = models.ForeignKey(Asset, null=True, on_delete=models.SET_NULL, related_name="+")
    asset_uuid = models.UUIDField()
    filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    content_type = models.CharField(max_length=32)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    split = models.CharField(max_length=8)

    class Meta:
        ordering = ("sha256", "asset_uuid")


class DatasetBox(_FrozenModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    image = models.ForeignKey(DatasetImage, on_delete=models.CASCADE, related_name="boxes")
    source_annotation_id = models.UUIDField()
    label = models.CharField(max_length=120)
    origin = models.CharField(max_length=16)
    model_name = models.CharField(max_length=200, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    # Plain integers (not FKs): the snapshot must survive user deletion and stays opaque in exports.
    created_by_id_snapshot = models.IntegerField(null=True, blank=True)
    reviewed_by_id_snapshot = models.IntegerField(null=True, blank=True)
    x = models.DecimalField(max_digits=12, decimal_places=8)
    y = models.DecimalField(max_digits=12, decimal_places=8)
    width = models.DecimalField(max_digits=12, decimal_places=8)
    height = models.DecimalField(max_digits=12, decimal_places=8)

    class Meta:
        ordering = ("source_annotation_id",)


# --- Training runs, model versions and the audit trail ----------------------------------


class _GuardedQuerySet(models.QuerySet):
    """Refuses bulk updates that touch a model's ``IMMUTABLE_FIELDS``."""

    def update(self, **kwargs):
        touched = set(kwargs) & set(getattr(self.model, "IMMUTABLE_FIELDS", ()))
        if touched:
            raise ValueError(f"{self.model.__name__} fields are immutable: {', '.join(sorted(touched))}.")
        return super().update(**kwargs)


class _GuardedModel(models.Model):
    """Rows that change state over time, but whose defining facts are write-once."""

    IMMUTABLE_FIELDS: tuple[str, ...] = ()
    objects = _GuardedQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding and self.IMMUTABLE_FIELDS:
            attnames = [self._meta.get_field(name).attname for name in self.IMMUTABLE_FIELDS]
            stored = type(self).objects.filter(pk=self.pk).values(*attnames).first()
            if stored is not None and any(stored[name] != getattr(self, name) for name in attnames):
                raise ValueError(f"{type(self).__name__} is immutable in: {', '.join(self.IMMUTABLE_FIELDS)}.")
        super().save(*args, **kwargs)


class _AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("This record is append-only and cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("This record is append-only and cannot be changed.")

    def delete(self):
        raise ValueError("This record is append-only and cannot be deleted.")


class _AppendOnlyModel(models.Model):
    objects = _AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("This record is append-only and cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("This record is append-only and cannot be deleted.")


class TrainingRun(_GuardedModel):
    """One request to train a model on one frozen dataset version."""

    QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED = "queued", "running", "succeeded", "failed", "cancelled"
    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (SUCCEEDED, "Finished"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]
    ACTIVE_STATUSES = (QUEUED, RUNNING)
    IMMUTABLE_FIELDS = ("project", "dataset_version", "backend", "config", "config_sha256", "dataset_manifest_sha256")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="training_runs")
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.RESTRICT, related_name="training_runs")
    dataset_manifest_sha256 = models.CharField(max_length=64)
    backend = models.CharField(max_length=80)
    config = models.JSONField(default=dict)
    config_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    cancel_requested = models.BooleanField(default=False)
    progress = models.FloatField(default=0.0)
    logs = models.TextField(blank=True)
    error = models.TextField(blank=True)
    metrics = models.JSONField(default=dict)
    runtime_info = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = (
            models.UniqueConstraint(
                fields=("project",),
                condition=models.Q(status__in=("queued", "running")),
                name="trainingrun_one_active_per_project",
            ),
        )

    def __str__(self) -> str:
        return f"{self.project_id}:{self.backend}:{self.status}"


class ModelVersion(_GuardedModel):
    """A trained (or imported) model. ``stage`` only moves forward through human-confirmed steps."""

    CANDIDATE, STAGED, APPROVED, RETIRED = "candidate", "staged", "approved", "retired"
    STAGE_CHOICES = [
        (CANDIDATE, "Candidate"),
        (STAGED, "Staged for review"),
        (APPROVED, "Approved"),
        (RETIRED, "Retired"),
    ]
    IMMUTABLE_FIELDS = (
        "project",
        "training_run",
        "backend",
        "version",
        "artifact_path",
        "artifact_sha256",
        "artifact_size_bytes",
        "label_names",
        "evaluation",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="model_versions")
    version = models.PositiveIntegerField()
    training_run = models.ForeignKey(
        TrainingRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="model_versions"
    )
    backend = models.CharField(max_length=80)
    # Relative to the data home; never shown to API clients.
    artifact_path = models.CharField(max_length=500)
    artifact_sha256 = models.CharField(max_length=64)
    artifact_size_bytes = models.PositiveBigIntegerField(default=0)
    label_names = models.JSONField(default=list)
    evaluation = models.JSONField(default=dict)
    stage = models.CharField(max_length=16, choices=STAGE_CHOICES, default=CANDIDATE)
    staged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    staged_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_note = models.TextField(blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    previous_approved = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="superseded_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version",)
        constraints = (
            models.UniqueConstraint(fields=("project", "version"), name="modelversion_unique_number"),
            models.UniqueConstraint(
                fields=("project",),
                condition=models.Q(stage="approved"),
                name="modelversion_one_approved_per_project",
            ),
        )

    def __str__(self) -> str:
        return f"{self.project_id} model v{self.version} ({self.stage})"


class ModelEvent(_AppendOnlyModel):
    """Audit row for run creation/outcome and every model stage transition. Never edited or deleted."""

    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="model_events")
    model_version = models.ForeignKey(ModelVersion, null=True, on_delete=models.CASCADE, related_name="events")
    training_run = models.ForeignKey(TrainingRun, null=True, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=32)
    from_state = models.CharField(max_length=16, blank=True)
    to_state = models.CharField(max_length=16, blank=True)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_id_snapshot = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("id",)


# --- Inference apps, observations and operator feedback ------------------------------------


class InferenceApp(models.Model):
    """An operator-configured screen that runs one *approved* model version on uploaded images."""

    ACTIVE, ARCHIVED = "active", "archived"
    STATUS_CHOICES = [(ACTIVE, "Active"), (ARCHIVED, "Archived")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="inference_apps")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    model_version = models.ForeignKey(ModelVersion, on_delete=models.RESTRICT, related_name="inference_apps")
    config = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")

    def __str__(self) -> str:
        return self.slug


class ObservationRecord(_GuardedModel):
    """One uploaded image and what one exact model version predicted for it."""

    QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"
    STATUS_CHOICES = [(QUEUED, "Queued"), (RUNNING, "Running"), (SUCCEEDED, "Finished"), (FAILED, "Failed")]
    IMMUTABLE_FIELDS = (
        "app",
        "model_version",
        "image_key",
        "image_sha256",
        "content_type",
        "size_bytes",
        "width",
        "height",
        "config_snapshot",
        "created_by",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app = models.ForeignKey(InferenceApp, on_delete=models.CASCADE, related_name="observations")
    model_version = models.ForeignKey(ModelVersion, on_delete=models.RESTRICT, related_name="observations")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    image_key = models.CharField(max_length=500)
    image_sha256 = models.CharField(max_length=64)
    filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=32)
    size_bytes = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    # The app configuration in force when the image was submitted; results are always read against it.
    config_snapshot = models.JSONField(default=dict)
    predictions = models.JSONField(default=list)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.app_id}:{self.status}"


class FeedbackRecord(_AppendOnlyModel):
    """A person's verdict on one prediction (or a missed object). Immutable; never touches the model."""

    TRUE_POSITIVE, FALSE_POSITIVE, FALSE_NEGATIVE = "true_positive", "false_positive", "false_negative"
    VERDICT_CHOICES = [
        (TRUE_POSITIVE, "Correct detection"),
        (FALSE_POSITIVE, "Wrong detection"),
        (FALSE_NEGATIVE, "Missed object"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    observation = models.ForeignKey(ObservationRecord, on_delete=models.CASCADE, related_name="feedback")
    # None means "the model missed this" (verdict false_negative).
    prediction_index = models.PositiveIntegerField(null=True, blank=True)
    label = models.CharField(max_length=120, blank=True)
    box = models.JSONField(null=True, blank=True)
    verdict = models.CharField(max_length=16, choices=VERDICT_CHOICES)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_by_snapshot = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("created_at", "id")


class ReviewDecision(_AppendOnlyModel):
    """A person's overall decision about one observation (confirm / reject / needs a second look)."""

    CONFIRM, REJECT, SECOND_LOOK = "confirm", "reject", "needs_second_look"
    DECISION_CHOICES = [(CONFIRM, "Confirm"), (REJECT, "Reject"), (SECOND_LOOK, "Needs a second look")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    observation = models.ForeignKey(ObservationRecord, on_delete=models.CASCADE, related_name="decisions")
    decision = models.CharField(max_length=24, choices=DECISION_CHOICES)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_by_snapshot = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("created_at", "id")


# --- Dataset export audit ------------------------------------------------------------------


class ExportEvent(_AppendOnlyModel):
    """One row per dataset export attempt, successful or not. Never edited or deleted."""

    id = models.BigAutoField(primary_key=True)
    version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="export_events")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    user_id_snapshot = models.IntegerField(null=True, blank=True)
    format = models.CharField(max_length=16, blank=True)
    # Bytes actually streamed to the client (0 when refused before streaming started).
    bytes_sent = models.PositiveBigIntegerField(default=0)
    success = models.BooleanField(default=False)
    status_code = models.PositiveSmallIntegerField(default=200)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-id",)

    def __str__(self) -> str:
        return f"{self.version_id} {self.format} {'ok' if self.success else 'failed'}"
