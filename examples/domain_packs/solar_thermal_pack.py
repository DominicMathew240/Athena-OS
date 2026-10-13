# SPDX-License-Identifier: Apache-2.0
"""Open-source reference Solar Thermal Domain Pack for Athena.

Implements the `DomainPack` interface per IEC TS 62446-3 guidelines for
aerial and field thermographic inspection of photovoltaic modules.
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


class SolarThermalDomainPack(DomainPack):
    """Reference Solar Thermal Domain Pack implementing IEC TS 62446-3 taxonomy."""

    def __init__(self, policy_id: str = "policy_solar_thermal_iec_v1") -> None:
        self._policy = DomainPolicy(
            policy_id=policy_id,
            domain="solar",
            version="1.0.0",
            label_taxonomy=[
                "hotspot_single_cell",
                "hotspot_multi_cell",
                "bypass_diode_activated",
                "string_disconnected",
                "pid_suspected",
                "cell_crack",
                "soiling_uniform",
                "soiling_localized",
                "vegetation_shading",
                "undetermined",
            ],
            severity_map={
                "hotspot_single_cell": Severity.HIGH,
                "hotspot_multi_cell": Severity.CRITICAL,
                "bypass_diode_activated": Severity.HIGH,
                "string_disconnected": Severity.CRITICAL,
                "pid_suspected": Severity.HIGH,
                "cell_crack": Severity.MEDIUM,
                "soiling_uniform": Severity.LOW,
                "soiling_localized": Severity.MEDIUM,
                "vegetation_shading": Severity.MEDIUM,
                "undetermined": Severity.UNKNOWN,
            },
            effective_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            metadata={
                "standard": "IEC TS 62446-3:2017",
                "description": "Photovoltaic system thermographic inspection decision policy",
                "open_reference": True,
            },
        )

    @property
    def policy(self) -> DomainPolicy:
        return self._policy

    def evaluate(self, finding: AssetStateFinding) -> list[RecommendedAction]:
        """Evaluate an AssetStateFinding against Solar Thermal Domain Policy."""
        actions: list[RecommendedAction] = []
        now = datetime.now(timezone.utc)
        label = finding.label.lower()

        if finding.is_unknown_state or label == "undetermined":
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="manual_reinspection_required",
                    description="Unknown or ambiguous thermal anomaly detected. Dispatch field inspector for visual and I-V curve check.",
                    priority=Severity.MEDIUM,
                    created_at=now,
                    metadata={"reason": "is_unknown_state_asserted"},
                )
            )
            return actions

        if label == "hotspot_multi_cell" or label == "string_disconnected":
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="urgent_isolate_and_replace",
                    description="Severe thermal runaway / multi-cell hotspot or disconnected string. Isolate electrical circuit immediately to prevent fire hazard.",
                    priority=Severity.CRITICAL,
                    created_at=now,
                    metadata={"iec_class": "Class 3 - Critical"},
                )
            )
        elif label == "hotspot_single_cell" or label == "bypass_diode_activated" or label == "pid_suspected":
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="scheduled_module_service",
                    description="Significant thermal delta observed. Schedule junction box / bypass diode inspection and module replacement at next maintenance window.",
                    priority=Severity.HIGH,
                    created_at=now,
                    metadata={"iec_class": "Class 2 - Medium/High"},
                )
            )
        elif label in ("soiling_uniform", "soiling_localized"):
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="schedule_panel_cleaning",
                    description="Soiling accumulation detected. Dispatch automated or robotic cleaning crew.",
                    priority=Severity.LOW if label == "soiling_uniform" else Severity.MEDIUM,
                    created_at=now,
                    metadata={"cleaning_type": "wet_wash"},
                )
            )
        elif label == "vegetation_shading":
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="grounds_vegetation_trimming",
                    description="Foliage or grass shading lower module edge. Dispatch groundskeeping crew.",
                    priority=Severity.MEDIUM,
                    created_at=now,
                )
            )
        elif label == "cell_crack":
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="monitor_degradation_trend",
                    description="Micro-crack detected without immediate thermal runaway. Log in digital twin and monitor temperature trend.",
                    priority=Severity.MEDIUM,
                    created_at=now,
                )
            )
        else:
            # Fallback policy rule
            actions.append(
                RecommendedAction(
                    action_id=f"act_{uuid.uuid4().hex[:12]}",
                    finding_id=finding.finding_id,
                    policy_id=self._policy.policy_id,
                    action_code="log_and_monitor",
                    description=f"Recorded finding '{finding.label}'. Continue regular monitoring cycle.",
                    priority=finding.severity,
                    created_at=now,
                )
            )

        return actions
