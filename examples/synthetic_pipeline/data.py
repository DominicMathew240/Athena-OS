# SPDX-License-Identifier: Apache-2.0
"""Synthetic, illustrative-only data for the example pipeline and contract tests.

No production or customer-derived data appears here or anywhere else in this
package, per the Gate 1 open-source data policy.
"""

from __future__ import annotations

from datetime import datetime, timezone

from athena_sdk.contracts import (
    Asset,
    AssetStateFinding,
    DomainPolicy,
    Evidence,
    Modality,
    Model,
    ModelStage,
    Observation,
    RecommendedAction,
    Severity,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build_synthetic_records() -> dict[str, object]:
    """One synthetic instance of each of the seven Core contracts, keyed by class name."""

    observation = Observation(
        observation_id="obs-synthetic-001",
        modality=Modality.THERMAL_IMAGE,
        captured_at=_NOW,
        source_uri="synthetic://observations/obs-synthetic-001.tiff",
        sensor_id="synthetic-sensor-01",
        site_id="synthetic-site-01",
        quality_score=0.92,
    )

    asset = Asset(
        asset_id="asset-synthetic-001",
        asset_type="solar_panel",
        site_id="synthetic-site-01",
    )

    model = Model(
        model_id="model-synthetic-001",
        name="synthetic-hotspot-detector",
        stage=ModelStage.EVALUATION,
        task_type="detection",
        version="0.1.0-synthetic",
        created_at=_NOW,
        checksum="sha256:synthetic0000000000000000000000000000000000000000000000000000",
    )

    finding = AssetStateFinding(
        finding_id="finding-synthetic-001",
        asset_id=asset.asset_id,
        observation_ids=[observation.observation_id],
        model_id=model.model_id,
        label="thermal_hotspot",
        confidence=0.87,
        severity=Severity.MEDIUM,
        created_at=_NOW,
    )

    evidence = Evidence(
        evidence_id="evidence-synthetic-001",
        subject_type="finding",
        subject_id=finding.finding_id,
        kind="observation_ref",
        uri="synthetic://evidence/evidence-synthetic-001.json",
        created_at=_NOW,
    )

    policy = DomainPolicy(
        policy_id="policy-synthetic-solar-001",
        domain="solar",
        version="0.1.0-synthetic",
        label_taxonomy=["thermal_hotspot", "cell_crack", "delamination"],
        severity_map={"thermal_hotspot": Severity.MEDIUM},
        effective_at=_NOW,
    )

    action = RecommendedAction(
        action_id="action-synthetic-001",
        finding_id=finding.finding_id,
        policy_id=policy.policy_id,
        action_code="inspect_manual",
        description="Schedule a manual thermal re-inspection within 30 days.",
        priority=Severity.MEDIUM,
        created_at=_NOW,
    )

    return {
        "Observation": observation,
        "Asset": asset,
        "Model": model,
        "AssetStateFinding": finding,
        "Evidence": evidence,
        "DomainPolicy": policy,
        "RecommendedAction": action,
    }
