# Writing a training backend

A **training backend** turns an exported dataset directory into a model artifact, and can
run that artifact for evaluation and prediction. Backends are replaceable: the web process
only queues work and describes backends; a worker process does the model work.

The contract is `TrainingBackend` in `web/projects/training/base.py`; the registry is
`web/projects/training/__init__.py`; the worker is `web/projects/training/runner.py` (read
`web/projects/training/README.md` too — it is the in-tree summary of the same contract).
Reference implementations to read alongside this guide: `torchvision_frcnn.py` (the image
default), `audio_wav.py` (non-vision, generic snapshots), `tabular_sklearn.py`,
`ocr_trocr.py` (non-vision, ready-made model downloads), `dfine.py`.

> **Status.** The interface is exercised by the built-ins and the test suite, mostly on
> synthetic data; the D-FINE backend is opt-in. No third-party backend has been shipped
> against the entry point yet (ROADMAP.md M3). The `evaluate`-default contract (below) is
> the strongest guarantee you get; everything else is convention documented in docstrings.

## The one rule that shapes everything: the web process never imports torch

Importing your module — or any module that leads to it — must never import torch or any
other heavy framework. The web process lists and describes backends
(`describe_backend` calls `check_available()`), and only the worker (`manage.py
train_worker`) calls `train` / `evaluate` / `predict`. Import frameworks **lazily, inside
the methods** that need them. The audio backend's `_torch_and_numpy()` helper
(`web/projects/training/audio_wav.py`) is the pattern: import inside the function, raise a
plain-language error naming `manage.py train_worker` when it is missing.

Run **one worker per deployment**, in an environment that has the backend's requirements
(for the reference torchvision backend, `web/requirements-autolabel.txt`). Workers publish a
heartbeat; the console's backends view combines "can the web process see the framework"
(`check_available()`) with "does a live worker report it".

## Registration, three ways

```python
# 1. Entry point — ship a package, no settings change (the third-party path):
[project.entry-points."athena.training_backends"]
my-backend = "my_package.backend:MyBackend"

# 2. Settings (a "module:Class" string list):
TRAINING = {"BACKENDS": ["my_package.backend:MyBackend"]}

# 3. From code:
from projects.training import register_backend
register_backend(MyBackend)
```

`name` is matched against `^[a-z0-9][a-z0-9_-]{0,78}$` at registration, and **is stored on
every run and every model version — never rename it**. A duplicate name raises unless
`replace=True`. The registry loads once and caches (`reload_backends()` for tests). `BUILTIN_BACKENDS`
currently registers `torchvision-fasterrcnn`, `audio-wav-cnn` and `tabular-sklearn`;
`web/athena_web/settings.py` opts into `dfine` and `ocr-trocr` via
`TRAINING["BACKENDS"]`.

How runs pick a backend: the request's own choice, else the recipe's entry in
`RECIPE_DEFAULT_BACKENDS` (`web/projects/training_views.py`), else `TRAINING`'s
`DEFAULT_BACKEND` (env override `ATHENA_TRAINING_BACKEND`). See
[writing a recipe](writing-a-recipe.md) for the routing rule.

## The interface

### Required

| Member | Contract |
| --- | --- |
| `name` (class attr) | Stable identifier (the pattern above). Stored on runs and models. |
| `description` (class attr) | One plain sentence shown in the console. |
| `license_note` (class attr) | Plain-language licence of the backend code **and** of any weights it downloads or ships. Fill it in; the console shows it. |
| `check_available()` | Cheap check that never imports the heavy framework — use `importlib.util.find_spec`. Returns `Availability(ok, message, how_to_enable)`. |
| `train(dataset_dir, config, progress_cb, cancel_cb)` | Train from the exported dataset directory. Returns `TrainResult`. |

`Availability` and `TrainResult` are frozen dataclasses in `base.py`:
`Availability(ok, message, how_to_enable)` with an `as_dict()`; `TrainResult(artifact_path,
metrics, label_names)` where `artifact_path` is a file (or directory — see below) inside the
scratch work dir, which the runner moves under `<data home>/models/` and hashes.

Callbacks: `progress_cb(fraction_0_to_1, message)` — call it regularly; it may raise to stop
training (timeout/cancel). `cancel_cb()` — poll it inside your epoch/step loop and raise
`TrainingCancelled` when it returns true.

### The honest-availability pattern

`check_available()` is what the console *says*, not what the worker *has*. The pattern,
straight from the audio backend:

```python
import importlib.util

def check_available(self) -> Availability:
    missing = [name for name in ("torch", "numpy") if importlib.util.find_spec(name) is None]
    if not missing:
        return Availability(True, "torch and numpy are installed.")
    return Availability(
        False,
        f"{' and '.join(missing)} not installed in this environment.",
        "Install torch and numpy in the environment that runs `manage.py train_worker`, "
        "then start the worker.",
    )
```

