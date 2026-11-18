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
    # The recipe key the project was created with (see projects.tasks). Null for projects made
    # before recipes existed and for callers that did not pick one; ``task`` above stays the
    # human-readable text. Read-only after creation.
    recipe = models.CharField(max_length=64, null=True, blank=True)
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
    # Modality-specific facts the plugin's validate_upload found (duration and sample rate for
    # audio, a column summary for tables, ...). Images keep width/height as columns and store
    # nothing here. Served merged into the asset list response.
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return self.filename


class AssetAnnotation(models.Model):
    """A generic annotation on one asset, for modalities whose labels are not boxes.

    Mirrors BoxAnnotation's semantics: ``origin`` manual/model, ``status`` accepted/suggested,
    write-once data (edited data replaces the row through the same PATCH review flow the box
    endpoints use), and suggested rows never count as ground truth. Image projects keep using
    BoxAnnotation exactly as before; ``kind`` is the recipe's ``annotation_kind`` slug and
    ``data`` is whatever the modality plugin's ``validate_annotation`` normalized.
    """

    ORIGIN_MANUAL, ORIGIN_MODEL = "manual", "model"
    ORIGIN_CHOICES = [(ORIGIN_MANUAL, "Entered by a person"), (ORIGIN_MODEL, "Proposed by a model")]
    STATUS_ACCEPTED, STATUS_SUGGESTED = "accepted", "suggested"
    STATUS_CHOICES = [(STATUS_ACCEPTED, "Accepted"), (STATUS_SUGGESTED, "Needs review")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="asset_annotations")
    kind = models.CharField(max_length=64)
    data = models.JSONField()
    origin = models.CharField(max_length=16, choices=ORIGIN_CHOICES, default=ORIGIN_MANUAL)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACCEPTED)
    confidence = models.FloatField(null=True, blank=True)
    model_name = models.CharField(max_length=200, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Who entered a manual annotation. Never exposed in the annotation API.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(origin="model") | models.Q(status="accepted"),
                name="assetannotation_manual_is_accepted",
            ),
        )

    def __str__(self) -> str:
        return f"{self.kind}:{self.asset_id}"


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
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.CASCADE, related_name="autolabel_jobs")
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
    job = models.ForeignKey(AutoLabelJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="annotations")
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
        constraints = (models.UniqueConstraint(fields=("project", "version"), name="datasetversion_unique_number"),)

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


