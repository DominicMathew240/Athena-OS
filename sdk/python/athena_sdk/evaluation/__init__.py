# SPDX-License-Identifier: Apache-2.0
"""Evaluation harness interfaces for the Athena open industrial AI standard.

This package defines the boundary between a ``ModelAdapter`` and a dataset of
labeled observations. Concrete benchmark runners, data loaders, and metric
implementations live outside this open-source package.
"""

from athena_sdk.evaluation.harness import (
    EvaluationHarness,
    EvaluationResult,
    LabeledDataset,
    Sample,
)
from athena_sdk.evaluation.metrics import ExactMatchMetric, Metric

__all__ = [
    "EvaluationHarness",
    "EvaluationResult",
    "ExactMatchMetric",
    "LabeledDataset",
    "Metric",
    "Sample",
]
