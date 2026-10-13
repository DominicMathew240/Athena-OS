# SPDX-License-Identifier: Apache-2.0
"""Runtime interface.

The Runtime is the composition point that dispatches Observations to a
registered `ModelAdapter` and, optionally, interprets the resulting
findings through a registered `DomainPack`. This package fixes only the
interface and ships one dependency-free reference implementation
(`LocalRuntime`) — sufficient for interface-conformance testing, examples,
and single-process use. It is not the closed-source, distributed Athena
Runtime; proprietary runtimes implement the same `Runtime` interface
without exposing scheduling, scaling, or deployment internals here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from athena_sdk.contracts import AssetStateFinding, Observation, RecommendedAction
from athena_sdk.interfaces.adapter import ModelAdapter
from athena_sdk.interfaces.domain_pack import DomainPack


class Runtime(ABC):
    """Interface every runtime implements, regardless of deployment target."""

    @abstractmethod
    def register_adapter(self, model_id: str, adapter: ModelAdapter) -> None:
        """Register a ModelAdapter under a Model id known to a ModelRegistry."""

    @abstractmethod
    def register_domain_pack(self, domain: str, pack: DomainPack) -> None:
        """Register a DomainPack under a domain name."""

    @abstractmethod
    def run(
        self,
        *,
        model_id: str,
        observations: list[Observation],
        domain: str | None = None,
    ) -> tuple[list[AssetStateFinding], list[RecommendedAction]]:
        """Run inference for `model_id`, optionally interpreted by a DomainPack.

        Raises KeyError if `model_id` (or `domain`, when given) is not
        registered. Returns (findings, actions); `actions` is empty when
        `domain` is None.
        """


class LocalRuntime(Runtime):
    """Reference in-process Runtime implementation.

    Dispatches synchronously to registered adapters/domain packs by id
    lookup in local dicts. Suitable for tests, examples, and single-process
    deployments — not a distributed, multi-tenant, or queued runtime.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {}
        self._domain_packs: dict[str, DomainPack] = {}

    def register_adapter(self, model_id: str, adapter: ModelAdapter) -> None:
        self._adapters[model_id] = adapter

    def register_domain_pack(self, domain: str, pack: DomainPack) -> None:
        self._domain_packs[domain] = pack

    def run(
        self,
        *,
        model_id: str,
        observations: list[Observation],
        domain: str | None = None,
    ) -> tuple[list[AssetStateFinding], list[RecommendedAction]]:
        if model_id not in self._adapters:
            raise KeyError(f"No adapter registered for model_id={model_id!r}")
        findings = self._adapters[model_id].predict(observations)

        actions: list[RecommendedAction] = []
        if domain is not None:
            if domain not in self._domain_packs:
                raise KeyError(f"No domain pack registered for domain={domain!r}")
            pack = self._domain_packs[domain]
            for finding in findings:
                actions.extend(pack.evaluate(finding))
        return findings, actions


__all__ = ["LocalRuntime", "Runtime"]