Two honest layers: `message` states the fact in this environment; `how_to_enable` tells the
administrator exactly what to do. Never import the framework here — the web process calls
this on every backends listing. The worker's own failure path (`train` importing and
raising a plain `ValueError` naming the worker command) is the second layer; keep both.

### Image backends: `dataset_format`

```python
dataset_format = "yolo"   # or "coco"
```

Image backends set only `dataset_format`; the runner's `export_dataset` writes the frozen
dataset version to a scratch directory in that format (verifying checksums), calls
`train()`, and scores the held-out test split itself. You get the standard `EvalItem` list
(`path`, `boxes` of `(label, x, y, w, h)` normalized tuples, `width`, `height`, optional
`asset_id`) and `labels` — and the evaluation below is judged by Athena's own metric.

### Non-vision backends: the `build_dataset` hook

```python
def build_dataset(self, run, dest: Path) -> tuple[list, list[str]]:
    ...
```

Anything that does not train on YOLO/COCO-formatted images implements this hook instead of
`dataset_format`: write the run's frozen dataset under `dest` in whatever layout `train()`
reads, and return `(eval_items, labels)` in whatever shape your `evaluate()` scores
(vision's `EvalItem` is one convention, not a requirement — the audio backend returns its
own `ClipEvalItem` objects). The runner detects the override
(`type(backend).build_dataset is not TrainingBackend.build_dataset`) and calls your hook
instead of the image export.

Two ready-made helpers in `web/projects/training/runner.py`:

- `export_entries(run, dest)` — materializes a generic version's entry files into
  `dest/entries/<split>/<n>_<sha12>.<ext>`, verifying every checksum. Raises plain
  `ValueError` when the version has no entries or a file changed.
- `entry_ground_truth(run)` — every frozen entry as a dict (`split`, `label` — the
  annotation's primary project label, `annotation` — the plugin-normalized accepted
  annotation verbatim, `asset_id`, `filename`, `sha256`), in the same deterministic order
  as `export_entries` (pair by position: `ground_truth[i]["sha256"]` matches file `i`).
- `build_generic_dataset(run, dest)` — combines the two into one documented layout for
  files-plus-annotations trainers (what OCR uses): `images/<split>/...` plus a
  `transcripts.json`.

Read the snapshot rows (via these helpers), not the live project: what a backend trains on
and scores against is exactly what was frozen.

### Config, options and preflight (all optional, all defaulted)

| Method | Contract |
| --- | --- |
| `default_config()` | The config a run gets when the caller sends none. Default `{}`. |
| `validate_config(config)` | Return the normalized config or raise `ValueError` with a plain message (HTTP 400). The default refuses any settings ("This backend takes no settings."). |
| `start_options(worker)` | Optional "how should it start?" choices for the Teach panel. `worker` is `{"online": bool, "downloads_allowed": bool \| None}`. The default is no choices. |
| `start_check(config, worker)` | Server-side pre-queue check: `{"needs_acknowledgement": bool, "blocked": str}`. The page's tick-box is only a convenience. |
| `resolve_start(worker)` | What an automatic "start from the best ready-made model" would pick, or `None`. |
| `prepare_config(config, *, train_pictures, worker)` | Resolve `"auto"` choices and fill derived settings before `validate_config`. `ValueError` → 400; `StartBlocked` → 409. |
| `teach_preview(train_pictures, start)` | Settings the one-click start would derive, shown before starting. `None` means no derivation. |
| `runtime_info()` | Framework versions and device, captured when a run starts. No paths, no host names. |

`constraints` (a tuple of plain strings) is shown to operators as the backend's documented
limits (hardware, runtime, expected quality) — write yours honestly; the audio backend's
seven-entry tuple in `audio_wav.py` is the bar.

### Training: the `train` contract

```python
def train(self, dataset_dir: Path, config: dict, progress_cb, cancel_cb) -> TrainResult:
```

- Poll `cancel_cb()` inside loops; raise `TrainingCancelled("...")` when it fires.
- Call `progress_cb(fraction, message)` regularly — the runner rate-limits writes.
- Raise `ValueError` with a plain message for a bad dataset or config (the run fails with
  your message); `BackendUnavailable` when the environment cannot run you.
- `TrainResult.artifact_path` points inside the scratch work dir; the runner moves it under
  `<data home>/models/` and hashes it. The audio backend returns a directory (its
  `model.bin` plus `metadata.json` sidecar — the runner copies every file of the folder);
  the torchvision backend returns a single file. Both work.
- `metrics` is stored as the run's training metrics; `label_names` is stored on the
  candidate model version.
- One run has a time budget (`TRAINING["RUN_TIMEOUT_SECONDS"]`, 6 h default) and a
  heartbeat expectation; the worker fails stale runs itself — do not manage that.

### Evaluation: what you are judged by

```python
def evaluate(self, artifact: Path, items, labels, thresholds) -> dict:
```

