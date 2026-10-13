# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from athena_sdk import contracts
from athena_sdk.contracts.v1 import __all__ as _all_names

CONTRACT_MODELS = [
    getattr(contracts, name)
    for name in _all_names
    if isinstance(getattr(contracts, name), type)
    and issubclass(getattr(contracts, name), contracts.AthenaContractModel)
    and getattr(contracts, name) is not contracts.AthenaContractModel
]


def run() -> int:
    from examples.synthetic_pipeline.data import build_synthetic_records

    records = build_synthetic_records()
    failures = []

    for model_cls in CONTRACT_MODELS:
        instance = records[model_cls.__name__]
        payload = instance.model_dump_json()
        restored = model_cls.model_validate_json(payload)
        if restored != instance:
            failures.append(model_cls.__name__)
        print(f"  ok  {model_cls.__name__}")

    if failures:
        print(f"FAILED round-trip for: {', '.join(failures)}")
        return 1

    print(f"All {len(CONTRACT_MODELS)} Core contracts round-tripped successfully.")
    return 0
