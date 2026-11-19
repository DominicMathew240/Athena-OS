# Training and inference backends

**Process split.** The web process only queues work and reads results; it never imports torch.
`manage.py train_worker` (training runs) and `manage.py inference_worker` (images submitted to inference
apps) do the model work. Run **one of each per deployment**, in an environment that has the backend's
requirements (`web/requirements-autolabel.txt` for the torchvision reference backend). Both publish a
heartbeat; readiness/backends endpoints combine "can the web process see the framework" with "does a live
worker report it".

**Writing a backend.** Subclass `projects.training.base.TrainingBackend` (see `base.py`), keep the module
import free of heavy frameworks (import torch etc. inside `train`/`predict`), and register it without
touching core: declare an entry point in your package,

    [project.entry-points."athena.training_backends"]
    my-backend = "my_package.backend:MyBackend"

or list `"module:Class"` in `settings.TRAINING["BACKENDS"]`, or call `register_backend(MyBackend)`.
`name` is stored on every run and model, so never rename it. Fill in `license_note` (code **and** weights).

**Image backends** set `dataset_format` (`"yolo"` or `"coco"`); the core exports the frozen dataset
version to a scratch directory in that format, calls `train()`, scores the held-out test split with
`projects.training.evaluation` (so all backends are judged by the same code), moves the artifact under
`<data home>/models/`, and creates a `candidate`.

**Non-vision backends** (audio, tables, OCR, ...) implement the `build_dataset(run, dest)` hook instead
of `dataset_format`: it writes the run's frozen dataset to `dest` in whatever layout `train()` reads and
returns `(eval_items, labels)`, where `eval_items` is whatever shape the backend's `evaluate()` scores
(vision's `EvalItem` is one convention, not a requirement). The runner calls `build_dataset` when the
backend overrides it and falls back to the image export otherwise, so the two existing backends are
unchanged. Non-vision backends also record their own evaluation JSON on `ModelVersion.evaluation`
(modality-defined; readers treat it as opaque, and the results pages fall back to a plain "see the
evaluation report" answer for shapes they do not understand).

**The handwriting-OCR backend** (`ocr-trocr`, `training/ocr_trocr.py`) is the first non-vision one.
Its dataset layout comes from the runner's ready-made `build_generic_dataset` helper: the frozen
entry files under `images/<split>/` plus one `transcripts.json` (`[{split, asset_id, filename,
sha256, text, path}]`); any backend that trains on files-plus-annotations can reuse it. It
fine-tunes Microsoft's `trocr-small-handwritten` (MIT on the model card; download-gated by the same
administrator switch as every ready-made model, cached under `models/pretrained/trocr/`), evaluates
with the character error rate (CER) and word accuracy on the held-out test split, and saves a single
`model.pt` artifact (state dict + configs + processor settings; no pickle of live objects). Its
`predict()` is deliberately a refusal: the check-screen/inference UI is box-shaped and cannot show a
text answer yet, so `predict_text()` exists at the backend level for API completeness and the console
check screens say the model type is not supported there yet.

**Settings** (`settings.TRAINING`, all optional): `DEFAULT_BACKEND`, `BACKENDS`, `RUN_TIMEOUT_SECONDS`
(6 h), `STALE_HEARTBEAT_SECONDS` (600), `INFERENCE_TIMEOUT_SECONDS` (120), `INFERENCE_QUEUE_LIMIT` (50),
`MAX_LOG_CHARS`, `SCRATCH_DIR`. Env overrides: `ATHENA_TRAINING_BACKEND`, `ATHENA_TRAINING_RUN_TIMEOUT`.

**Safety rules enforced server-side** (`lifecycle.py`): training success never goes beyond `candidate`;
`staged` needs an available held-out evaluation; `approved` needs a note, only from `staged`, and atomically
retires the previous approved model (rollback pointer kept); every transition appends a `ModelEvent`.
