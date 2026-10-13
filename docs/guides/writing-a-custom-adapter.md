# Writing a custom adapter

A `ModelAdapter` is the entire integration surface between a model —
community, third-party, or proprietary — and the Athena Runtime. This guide
walks through implementing one, running it through `LocalRuntime`, and
proving it conforms to the interface contract.

Already trained a model and just need the adapter mechanics? Keep reading.
Not sure where training happens at all, or working with a data type other
than images? [Bring your own model or data type](bring-your-own-model.md)
starts one step earlier.

## The interface

```python
from athena_sdk.interfaces import ModelAdapter, AdapterCapabilities
```

`ModelAdapter` fixes three members:

| Member | Contract |
| --- | --- |
| `capabilities` (property) | Returns a static `AdapterCapabilities` declaration. Must not run inference to compute. |
| `predict(observations)` | `Observation`s in, `AssetStateFinding`s out. `predict([])` must return `[]`. |
| `health()` | Returns `bool`. Defaults to `True`; override for adapters with loadable/unloadable state. |

Nothing else is required. The interface has no opinion on model
architecture, weights format, training process, or inference backend — that
is precisely what keeps a community YOLO/SAM wrapper and a closed-source
Athena Family Model implementation-compatible behind the same contract.

## Step 1: declare capabilities

```python
from athena_sdk.contracts import Modality
from athena_sdk.interfaces import AdapterCapabilities, AdapterInterfaceVersion

class MyAdapter(ModelAdapter):
    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            interface_version=AdapterInterfaceVersion.V1,
            adapter_name="my-model",
            adapter_version="0.1.0",
            task_type="detection",
            modalities=[Modality.RGB_IMAGE],
            supports_batch=False,
            labels=["defect_a", "defect_b"],
        )
```

`interface_version` defaults to the current `AdapterInterfaceVersion` and
should only be set explicitly when pinning to an older major version. A
`Runtime` rejects adapters declaring an unknown major interface version
rather than guessing method semantics.

## Step 2: implement `predict`

```python
from athena_sdk.contracts import AssetStateFinding, Observation

class MyAdapter(ModelAdapter):
    ...
    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return []
        # Run inference, then map each result back to an AssetStateFinding
        # referencing the observation_id(s) it was grounded in.
        ...
```

Every `AssetStateFinding` you return must reference at least one
`observation_id` present in the input batch — the conformance suite (below)
checks this. If an adapter doesn't batch internally, loop over
`observations` yourself; callers may pass a single-element list regardless
of `capabilities.supports_batch`.

## Step 3: check conformance

`athena_sdk.interfaces.conformance` ships the standard battery any adapter
implementation should pass:

```python
from athena_sdk.interfaces import assert_adapter_conforms

def test_my_adapter_conforms():
    assert_adapter_conforms(MyAdapter(...), sample_observations)
```

It checks the interface contract, not model accuracy: capability shape,
`predict([]) == []`, output type/shape, and that findings only reference
observation ids from the input batch. See
[`examples/adapters/yolo_adapter.py`](../../examples/adapters/yolo_adapter.py)
(wraps a real third-party model, Ultralytics YOLO, image modality) and
[`examples/adapters/audio_noise_adapter.py`](../../examples/adapters/audio_noise_adapter.py)
(a from-scratch adapter with no trained model at all, audio/sensor-timeseries
modality) for two complete examples, and their conformance/end-to-end tests
in [`tests/test_interfaces_conformance.py`](../../tests/test_interfaces_conformance.py).

## Step 4: register with a Runtime

```python
from athena_sdk.interfaces import LocalRuntime

runtime = LocalRuntime()
runtime.register_adapter("my-model", MyAdapter())

findings, actions = runtime.run(
    model_id="my-model",
    observations=my_observations,
)
```

`LocalRuntime` is the open, dependency-free reference implementation —
sufficient for tests, examples, and single-process use. Optionally register
a `DomainPack` under a `domain` name to also get `RecommendedAction`s back
from `run(...)`; see [`athena_sdk.interfaces.domain_pack`](../../sdk/python/athena_sdk/interfaces/domain_pack.py).
Proprietary, distributed runtimes implement the same `Runtime` interface
without exposing scheduling, scaling, or deployment internals here.

## What this interface does not do

- It does not define how weights are stored, downloaded, or licensed. Your
  adapter's `__init__` handles that (see the YOLO example's optional-import
  guard for a pattern).
- It does not define a network/RPC transport. `Runtime.run` is an in-process
  call; a distributed runtime wraps the same adapter behind its own
  transport.
- It does not leak any Athena proprietary implementation detail — the
  interface package (`athena_sdk.interfaces`) contains only abstract
  contracts and the `LocalRuntime` reference implementation.
