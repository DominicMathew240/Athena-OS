# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations


def run() -> int:
    from examples.adapters import audio_noise_adapter
    from examples.audio_pipeline.pipeline import run_audio_pipeline

    if audio_noise_adapter.np is None:
        # Checked explicitly (rather than catching ImportError around the
        # pipeline run) so an unrelated ImportError elsewhere in the call
        # path can never be mislabeled as "numpy is missing".
        print(
            "This example requires the optional 'numpy' dependency used by "
            "AudioNoiseAnalysisAdapter. Install it with `pip install numpy`."
        )
        return 1

    result = run_audio_pipeline()
    print("Nominal recording -> AssetStateFinding:")
    print(result["nominal_finding"].model_dump_json(indent=2))
    print()
    print("Noisy recording -> AssetStateFinding:")
    print(result["noisy_finding"].model_dump_json(indent=2))
    print()
    print("RecommendedActions from the Acoustic Condition Domain Pack:")
    for action in result["recommended_actions"]:
        print(action.model_dump_json(indent=2))
    return 0
