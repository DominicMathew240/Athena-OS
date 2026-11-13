# SPDX-License-Identifier: Apache-2.0
"""Round-trip serialize/deserialize tests for each of the seven Core contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from examples.synthetic_pipeline.data import build_synthetic_records
from pydantic import ValidationError

from athena_sdk import contracts

RECORDS = build_synthetic_records()
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", sorted(RECORDS.keys()))
def test_contract_round_trips(name: str) -> None:
    instance = RECORDS[name]
    model_cls = type(instance)

    payload = instance.model_dump_json()
    restored = model_cls.model_validate_json(payload)

    assert restored == instance
    assert restored.contract_version == contracts.ContractVersion.V1


def test_all_seven_contracts_covered() -> None:
    assert set(RECORDS.keys()) == {
        "Observation",
        "Asset",
        "Model",
        "AssetStateFinding",
        "Evidence",
        "DomainPolicy",
        "RecommendedAction",
    }


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        contracts.Asset(
            asset_id="a1",
            asset_type="solar_panel",
            unexpected_field="not allowed",
        )


def test_generated_schema_matches_models(tmp_path) -> None:
    """Guards against hand-editing contracts/v1/*.schema.json out of sync
    with the Pydantic models that generate them."""

    generated_dir = tmp_path / "generated"
    script = REPO_ROOT / "contracts" / "generate_json_schema.py"

    result = subprocess.run(
        [sys.executable, str(script), str(generated_dir)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    checked_in_dir = REPO_ROOT / "contracts" / "v1"
    for checked_in_file in checked_in_dir.glob("*.schema.json"):
        generated_file = generated_dir / "v1" / checked_in_file.name
        assert generated_file.exists(), f"missing generated file for {checked_in_file.name}"
        assert json.loads(generated_file.read_text()) == json.loads(
            checked_in_file.read_text()
        ), f"{checked_in_file.name} is out of date; re-run contracts/generate_json_schema.py"


def test_asset_state_finding_requires_at_least_one_observation() -> None:
    with pytest.raises(ValidationError):
        contracts.AssetStateFinding(
            finding_id="f1",
            asset_id="a1",
            observation_ids=[],
            model_id="m1",
            label="thermal_hotspot",
            created_at=RECORDS["AssetStateFinding"].created_at,
        )
