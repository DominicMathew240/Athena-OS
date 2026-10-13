# SPDX-License-Identifier: Apache-2.0
"""Reference third-party adapter: wraps an Ultralytics YOLO model behind
the Athena `ModelAdapter` interface.

Ultralytics/YOLO is an independent, third-party project unaffiliated with
Athena. This adapter exists to prove `ModelAdapter` works for community
models without any Athena-specific code inside the model itself — the
adapter is the entire integration surface.

`ultralytics` is an optional dependency of this example, not of
`athena-sdk`. Importing this module is safe even without it installed;
instantiating `YOLOAdapter` raises a clear `ImportError` if it is missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from athena_sdk.contracts import AssetStateFinding, Modality, Observation, Severity
from athena_sdk.interfaces.adapter import ModelAdapter
from athena_sdk.interfaces.capability import (
    AdapterCapabilities,
    AdapterInterfaceVersion,
)

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover - exercised via optional-dependency skip
    YOLO = None  # type: ignore[assignment,misc]


class YOLOAdapter(ModelAdapter):
    """`ModelAdapter` wrapper around an Ultralytics YOLO detector.

    `model_spec` is anything `ultralytics.YOLO(...)` accepts: a `.pt`
    weights path/name, or a `.yaml` architecture definition. `.yaml` specs
    build an untrained model from ultralytics' bundled architecture configs
    with no network fetch — useful for interface-conformance testing
    without downloading weights. Swap in a pretrained `.pt` for real
    detection.
    """

    ADAPTER_NAME = "yolo-ultralytics"

    def __init__(
        self,
        model_spec: str,
        *,
        adapter_version: str = "0.1.0",
        confidence_threshold: float = 0.25,
    ) -> None:
        if YOLO is None:
            raise ImportError(
                "The 'ultralytics' package is required to use YOLOAdapter. "
                "Install it with `pip install ultralytics`."
            )
        self._model = YOLO(model_spec)
        self._adapter_version = adapter_version
        self._confidence_threshold = confidence_threshold

    @property
    def capabilities(self) -> AdapterCapabilities:
        names: dict[int, str] = getattr(self._model, "names", None) or {}
        return AdapterCapabilities(
            interface_version=AdapterInterfaceVersion.V1,
            adapter_name=self.ADAPTER_NAME,
            adapter_version=self._adapter_version,
            task_type="detection",
            modalities=[Modality.RGB_IMAGE],
            supports_batch=True,
            labels=list(names.values()),
        )

    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return []

        sources = [obs.source_uri for obs in observations]
        results: list[Any] = self._model.predict(sources, verbose=False)

        findings: list[AssetStateFinding] = []
        for observation, result in zip(observations, results):
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                findings.append(self._no_detection_finding(observation))
                continue

            kept_any = False
            for i in range(len(boxes)):
                confidence = float(boxes.conf[i].item())
                if confidence < self._confidence_threshold:
                    continue
                kept_any = True
                cls_id = int(boxes.cls[i].item())
                label = result.names.get(cls_id, str(cls_id))
                findings.append(
                    AssetStateFinding(
                        finding_id=f"{observation.observation_id}-box-{i}",
                        asset_id=observation.metadata.get("asset_id", "unknown"),
                        observation_ids=[observation.observation_id],
                        model_id=self.ADAPTER_NAME,
                        label=label,
                        confidence=confidence,
                        severity=Severity.UNKNOWN,
                        is_unknown_state=False,
                        created_at=datetime.now(timezone.utc),
                        metadata={"bbox_xyxy": boxes.xyxy[i].tolist()},
                    )
                )

            if not kept_any:
                # Every box for this observation was below threshold.
                findings.append(self._no_detection_finding(observation))

        return findings

    def _no_detection_finding(self, observation: Observation) -> AssetStateFinding:
        return AssetStateFinding(
            finding_id=f"{observation.observation_id}-no-detection",
            asset_id=observation.metadata.get("asset_id", "unknown"),
            observation_ids=[observation.observation_id],
            model_id=self.ADAPTER_NAME,
            label="no_detection",
            confidence=None,
            severity=Severity.UNKNOWN,
            is_unknown_state=True,
            created_at=datetime.now(timezone.utc),
        )

    def health(self) -> bool:
        return self._model is not None


__all__ = ["YOLOAdapter"]
