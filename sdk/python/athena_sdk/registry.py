# SPDX-License-Identifier: Apache-2.0
"""Model-loading and registration interfaces.

These are abstract contracts only. Concrete registries (backed by a real
model store, artifact repository, or the closed-source Athena Forge control
plane) implement this interface outside this package.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from athena_sdk.contracts import Model


@runtime_checkable
class ModelHandle(Protocol):
    """Opaque handle to a loaded model artifact, returned by a ModelRegistry."""

    model_id: str


class ModelRegistry(ABC):
    """Interface for registering and resolving Model artifacts.

    Implementations decide storage, access control, and versioning policy.
    This package defines only the surface a client depends on.
    """

    @abstractmethod
    def register(self, model: Model) -> None:
        """Register a Model artifact's metadata. Does not move bytes."""

    @abstractmethod
    def get(self, model_id: str) -> Model:
        """Fetch a registered Model's metadata by id."""

    @abstractmethod
    def load(self, model_id: str) -> ModelHandle:
        """Resolve a registered Model to a loadable handle for inference."""

    @abstractmethod
    def list(self, *, task_type: str | None = None) -> list[Model]:
        """List registered models, optionally filtered by task type."""


__all__ = ["ModelHandle", "ModelRegistry"]
