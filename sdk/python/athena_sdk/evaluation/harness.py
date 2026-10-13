# SPDX-License-Identifier: Apache-2.0
"""Evaluation harness interface.

An ``EvaluationHarness`` runs a ``ModelAdapter`` against a labeled dataset and
returns an ``EvaluationResult``. This is the open boundary; closed-source
benchmark runners and dataset registries implement it without exposing their
internals here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from athena_sdk.contracts import Observation
from athena_sdk.interfaces.adapter import ModelAdapter


class Sample(BaseModel):
    """A single labeled evaluation sample."""

    model_config = ConfigDict(extra="forbid")

    observation: Observation
    ground_truth_label: str = Field(..., description="Expected state/defect label.")
    asset_id: str | None = Field(
        default=None, description="Optional asset identifier for hierarchical metrics."
    )


class LabeledDataset(BaseModel):
    """A dataset of labeled samples for evaluation."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(..., description="Globally unique dataset identifier.")
    samples: list[Sample] = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    """Result of running an evaluation harness."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    model_id: str
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Metric name -> computed value."
    )
    sample_count: int
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationHarness(ABC):
    """Interface every evaluation harness implements."""

    @abstractmethod
    def evaluate(
        self,
        *,
        adapter: ModelAdapter,
        dataset: LabeledDataset,
    ) -> EvaluationResult:
        """Run ``adapter`` over ``dataset`` and return computed metrics."""


__all__ = ["EvaluationHarness", "EvaluationResult", "LabeledDataset", "Sample"]
