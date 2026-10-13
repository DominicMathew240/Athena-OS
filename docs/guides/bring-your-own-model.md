# Bring your own model or data type

[Writing a custom adapter](writing-a-custom-adapter.md) assumes you already
have a trained model in hand. This guide is for two audiences one step
earlier than that: **people newer to the ML tooling landscape**, and
**people whose data isn't images** (audio, other sensor timeseries, point
clouds, ...). It answers a more basic question first: *where does training
even happen, and how does what I train end up running inside Athena?*

## The short answer

Athena Open Source does not train models, host models, or ship any
pretrained weights — see [architecture overview](../architecture-overview.md).
What it ships is a small, stable interface (`ModelAdapter`) that your
already-trained model plugs into. So the workflow is always:

1. **Pick (or already have) a tool that trains on your kind of data.** You
   are not restricted to any Athena-specific tool here — use whatever the
   wider ML ecosystem offers for your data type (see the table below for a
   starting point per modality).
2. **Train it, however that tool normally works.** This step happens
   entirely outside Athena, with your own data, on your own machine or cloud
   account. Athena never sees this step and imposes no format on it.
3. **Wrap the trained model in a `ModelAdapter`** — a small Python class with
   one property (`capabilities`) and one method (`predict`). See
   [Writing a custom adapter](writing-a-custom-adapter.md) for the mechanics.
4. **Run it through `LocalRuntime`** to get back typed `AssetStateFinding`s,
   optionally interpreted by a `DomainPack` into `RecommendedAction`s.

Steps 3 and 4 are the same regardless of what your model does or what data
type it consumes — that's the whole point of the interface.

## Two complete, runnable examples

Rather than read this abstractly, run both bundled reference pipelines and
read their source — each is under 200 lines and has no proprietary code in
it:

| Data type | Command | Adapter source | What it does |
| --- | --- | --- | --- |
| Images (`RGB_IMAGE`) | `athena run-example` | [`examples/adapters/yolo_adapter.py`](../../examples/adapters/yolo_adapter.py) | Wraps a real, independent, pretrained-or-untrained Ultralytics YOLO model. |
| Audio / sensor timeseries (`SENSOR_TIMESERIES`) | `athena run-audio-example` | [`examples/adapters/audio_noise_adapter.py`](../../examples/adapters/audio_noise_adapter.py) | A from-scratch noise-floor/SNR analysis — no trained model at all, to prove you don't need one to get started. |

Both register a `ModelAdapter` and a `DomainPack` with `LocalRuntime` and
produce real `AssetStateFinding`/`RecommendedAction` output from generated,
synthetic input — nothing about either example requires downloading a
dataset or model weights first.

## Which `Modality` is my data?

`Observation.modality` (in [`athena_sdk.contracts`](../../sdk/python/athena_sdk/contracts/v1.py))
is a closed set. Pick the closest fit; when nothing fits well, use `OTHER`
and describe the real shape in `Observation.metadata`:

| Your data | `Modality` |
| --- | --- |
| Photos, camera frames, screenshots | `RGB_IMAGE` |
| Thermal/IR camera frames | `THERMAL_IMAGE` |
| Depth maps | `DEPTH` |
| LiDAR / 3D scans | `POINT_CLOUD` |
| Audio, vibration, current draw, temperature logs, or any other signal sampled over time | `SENSOR_TIMESERIES` |
| Anything else | `OTHER` |

The `audio_noise_adapter.py` example above treats a WAV file as
`SENSOR_TIMESERIES` — audio is, mechanically, a sensor sampled over time —
rather than inventing a new modality. Tabular/structured data (spreadsheets,
database rows) usually fits `SENSOR_TIMESERIES` too if there's a time axis,
or `OTHER` if there isn't; put your actual column/feature names in
`Observation.metadata` either way.

## If you've never trained a model before

You don't need Athena-specific knowledge to do step 2 above — you need
whatever the standard tutorial for your data type teaches. A few honest
starting points, none of them Athena-affiliated:

- **Images:** [Ultralytics YOLO's own quickstart](https://docs.ultralytics.com/quickstart/)
  (the same library `yolo_adapter.py` wraps) trains a detector on your own
  labeled images in a handful of commands.
- **Audio:** libraries like `librosa` (feature extraction) paired with
  `scikit-learn` (a plain classifier) are a common, approachable starting
  point before reaching for deep learning.
- **Tabular / general sensor data:** `scikit-learn`'s own
  [getting-started guide](https://scikit-learn.org/stable/getting_started.html)
  covers fitting and using a classifier or regressor in a few lines, and is
  usually the right first thing to try before anything more complex.

Whichever you pick, the output you need for step 3 is just: *some Python
object with a way to call `.predict(...)` on it, or equivalent.* For
example, a trained `scikit-learn` classifier wrapped as a `ModelAdapter`
might look like:

```python
from athena_sdk.contracts import AssetStateFinding, Modality, Observation, Severity
from athena_sdk.interfaces import AdapterCapabilities, ModelAdapter
from datetime import datetime, timezone

class MySklearnAdapter(ModelAdapter):
    def __init__(self, trained_classifier, feature_extractor):
        self._model = trained_classifier      # already .fit() elsewhere
        self._features = feature_extractor      # your own feature function

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_name="my-sklearn-model",
            adapter_version="0.1.0",
            task_type="classification",
            modalities=[Modality.SENSOR_TIMESERIES],
            labels=list(self._model.classes_),
        )

    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return []
        features = [self._features(obs.source_uri) for obs in observations]
        predictions = self._model.predict(features)
        return [
            AssetStateFinding(
                finding_id=f"{obs.observation_id}-finding",
                asset_id=obs.metadata.get("asset_id", "unknown"),
                observation_ids=[obs.observation_id],
                model_id="my-sklearn-model",
                label=str(label),
                severity=Severity.UNKNOWN,
                created_at=datetime.now(timezone.utc),
            )
            for obs, label in zip(observations, predictions)
        ]
```

Nothing here is `scikit-learn`-specific by contract — swap in any trained
model with a `.predict`-shaped call, for any data type. That is the entire
integration surface; see
[Writing a custom adapter](writing-a-custom-adapter.md) for the conformance
checklist your adapter should pass before you consider it done.
