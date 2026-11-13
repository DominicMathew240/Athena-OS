# SPDX-License-Identifier: Apache-2.0
"""Example pipeline wiring the open SDK interfaces to synthetic, in-memory
implementations. This is a reference for SDK consumers, not a production
inference backend — real registries/runtimes are supplied by implementers.
"""

from __future__ import annotations

from athena_sdk.client import AthenaClient, InferenceClient
from athena_sdk.contracts import AssetStateFinding, Model, Observation
from athena_sdk.registry import ModelRegistry
from examples.synthetic_pipeline.data import build_synthetic_records


class InMemoryModelRegistry(ModelRegistry):
    def __init__(self) -> None:
        self._models: dict[str, Model] = {}

    def register(self, model: Model) -> None:
        self._models[model.model_id] = model

    def get(self, model_id: str) -> Model:
        return self._models[model_id]

    def load(self, model_id: str):
        return self._models[model_id]

    def list(self, *, task_type: str | None = None) -> list[Model]:
        values = list(self._models.values())
        if task_type is None:
            return values
        return [m for m in values if m.task_type == task_type]


class SyntheticInferenceClient(InferenceClient):
    """Returns a canned synthetic AssetStateFinding regardless of input.

    Stands in for a real inference backend so the example is runnable
    without any model weights or proprietary runtime.
    """

    def __init__(self, canned_finding: AssetStateFinding) -> None:
        self._canned_finding = canned_finding

    def infer(self, *, model_id: str, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return []
        return [self._canned_finding]

    def health(self) -> bool:
        return True


def run_synthetic_pipeline() -> AssetStateFinding:
    records = build_synthetic_records()

    registry = InMemoryModelRegistry()
    registry.register(records["Model"])

    client = AthenaClient(
        inference_client=SyntheticInferenceClient(canned_finding=records["AssetStateFinding"])
    )

    assert client.health()

    findings = client.infer(
        model_id=records["Model"].model_id,
        observations=[records["Observation"]],
    )
    return findings[0]
