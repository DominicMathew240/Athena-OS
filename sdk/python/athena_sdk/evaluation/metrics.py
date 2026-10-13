# SPDX-License-Identifier: Apache-2.0
"""Metric interface and reference implementations for evaluation harnesses.

Metrics are intentionally dependency-free. Framework-specific implementations
(scikit-learn, torchmetrics, etc.) can wrap these interfaces without forcing
a heavy dependency tree on the core SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Metric(ABC):
    """Abstract evaluation metric."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable metric identifier, e.g. ``exact_match``."""

    @abstractmethod
    def compute(self, predictions: list[Any], references: list[Any]) -> float:
        """Compute the metric over paired prediction/reference lists."""


class ExactMatchMetric(Metric):
    """Simple reference metric: fraction of predictions equal to references."""

    @property
    def name(self) -> str:
        return "exact_match"

    def compute(self, predictions: list[Any], references: list[Any]) -> float:
        if len(predictions) != len(references):
            raise ValueError("predictions and references must have the same length")
        if not predictions:
            return 0.0
        matches = sum(1 for p, r in zip(predictions, references) if p == r)
        return matches / len(predictions)


__all__ = ["ExactMatchMetric", "Metric"]
