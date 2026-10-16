# Deploying the Athena console

This guide runs the console (Django + gunicorn) and the auto-label worker on one host.
It covers what is implemented today. Read **Known limits** before putting real data in.

## What you get

| Piece | Role |
| --- | --- |
| `web` | The console UI and JSON API. Sign-in required for every page and API route. |
| `worker` | `manage.py autolabel_worker`. Loads SAM + CLIP and fills the job queue. The web process never imports torch. |
| `train_worker` | `manage.py train_worker`. Claims queued training runs, trains on the frozen dataset version, evaluates on its held-out test split and files a `candidate` model. **Run exactly one.** |
| `inference_worker` | `manage.py inference_worker`. Runs images submitted to inference apps through the approved model version. **Run exactly one.** |
| Dataset API | `/api/projects/<id>/datasets…`: immutable dataset versions of reviewed boxes and COCO/YOLO zip export. API only, no UI. |
| `/data` volume | SQLite database (`projects.sqlite3`), uploaded images (`media/`), login-throttle cache, model weights (`models/`). |

## Quick start (Docker Compose)

```bash
cd web
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into DJANGO_SECRET_KEY
$EDITOR .env                                                      # set ATHENA_ALLOWED_HOSTS and ATHENA_CSRF_TRUSTED_ORIGINS
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Put a TLS-terminating reverse proxy in front of `127.0.0.1:8000` and send
`X-Forwarded-Proto: https`. The console redirects plain HTTP to HTTPS and sets Secure,
HttpOnly, SameSite cookies, HSTS, and a strict Content-Security-Policy. Minimal Caddy example:

```
console.example.com {
    request_body { max_size 110MB }
    reverse_proxy 127.0.0.1:8000
}
```

The first auto-label job downloads the SAM (`facebook/sam-vit-base`, ~360 MB) and CLIP
(`openai/clip-vit-base-patch32`, ~600 MB) weights into `/data/models`. Their licences are
separate from Athena's (see [Licences and model terms](#licences-and-model-terms) before enabling the
worker). Once cached, set `ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0` to forbid further downloads.

## Without Docker

```bash
python3.13 -m venv .venv && . .venv/bin/activate          # Django 6.1 supports Python 3.12–3.14
pip install -r requirements.txt
# Worker host only. Install torch from the CPU index, exactly as web/Dockerfile does. Without
# --extra-index-url, pip on Linux x86_64 resolves torch from PyPI, which is the CUDA build and
# pulls in ~15 proprietary NVIDIA `nvidia-*-cu12` packages (+ triton) you did not choose to accept.
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-autolabel.txt
export DJANGO_SECRET_KEY=... ATHENA_ALLOWED_HOSTS=console.example.com ATHENA_DATA_HOME=/srv/athena
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
python -m gunicorn athena_web.wsgi:application -c gunicorn.conf.py     # web
python manage.py autolabel_worker                                      # worker (run exactly one)
python manage.py train_worker                                          # training worker (run exactly one)
python manage.py inference_worker                                      # inference worker (run exactly one)
python manage.py check --deploy                                        # must report no issues
```

`web/Dockerfile` installs the same way when built with `--build-arg WITH_AUTOLABEL=1`
(`pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-autolabel.txt`), and
`docker-compose.yml` is the supported path. With the CPU index pip selects `torch==2.9.1+cpu`, which declares
no NVIDIA dependencies (checked in [`docs/LICENSE_AUDIT.md`](../docs/LICENSE_AUDIT.md) caveat 3). macOS wheels
from PyPI are already CPU/MPS builds, so the flag is harmless there. If you want a GPU deployment, read
NVIDIA's licence terms first: the audit did not review them.

## Training and inference workers

`docker-compose.yml` defines `train_worker` and `inference_worker` next to `worker`. All three use the
`athena-console-worker` image (built with `WITH_AUTOLABEL=1`), which installs `torch==2.9.1` and
`torchvision==0.24.1` from the CPU index, so the torchvision Faster R-CNN reference backend is available
in any of them. The web process never imports torch: it only queues work and reads results.

```bash
docker compose up -d --build                       # starts web, worker, train_worker, inference_worker
docker compose logs -f train_worker                # "Training worker ready. Backends usable here: ..."
```

- **One instance each.** Both workers consume a single SQLite-backed queue and assume they are the only
  consumer; a second copy would race on claims and on the heartbeat. Restarts are safe: on start a worker
  fails any run/observation a previous worker left `running`, and a run whose heartbeat is older than
  `STALE_HEARTBEAT_SECONDS` (600 s) is failed by the next pass.
- **Without Docker** run the `manage.py` commands above in a venv that installed `requirements-autolabel.txt`
  (CPU index). They share `ATHENA_DATA_HOME` with the web process; scratch space for exports is
  `<data home>/training-scratch` and trained artifacts go under `<data home>/models/`.
- **Light deployments** that only label and export can leave both workers off: runs and images are accepted and
  wait in the queue until a worker starts. Each worker publishes a heartbeat that the readiness endpoints use,
  and reports which backends it can actually load.
- GPUs are not configured. Read NVIDIA's licence terms first (see the licence audit).

## Configuration

Settings are production-safe by default; the app refuses to start without a strong secret
key and an explicit host list. For local development set `DJANGO_DEBUG=1`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | required | ≥ 50 characters, not `django-insecure-…`. |
| `ATHENA_ALLOWED_HOSTS` | required | Comma-separated host names; `*` is rejected. |
| `ATHENA_CSRF_TRUSTED_ORIGINS` | empty | `https://console.example.com` when behind a proxy. |
| `ATHENA_BEHIND_PROXY` | `0` | `1` trusts `X-Forwarded-Proto` for HTTPS detection. |
| `ATHENA_SECURE_SSL` | `1` | Set `0` only on a trusted internal network without TLS (cookies lose `Secure`). |
| `ATHENA_HSTS_SECONDS` | 31536000 | HSTS lifetime when HTTPS is enforced. |
| `ATHENA_DATA_HOME` | `~/.athena-os` | Database, media, cache. Must be local disk (SQLite WAL does not work on NFS). |
| `ATHENA_SESSION_HOURS` | 12 | Sign-in lifetime. |
| `ATHENA_LOGIN_MAX_FAILURES` / `ATHENA_LOGIN_LOCKOUT_SECONDS` | 8 / 900 | Per-address and per-username lockout. |
| `ATHENA_TRUST_FORWARDED_FOR` | `0` | Use the last `X-Forwarded-For` hop as the client address for lockouts. |
| `ATHENA_WEB_WORKERS` / `ATHENA_WEB_THREADS` / `ATHENA_WEB_TIMEOUT` | auto / 2 / 120 | gunicorn sizing. |
| `ATHENA_AUTOLABEL_BACKEND` | `sam` | `sam` or `disabled` (hides auto-label in the console). |
| `ATHENA_SAM_MODEL` / `ATHENA_CLIP_MODEL` | see above | Hugging Face model ids. |
| `ATHENA_AUTOLABEL_DEVICE` | `auto` | `auto` picks CUDA, then Apple MPS, then CPU. |
| `ATHENA_SAM_POINTS_PER_SIDE` | 16 | Grid density; higher finds smaller objects but is slower (cost grows with its square). |
| `ATHENA_AUTOLABEL_JOB_TIMEOUT` | 3600 | Seconds before a job left `running` by a dead worker is marked failed. |
| `ATHENA_TRAINING_BACKEND` | `torchvision-fasterrcnn` | Default training backend name for new runs. Read by the web process and the workers. |
| `ATHENA_TRAINING_RUN_TIMEOUT` | 21600 | Seconds after which a run still marked `running` is failed by the worker. Whole number. |
| `ATHENA_LOG_LEVEL` | `INFO` | Logs go to stdout. |

