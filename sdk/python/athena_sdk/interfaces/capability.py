# SPDX-License-Identifier: Apache-2.0
"""Adapter capability declaration and interface versioning.

Separated from `adapter.py` so `Runtime` and `DomainPack` implementations
can depend on the capability shape without importing the full adapter
contract.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from athena_sdk.contracts import Modality


class AdapterInterfaceVersion(str, Enum):
    """Version of the `ModelAdapter` interface contract itself.

    Distinct from `adapter_version`, which identifies the concrete
    implementation. Runtimes reject adapters declaring an unknown major
    interface version rather than guess method semantics.
    """

    V1 = "v1"


class AdapterCapabilities(BaseModel):
    """Static, implementation-independent capability declaration.

    A `ModelAdapter` exposes this so a `Runtime` can route work, validate
    compatibility, and surface adapter metadata without invoking the
    underlying model.
    """

    model_config = ConfigDict(extra="forbid")

    interface_version: AdapterInterfaceVersion = Field(
        default=AdapterInterfaceVersion.V1,
        description="ModelAdapter interface version this adapter implements.",
    )
    adapter_name: str = Field(..., description="Stable, human-readable adapter identifier.")
    adapter_version: str = Field(
        ..., description="Version of this concrete adapter implementation."
    )
    task_type: str = Field(..., description="e.g. 'detection', 'segmentation', 'classification'.")
    modalities: list[Modality] = Field(
        ..., min_length=1, description="Observation modalities this adapter can consume."
    )
    supports_batch: bool = Field(
        default=False, description="True if predict() batches internally rather than looping."
    )
    labels: list[str] = Field(
        default_factory=list, description="Known output label vocabulary, if fixed."
    )


__all__ = ["AdapterCapabilities", "AdapterInterfaceVersion"]