class DatasetEntry(_FrozenModel):
    """One non-image asset frozen into a version, with its accepted annotation verbatim.

    The generic-modality twin of :class:`DatasetImage` + :class:`DatasetBox` in one row: a modality
    whose labels are not boxes (a sound clip's class, a form field's transcript, ...) freezes one
    entry per asset, carrying the plugin-normalized annotation ``data`` and its primary ``label``
    (the project label the annotation names, or "" when the data has no single label). Image
    projects never write these rows.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="entries")
    asset = models.ForeignKey(Asset, null=True, on_delete=models.SET_NULL, related_name="+")
    asset_uuid = models.UUIDField()
    filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    split = models.CharField(max_length=8)
    label = models.CharField(max_length=120, blank=True, default="")
    annotation = models.JSONField()

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

    def _raw_delete(self, using):
        # The fast path under delete() and cascades: it would empty the table without calling delete().
        raise ValueError("This record is append-only and cannot be deleted.")

    def bulk_create(self, objs, *args, update_conflicts=False, **kwargs):
        # Adding rows is the point; an upsert would rewrite one that exists.
        if update_conflicts:
            raise ValueError("This record is append-only and cannot be changed.")
        return super().bulk_create(objs, *args, update_conflicts=update_conflicts, **kwargs)


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
    # What was being learned: the recipe key (see projects.tasks). Null means the vision recipe
    # ("vision-detection"), which every run created before recipes existed belongs to.
    task = models.CharField(max_length=64, null=True, blank=True)
    # The annotation style the run's labels use ("boxes" for vision, ...). Same defaulting rule.
    annotation_kind = models.CharField(max_length=64, null=True, blank=True)
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
    # The recipe key this model was trained for (see projects.tasks). Null = the vision recipe.
    task = models.CharField(max_length=64, null=True, blank=True)
    # Relative to the data home; never shown to API clients.
    artifact_path = models.CharField(max_length=500)
    artifact_sha256 = models.CharField(max_length=64)
    artifact_size_bytes = models.PositiveBigIntegerField(default=0)
    label_names = models.JSONField(default=list)
    # Modality-defined JSON: the evaluation evidence the recipe's backend recorded (for vision,
    # the box-detection report built by projects.training.report; another modality stores its
    # own numbers, with "available" plus a plain-language "reason" when there are none). Readers
    # must treat it as opaque and use defensive access; results pages fall back to plain
    # "see the evaluation report" answers for shapes they do not understand.
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


class FeedbackImport(_AppendOnlyModel):
    """The record that one check-screen observation was copied into the project as a reviewable picture.

    One row per observation, ever (the unique constraint is what makes importing idempotent and safe against
    two people pressing the button at once). Never edited or deleted. It is the provenance of the picture and
    its Needs-review boxes: observation -> model version (id and artifact checksum) -> picture -> boxes.

    The picture and the model are kept as plain text as well as links: if the picture is deleted later the
    link goes empty (like ``DatasetImage.asset``) and the text still says what it was. Nothing here points at
    a model version, run or dataset with a real foreign key, so this table can never hold one back.
    People appear as an opaque ``u<id>`` plus the role they held, never as a name.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="feedback_imports")
    observation = models.ForeignKey(ObservationRecord, on_delete=models.CASCADE, related_name="feedback_imports")
    observation_ref = models.CharField(max_length=36)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="feedback_imports")
    asset_ref = models.CharField(max_length=36, blank=True)
    model_version_ref = models.CharField(max_length=36)
    model_version_number = models.PositiveIntegerField()
    artifact_sha256 = models.CharField(max_length=64)
    image_sha256 = models.CharField(max_length=64)
    # How many detections the operator called right / wrong and how many missed objects they reported.
    kinds = models.JSONField(default=dict)
    boxes_created = models.PositiveIntegerField(default=0)
    missed_without_box = models.PositiveIntegerField(default=0)
    # Boxes that were not made, and why: [{"reason": "label_not_in_project", "label": ..., "kind": ...}, ...]
    skipped = models.JSONField(default=list)
    # Ids (as text) of the Needs-review boxes made; they may be edited or removed later, the ids stay.
    annotation_ids = models.JSONField(default=list)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    imported_by_ref = models.CharField(max_length=24, blank=True)  # "u3"
    imported_by_role = models.CharField(max_length=16, blank=True)  # the role held at the time
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = (models.UniqueConstraint(fields=("observation",), name="feedbackimport_one_per_observation"),)

    def __str__(self) -> str:
        return f"{self.observation_ref} -> {self.asset_ref or 'removed picture'}"


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


# --- Access roles: per-project assignments and the record of every change -------------------

ROLE_CHOICES = [
    ("viewer", "Viewer"),
    ("labeler", "Labeler"),
    ("trainer", "Trainer"),
    ("approver", "Approver"),
]


class ProjectRole(models.Model):
    """A role given to one person on one project. It replaces their console-wide role *for that project*."""

    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="role_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="athena_project_roles")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("project_id", "user_id")
        constraints = (
            models.UniqueConstraint(fields=("project", "user"), name="projectrole_one_per_person_and_project"),
        )

    def __str__(self) -> str:
        return f"{self.user_id}:{self.project_id}:{self.role}"