Further training and inference limits (stale-heartbeat seconds, inference timeout and queue length, log size,
scratch directory, extra backends) are set through `settings.TRAINING`; see `projects/training/README.md`.

## Operating it

- **Health:** `GET /healthz` (no sign-in) returns `{"status":"ok"}` or 503 when the database or media directory is unusable.
- **Worker status:** the console shows `READY` / `WORKER OFF` from a heartbeat the worker writes every 10 s. Jobs queue safely while it is off.
- **Backups:** stop writes or use SQLite's online backup, and copy the media folder with it:
  `sqlite3 /data/projects.sqlite3 ".backup '/backup/projects.sqlite3'"` then `rsync -a /data/media/ /backup/media/`.
- **Upgrades:** `docker compose up -d --build` runs `migrate` on start. Take a backup first.
- **Users:** `python manage.py createsuperuser`; reset with `changepassword <name>`. Passwords need 12+ characters.

## Auto-labelling: what it does and does not do

SAM proposes object regions from a grid of point prompts; CLIP names each region using the project's
label names (plus background prompts so scenery is dropped). Every proposal is saved as **Needs
review** with the model name, job, and a score (label match × mask quality, not a calibrated accuracy).
Proposals never count as accepted labels until a person accepts or edits them, and a re-run replaces only
unreviewed proposals. It works best when labels name visible things ("knife", "laptop"); state labels
such as "damaged" cannot be judged from a crop and will need relabelling in review.

