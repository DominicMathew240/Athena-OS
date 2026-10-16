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
The core exports the frozen dataset version to a scratch directory in `dataset_format` (`yolo` or `coco`),
calls `train()`, scores the held-out test split with `projects.training.evaluation` (so all backends are
judged by the same code), moves the artifact under `<data home>/models/`, and creates a `candidate`.

**Settings** (`settings.TRAINING`, all optional): `DEFAULT_BACKEND`, `BACKENDS`, `RUN_TIMEOUT_SECONDS`
(6 h), `STALE_HEARTBEAT_SECONDS` (600), `INFERENCE_TIMEOUT_SECONDS` (120), `INFERENCE_QUEUE_LIMIT` (50),
`MAX_LOG_CHARS`, `SCRATCH_DIR`. Env overrides: `ATHENA_TRAINING_BACKEND`, `ATHENA_TRAINING_RUN_TIMEOUT`.

**Safety rules enforced server-side** (`lifecycle.py`): training success never goes beyond `candidate`;
`staged` needs an available held-out evaluation; `approved` needs a note, only from `staged`, and atomically
retires the previous approved model (rollback pointer kept); every transition appends a `ModelEvent`.
