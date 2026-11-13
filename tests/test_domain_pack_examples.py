# SPDX-License-Identifier: Apache-2.0
"""Tests for open-source reference domain packs."""

import uuid
from datetime import datetime, timezone

from examples.domain_packs.acoustic_condition_pack import AcousticConditionDomainPack
from examples.domain_packs.solar_thermal_pack import SolarThermalDomainPack

from athena_sdk.contracts import AssetStateFinding, Severity
from athena_sdk.interfaces.domain_pack import DomainPack


def test_solar_thermal_domain_pack_implements_interface():
    pack = SolarThermalDomainPack()
    assert isinstance(pack, DomainPack)
    policy = pack.policy
    assert policy.domain == "solar"
    assert policy.version == "1.0.0"
    assert "hotspot_multi_cell" in policy.label_taxonomy
    assert policy.severity_map["hotspot_multi_cell"] == Severity.CRITICAL


def test_solar_thermal_evaluation_critical_hotspot():
    pack = SolarThermalDomainPack()
    finding = AssetStateFinding(
        finding_id=f"fnd_{uuid.uuid4().hex[:10]}",
        asset_id="asset_pv_string_04",
        observation_ids=["obs_thermal_001"],
        model_id="mod_thermal_pv_v1",
        label="hotspot_multi_cell",
        confidence=0.94,
        severity=Severity.CRITICAL,
        is_unknown_state=False,
        created_at=datetime.now(timezone.utc),
    )

    actions = pack.evaluate(finding)
    assert len(actions) == 1
    action = actions[0]
    assert action.finding_id == finding.finding_id
    assert action.policy_id == pack.policy.policy_id
    assert action.action_code == "urgent_isolate_and_replace"
    assert action.priority == Severity.CRITICAL


def test_solar_thermal_evaluation_unknown_state():
    pack = SolarThermalDomainPack()
    finding = AssetStateFinding(
        finding_id=f"fnd_{uuid.uuid4().hex[:10]}",
        asset_id="asset_pv_string_05",
        observation_ids=["obs_thermal_002"],
        model_id="mod_thermal_pv_v1",
        label="undetermined",
        confidence=0.35,
        severity=Severity.UNKNOWN,
        is_unknown_state=True,
        created_at=datetime.now(timezone.utc),
    )

    actions = pack.evaluate(finding)
    assert len(actions) == 1
    action = actions[0]
    assert action.action_code == "manual_reinspection_required"
    assert action.priority == Severity.MEDIUM


def test_acoustic_condition_domain_pack_implements_interface():
    pack = AcousticConditionDomainPack()
    assert isinstance(pack, DomainPack)
    policy = pack.policy
    assert policy.domain == "acoustic_condition_monitoring"
    assert "signal_masked_by_noise" in policy.label_taxonomy
    assert policy.severity_map["signal_masked_by_noise"] == Severity.HIGH


def test_acoustic_condition_evaluation_signal_masked_by_noise():
    pack = AcousticConditionDomainPack()
    finding = AssetStateFinding(
        finding_id=f"fnd_{uuid.uuid4().hex[:10]}",
        asset_id="asset_pump_01",
        observation_ids=["obs_audio_001"],
        model_id="audio-noise-analysis-reference",
        label="signal_masked_by_noise",
        confidence=0.8,
        severity=Severity.HIGH,
        is_unknown_state=False,
        created_at=datetime.now(timezone.utc),
        metadata={"estimated_snr_db": 2.1},
    )

    actions = pack.evaluate(finding)
    assert len(actions) == 1
    action = actions[0]
    assert action.finding_id == finding.finding_id
    assert action.policy_id == pack.policy.policy_id
    assert action.action_code == "urgent_inspect_acoustic_sensor_and_source"
    assert action.priority == Severity.HIGH
    assert action.metadata["estimated_snr_db"] == 2.1


def test_acoustic_condition_evaluation_elevated_noise_floor():
    pack = AcousticConditionDomainPack()
    finding = AssetStateFinding(
        finding_id=f"fnd_{uuid.uuid4().hex[:10]}",
        asset_id="asset_pump_01",
        observation_ids=["obs_audio_003"],
        model_id="audio-noise-analysis-reference",
        label="elevated_noise_floor",
        confidence=0.7,
        severity=Severity.MEDIUM,
        is_unknown_state=False,
        created_at=datetime.now(timezone.utc),
        metadata={"noise_floor_db": -22.5},
    )

    actions = pack.evaluate(finding)
    assert len(actions) == 1
    action = actions[0]
    assert action.finding_id == finding.finding_id
    assert action.policy_id == pack.policy.policy_id
    assert action.action_code == "schedule_acoustic_baseline_review"
    assert action.priority == Severity.MEDIUM
    assert action.metadata["noise_floor_db"] == -22.5


def test_acoustic_condition_evaluation_nominal():
    pack = AcousticConditionDomainPack()
    finding = AssetStateFinding(
        finding_id=f"fnd_{uuid.uuid4().hex[:10]}",
        asset_id="asset_pump_01",
        observation_ids=["obs_audio_002"],
        model_id="audio-noise-analysis-reference",
        label="nominal",
        confidence=0.9,
        severity=Severity.INFO,
        is_unknown_state=False,
        created_at=datetime.now(timezone.utc),
    )

    actions = pack.evaluate(finding)
    assert len(actions) == 1
    assert actions[0].action_code == "log_and_monitor"
    assert actions[0].priority == Severity.INFO
