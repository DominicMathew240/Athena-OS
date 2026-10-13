# Athena Core Contracts

The Athena open industrial AI standard: seven versioned, implementation-free
data contracts describing an inspection pipeline end to end.

```
Observation -> Asset -> Model -> AssetStateFinding -> Evidence
             -> DomainPolicy -> RecommendedAction
```

| Contract | Meaning |
| --- | --- |
| `Observation` | An immutable, provenance-linked capture or derived input. Evidence input, not a conclusion. |
| `Asset` | A physical asset or component that can be the subject of a finding. |
| `Model` | An immutable, identified model artifact. Loading or passing a smoke test does not make it a release. |
| `AssetStateFinding` | A domain-neutral asserted or unknown state of an asset, grounded in observations and produced by a model. |
| `Evidence` | Retained material supporting a specific statement or decision. References, does not embed, its artifacts. |
| `DomainPolicy` | Versioned domain knowledge (label taxonomy, severity mapping) used to interpret model output. |
| `RecommendedAction` | A versioned policy output traceable to a finding and a domain policy — never free-form model advice. |

## Source of truth

The **Pydantic models** in [`sdk/python/athena_sdk/contracts/v1.py`](../sdk/python/athena_sdk/contracts/v1.py)
are authoritative. The JSON Schema files in this directory (`v1/*.schema.json`)
are generated from them:

```bash
python contracts/generate_json_schema.py
```

CI fails if the checked-in schema files drift from the models (see
`tests/test_contracts.py::test_generated_schema_matches_models` and
`.github/workflows/ci.yml`).

## Versioning policy

- Each contract carries a `contract_version` field (currently `"v1"`).
- Adding an optional field is a **minor** change and does not bump the version.
- Removing, renaming, or narrowing a field, or changing a field's required-ness,
  is a **breaking** change and requires a new major contract version
  (`v2/`, published alongside `v1/` — never edited in place).
- `extra="forbid"` on every contract model means unknown fields fail
  validation rather than being silently dropped or ignored.

## Scope boundary

These are **schemas only** — field shape and validation. They contain no
storage, inference, policy-evaluation, or approval logic. Concrete
implementations (real registries, runtimes, evidence stores, domain policy
engines) are out of scope for this package by design; see
[`docs/architecture-overview.md`](../docs/architecture-overview.md) for the
open/closed boundary this repo sits behind.
