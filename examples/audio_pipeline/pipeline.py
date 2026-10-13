# SPDX-License-Identifier: Apache-2.0
"""Example pipeline running a real (non-canned) reference `ModelAdapter` and
`DomainPack` for a non-image modality, end to end through `LocalRuntime`.

Unlike `examples/synthetic_pipeline/pipeline.py` (which stands in a canned
`InferenceClient` for illustration), this pipeline runs the actual
`AudioNoiseAnalysisAdapter` DSP analysis against generated audio, so the two
findings below genuinely differ based on their input rather than being
pre-selected. This is the pattern a non-technical user follows to bring
their own trained model for a new modality: generate/collect Observations,
run them through your adapter, optionally interpret the findings with a
DomainPack, done.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from athena_sdk.contracts import Modality, Observation
from athena_sdk.interfaces import LocalRuntime

from examples.adapters.audio_noise_adapter import AudioNoiseAnalysisAdapter
from examples.audio_pipeline.data import write_synthetic_wav
from examples.domain_packs.acoustic_condition_pack import AcousticConditionDomainPack

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DOMAIN = "acoustic_condition_monitoring"


def run_audio_pipeline() -> dict[str, object]:
    """Run the acoustic noise-analysis adapter against a nominal and a noisy
    synthetic recording, interpret both through the Acoustic Condition
    Domain Pack, and return everything a caller would want to inspect.
    """
    adapter = AudioNoiseAnalysisAdapter()
    domain_pack = AcousticConditionDomainPack()

    runtime = LocalRuntime()
    runtime.register_adapter(adapter.capabilities.adapter_name, adapter)
    runtime.register_domain_pack(_DOMAIN, domain_pack)

    with tempfile.TemporaryDirectory() as tmp_dir:
        nominal_path = Path(tmp_dir) / "nominal.wav"
        noisy_path = Path(tmp_dir) / "noisy.wav"
        write_synthetic_wav(str(nominal_path), noisy=False, seed=1)
        write_synthetic_wav(str(noisy_path), noisy=True, seed=2)

        observations = [
            Observation(
                observation_id="obs-audio-nominal-001",
                modality=Modality.SENSOR_TIMESERIES,
                captured_at=_NOW,
                source_uri=str(nominal_path),
                metadata={"asset_id": "asset-pump-01"},
            ),
            Observation(
                observation_id="obs-audio-noisy-001",
                modality=Modality.SENSOR_TIMESERIES,
                captured_at=_NOW,
                source_uri=str(noisy_path),
                metadata={"asset_id": "asset-pump-01"},
            ),
        ]

        findings, actions = runtime.run(
            model_id=adapter.capabilities.adapter_name,
            observations=observations,
            domain=_DOMAIN,
        )

    findings_by_observation = {f.observation_ids[0]: f for f in findings}
    return {
        "nominal_finding": findings_by_observation["obs-audio-nominal-001"],
        "noisy_finding": findings_by_observation["obs-audio-noisy-001"],
        "recommended_actions": actions,
    }


__all__ = ["run_audio_pipeline"]