class RoleChange(_AppendOnlyModel):
    """One row for every change of who holds which role. Never edited or deleted.

    Names are not stored: people are kept as an opaque id (``u3``) and a link that is cleared if the
    account is deleted, projects as an id and the name they had at the time.
    """

    CONSOLE, PROJECT = "console", "project"
    SCOPE_CHOICES = [(CONSOLE, "Whole console"), (PROJECT, "One project")]
    VIA_SCREEN, VIA_COMMAND, VIA_OTHER = "screen", "command", "other"
    VIA_CHOICES = [
        (VIA_SCREEN, "Access screen"),
        (VIA_COMMAND, "athena_roles command"),
        (VIA_OTHER, "Another tool"),
    ]

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    scope = models.CharField(max_length=8, choices=SCOPE_CHOICES)
    via = models.CharField(max_length=8, choices=VIA_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_ref = models.CharField(max_length=24, blank=True)  # "u3", or "" when no signed-in person did it
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    user_ref = models.CharField(max_length=24)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    project_ref = models.CharField(max_length=36, blank=True)
    project_name = models.CharField(max_length=120, blank=True)
    before = models.CharField(max_length=16, blank=True)  # "" = no role of this kind
    after = models.CharField(max_length=16, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("-id",)
        indexes = (models.Index(fields=("project_ref", "-id")), models.Index(fields=("user_ref", "-id")))

    def __str__(self) -> str:
        return f"{self.user_ref} {self.scope} {self.before or '-'} -> {self.after or '-'}"


class ConsoleSetting(models.Model):
    """A console-wide switch an administrator can change without touching the server's environment.

    One row per setting, ``key`` being the name. No row means "never set in the console": the environment (or
    the built-in default) decides. Only :mod:`projects.console_settings` writes here, and every change it makes
    also writes a :class:`ConsoleSettingEvent`.
    """

    key = models.CharField(max_length=64, primary_key=True)
    value = models.CharField(max_length=16)  # "on" or "off"
    updated_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("key",)

    def __str__(self) -> str:
        return f"{self.key}={self.value}"


class ConsoleSettingEvent(_AppendOnlyModel):
    """One row for every change of a console setting. Never edited or deleted.

    Like :class:`RoleChange`, no names are stored: the person is an opaque id (``u3``) plus the role they
    held at the time, with a link that is cleared if the account is deleted.
    """

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    key = models.CharField(max_length=64)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_ref = models.CharField(max_length=24, blank=True)  # "u3"
    actor_role = models.CharField(max_length=16, blank=True)  # "administrator" (a superuser) at the time
    before = models.CharField(max_length=16)  # what was in force: "on" or "off"
    before_source = models.CharField(max_length=16)  # where that came from: "console", "environment" or "default"
    after = models.CharField(max_length=16)
    terms_acknowledged = models.BooleanField(default=False)  # the licence tick, when turning something on
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("-id",)
        indexes = (models.Index(fields=("key", "-id")),)

    def __str__(self) -> str:
        return f"{self.key} {self.before} -> {self.after}"


class AIConnection(models.Model):
    """A person's own vision-language model: any OpenAI-compatible endpoint. One per person.

    With ``use_own`` off (or no row at all) that person's inspections use the built-in model from ``settings.VISION``.
    The API key is stored encrypted (``projects.vision.secrets``) and is never sent back to a browser.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_connection")
    use_own = models.BooleanField(default=False)
    label = models.CharField(max_length=80, blank=True)
    base_url = models.CharField(max_length=300, blank=True)
    model = models.CharField(max_length=160, blank=True)
    api_key_encrypted = models.TextField(blank=True)
    key_hint = models.CharField(max_length=8, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.user_id} {self.base_url} {self.model}"


class Site(models.Model):
    """A user's private operational location; this stores no source credentials."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="operation_sites")
    name = models.CharField(max_length=120)
    timezone_name = models.CharField(max_length=64, default="UTC")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "id")


class OperationSource(models.Model):
    """Private source metadata scoped to a site; connection and secret data are out of scope."""

    KIND_CHOICES = (
        ("recorded_video", "Recorded video"),
        ("camera", "Camera"),
        ("image_collection", "Image collection"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="sources")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "id")


class Operation(models.Model):
    """A private operation explicitly bound to one site and one source."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="operations")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="operations")
    source = models.ForeignKey(OperationSource, on_delete=models.PROTECT, related_name="operations")
    name = models.CharField(max_length=120)
    is_private = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class SourceViewVersion(_FrozenModel):
    """Immutable capture orientation and dimensions for an operation source."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(OperationSource, on_delete=models.PROTECT, related_name="view_versions")
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    configuration = models.JSONField()
    configuration_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version", "-id")
        constraints = (models.UniqueConstraint(fields=("source", "version"), name="sourceviewversion_unique_number"),)


class GeometryVersion(_FrozenModel):
    """Immutable operation geometry pinned to the exact source-view version it uses."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="geometry_versions")
    source_view_version = models.ForeignKey(
        SourceViewVersion, on_delete=models.PROTECT, related_name="geometry_versions"
    )
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    geometry = models.JSONField()
    geometry_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version", "-id")
        constraints = (models.UniqueConstraint(fields=("operation", "version"), name="geometryversion_unique_number"),)


class RecipeVersion(_FrozenModel):
    """Immutable versioned declaration of one recipe's executable rule parameters."""

    SUPPORTED_KEYS = ("people_counting",)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="recipe_versions")
    recipe_key = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    configuration = models.JSONField()
    configuration_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version", "-id")
        constraints = (models.UniqueConstraint(fields=("operation", "version"), name="recipeversion_unique_number"),)


