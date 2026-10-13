# SPDX-License-Identifier: Apache-2.0
"""Interface-conformance battery for `ModelAdapter` implementations.

Any adapter — community, third-party, or Athena proprietary — should pass
`assert_adapter_conforms` against a small sample of real `Observation`s for
that adapter's declared modality. This is the standard battery referenced by
TSD-13's acceptance criteria: it checks the contract `ModelAdapter` promises,
not any particular model's accuracy.

Usage:

    from athena_sdk.interfaces.conformance import assert_adapter_conforms

    def test_my_adapter_conforms():
        assert_adapter_conforms(MyAdapter(...), sample_observations)
"""

from __future__ import annotations

from athena_sdk.contracts import AssetStateFinding, Observation
from athena_sdk.interfaces.adapter import ModelAdapter
from athena_sdk.interfaces.capability import (
    AdapterCapabilities,
    AdapterInterfaceVersion,
)


class AdapterConformanceError(AssertionError):
    """Raised when a `ModelAdapter` implementation violates the interface contract."""


def assert_adapter_conforms(
    adapter: ModelAdapter, observations: list[Observation]
) -> list[AssetStateFinding]:
    """Assert `adapter` satisfies the `ModelAdapter` contract.

    `observations` must be non-empty and use a modality declared in the
    adapter's `capabilities.modalities`. Returns the findings from the
    non-empty-input call so callers can layer adapter-specific assertions on
    top without invoking `predict` twice.

    Raises `AdapterConformanceError` on the first violation found.
    """
    if not observations:
        raise ValueError("assert_adapter_conforms requires at least one sample Observation")

    _check_capabilities(adapter)
    _check_empty_input_returns_empty_output(adapter)
    findings = _check_predict_output_shape(adapter, observations)
    _check_health_returns_bool(adapter)
    return findings


def _check_capabilities(adapter: ModelAdapter) -> None:
    capabilities = adapter.capabilities
    if not isinstance(capabilities, AdapterCapabilities):
        raise AdapterConformanceError(
            f"{adapter!r}.capabilities must return AdapterCapabilities, "
            f"got {type(capabilities)!r}"
        )
    if capabilities.interface_version not in AdapterInterfaceVersion:
        raise AdapterConformanceError(
            f"{adapter!r}.capabilities.interface_version "
            f"{capabilities.interface_version!r} is not a known AdapterInterfaceVersion"
        )
    if not capabilities.modalities:
        raise AdapterConformanceError(
            f"{adapter!r}.capabilities.modalities must declare at least one modality"
        )


def _check_empty_input_returns_empty_output(adapter: ModelAdapter) -> None:
    result = adapter.predict([])
    if result != []:
        raise AdapterConformanceError(
            f"{adapter!r}.predict([]) must return [], got {result!r}"
        )


def _check_predict_output_shape(
    adapter: ModelAdapter, observations: list[Observation]
) -> list[AssetStateFinding]:
    findings = adapter.predict(observations)
    if not isinstance(findings, list):
        raise AdapterConformanceError(
            f"{adapter!r}.predict(...) must return a list, got {type(findings)!r}"
        )

    known_observation_ids = {obs.observation_id for obs in observations}
    for finding in findings:
        if not isinstance(finding, AssetStateFinding):
            raise AdapterConformanceError(
                f"{adapter!r}.predict(...) returned a non-AssetStateFinding element: {finding!r}"
            )
        if not finding.observation_ids:
            raise AdapterConformanceError(
                f"AssetStateFinding {finding.finding_id!r} has no observation_ids"
            )
        unknown = set(finding.observation_ids) - known_observation_ids
        if unknown:
            raise AdapterConformanceError(
                f"AssetStateFinding {finding.finding_id!r} references observation_ids "
                f"{unknown!r} not present in the input batch"
            )
    return findings


def _check_health_returns_bool(adapter: ModelAdapter) -> None:
    healthy = adapter.health()
    if not isinstance(healthy, bool):
        raise AdapterConformanceError(
            f"{adapter!r}.health() must return bool, got {type(healthy)!r}"
        )


__all__ = ["AdapterConformanceError", "assert_adapter_conforms"]
