# Writing a modality plugin

A **modality plugin** owns everything modality-specific about an asset: which files are
accepted (and their metadata), which annotation bodies are valid, and what the asset list
says about each asset. Core code (views, models, the training runner) talks to plugins
through one interface only, so a new data kind lands as a plugin plus a recipe — never as
`if modality == ...` branches in core code.

The contract is `ModalityPlugin` in `web/projects/modalities/base.py`; the registry is
`web/projects/modalities/registry.py`. The built-ins to read alongside this guide:
`image.py`, `audio.py`, `table.py` (a modality that owns its annotation style) and `ocr.py`
(an annotation-style plugin that shares a modality with the image builtin).

> **Status.** These interfaces are exercised by the built-ins and the test suite on
> synthetic data only. No community plugin exists yet, and there is **no versioning or
> compatibility policy for annotation payloads** (ROADMAP.md M3 tracks this as not done):
> the JSON body your `validate_annotation` returns may need rework across Athena releases.

## The interface, end to end

```python
from projects.modalities.base import ModalityPlugin, check_not_empty, digest_upload
```

| Member | Contract |
| --- | --- |
| `modality` (class attr) | Which data kind this plugin handles; one of `Project.MODALITY_CHOICES`'s values (`web/projects/models.py`). Must be non-empty or `register_plugin` refuses. |
| `asset_noun` (class attr) | What the assets are called in operator-facing sentences, e.g. `"picture"`, `"sound clip"`. |
| `validate_upload(upload)` (abstract) | Check one uploaded file; return its asset metadata dict. Raise `django.core.exceptions.ValidationError` with a plain-language message on a bad file — the view names the file and refuses the whole batch. |
| `storage_suffix` (class attr) | Extension for storage keys, without the dot. The default (empty) uses whatever `validate_upload` reported in `"extension"` — correct for every built-in. |
| `annotation_kind` (property) | Slug of the annotation style this modality accepts (e.g. `"boxes"`, `"clip_labels"`). Empty by default; recipes and annotation dispatch key on it. |
| `validate_annotation(payload, project)` | Check one JSON annotation body; return the normalized dict stored verbatim as `AssetAnnotation.data`. The default raises: "This project's data type does not have labeling tools yet." Override to support labeling. |
| `asset_view_metadata(asset)` | What the asset list API returns for one asset beyond the shared fields. The default returns `dict(asset.metadata or {})`; image returns `{}` because width/height are real columns already in the shared payload. |
| `max_uploads()` | How many files one upload request may carry (default 20). The view enforces it. |
| `max_batch_bytes()` | How much one upload request may carry in total (default 100 MB). The view enforces it. |

Two helpers in the same module every file-storing plugin should use:

- `check_not_empty(upload, what)` — refuses an empty upload in the plugin's own words
  (`what` is plural, e.g. `"Sound clips"`).
- `digest_upload(upload, max_bytes=..., limit_message=...)` — streams the upload through
  SHA-256 chunk by chunk (a huge upload never sits whole in memory; the client's
  `Content-Length` is not trusted — the size limit is enforced again mid-stream) and returns
  `(hex_digest, size_bytes)`, leaving the upload rewound for the format check that follows.

### The metadata dict `validate_upload` returns

Five keys are required — they become `Asset` columns plus the storage-key extension:

- `filename` (a cleaned basename; each built-in has its own `safe_filename`),
- `sha256`, `size_bytes`,
- `extension` (without the dot),
- `content_type`.

Anything else is stored in `Asset.metadata` (the JSON column added by migration
`web/projects/migrations/0025_multimodal_foundation.py`). Image adds `width`/`height` as
real columns, audio adds `duration_seconds`, `sample_rate`, `channels`, `bits_per_sample`,
tables add `columns`, `row_count`, `summary_note`.

### What `validate_annotation` must satisfy

Return a JSON-serializable dict; the annotation view stores it verbatim on
`AssetAnnotation.data` (with the `kind` slug from `annotation_kind`). Two conventions matter
downstream:

1. **`data["label"]` is the primary project label.** The generic snapshot layer
   (`web/projects/datasets/generic.py`) reads `data["label"]` as a string that must be one
   of the project's label names, and freezes it as `DatasetEntry.label`. Audio's clip label
   and table's target-column choice are exactly this key. Free-form content (like a
   transcript) must not go there — the OCR plugin keeps a single project label named
   `"transcript"` to satisfy the convention and puts the text in `data["text"]` (see
   `web/projects/modalities/ocr.py` for the full reasoning).
2. **Unknown keys are yours to refuse.** The built-ins all reject anything beyond their
   expected keys with a plain-language message.

Raise `ValidationError` with a plain-language message on a bad body; the caller surfaces it
to the person labeling.

### What the generic snapshot layer gives you for free

If your plugin has an `annotation_kind` and `validate_annotation`, freezing dataset versions
needs no plugin code. `web/projects/datasets/generic.py` mirrors the image path step for
step: readiness analysis, deterministic splits keyed on the asset's SHA-256 (80/10/10 by
default; byte-identical files land in the same split; the min-1 rule per split), a canonical
manifest with a checksum, and one immutable `DatasetEntry` per asset (migration
`0026_dataset_entries.py`) carrying the accepted annotation verbatim. `Needs review`
(model-proposed) annotations are never exported. The runner side
(`web/projects/training/runner.py`: `export_entries`, `entry_ground_truth`) materializes the
frozen files checksum-verified and pairs them with their frozen ground truth by position —
a training backend then just reads them (see the [training backend guide](writing-a-training-backend.md)).