class PolicyVersion(_FrozenModel):
    """Immutable versioned operation policy: retention and coverage thresholds."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="policy_versions")
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    configuration = models.JSONField()
    configuration_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version", "-id")
        constraints = (models.UniqueConstraint(fields=("operation", "version"), name="policyversion_unique_number"),)


class OperationModelBinding(_FrozenModel):
    """Immutable, server-verified binding of an operation to one exact model version.

    The binding record is the operation's model pin: it stores which model version was
    checked, the compatibility checks the server performed, and their results. It does
    not make the model run; nothing executes an operation yet.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="model_bindings")
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    model_version = models.ForeignKey("ModelVersion", on_delete=models.PROTECT, related_name="operation_bindings")
    compatibility = models.JSONField()
    compatibility_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version", "-id")
        constraints = (
            models.UniqueConstraint(fields=("operation", "version"), name="operationmodelbinding_unique_number"),
        )


class OperationScreenVersion(_FrozenModel):
    """Immutable versioned operation board layout; validated configuration only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="screen_versions")
    version = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    configuration = models.JSONField()
    configuration_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version", "-id")
        constraints = (
            models.UniqueConstraint(fields=("operation", "version"), name="operationscreenversion_unique_number"),
        )


class OperationReleaseBundle(_AppendOnlyModel):
    """Append-only release bundle assembled from one draft revision's verified pins.

    A bundle is a release candidate, not a publication: no endpoint activates or
    deactivates it. It stores its own full snapshot and digest, so later edits to the
    draft or its components cannot alter it. The PROTECT foreign keys intentionally
    refuse deleting a draft or operation that a bundle was built from.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="release_bundles")
    draft = models.ForeignKey("OperationDraft", on_delete=models.PROTECT, related_name="release_bundles")
    draft_revision = models.PositiveIntegerField()
    number = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    snapshot = models.JSONField()
    snapshot_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-number", "-id")
        constraints = (
            models.UniqueConstraint(fields=("operation", "number"), name="operationreleasebundle_unique_number"),
        )


