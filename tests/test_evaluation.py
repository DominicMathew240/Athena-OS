# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the open evaluation harness interfaces."""

from __future__ import annotations

from examples.synthetic_pipeline.data import build_synthetic_records

from athena_sdk.evaluation import (
    EvaluationResult,
    ExactMatchMetric,
    LabeledDataset,
    Sample,
)


def test_exact_match_metric() -> None:
    metric = ExactMatchMetric()
    assert metric.name == "exact_match"
    assert metric.compute(["a", "b", "c"], ["a", "b", "d"]) == 2 / 3
    assert metric.compute([], []) == 0.0


def test_evaluation_result_round_trips() -> None:
    records = build_synthetic_records()
    sample = Sample(
        observation=records["Observation"],
        ground_truth_label="thermal_hotspot",
    )
    dataset = LabeledDataset(
        dataset_id="synthetic-eval-001",
        samples=[sample],
    )
    result = EvaluationResult(
        dataset_id=dataset.dataset_id,
        model_id="model-synthetic-001",
        metrics={"exact_match": 1.0},
        sample_count=len(dataset.samples),
    )
    payload = result.model_dump_json()
    restored = EvaluationResult.model_validate_json(payload)
    assert restored.model_id == result.model_id
    assert restored.metrics == result.metrics