**The default is the deal: it runs your `predict` and scores it with Athena's own metric**
(`projects.training.evaluation.evaluate_items`), so every backend that predicts is judged by
the same code — override only to speed up inference. Non-vision backends override it and
record their own evaluation JSON on `ModelVersion.evaluation` — modality-defined, read as
opaque by the rest of the console; the results pages fall back to a plain "see the
evaluation report" answer for shapes they do not understand. Document your shape (the audio
backend documents its keys — `available`, `reason`, `split`, `accuracy`, `per_label`,
`confusion`, `data`, `limits`, `eval_kind` — in its module docstring; do the same).

`predict_split(artifact, items, labels, thresholds)` (optional) serves the
operating-points block; return `None` when your backend cannot do it — the evaluation then
simply has no `operating_points`.

### Prediction

```python
def predict(self, artifact: Path, image: Image.Image, thresholds: dict) -> list[BoxPrediction]:
```

`thresholds` carries `confidence` (0-1) and `max_boxes`; `BoxPrediction(label, x, y, width,
height, score)` is normalized (0-1), top-left origin. This method is box-shaped **by
design** — a backend whose task has no box answer refuses it plainly: the audio backend
raises `NotImplementedError("This backend classifies sound clips, not pictures.")`, and the
OCR backend keeps a backend-level `predict_text()` for API completeness while the
box-shaped check-screen UI honestly refuses to serve it. If your task is not box-shaped,
refuse; do not bend boxes into something they are not.

## A worked minimal skeleton (shape only; the real audio backend is the reference)

```python
import importlib.util
from pathlib import Path
from typing import Any, Callable

from projects.training.base import (
    Availability, CancelCallback, ProgressCallback, TrainingBackend, TrainingCancelled, TrainResult,
)

DEFAULTS = {"epochs": 5}


class MyBackend(TrainingBackend):
    name = "my-task"
    description = "One plain sentence about what this teaches."
    license_note = "Code: MIT. Weights: none downloaded or shipped; starts from random init."
    constraints = ("Needs torch in the worker environment.", "CPU only.")

    def check_available(self) -> Availability:
        if importlib.util.find_spec("torch") is None:
            return Availability(False, "torch is not installed in this environment.",
                                "Install torch in the environment that runs `manage.py train_worker`.")
        return Availability(True, "torch is installed.")

    def default_config(self) -> dict[str, Any]:
        return dict(DEFAULTS)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        return dict(config or DEFAULTS)

    def build_dataset(self, run, dest: Path) -> tuple[list, list[str]]:
        from .runner import entry_ground_truth, export_entries   # the frozen snapshot helpers

        written = export_entries(run, dest)          # checksum-verified files under dest/entries/<split>/
        truth = entry_ground_truth(run)              # paired by position
        labels = list(run.dataset_version.label_names)
        items = [t for t in truth if t["split"] == "test"]
        return items, labels

    def train(self, dataset_dir: Path, config: dict[str, Any],
              progress_cb: ProgressCallback, cancel_cb: CancelCallback) -> TrainResult:
        import torch  # lazy: the web process never imports this

        ...  # read dataset_dir, train, poll cancel_cb, call progress_cb
        artifact = dataset_dir / "model.pt"
        torch.save(..., artifact)
        return TrainResult(artifact_path=artifact, metrics={...}, label_names=labels)
```

Register it, then start the worker (`python manage.py train_worker`) in an environment with
your requirements, and check `check_available()`'s answer in the console's backends listing
before queueing a run.

## Limits to know before you start

- **Lifecycle is not yours.** Training success never goes beyond `candidate`; `staged`
  needs an available held-out evaluation; `approved` needs a note and atomically retires
  the previous approved model (rollback pointer kept); every transition appends a
  `ModelEvent`. The server enforces this in `lifecycle.py` — a backend cannot approve
  itself.
- **No versioning policy for artifacts or evaluation JSON.** `ModelVersion.evaluation` is
  opaque to the console; nothing stamps or checks a schema version. Include your own
  format marker in your metadata sidecar (the audio backend's `"format":
  "athena.audio-wav-cnn/1"`) — that is convention, not contract.
- **One training worker per deployment** is the supported topology; the runner fails
  orphaned `running` runs at worker start-up on that assumption.
- **Settings** (`TRAINING` in settings, all optional): `DEFAULT_BACKEND`, `BACKENDS`,
  `RUN_TIMEOUT_SECONDS` (6 h), `STALE_HEARTBEAT_SECONDS` (600),
  `INFERENCE_TIMEOUT_SECONDS` (120), `INFERENCE_QUEUE_LIMIT` (50), `MAX_LOG_CHARS`,
  `SCRATCH_DIR`. Env overrides: `ATHENA_TRAINING_BACKEND`, `ATHENA_TRAINING_RUN_TIMEOUT`.
- **Nothing sanity-checks what you read from the snapshot.** `export_entries` verifies
  checksums, but reading the right pairing (files to ground truth, labels to
  `label_names`) is your responsibility; a silent mismatch trains on wrong data. The
  built-ins' tests are the pattern to copy.
