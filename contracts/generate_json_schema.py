# SPDX-License-Identifier: Apache-2.0
"""Regenerate contracts/v1/*.schema.json from the athena_sdk Pydantic models.

The Pydantic models in sdk/python/athena_sdk/contracts/v1.py are the source
of truth. This script only exports them; it contains no contract logic of
its own. Run after any change to the models:

    python contracts/generate_json_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))

from athena_sdk.contracts import v1  # noqa: E402

CONTRACT_CLASS_NAMES = [
    "Observation",
    "Asset",
    "Model",
    "AssetStateFinding",
    "Evidence",
    "DomainPolicy",
    "RecommendedAction",
]

_FILE_NAME = {
    "AssetStateFinding": "asset_state_finding",
    "DomainPolicy": "domain_policy",
    "RecommendedAction": "recommended_action",
}


def _file_stem(class_name: str) -> str:
    return _FILE_NAME.get(class_name, class_name.lower())


def main() -> None:
    out_dir = Path(sys.argv[1]) / "v1" if len(sys.argv) > 1 else REPO_ROOT / "contracts" / "v1"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in CONTRACT_CLASS_NAMES:
        model_cls = getattr(v1, name)
        schema = model_cls.model_json_schema()
        schema["$id"] = f"https://athena-forge.dev/schemas/v1/{_file_stem(name)}.schema.json"
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"

        out_path = out_dir / f"{_file_stem(name)}.schema.json"
        out_path.write_text(json.dumps(schema, indent=2, sort_keys=False) + "\n")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
