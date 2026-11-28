# Generated manually for T33: event-linked learning examples and verified negatives.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0033_operationdraft_bays"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationLearningExample",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("recording_sha256", models.CharField(max_length=64)),
                ("bundle_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("time_pts", models.FloatField(help_text="Seconds into the recording (source timeline).")),
                (
                    "polarity",
                    models.CharField(
                        choices=[
                            ("positive", "Positive (object/event present)"),
                            ("negative", "Negative (verified empty / false alarm)"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "selection_kind",
                    models.CharField(
                        choices=[
                            ("miss", "Missed detection / event"),
                            ("extra", "Extra / false positive"),
                            ("matched", "Matched (optional keep)"),
                            ("verified_negative", "Verified negative"),
                            ("operator", "Operator selection"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("candidate", "Candidate"),
                            ("verified", "Verified"),
                            ("rejected", "Rejected"),
                        ],
                        default="candidate",
                        max_length=16,
                    ),
                ),
                ("content_sha256", models.CharField(max_length=64)),
                ("evidence", models.JSONField(blank=True, default=dict)),
                ("event_identity", models.JSONField(blank=True, default=dict)),
                ("note", models.CharField(blank=True, default="", max_length=500)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="learning_examples",
                        to="projects.operationevent",
                    ),
                ),
                (
                    "execution",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="learning_examples",
                        to="projects.operationexecution",
                    ),
                ),
                (
                    "operation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learning_examples",
                        to="projects.operation",
                    ),
                ),
                (
                    "recording",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="learning_examples",
                        to="projects.operationsourcerecording",
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="learning_examples",
                        to="projects.site",
                    ),
                ),
                (
                    "verified_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="operationlearningexample",
            index=models.Index(fields=["operation", "status", "polarity"], name="projects_op_operati_t33a_idx"),
        ),
        migrations.AddIndex(
            model_name="operationlearningexample",
            index=models.Index(fields=["recording_sha256", "time_pts"], name="projects_op_recordi_t33b_idx"),
        ),
        migrations.AddIndex(
            model_name="operationlearningexample",
            index=models.Index(fields=["site", "created_at"], name="projects_op_site_id_t33c_idx"),
        ),
        migrations.AddConstraint(
            model_name="operationlearningexample",
            constraint=models.UniqueConstraint(
                fields=("operation", "content_sha256"),
                name="operationlearningexample_unique_content",
            ),
        ),
    ]
