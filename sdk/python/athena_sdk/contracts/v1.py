# SPDX-License-Identifier: Apache-2.0
"""Athena Open Industrial AI Standard — Core contracts, v1.

Seven versioned data contracts for describing an industrial AI inspection
pipeline end to end:

    Observation -> Asset -> Model -> AssetStateFinding -> Evidence
                 -> DomainPolicy -> RecommendedAction

These are schema-only definitions. They carry field validation and no
business logic, storage, inference, or policy-evaluation behavior — those
implementations live outside this open-source package.

Each model exposes a stable `contract_version` discriminator so consumers can
detect breaking changes across future v2+ releases.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ContractVersion(str, Enum):
    V1 = "v1"


class AthenaContractModel(BaseModel):
    """Shared base for all Athena Core contract types."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    contract_version: ContractVersion = Field(
        default=ContractVersion.V1,
        description="Wire version of this contract. Consumers must reject "
        "unknown major versions rather than guess field meaning.",
    )


class Modality(str, Enum):
    RGB_IMAGE = "rgb_image"
    THERMAL_IMAGE = "thermal_image"
    DEPTH = "depth"
    POINT_CLOUD = "point_cloud"
    SENSOR_TIMESERIES = "sensor_timeseries"
    OTHER = "other"


class Observation(AthenaContractModel):
    """An immutable, provenance-linked capture or derived input.

    An Observation is evidence input, not a model conclusion.
    """

    observation_id: str = Field(..., description="Globally unique observation identifier.")
    modality: Modality
    captured_at: datetime = Field(..., description="Capture timestamp, UTC.")
    source_uri: str = Field(
        ..., description="Locator for the raw or derived observation artifact."
    )
    sensor_id: str | None = Field(
        default=None, description="Identifier of the capturing sensor/device, if known."
    )
    site_id: str | None = Field(default=None)
    quality_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Optional normalized capture-quality signal."
    )
    metadata: dict = Field(default_factory=dict)


class Asset(AthenaContractModel):
    """A physical asset or component that can be the subject of a finding."""

    asset_id: str = Field(..., description="Globally unique asset identifier.")
    asset_type: str = Field(..., description="Domain-defined asset type, e.g. 'solar_panel'.")
    site_id: str | None = Field(default=None)
    parent_asset_id: str | None = Field(
        default=None, description="Parent asset identifier for hierarchical assets."
    )
    metadata: dict = Field(default_factory=dict)


class ModelStage(str, Enum):
    TRAINING = "training"
    EVALUATION = "evaluation"
    STAGED = "staged"
    RELEASED = "released"
    DEPRECATED = "deprecated"


class Model(AthenaContractModel):
    """An immutable, identified artifact produced by a training or conversion run.

    Loading, exporting, or passing a smoke test does not make a Model a
    release; release/approval status is tracked outside this contract.
    """

    model_id: str = Field(..., description="Globally unique model artifact identifier.")
    name: str
    stage: ModelStage
    task_type: str = Field(..., description="e.g. 'detection', 'segmentation', 'classification'.")
    version: str = Field(..., description="Semantic or content-addressed model version.")
    created_at: datetime
    checksum: str | None = Field(default=None, description="Content hash of the artifact.")
    metadata: dict = Field(default_factory=dict)


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AssetStateFinding(AthenaContractModel):
    """A domain-neutral record of an asserted or unknown asset/component state.

    Grounded in one or more Observations, produced by a Model, and optionally
    interpreted by a DomainPolicy. This is the open, minimal shape of the
    concept described in the Athena architecture glossary — it intentionally
    omits proprietary calibration, topology, and measurement-provenance
    fields that remain part of the closed implementation.
    """

    finding_id: str = Field(..., description="Globally unique finding identifier.")
    asset_id: str
    observation_ids: list[str] = Field(
        ..., min_length=1, description="Observations this finding is grounded in."
    )
    model_id: str = Field(..., description="Model artifact that produced this finding.")
    label: str = Field(..., description="Domain-defined state or defect label.")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    severity: Severity = Field(default=Severity.UNKNOWN)
    is_unknown_state: bool = Field(
        default=False,
        description="True when the model could not confidently classify the state, "
        "rather than asserting a specific known label.",
    )
    created_at: datetime
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class Evidence(AthenaContractModel):
    """Retained material supporting a specific statement or decision.

    Evidence references, not embeds, its underlying artifacts.
    """

    evidence_id: str = Field(..., description="Globally unique evidence identifier.")
    subject_type: str = Field(
        ..., description="Type of the entity this evidence supports, e.g. 'finding', 'release'."
    )
    subject_id: str
    kind: str = Field(..., description="e.g. 'observation_ref', 'metric_report', 'review_record'.")
    uri: str = Field(..., description="Locator for the retained evidence artifact.")
    created_at: datetime
    checksum: str | None = Field(default=None)
    metadata: dict = Field(default_factory=dict)


class DomainPolicy(AthenaContractModel):
    """Versioned domain knowledge used to interpret model output.

    Does not change Core contract semantics; specializes label vocabulary,
    severity mapping, and action policy for a domain (e.g. solar, assembly).
    """

    policy_id: str = Field(..., description="Globally unique domain policy identifier.")
    domain: str = Field(..., description="e.g. 'solar', 'assembly'.")
    version: str
    label_taxonomy: list[str] = Field(
        default_factory=list, description="Labels this policy knows how to interpret."
    )
    severity_map: dict[str, Severity] = Field(
        default_factory=dict, description="Label -> severity mapping owned by this policy."
    )
    effective_at: datetime
    metadata: dict = Field(default_factory=dict)


class RecommendedAction(AthenaContractModel):
    """A versioned policy output, not free-form model-generated advice.

    Always traceable to the DomainPolicy and AssetStateFinding that produced it.
    """

    action_id: str = Field(..., description="Globally unique recommended-action identifier.")
    finding_id: str
    policy_id: str = Field(..., description="DomainPolicy that authored this action.")
    action_code: str = Field(..., description="Domain-defined action identifier, e.g. 'inspect_manual'.")
    description: str
    priority: Severity = Field(default=Severity.UNKNOWN)
    created_at: datetime
    metadata: dict = Field(default_factory=dict)


__all__ = [
    "Asset",
    "AssetStateFinding",
    "AthenaContractModel",
    "ContractVersion",
    "DomainPolicy",
    "Evidence",
    "Modality",
    "Model",
    "ModelStage",
    "Observation",
    "RecommendedAction",
    "Severity",
]
