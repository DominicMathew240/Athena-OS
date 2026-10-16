# Athena console (Django)

The Athena console is the self-hosted web UI for projects, image import, bounding-box labelling, and
model-assisted auto-labelling with human review, written in plain language for non-specialists. A JSON API
can also freeze the reviewed boxes into numbered dataset versions and export them as COCO or YOLO zips (no
screen for that yet). Sign-in is required for every page and API route.

- **Deploy it:** see [DEPLOYMENT.md](DEPLOYMENT.md) (Docker Compose, environment reference, backups, model licences, known limits).
- **Export a dataset:** see [../docs/guides/data-export.md](../docs/guides/data-export.md) (API only).
- **Run it locally:** below.

## Run locally

Use Python 3.12–3.14 (Django 6.1's supported range). The project has also been run on 3.15, which Django does not yet list.

```bash
python3.13 -m venv .venv-web && source .venv-web/bin/activate
python -m pip install -r web/requirements.txt numpy   # numpy is only needed by the tests
export DJANGO_DEBUG=1                                  # development mode: loopback hosts, no HTTPS redirect
python web/manage.py migrate
python web/manage.py createsuperuser
python web/manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/> and sign in. Without `DJANGO_DEBUG=1` the app runs in production mode and
refuses to start without `DJANGO_SECRET_KEY` and `ATHENA_ALLOWED_HOSTS`.

Data lives in `~/.athena-os/` (`projects.sqlite3`, `media/`); set `ATHENA_DATA_HOME` to move it.

### Auto-labelling locally

The web process only queues jobs. To run them, install the model packages and start the worker in a second terminal:

```bash
python -m pip install -r web/requirements-autolabel.txt
python web/manage.py autolabel_worker          # first run downloads SAM + CLIP weights (~1 GB)
```

Then open an image project, add images, and choose **Auto-label unlabeled images**. Set
`ATHENA_AUTOLABEL_BACKEND=disabled` to hide the feature.

### Tests

```bash
python web/manage.py test projects
```

The suite covers sign-in, lockout, CSRF, CSP, deploy-time settings validation, the API, the auto-label queue and
review rules (with a deterministic stand-in for the model), and the dataset API (review gating, validation,
splits, immutability, COCO/YOLO round trips). The real SAM + CLIP pipeline is not run in the suite.

## Implemented

- Sign-in with lockout after repeated failures; one role (every user can see every project).
- Create, list, filter, search, and delete project metadata for ten modality types; only image projects have a task workspace.
- A project checklist and short plain-language explanations on each panel (what a project, label, or suggestion is), rather than tool jargon.
- Label editor: add, rename, reorder, and remove a project's labels without code. Renaming a label also renames it on boxes already drawn.
- Import, preview, and delete JPEG/PNG/WebP images (validated, capped at 20 MB / 25 MP per image and 100 MB per batch, SHA-256 recorded).
- Manual bounding boxes: pointer or keyboard creation, class reassignment, move/resize, delete, 50–400% zoom, pan, undo/redo of the last 100 changes while the dialog is open.
- Auto-labelling: SAM proposes regions, CLIP names them with the project's labels. Jobs run per image or per project in a worker, with progress and cancel. Proposals are marked **Needs review** with model name and score, and are accepted, corrected, or rejected one by one or in bulk. A re-run replaces only unreviewed proposals.
- Dataset versions and export, **backend JSON API only (no console screen yet)**: `GET …/datasets/readiness` previews what would be exported and what blocks it; `POST …/datasets` freezes an immutable, numbered version containing only accepted boxes (model proposals still marked **Needs review** are excluded and counted), with a deterministic 80/10/10 train/val/test split by image checksum and a `manifest.json` carrying a SHA-256; `GET …/datasets/<id>/export?format=coco|yolo` streams a zip. Geometry, label, and file-checksum problems block a version and are reported per image. See [../docs/guides/data-export.md](../docs/guides/data-export.md).
- `/healthz`, strict CSP, HSTS, secure cookies, Docker packaging.

## Not implemented

Roles and per-project access, encryption at rest, rights evidence/review, a console screen for dataset
versions and export (only the API exists), near-duplicate (not just byte-identical) leak
detection, re-importing an exported dataset, model training/evaluation, operational inference, scanner
connectors, modality plugins, and any benchmark of auto-label accuracy. Undo/redo is in memory only; full
assistive-technology verification has not been done. A dataset version reflects only which boxes a person
accepted; it says nothing about whether you have the rights to use the images, and no training step exists yet to gate on it.
