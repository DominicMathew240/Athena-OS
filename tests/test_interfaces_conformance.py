# SPDX-License-Identifier: Apache-2.0
"""Interface-conformance and end-to-end tests for the interop surfaces
(`ModelAdapter`, `DomainPack`, `Runtime`) defined in `athena_sdk.interfaces`.

Covers TSD-13's acceptance criteria: the conformance battery rejects a
contract-violating adapter, a minimal from-scratch adapter passes it, and the
bundled third-party reference adapter (`examples/adapters/yolo_adapter.py`)
registers and runs through `LocalRuntime` end to end.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone

import pytest

from athena_sdk.contracts import (
    AssetStateFinding,
    DomainPolicy,
    Modality,
    Observation,
    RecommendedAction,
    Severity,
)
from athena_sdk.interfaces import (
    AdapterCapabilities,
    AdapterConformanceError,
    DomainPack,
    LocalRuntime,
    ModelAdapter,
    assert_adapter_conforms,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sample_observations() -> list[Observation]:
    return [
        Observation(
            observation_id="obs-conformance-001",
            modality=Modality.RGB_IMAGE,
            captured_at=_NOW,
            source_uri="synthetic://observations/obs-conformance-001.jpg",
            metadata={"asset_id": "asset-conformance-001"},
        )
    ]


class MinimalAdapter(ModelAdapter):
    """From-scratch adapter with no third-party dependency, used to prove
    the conformance battery works for adapters that aren't the bundled YOLO
    example."""

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_name="minimal-reference",
            adapter_version="0.1.0",
            task_type="classification",
            modalities=[Modality.RGB_IMAGE],
        )

    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        return [
            AssetStateFinding(
                finding_id=f"{obs.observation_id}-finding",
                asset_id=obs.metadata.get("asset_id", "unknown"),
                observation_ids=[obs.observation_id],
                model_id="minimal-reference",
                label="nominal",
                confidence=0.99,
                severity=Severity.INFO,
                created_at=_NOW,
            )
            for obs in observations
        ]


class EmptyInputViolatingAdapter(MinimalAdapter):
    """Deliberately breaks the `predict([]) == []` contract clause."""

    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return [
                AssetStateFinding(
                    finding_id="phantom",
                    asset_id="unknown",
                    observation_ids=["nonexistent"],
                    model_id="broken",
                    label="nominal",
                    created_at=_NOW,
                )
            ]
        return super().predict(observations)


class DanglingReferenceAdapter(MinimalAdapter):
    """Deliberately references an observation_id outside the input batch."""

    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return []
        return [
            AssetStateFinding(
                finding_id="dangling",
                asset_id="unknown",
                observation_ids=["not-in-the-batch"],
                model_id="broken",
                label="nominal",
                created_at=_NOW,
            )
        ]


class PassthroughDomainPack(DomainPack):
    def __init__(self, policy: DomainPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> DomainPolicy:
        return self._policy

    def evaluate(self, finding: AssetStateFinding) -> list[RecommendedAction]:
        return [
            RecommendedAction(
                action_id=f"{finding.finding_id}-action",
                finding_id=finding.finding_id,
                policy_id=self._policy.policy_id,
                action_code="log_only",
                description="Nominal finding, no action required.",
                priority=finding.severity,
                created_at=_NOW,
            )
        ]


def test_minimal_adapter_passes_conformance() -> None:
    findings = assert_adapter_conforms(MinimalAdapter(), _sample_observations())
    assert len(findings) == 1
    assert findings[0].observation_ids == ["obs-conformance-001"]


def test_conformance_rejects_predict_empty_violation() -> None:
    with pytest.raises(AdapterConformanceError, match=r"predict\(\[\]\)"):
        assert_adapter_conforms(EmptyInputViolatingAdapter(), _sample_observations())


def test_conformance_rejects_dangling_observation_reference() -> None:
    with pytest.raises(AdapterConformanceError, match="not present in the input batch"):
        assert_adapter_conforms(DanglingReferenceAdapter(), _sample_observations())


def test_conformance_requires_nonempty_sample_observations() -> None:
    with pytest.raises(ValueError):
        assert_adapter_conforms(MinimalAdapter(), [])


def test_local_runtime_end_to_end_with_domain_pack() -> None:
    runtime = LocalRuntime()
    runtime.register_adapter("minimal-reference", MinimalAdapter())

    policy = DomainPolicy(
        policy_id="policy-conformance-001",
        domain="generic",
        version="0.1.0",
        label_taxonomy=["nominal"],
        effective_at=_NOW,
    )
    runtime.register_domain_pack("generic", PassthroughDomainPack(policy))

    findings, actions = runtime.run(
        model_id="minimal-reference",
        observations=_sample_observations(),
        domain="generic",
    )

    assert len(findings) == 1
    assert len(actions) == 1
    assert actions[0].finding_id == findings[0].finding_id
    assert actions[0].policy_id == policy.policy_id


def test_local_runtime_raises_for_unknown_model_id() -> None:
    runtime = LocalRuntime()
    with pytest.raises(KeyError):
        runtime.run(model_id="does-not-exist", observations=_sample_observations())


_NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None


@pytest.mark.skipif(
    not _NUMPY_AVAILABLE,
    reason="numpy is an optional example dependency, not installed by default",
)
def test_audio_noise_adapter_conforms_and_runs_end_to_end(tmp_path) -> None:
    """Proves the interface is modality-agnostic: a from-scratch adapter with
    no trained model at all, declaring `Modality.SENSOR_TIMESERIES` instead
    of an image modality, still passes the same conformance battery and runs
    through `LocalRuntime` end to end. Uses a locally generated synthetic
    WAV file so this test needs no network access or external audio file."""
    from examples.adapters.audio_noise_adapter import AudioNoiseAnalysisAdapter
    from examples.audio_pipeline.data import write_synthetic_wav

    wav_path = tmp_path / "synthetic.wav"
    write_synthetic_wav(str(wav_path), noisy=False, seed=42)

    adapter = AudioNoiseAnalysisAdapter()
    assert_adapter_conforms(
        adapter,
        [
            Observation(
                observation_id="obs-audio-conformance-001",
                modality=Modality.SENSOR_TIMESERIES,
                captured_at=_NOW,
                source_uri=str(wav_path),
                metadata={"asset_id": "asset-audio-conformance-001"},
            )
        ],
    )

    runtime = LocalRuntime()
    runtime.register_adapter(adapter.capabilities.adapter_name, adapter)
    findings, _ = runtime.run(
        model_id=adapter.capabilities.adapter_name,
        observations=[
            Observation(
                observation_id="obs-audio-conformance-002",
                modality=Modality.SENSOR_TIMESERIES,
                captured_at=_NOW,
                source_uri=str(wav_path),
                metadata={"asset_id": "asset-audio-conformance-002"},
            )
        ],
    )
    assert isinstance(findings, list)
    assert len(findings) == 1
    assert findings[0].label in {"nominal", "elevated_noise_floor", "signal_masked_by_noise"}


_ULTRALYTICS_AVAILABLE = importlib.util.find_spec("ultralytics") is not None


@pytest.mark.skipif(
    not _ULTRALYTICS_AVAILABLE,
    reason="ultralytics is an optional example dependency, not installed by default",
)
def test_yolo_reference_adapter_conforms_and_runs_end_to_end(tmp_path) -> None:
    """Proves the interface works for a real, unmodified third-party model:
    the adapter is the entire integration surface, per TSD-13's acceptance
    criteria. Uses an untrained `.yaml` architecture spec and a locally
    generated image so this test needs no network access, downloaded
    weights, or external image fetch."""
    import numpy as np
    from examples.adapters.yolo_adapter import YOLOAdapter
    from PIL import Image

    image_path = tmp_path / "synthetic.jpg"
    Image.fromarray(np.zeros((64, 64, 3), dtype=np.uint8)).save(image_path)

    adapter = YOLOAdapter("yolov8n.yaml")
    assert_adapter_conforms(
        adapter,
        [
            Observation(
                observation_id="obs-yolo-001",
                modality=Modality.RGB_IMAGE,
                captured_at=_NOW,
                source_uri=str(image_path),
                metadata={"asset_id": "asset-yolo-001"},
            )
        ],
    )

    runtime = LocalRuntime()
    runtime.register_adapter(adapter.capabilities.adapter_name, adapter)
    findings, _ = runtime.run(
        model_id=adapter.capabilities.adapter_name,
        observations=[
            Observation(
                observation_id="obs-yolo-002",
                modality=Modality.RGB_IMAGE,
                captured_at=_NOW,
                source_uri=str(image_path),
                metadata={"asset_id": "asset-yolo-002"},
            )
        ],
    )
    assert isinstance(findings, list)