## Licences and model terms

Athena OS is Apache-2.0, and the auto-label worker downloads third-party model weights at runtime that are
**not** covered by that licence. They are not in the repository or the Docker image; your worker fetches them
from Hugging Face on first use, so their terms apply to *you*. This section summarises
[`docs/LICENSE_AUDIT.md`](../docs/LICENSE_AUDIT.md) (audit date 2026-10-16, an engineering audit, **not legal advice**);
read its §5 for the sources.

| Model | What the audit verified | Practical reading |
| --- | --- | --- |
| SAM `facebook/sam-vit-base` | Hugging Face tag and model card declare **Apache-2.0**; the upstream repository's LICENSE is Apache-2.0. | Commercial use is permitted; keep the Apache-2.0 notice. The SA-1B *training dataset* has its own research licence that governs downloading the dataset, not using the weights; its text could not be fetched, so its terms are unverified. |
| CLIP `openai/clip-vit-base-patch32` | The Hugging Face repo **declares no licence**. The upstream code repo is MIT, but no statement explicitly licensing the *weights* was found. The model card says: "**Any** deployed use case of the model - whether commercial or not - is currently out of scope." The training data is undisclosed web-scraped image-caption data. | This is an intended-use statement, not a licence term, but a customer or regulator can point to it, and running the auto-labeller on your server is a deployed use. Athena's design (CLIP only proposes labels, a person must accept each one) reduces the exposure; it does not remove it. |

Before you enable the worker, an operator should:

1. **Evaluate** whether your use is acceptable under the CLIP model card and your own legal advice, especially if
   you offer auto-labelling to customers as a hosted or paid feature.
2. **Opt out** if it is not: leave the worker off, or set `ATHENA_AUTOLABEL_BACKEND=disabled`. Manual labelling,
   review, and dataset export do not use these models.
3. **Or swap the classifier.** `ATHENA_CLIP_MODEL` takes a Hugging Face model id that loads through
   `transformers`' `CLIPModel`. Athena has not tested any alternative, and a different model needs its own
   licence and quality review. Prefer one that states an explicit weights licence.
4. Keep `ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0` after the first download so the weights you reviewed are the ones in use.

Other terms to know (details in the audit): Python dependencies are permissive (certifi and tqdm are MPL-2.0, fine
unmodified; attributions are in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)); if you *distribute* the
Docker image you take on Debian's GPL/LGPL source-availability obligations for the base layer; and Hugging Face's own
terms apply to the downloads (not reviewed).

## Known limits

- One role: every signed-in user can read and change every project. **There are no roles yet, so any signed-in user
  can approve a model for inference** (`POST .../models/<id>/approve`); approval needs a note and is recorded with the
  user, but nothing restricts who may do it. Give accounts only to people you trust with that decision.
- Audit trail is partial: model/training transitions (`ModelEvent`) and dataset export attempts (`ExportEvent`,
  `GET .../datasets/<id>/exports`) are append-only. Project, label and feedback-export changes are not audited.
- Deleting a picture that belongs to a frozen dataset version is refused (HTTP 409) unless the caller passes
  `?remove_from_snapshots=1`; then those versions are permanently marked incomplete and can no longer be exported.
- No encryption at rest by Athena; use disk or volume encryption for sensitive images.
- SQLite on one host. Run one worker. Scale-out needs a different database and queue.
- Rights/licence review is not implemented. Dataset versioning and COCO/YOLO export exist as a JSON API; see [`docs/guides/data-export.md`](../docs/guides/data-export.md).
- Auto-label quality has not been benchmarked against labelled data; treat proposals as a starting point.
