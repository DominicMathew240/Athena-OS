# SPDX-License-Identifier: Apache-2.0
"""Model adapter interface.

Any model implementation — community, third-party, or Athena proprietary —
plugs into the Athena Runtime by implementing `ModelAdapter`. The interface
fixes only the boundary: a static capability declaration, and
Observations-in / AssetStateFindings-out for inference. It has no opinion
on model architecture, weights format, training process, or inference
backend, so the same contract can wrap a community YOLO/SAM checkpoint or
a closed-source Athena Family Model without either side seeing the other's
implementation details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from athena_sdk.contracts import AssetStateFinding, Observation
from athena_sdk.interfaces.capability import AdapterCapabilities


class ModelAdapter(ABC):
    """Interface every model adapter implements, regardless of origin."""

    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Static capability declaration for this adapter instance.

        Must not require running inference to compute.
        """

    @abstractmethod
    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        """Run inference over a batch of Observations.

        Contract: an empty input list returns an empty output list. Callers
        may pass a single-element list regardless of
        `capabilities.supports_batch`; adapters that do not batch internally
        loop over `observations` themselves.
        """

    def health(self) -> bool:
        """Return True if the adapter is ready to serve `predict`.

        Default assumes readiness; override for adapters with loadable or
        unloadable model state (e.g. lazy weight loading, remote backends).
        """
        return True


__all__ = ["ModelAdapter"]
