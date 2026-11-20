# Extending Athena: modality plugins, recipes, and training backends

Athena OS separates *what the console accepts* from *what the console is*. Three registries
let a new data kind or a new trainer land as a plugin package, without editing core code:

| Guide | What you are adding | Contract lives in |
| --- | --- | --- |
| [Writing a modality plugin](writing-a-modality-plugin.md) | A data kind: which files are accepted, how they are annotated, what the asset list says. | `web/projects/modalities/base.py` |
| [Writing a recipe](writing-a-recipe.md) | An operator-facing task ("Recognize sounds...") that ties a modality to an annotation style. | `web/projects/tasks/base.py` |
| [Writing a training backend](writing-a-training-backend.md) | A trainer that turns a frozen dataset version into a model artifact and an evaluation. | `web/projects/training/base.py` |

Related but separate contracts: [writing a custom adapter](writing-a-custom-adapter.md)
(running an already-trained model in the Athena Runtime via the SDK, no training involved)
and [writing a connector](writing-a-connector.md) (moving captured data into the console).

## How the three fit together

A **modality plugin** owns the files: it validates each upload, returns the metadata stored
on `Asset`, and validates annotation bodies. A **recipe** owns the offer: it names the
modality and the annotation style a project of that recipe uses, and appears in the guided
project form. A **training backend** owns the teaching: the worker exports the project's
frozen dataset version, the backend trains on it and is judged by its held-out evaluation.

The audio slice is the smallest full example of all three at once: the
`AudioModalityPlugin` (`web/projects/modalities/audio.py`, modality `audio`, annotation kind
`clip_labels`), the `audio-classification` recipe (`web/projects/tasks/__init__.py`), and the
`audio-wav-cnn` backend (`web/projects/training/audio_wav.py`).

All three registries follow the same shape: built-ins, a `settings` list (`MODALITY_PLUGINS`,
`RECIPES`, `TRAINING["BACKENDS"]` — each a list of `"module:Class"` strings), and/or a
`register_*()` call from your own module. The training backends additionally support the
`athena.training_backends` entry-point group, so a third-party backend can ship as an
installed package with no settings change at all.

## What you get for free from the data contracts

Two database migrations define what plugins plug into (`web/projects/migrations/`):

- **0025** (`multimodal_foundation`): `Asset.metadata` (a JSON column for modality-specific
  facts — duration, sample rate, table columns), the generic `AssetAnnotation` row (a `kind`
  slug, a JSON `data` body, an origin of `manual` or `model`, a status of `accepted` or
  `suggested`), and recipe/annotation-kind fields on projects, runs and model versions.
- **0026** (`dataset_entries`): `DatasetEntry` — the generic frozen snapshot. One row per
  asset per dataset version, carrying the accepted annotation verbatim, with deterministic
  SHA-256-keyed splits (80/10/10 by default), a manifest and a recorded checksum.

If your plugin's annotations are validated and your recipe routes to a backend, the freezing,
splitting, integrity checking and export of dataset versions happen with no extra code from
you — the details are in the modality-plugin guide.

## Honest limits, stated plainly

- **There is no versioning or compatibility policy for modality payloads yet.** The JSON
  bodies that `validate_annotation` normalizes and `DatasetEntry.annotation` freezes have no
  schema version, no migration path, and no promise of stability across Athena releases
  (ROADMAP.md M3 tracks this as not done). If you write a plugin today, its annotation shape
  may need rework when a policy lands. Built-in payloads (`boxes`, `clip_labels`,
  `transcripts`, `table_target`) carry the same risk.
- **No community plugin exists yet.** These interfaces have been exercised by the built-ins
  and the test suite (synthetic data), not by third-party authors. Expect rough edges, and
  please report them.
- **Modality payloads are not cross-checked between plugin and backend by anything except
  tests.** A backend that misreads what a plugin froze will train on wrong data silently;
  the guides say where to look.
- **Registration is per-process and lazy.** The registries load once and cache; installing a
  plugin means restarting the web process and the workers (the `reload_*()` functions exist
  for tests).

## Where the source is

Everything documented in these guides lives under `web/projects/`:

```
web/projects/modalities/    base.py (ModalityPlugin), registry.py, image.py, audio.py, table.py, ocr.py
web/projects/tasks/         base.py (TaskRecipe), __init__.py (recipe registry)
web/projects/training/      base.py (TrainingBackend), __init__.py (backend registry), runner.py,
                            README.md (the worker split, in-tree)
web/projects/annotations.py recipe-aware annotation dispatch (recipe kind wins, modality fallback)
web/projects/datasets/      generic.py (generic frozen snapshots), splits.py, manifest.py
```