## A worked minimal example (abridged from the real audio plugin)

The real thing is `web/projects/modalities/audio.py`; read it in full. Abridged to the
shape:

```python
import io
import wave
from typing import Any

from django.core.exceptions import ValidationError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_AUDIO_BYTES = 20 * 1024 * 1024
AUDIO_EXTENSIONS = {".wav", ".wave"}


class AudioModalityPlugin(ModalityPlugin):
    modality = "audio"
    asset_noun = "sound clip"
    annotation_kind = "clip_labels"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Sound clips")
        if upload.size > MAX_AUDIO_BYTES:
            raise ValidationError("Each sound clip must be 20 MB or smaller.")

        name = (upload.name or "").rsplit("/", 1)[-1]
        suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if f".{suffix}" not in AUDIO_EXTENSIONS:
            raise ValidationError("Only WAV sound clips are supported.")

        sha256, size_bytes = digest_upload(
            upload, max_bytes=MAX_AUDIO_BYTES,
            limit_message="Each sound clip must be 20 MB or smaller.",
        )

        try:
            with wave.open(io.BytesIO(upload.read()), "rb") as clip:
                sample_rate = clip.getframerate()
                ...
        except (wave.Error, EOFError, ValueError) as error:
            raise ValidationError("The uploaded file is not a valid WAV sound clip.") from error
        finally:
            upload.seek(0)

        # ...more plain-language range checks (sample rate, channels, duration)...

        return {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "extension": "wav",
            "content_type": "audio/wav",
            "duration_seconds": round(duration, 3),
            "sample_rate": sample_rate,
            # ...anything else lands in Asset.metadata
        }

    def validate_annotation(self, payload: dict[str, Any], project) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"label"}:
            raise ValidationError("A clip label needs exactly one label.")
        label = payload.get("label")
        if not isinstance(label, str) or label not in project.labels:
            raise ValidationError("Choose a label defined in this project.")
        return {"label": label}
```

Notes worth copying:

- The real plugin checks the client-reported size first (a fast refusal), then lets
  `digest_upload` enforce the same limit mid-stream (the client's word is not trusted).
- The upload is rewound (`seek(0)`) after every read, in a `finally` — the view reads it
  again after validation.
- Every refusal message is one plain sentence naming the limit. The image plugin's messages
  are pinned by tests ("do not reword them casually", per its module docstring); expect
  yours to be held to the same style.

## Registration

Three ways, no core file edits (all in `web/projects/modalities/registry.py`):

```python
# 1. From your own module, at import time (what a settings-listed path loads):
from projects.modalities import register_plugin
register_plugin(MyModalityPlugin)          # refuses a duplicate modality unless replace=True

# 2. Via settings (a "module:Class" string list; loaded once, then cached):
MODALITY_PLUGINS = ["my_package.plugin:MyModalityPlugin"]

# 3. In tests or a live process:
from projects.modalities import reload_plugins   # forgets what was discovered
unregister_plugin("mymodality")
```

Core code looks plugins up with `get_plugin(modality)` (returns `None` when there is none)
or `plugin_or_refusal(modality)` (raises `UnsupportedModalityError`, whose `.message` is the
plain sentence shown to operators: "This project's data type isn't supported yet...").
`available_modalities()` lists what is registered. A duplicate registration for the same
modality raises `ValueError` unless `replace=True`.

### If your plugin shares a modality with a builtin (annotation-style plugins)

The handwriting-OCR plugin annotates pictures but must not take over the `image` modality's
upload validation. Register it **by annotation kind only**:

```python
from projects.modalities.registry import register_annotation_plugin
register_annotation_plugin(OcrImagePlugin)   # modality slot stays with the image builtin
```

`get_annotation_plugin(kind)` finds it (the recipe's annotation kind wins over the
modality's own plugin — the dispatch rule in `web/projects/annotations.py`). Loading your
module must be the whole registration: for the built-ins this is why
`BUILTIN_ANNOTATION_PLUGINS` exists in the registry (importing `projects.modalities.ocr`
registers the plugin and replaces the recipe placeholder, as a live server otherwise never
imports it). For your own, make sure something imports your module — the settings path
(`MODALITY_PLUGINS`) imports the module you name, so a module-level
`register_annotation_plugin(...)` call works there too.

## Limits to know before you start

- **The web process calls `validate_upload` and `validate_annotation` on every request.**
  Importing your module must stay light — no torch, no audio frameworks. Anything heavy
  belongs inside a method, imported lazily, and should self-skip in tests when its framework
  is missing. The image plugin's `IMAGE_FORMATS`/`PIL` use and the table plugin's
  stdlib-only CSV/XLSX checks are the bar to clear.
- **Upload limits are enforced per request by the view** (`web/projects/views.py`,
  `project_assets`): `max_uploads()` files and `max_batch_bytes()` total per request.
  The image modality's limits are the view's constants (`MAX_IMAGE_UPLOADS`,
  `MAX_IMAGE_BATCH_BYTES`), not the plugin's methods.
- **One annotation style per modality slot.** A modality's registered plugin's
  `annotation_kind` is what projects without a recipe get; anything else needs a recipe and
  the by-kind registration above.
- **`Asset.metadata` is not validated by anything except you.** What you return beyond the
  five required keys is stored as-is; keep it small and JSON-clean.
- **No versioning policy for annotation payloads.** Frozen `DatasetEntry.annotation` rows
  and live `AssetAnnotation.data` bodies have no schema version today. If your payload
  evolves, old frozen snapshots keep the old shape — your training backend must read what
  was frozen, not what your current code expects.
