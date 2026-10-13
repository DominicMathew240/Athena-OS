# Django web app

This is Athena's first self-hosted web slice. It uses Django and SQLite, serves the Multimodal Signal Board, persists project metadata for ten built-in modality types, and imports local JPEG, PNG, and WebP files into image projects.

## Run locally

Use Python 3.15 for the web app. Django 6.1.2 installs and passes the project's web tests under Python 3.15, but Django's current official [Python compatibility matrix](https://docs.djangoproject.com/en/6.1/releases/6.1/#python-compatibility) lists Python 3.12–3.14 only. Treat Python 3.15 as runtime-tested but not upstream-supported until Django adds it to that matrix.

From the repository root:

```bash
python3 --version  # confirm this is 3.15.x
python3 -m venv .venv-web
source .venv-web/bin/activate
python -m pip install -r web/requirements.txt
python web/manage.py migrate
python web/manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. The development server binds to loopback by default. Project metadata is stored in `~/.athena-os/projects.sqlite3`, and image files in `~/.athena-os/media/`; set `ATHENA_DATA_HOME` to select a different data directory or `ATHENA_MEDIA_ROOT` to override image storage. Run `python web/manage.py test projects` to test the Django app.

## Implemented

- Create, list, filter, search, and delete project metadata.
- Capture a project name, modality, task, and optional label names.
- Built-in types: image, video, audio, speech, text, table, time series, depth, point cloud, and custom.
- Import, list, preview, and delete image assets for image projects. JPEG, PNG, and WebP bytes are decoded and validated, capped at 20 MB and 25 megapixels per image and 100 MB per batch, and recorded with their original filename and SHA-256 hash.
- Create and list normalized image bounding boxes, reassign their class, move/resize them, and delete them. Coordinates are validated against the image bounds and project classes; boxes can be created by pointer drag or keyboard percentage entry. The workspace includes 50–400% zoom, scroll/trackpad panning, and undo/redo for the last 100 changes while the dialog remains open (buttons or Ctrl/Cmd+Z when the canvas is focused).
- Django CSRF protection for writes; delete requires explicit browser confirmation.

## Not implemented

The annotation slice is deliberately limited: boxes can be moved/resized with a pointer and repositioned/resized with keyboard commands; keyboard entry can create a box from percentage coordinates. Undo/redo is in-memory and clears when the dialog closes; review queues, independent-review provenance, and full assistive-technology verification are not implemented. The app also does not provide rights evidence/review, dataset versioning/export, model training/evaluation, operational inference, scanner connections, or modality plugins. Imported images and boxes remain unreviewed local candidates and are not qualified for export or training. No user authentication, access control, encryption at rest, or production deployment settings are included. Keep it on loopback and use synthetic or non-sensitive data only.

## Python / Django compatibility

`requirements.txt` pins Django 6.1.2 and Pillow 12.3.0. At the time this version was checked, Django's official 6.1 compatibility notes listed Python 3.12, 3.13, and 3.14; Python 3.15 is outside that supported matrix. The app's system check and tests have been exercised with Python 3.15.0, but this is not a claim of official Django support.