class OperationDraft(models.Model):
    """Private unpublished configuration, optionally bound to an operation; it does not produce results."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="operation_drafts")
    operation = models.ForeignKey("Operation", null=True, blank=True, on_delete=models.PROTECT, related_name="drafts")
    name = models.CharField(max_length=120)
    line = models.JSONField(null=True, blank=True)
    reference_events = models.JSONField(default=list)
    # The component versions this draft currently builds on. Pins are recorded per revision
    # into the draft-version snapshot; they are configuration references, not a release.
    geometry_version = models.ForeignKey(
        GeometryVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="drafts"
    )
    recipe_version = models.ForeignKey(
        RecipeVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="drafts"
    )
    policy_version = models.ForeignKey(
        PolicyVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="drafts"
    )
    model_binding = models.ForeignKey(
        OperationModelBinding, null=True, blank=True, on_delete=models.PROTECT, related_name="drafts"
    )
    screen_version = models.ForeignKey(
        OperationScreenVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="drafts"
    )
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")


class OperationDraftVersion(_GuardedModel):
    """Immutable snapshot of one saved draft revision; deleting the draft erases its snapshots too."""

    IMMUTABLE_FIELDS = ("draft", "revision", "created_by", "snapshot", "snapshot_sha256", "created_at")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(OperationDraft, on_delete=models.CASCADE, related_name="versions")
    revision = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    snapshot = models.JSONField()
    snapshot_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-revision", "-id")
        constraints = (
            models.UniqueConstraint(fields=("draft", "revision"), name="operationdraftversion_unique_revision"),
        )


class OperationSourceRecording(models.Model):
    """A durable, whole-file recorded-video import attached to one source.

    Unlike a draft's throwaway test recording, this is durable source material:
    the file is preserved as-is (never reduced to sampled stills), and the row
    records its checksum, dimensions, duration, the container's own creation
    time when present, and the moment Athena ingested it. Nothing here decodes
    the recording into events; that is later, separate work.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(OperationSource, on_delete=models.CASCADE, related_name="recordings")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    storage_key = models.CharField(max_length=500)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    duration_seconds = models.FloatField()
    # The recording's own clock: MP4 creation_time when the container carries it (UTC), else null.
    captured_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")


class OperationRecording(models.Model):
    """One private test recording attached to an unpublished draft."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.OneToOneField(OperationDraft, on_delete=models.CASCADE, related_name="recording")
    storage_key = models.CharField(max_length=500)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    duration_seconds = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)


class OperationRecordingCleanup(models.Model):
    """Durable deletion retry when a private recording cannot be removed immediately."""

    storage_key = models.CharField(max_length=500, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Inspection(models.Model):
    """One question asked about one to six pictures (or frames of a short video), and the model's answer."""

    STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED = "queued", "running", "done", "failed"
    STATUS_CHOICES = [(s, s.capitalize()) for s in (STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED)]
    ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
    VERDICT_CHOICES = [("clear", "All clear"), ("attention", "Needs attention"), ("critical", "Critical")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="inspections")
    template = models.CharField(max_length=40)
    title = models.CharField(max_length=120)
    instructions = models.TextField(blank=True)
    source = models.CharField(max_length=10, default="photo")  # "photo" or "video"
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    verdict = models.CharField(max_length=10, choices=VERDICT_CHOICES, blank=True)
    result = models.JSONField(default=dict, blank=True)
    regions = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    provider = models.CharField(max_length=10, blank=True)  # "builtin" or "own"
    model_label = models.CharField(max_length=200, blank=True)
    endpoint_host = models.CharField(max_length=200, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    feedback = models.CharField(max_length=8, blank=True)  # "right" or "wrong"
    feedback_note = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = (models.Index(fields=("created_by", "-created_at")),)

    def __str__(self) -> str:
        return self.title


class InspectionImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="images")
    position = models.PositiveSmallIntegerField()
    filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    content_type = models.CharField(max_length=40)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    frame_time = models.FloatField(null=True, blank=True)  # seconds into the video, for frames taken from one

    class Meta:
        ordering = ("position",)
        constraints = (models.UniqueConstraint(fields=("inspection", "position"), name="inspection_image_position"),)
