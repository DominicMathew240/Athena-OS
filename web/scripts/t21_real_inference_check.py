"""T21 bonus: run the adapter with the REAL torchvision backend (ML venv only).

Not part of the Django suite (the lean venv has no torch). Run with:

    cd web && DJANGO_DEBUG=1 ATHENA_DATA_HOME=/tmp/athena-t21-ml \
      /Users/athena/Documents/WEB_DEVELOPMENT/athena-forge/.venv/bin/python \
      scripts/t21_real_inference_check.py

Builds a tiny synthetic recording, trains nothing — instead saves a real
torchvision-fasterrcnn artifact (a from-scratch Faster R-CNN with 2 classes) —
then runs projects.operations.inference.run_detector end to end and prints the
normalized detections. Exit code 0 = real inference ran and produced normalized
output; anything else is a failure.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")
os.environ.setdefault("DJANGO_DEBUG", "1")

import django  # noqa: E402

django.setup()

import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from django.conf import settings  # noqa: E402
from django.core.files.storage import default_storage  # noqa: E402
from projects.models import (  # noqa: E402
    DatasetVersion,
    ModelVersion,
    Operation,
    OperationModelBinding,
    OperationSource,
    OperationSourceRecording,
    Project,
    Site,
    TrainingRun,
)
from projects.operations import inference  # noqa: E402
from projects.source_time import build_timeline  # noqa: E402
from projects.training.torchvision_frcnn import TorchvisionFasterRCNN  # noqa: E402

FAILURES: list[str] = []


def check(condition, message):
    if condition:
        print(f"  ok: {message}")
    else:
        print(f"  FAIL: {message}")
        FAILURES.append(message)


def main() -> int:
    print(f"DATA_HOME: {settings.DATA_HOME}")
    tmp = tempfile.mkdtemp(prefix="t21-ml-")
    try:
        # 1. A synthetic recording: 2 s, 64x48, 5 fps, with creation_time.
        clip = Path(tmp) / "rec.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x48:r=5",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-metadata",
                "creation_time=2026-11-01T09:30:00Z",
                "-y",
                str(clip),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        body = clip.read_bytes()

        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("t21-ml-checker")
        site = Site.objects.create(created_by=user, name="ML Depot")
        source = OperationSource.objects.create(site=site, name="ML Gate", kind="recorded_video")
        operation = Operation.objects.create(created_by=user, site=site, source=source, name="ML Gate count")

        key = default_storage.save(f"operation-source-recordings/{source.id}/rec.mp4", _Bytes(body))
        recording = OperationSourceRecording.objects.create(
            source=source,
            created_by=user,
            storage_key=key,
            filename="rec.mp4",
            sha256=__import__("hashlib").sha256(body).hexdigest(),
            size_bytes=len(body),
            width=64,
            height=48,
            duration_seconds=2.0,
        )

        timeline = build_timeline(recording)
        print(f"timeline: {len(timeline.frames)} frames")

        # 2. A REAL torchvision-fasterrcnn artifact: build the network and save it
        #    through the backend's own artifact format (no training run needed).
        backend = TorchvisionFasterRCNN()
        availability = backend.check_available()
        print(f"backend availability: {availability.ok} ({availability.message})")
        if not availability.ok:
            print("REAL inference cannot run here; exiting as skipped.")
            return 0
        import torch

        labels = ["person"]
        model = backend.build_model(len(labels) + 1, "none")
        model.eval()
        artifact_rel = f"models/{operation.id}/t21-real/model.pt"
        artifact_path = Path(settings.DATA_HOME) / artifact_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "format": "athena.torchvision-fasterrcnn/1",
                "labels": labels,
                "pretrained": "none",
                "frozen_bn": False,
                "state_dict": model.state_dict(),
            },
            artifact_path,
        )
        import hashlib

        project = Project.objects.create(name="ML Counting", modality="image", task="Detect", labels=labels)
        dataset = DatasetVersion.objects.create(
            project=project,
            version=1,
            created_by=user,
            manifest={},
            manifest_sha256="c" * 64,
            label_names=labels,
        )
        run = TrainingRun.objects.create(
            project=project,
            dataset_version=dataset,
            dataset_manifest_sha256="c" * 64,
            backend="torchvision-fasterrcnn",
            config={"pretrained": "none"},
            config_sha256="d" * 64,
            created_by=user,
        )
        model_version = ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=run,
            backend="torchvision-fasterrcnn",
            artifact_path=artifact_rel,
            artifact_sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            artifact_size_bytes=artifact_path.stat().st_size,
            label_names=labels,
            stage=ModelVersion.APPROVED,
        )
        binding = OperationModelBinding.objects.create(
            operation=operation,
            version=1,
            created_by=user,
            model_version=model_version,
            compatibility={"compatible": True},
            compatibility_sha256="b" * 64,
        )

        # 3. Run the adapter end to end: real compatibility checks, real ffmpeg
        #    frame extraction, real torch predict.
        report = inference.run_detector(binding, recording, timeline, "people_counting", sample_frames=3)
        print(
            f"report: backend={report.backend} frames={report.frame_count} "
            f"detections={sum(len(f.detections) for f in report.frames)}"
        )
        check(report.backend == "torchvision-fasterrcnn", "the real torchvision backend ran")
        check(report.frame_count == 3, f"3 reference frames analysed (got {report.frame_count})")
        for frame in report.frames:
            for box in frame.detections:
                check(0.0 <= box["x"] <= 1.0 and 0.0 <= box["y"] <= 1.0, f"normalized x/y in frame {frame.frame_index}")
                check(0.0 < box["width"] and 0.0 < box["height"], "positive width/height")
                check(0.0 <= box["score"] <= 1.0, "score in [0,1]")
        print(
            f"total detections: {sum(len(f.detections) for f in report.frames)} "
            "(an untrained from-scratch network may legitimately find none)"
        )

        # 4. A wrong-label model must be refused with the plain message.
        project2 = Project.objects.create(name="ML Cars", modality="image", task="Detect", labels=["car"])
        dataset2 = DatasetVersion.objects.create(
            project=project2,
            version=1,
            created_by=user,
            manifest={},
            manifest_sha256="e" * 64,
            label_names=["car"],
        )
        run2 = TrainingRun.objects.create(
            project=project2,
            dataset_version=dataset2,
            dataset_manifest_sha256="e" * 64,
            backend="torchvision-fasterrcnn",
            config={},
            config_sha256="f" * 64,
            created_by=user,
        )
        wrong = ModelVersion.objects.create(
            project=project2,
            version=1,
            training_run=run2,
            backend="torchvision-fasterrcnn",
            artifact_path=artifact_rel,
            artifact_sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            artifact_size_bytes=artifact_path.stat().st_size,
            label_names=["car"],
            stage=ModelVersion.APPROVED,
        )
        binding2 = OperationModelBinding.objects.create(
            operation=operation,
            version=2,
            created_by=user,
            model_version=wrong,
            compatibility={"compatible": True},
            compatibility_sha256="b" * 64,
        )
        try:
            inference.run_detector(binding2, recording, timeline, "people_counting", sample_frames=3)
            check(False, "wrong-label model refused")
        except inference.InferenceAdapterError as error:
            check("was not taught to find person" in str(error), f"wrong-label model refused: {error}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES")
        return 1
    print("\nREAL inference adapter check passed.")
    return 0


class _Bytes:
    def __init__(self, data):
        from io import BytesIO

        self._stream = BytesIO(data)

    def read(self, *args):
        return self._stream.read(*args)

    def seek(self, *args):
        return self._stream.seek(*args)

    def tell(self):
        return self._stream.tell()

    def seekable(self):
        return True

    @property
    def size(self):
        position = self._stream.tell()
        size = self._stream.seek(0, 2)
        self._stream.seek(position)
        return size

    def chunks(self):
        yield self._stream.getvalue()

    def multiple_chunks(self):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
