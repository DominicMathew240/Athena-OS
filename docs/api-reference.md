# Athena Open Source API reference

This reference describes the public Python surfaces of the Athena SDK and CLI.
All code is interface or schema-only unless explicitly labeled as a reference
implementation.

## `athena_sdk.contracts`

Versioned Core contracts. All models inherit from `AthenaContractModel`, which
sets `extra="forbid"` so unknown fields are rejected at validation time.

| Class | Purpose |
| --- | --- |
| `Observation` | Immutable capture or derived input. |
| `Asset` | Physical asset or component. |
| `Model` | Identified model artifact with stage and checksum. |
| `AssetStateFinding` | Domain-neutral asserted or unknown asset state. |
| `Evidence` | Retained supporting material by reference. |
| `DomainPolicy` | Versioned domain knowledge (labels, severity map). |
| `RecommendedAction` | Traceable policy output tied to a finding. |

Enums: `ContractVersion`, `Modality`, `ModelStage`, `Severity`.

## `athena_sdk.client`

| Class | Purpose |
| --- | --- |
| `AthenaClient` | Top-level composition root over an `InferenceClient`. |
| `InferenceClient` | Abstract surface: Observations in, `AssetStateFinding`s out. |

## `athena_sdk.registry`

| Class | Purpose |
| --- | --- |
| `ModelRegistry` | Abstract surface for registering and resolving `Model` metadata. |
| `ModelHandle` | Protocol for an opaque loaded-model handle. |

## `athena_sdk.interfaces`

| Class | Purpose |
| --- | --- |
| `ModelAdapter` | Interface every model implementation implements. |
| `AdapterCapabilities` | Static capability declaration for an adapter. |
| `AdapterInterfaceVersion` | Version of the adapter interface contract itself. |
| `Runtime` | Abstract runtime that dispatches observations to adapters. |
| `LocalRuntime` | Reference in-process runtime implementation. |
| `DomainPack` | Interface that maps findings to recommended actions. |
| `assert_adapter_conforms` | Standard conformance battery for any `ModelAdapter` implementation. |
| `AdapterConformanceError` | Raised by `assert_adapter_conforms` on the first contract violation found. |

## `athena_sdk.evaluation`

| Class | Purpose |
| --- | --- |
| `EvaluationHarness` | Abstract harness: adapter + labeled dataset → result. |
| `LabeledDataset` | Dataset of labeled `Sample`s for evaluation. |
| `Sample` | Single labeled observation. |
| `EvaluationResult` | Metric values, sample count, and timestamp. |
| `Metric` | Abstract metric interface. |
| `ExactMatchMetric` | Simple reference metric implementation. |

## `athena_cli`

Entrypoint: `athena`

Commands:

| Command | Description |
| --- | --- |
| `athena validate-contracts` | Round-trip validate the seven Core contracts. |
| `athena run-example` | Run the bundled synthetic example pipeline. |
| `athena run-audio-example` | Run the bundled acoustic noise-analysis example pipeline. |
| `athena knowledge add <file-or-folder>` | Index local `.txt`, `.md`, and `.faq` files. |
| `athena knowledge list` | List indexed local documents. |
| `athena knowledge search <words>` | Find keyword-matched passages with file-and-line citations. |
| `athena knowledge remove <path>` | Remove a document and its indexed passages. |
| `athena --version` | Print CLI version. |

The knowledge commands store source text in a local SQLite database by
default (`~/.athena/knowledge.sqlite3`) and accept `--database` to select a
different location. Search returns matching source passages; it is not
semantic retrieval or AI-generated answering.

## Examples

- [`examples/synthetic_pipeline/`](../examples/synthetic_pipeline/) — in-memory
  registry, inference client, and example pipeline using synthetic data.
- [`examples/adapters/yolo_adapter.py`](../examples/adapters/yolo_adapter.py) —
  reference third-party adapter wrapping an Ultralytics YOLO detector behind the
  `ModelAdapter` interface (image modality).
- [`examples/adapters/audio_noise_adapter.py`](../examples/adapters/audio_noise_adapter.py) —
  from-scratch acoustic noise/SNR analysis adapter, no trained model required
  (audio/sensor-timeseries modality).
- [`examples/domain_packs/acoustic_condition_pack.py`](../examples/domain_packs/acoustic_condition_pack.py) —
  reference `DomainPack` pairing with the audio adapter above.
- [`examples/audio_pipeline/`](../examples/audio_pipeline/) — synthetic WAV
  generation and an end-to-end `LocalRuntime` run of the audio adapter +
  domain pack.

## Guides

- [Writing a custom adapter](guides/writing-a-custom-adapter.md) — implement,
  register, and conformance-test a `ModelAdapter` for your own model.
- [Bring your own model or data type](guides/bring-your-own-model.md) — for
  readers newer to ML tooling, or working with a data type other than
  images: where training happens, and how a trained model plugs in.
