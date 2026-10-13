# SPDX-License-Identifier: Apache-2.0
"""Inference client interface.

Defines the request/response shape an Athena-compatible inference backend
must support. This package ships no inference implementation — only the
contract types and this abstract client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from athena_sdk.contracts import AssetStateFinding, Observation


class InferenceClient(ABC):
    """Interface for running a registered Model against Observations.

    Implementations may call a local runtime, a remote service, or the
    closed-source Athena Runtime. This package only fixes the contract
    boundary: Observations in, AssetStateFindings out.
    """

    @abstractmethod
    def infer(
        self,
        *,
        model_id: str,
        observations: list[Observation],
    ) -> list[AssetStateFinding]:
        """Run inference for `model_id` over the given Observations."""

    @abstractmethod
    def health(self) -> bool:
        """Return True if the backing inference endpoint is reachable and ready."""


class AthenaClient:
    """Top-level developer-facing SDK entrypoint.

    Thin composition root over a ModelRegistry and an InferenceClient. Holds
    no business logic of its own — it exists so SDK users have one object to
    construct and pass around.
    """

    def __init__(self, *, inference_client: InferenceClient) -> None:
        self._inference_client = inference_client

    def infer(
        self,
        *,
        model_id: str,
        observations: list[Observation],
    ) -> list[AssetStateFinding]:
        return self._inference_client.infer(model_id=model_id, observations=observations)

    def health(self) -> bool:
        return self._inference_client.health()


__all__ = ["AthenaClient", "InferenceClient"]
