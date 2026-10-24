#!/usr/bin/env python
"""Seed a DISPOSABLE data home for `browser_smoke.mjs`. Not for real use.

Creates one superuser (password taken from the environment, never printed), one project with 12 made-up pictures
and boxes, a saved snapshot, and two models produced by a clearly named STAND-IN training backend that learns
nothing: model 1 is approved and has a check screen, model 2 is "ready for trial" so the approve dialog can open.
Everything here is synthetic. The stand-in exists so the smoke test can reach every screen without PyTorch.

Usage (run_browser_smoke.sh does this for you):
    ATHENA_DATA_HOME=<empty dir> ATHENA_SMOKE_PASSWORD=<secret> DJANGO_DEBUG=1 python scripts/seed_browser_smoke.py

Prints one JSON line: {"project_id": ..., "slug": ..., "username": ...}.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")

if "ATHENA_DATA_HOME" not in os.environ:
    sys.exit("Refusing to run: set ATHENA_DATA_HOME to an empty scratch directory first.")
if not os.environ.get("ATHENA_SMOKE_PASSWORD"):
    sys.exit("Refusing to run: set ATHENA_SMOKE_PASSWORD (any throw-away secret).")
os.environ.setdefault("DJANGO_DEBUG", "1")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.storage import default_storage  # noqa: E402
from django.core.management import call_command  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

call_command("migrate", verbosity=0)

from projects import training  # noqa: E402
from projects.datasets import service as dataset_service, splits  # noqa: E402
from projects.inference import service as inference  # noqa: E402
from projects.models import Asset, BoxAnnotation, Project  # noqa: E402
from projects.training import lifecycle, runner  # noqa: E402
from projects.training.base import Availability, BoxPrediction, TrainingBackend, TrainResult  # noqa: E402


class StandInBackend(TrainingBackend):
    """SYNTHETIC stand-in: writes a few bytes and 'predicts' one fixed box. It learns nothing."""

    name = "smoke-stand-in"
    description = "Stand-in used only by the browser smoke test. It learns nothing."
    license_note = "Test only; no third-party code or weights."

    def check_available(self) -> Availability:
        return Availability(True, "ready")

    def default_config(self):
        return {"epochs": 1}

    def validate_config(self, config):
        return {"epochs": int(config.get("epochs", 1))}

    def train(self, dataset_dir, config, progress_cb, cancel_cb):
        progress_cb(1.0, "stand-in finished")
        artifact = dataset_dir.parent / "model.bin"
        artifact.write_bytes(b"stand-in-model")
        labels = [line for line in (dataset_dir / "classes.txt").read_text().splitlines() if line]
        return TrainResult(artifact, {"loss": 0.0}, labels)

    def predict(self, artifact, image, thresholds):
        return [BoxPrediction("damaged", 0.1, 0.2, 0.3, 0.4, 0.9)]


training.register_backend(StandInBackend, replace=True)

User = get_user_model()
user = User.objects.create_superuser("smoke", password=os.environ["ATHENA_SMOKE_PASSWORD"])

project = Project.objects.create(
    name="Smoke test bags (synthetic)", modality="image", task="Spot damaged bags", labels=["damaged", "normal"]
)
for index in range(12):
    width, height = 160, 120
    image = Image.new("RGB", (width, height), (40 + index * 5, 70, 100))
    box = (16, 24, 64, 72)
    ImageDraw.Draw(image).rectangle(box, fill=(240, 210, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    asset_id = uuid.uuid4()
    key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.png", io.BytesIO(data))
    asset = Asset.objects.create(
        id=asset_id, project=project, filename=f"bag-{index}.png", storage_key=key, content_type="image/png",
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), width=width, height=height,
    )
    BoxAnnotation.objects.create(
        asset=asset, label="damaged",
        x=Decimal(box[0] / width).quantize(Decimal("0.0001")), y=Decimal(box[1] / height).quantize(Decimal("0.0001")),
        width=Decimal((box[2] - box[0]) / width).quantize(Decimal("0.0001")),
        height=Decimal((box[3] - box[1]) / height).quantize(Decimal("0.0001")),
    )

version = None
for seed in range(300):
    analysis = dataset_service.analyse(project, splits.SplitConfig(seed, 0.6, 0.2, 0.2))
    counts = dataset_service.preview(analysis)["split_counts"]
    if counts["train"]["images"] > 0 and counts["test"]["images"] > 0:
        version = dataset_service.create_version(analysis, user)
        break
if version is None:
    sys.exit("could not find a snapshot split with training and test pictures")


def finished_model():
    lifecycle.create_run(project, user, dataset=version, backend_name=StandInBackend.name, config={})
    claimed = runner.claim_next_run()
    done = runner.run_training(claimed, StandInBackend())
    if done.status != "succeeded":
        sys.exit(f"stand-in run did not succeed: {done.error}")
    return done.model_versions.get()


first = finished_model()
lifecycle.stage_model(first, user)
first, _ = lifecycle.approve_model(first, user, "Stand-in model approved for the browser smoke test only.", acknowledge_weak_results=True)  # the stand-in has a tiny test set, which the server reads as "cannot tell"
app = inference.create_app(project, user, name="Smoke check screen", slug="smoke-check", model=first, config={})
second = finished_model()
lifecycle.stage_model(second, user)  # left "ready for trial": the approve dialog can be opened, never confirmed

print(json.dumps({"project_id": str(project.id), "slug": app.slug, "username": user.get_username(), "app_id": str(app.id)}))
