from __future__ import annotations

import uuid

from django.db import models

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


class BoxAnnotation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="annotations")
    label = models.CharField(max_length=120)
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
