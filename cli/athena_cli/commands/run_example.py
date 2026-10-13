# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations


def run() -> int:
    from examples.synthetic_pipeline.pipeline import run_synthetic_pipeline

    finding = run_synthetic_pipeline()
    print("Synthetic pipeline produced AssetStateFinding:")
    print(finding.model_dump_json(indent=2))
    return 0
