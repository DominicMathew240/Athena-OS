# SPDX-License-Identifier: Apache-2.0
"""Open-source reference Acoustic Condition Monitoring Domain Pack for Athena.

Implements the `DomainPack` interface for the label taxonomy
`examples/adapters/audio_noise_adapter.py` produces -- pairs with that
adapter to demonstrate a second, non-image modality end to end (the Solar
Thermal pack pairs with image-based thermal findings; this one pairs with
audio/sensor-timeseries findings). Not tied to any real facility, standard,
or customer policy -- the action taxonomy below is illustrative.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from athena_sdk.contracts import (
    AssetStateFinding,
    DomainPolicy,
    RecommendedAction,
    Severity,
)
from athena_sdk.interfaces.domain_pack import DomainPack


class AcousticConditionDomainPack(DomainPack):
    """Reference domain pack interpreting acoustic noise-analysis findings."""

    def __init__(self, policy_id: str = "policy_acoustic_condition_reference_v1") -> None:
        self._policy = DomainPolicy(
            policy_id=policy_id,
            domain="acoustic_condition_monitoring",
            version="1.0.0",
            label_taxonomy=[
                "nominal",
                "elevated_noise_floor",
                "signal_masked_by_noise",
            ],
            severity_map={
                "nominal": Severity.INFO,
                "elevated_noise_floor": Severity.MEDIUM,
                "signal_masked_by_noise": Severity.HIGH,
            },
            effective_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            metadata={
                "description": (
                    "Reference decision policy for acoustic condition-monitoring "
                    "findings (background noise floor / signal-to-noise degradation)."
                ),
                "open_reference": True,
            },
        )

    @property
    def policy(self) -> DomainPolicy:
        return self._policy

    def evaluate(self, finding: AssetStateFinding) -> list[RecommendedAction]:
        """Evaluate an AssetStateFinding against the Acoustic Condition Domain Policy."""
        now = datetime.now(timezone.utc)
        label = finding.label.lower()

        if label == "signal_masked_by_noise":
            return [
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="urgent_inspect_acoustic_sensor_and_source",
                    description=(
                        "Signal is no longer distinguishable from background noise. "
                        "Dispatch a technician to inspect the sensor mounting and "
                        "check for a new, unrelated noise source near the asset."
                    ),
                    priority=Severity.HIGH,
                    created_at=now,
                    metadata={"estimated_snr_db": finding.metadata.get("estimated_snr_db")},
                )
            ]

        if label == "elevated_noise_floor":
            return [
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="schedule_acoustic_baseline_review",
                    description=(
                        "Background noise floor has risen above the reference "
                        "baseline. Schedule a review to confirm whether this is a "
                        "developing mechanical issue or an environmental change."
                    ),
                    priority=Severity.MEDIUM,
                    created_at=now,
                    metadata={"noise_floor_db": finding.metadata.get("noise_floor_db")},
                )
            ]

        if finding.is_unknown_state:
            return [
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="manual_reinspection_required",
                    description="Unknown or ambiguous acoustic state. Dispatch for manual review.",
                    priority=Severity.MEDIUM,
                    created_at=now,
                )
            ]

        # "nominal" and any other/unmapped label: log and continue routine monitoring.
        return [
            RecommendedAction(
                action_id=f"act_{uuid.uuid4().hex[:12]}",
                finding_id=finding.finding_id,
                policy_id=self._policy.policy_id,
                action_code="log_and_monitor",
                description=f"Recorded finding '{finding.label}'. Continue routine monitoring cycle.",
                priority=finding.severity,
                created_at=now,
            )
        ]


__all__ = ["AcousticConditionDomainPack"]
