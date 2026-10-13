# Getting started with Athena Open Source

This guide shows how to install the Athena SDK and CLI, validate the Core
contracts, and run the bundled synthetic example pipeline.

## Install

From the `athena-oss/` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e sdk/python -e cli
```

The SDK depends only on **Pydantic v2**. The CLI depends on the SDK. Optional
example adapters (e.g. the Ultralytics YOLO adapter) require their own
dependencies and are not installed by default.

## Validate the Core contracts

```bash
athena validate-contracts
```

You should see all seven contracts round-trip successfully:

```
  ok  Observation
  ok  Asset
  ok  Model
  ok  AssetStateFinding
  ok  Evidence
  ok  DomainPolicy
  ok  RecommendedAction
All 7 Core contracts round-tripped successfully.
```

## Run the example pipeline

```bash
athena run-example
```

This exercises the SDK's `AthenaClient`, `InferenceClient`, and `ModelRegistry`
interfaces against a purely in-memory, synthetic implementation. No model
weights or proprietary runtime are required.

## Run the audio example pipeline

```bash
pip install numpy  # optional dependency of this example only
athena run-audio-example
```

This runs a second, complete reference pipeline for a different data
modality: a from-scratch acoustic noise-analysis adapter (no trained model,
no downloaded weights — just a short, readable signal-processing algorithm)
analyzes two generated recordings and reports genuinely different findings
for a "nominal" vs. a "noisy" one, then a Domain Pack turns those findings
into recommended actions. It exists to show the same `ModelAdapter` /
`DomainPack` / `Runtime` interfaces work unchanged for audio/sensor-timeseries
data, not just images — see
[Bring your own model or data type](guides/bring-your-own-model.md) for how
to do the same with your own trained model, on your own data type.

## Create a local document library

The early knowledge-library commands work with `.txt`, `.md`, and `.faq`
files. They store the text locally in `~/.athena/knowledge.sqlite3` by
default; no account or external model service is used.

```bash
athena knowledge add ./company-handbook.md
athena knowledge list
athena knowledge search "annual leave"
```

You can give `add` a folder to scan recursively, and use
`athena knowledge remove <path>` to delete an indexed source. Results are
keyword matches with file-and-line citations, not AI-generated answers. PDF import and graphical
document review are planned but are not available yet. Run
`athena knowledge --help` for all options.
The database is local plaintext, not encrypted. Do not import sensitive
documents into this early feature.

## Run the tests

```bash
pip install pytest
pytest
```

The test suite covers:

- Round-trip serialization for each Core contract.
- Rejection of unknown extra fields.
- Schema drift detection (checked-in JSON Schema must match the Pydantic
  models that generate them).
- CLI smoke tests (`--help`, `validate-contracts`, `run-example`,
  `run-audio-example`).
- Evaluation harness interface smoke tests.
- Model adapter interface conformance and end-to-end `Runtime` dispatch
  (including the bundled third-party YOLO reference adapter, when
  `ultralytics` is installed, and the bundled from-scratch acoustic
  noise-analysis adapter, when `numpy` is installed).

## Next steps

- Read the [architecture overview](architecture-overview.md) to understand the
  open/closed boundary.
- Browse the [API reference](api-reference.md).
- Look at the contracts in [`contracts/v1/`](../contracts/v1/).
- Bring your own model: [Writing a custom adapter](guides/writing-a-custom-adapter.md).
- New to ML tooling, or working with a data type Athena doesn't ship an
  example for yet? [Bring your own model or data type](guides/bring-your-own-model.md)
  walks through the whole loop — train with whatever tool fits your data,
  wrap it, run it — without assuming prior ML infrastructure experience.
