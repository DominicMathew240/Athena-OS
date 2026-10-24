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
| Dataset API | `/api/projects/<id>/datasets…`: immutable dataset versions of reviewed boxes and COCO/YOLO zip export. The console has a "Make a training set" panel for it (save a snapshot, download a zip); the rest is JSON API. |
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
separate from Athena's, and **CLIP ships enabled by default with an unresolved licence caveat** (see
[Licences and model terms](#licences-and-model-terms) before enabling the worker). Once cached, set `ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0` to forbid further downloads.

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
| `ATHENA_TRAINING_ALLOW_DOWNLOAD` | `0` in `.env.example` (unset = off) | May the training helper download ready-made models? `1`/`true`/`yes` = on, any other non-blank value = off. **A choice stored in the console (Access > Ready-made model downloads) wins over this**, in both directions, and cannot be cleared back to this setting. See [Ready-made starting models](#ready-made-starting-models-in-the-teach-panel). |
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
such as "damaged" cannot be judged from a crop and will need relabelling in review. Pictures that came into a
project from operator corrections (check-screen feedback, see [`docs/guides/improve-loop.md`](../docs/guides/improve-loop.md)) are never
auto-suggested: a re-run would delete their unreviewed boxes, so the job leaves them out and says how many.

## Licences and model terms

Athena OS is Apache-2.0, and the auto-label worker downloads third-party model weights at runtime that are
**not** covered by that licence. They are not in the repository or the Docker image; your worker fetches them
from Hugging Face on first use, so their terms apply to *you*. This section summarises
[`docs/LICENSE_AUDIT.md`](../docs/LICENSE_AUDIT.md) (audit date 2026-10-16, an engineering audit, **not legal advice**);
read its §5 for the sources.

| Model | What the audit verified | Practical reading |
| --- | --- | --- |
| SAM `facebook/sam-vit-base` | Hugging Face tag and model card declare **Apache-2.0**; the upstream repository's LICENSE is Apache-2.0. | Commercial use is permitted; keep the Apache-2.0 notice. The SA-1B *training dataset* has its own research licence that governs downloading the dataset, not using the weights; its text could not be fetched, so its terms are unverified. |
| CLIP `openai/clip-vit-base-patch32` | The Hugging Face repo **declares no licence** (no `license:` tag, no LICENSE file; rechecked 2026-10-17). The upstream code repo `openai/CLIP` is MIT, but that licence covers "the Software" in that repo, and no statement explicitly licensing the *weights* was found. The model card says: "**Any** deployed use case of the model - whether commercial or not - is currently out of scope." It also lists "surveillance and facial recognition" use as always out of scope, and says the training data (web-crawled image-caption data) will not be released. | This is an intended-use statement, not a licence term, but a customer or regulator can point to it, and running the auto-labeller on your server is a deployed use. Athena's design (CLIP only proposes labels, a person must accept each one) reduces the exposure; it does not remove it. **Do not describe these weights as MIT-licensed or cleared for commercial use.** |

### CLIP: what ships by default, and what you must decide yourself

**The default configuration ships with CLIP enabled.** `ATHENA_AUTOLABEL_BACKEND` defaults to `sam` and `ATHENA_CLIP_MODEL`
defaults to `openai/clip-vit-base-patch32` (`athena_web/settings.py`, `.env.example`). On 2026-10-17 the project owner decided to keep
CLIP as the default for auto-suggested labels. That is the **owner's own risk acceptance for the project's default setup. It is not a
legal clearance, and it does not transfer to you.** When you deploy, you run the worker, you download the weights, and you carry the
exposure. **Take your own legal advice before any commercial deployment**, in particular if you offer auto-labelling to customers, sell
a service around it, or use it on security, screening or other surveillance-adjacent data. The reasoning and the exact text read are in
[`docs/LICENSE_AUDIT.md`](../docs/LICENSE_AUDIT.md) ("Owner decision: CLIP stays enabled").

The caveat in one line: the Hugging Face repo states no licence for the weights, the model card calls any deployed use out of scope,
and the training data is undisclosed.

Your options:

1. **Keep CLIP** (the default) once you have evaluated the model card and taken advice. Athena has not measured auto-label accuracy on
   real items; treat every proposal as a starting point for a person to check.
2. **Switch auto-labelling off.** Set `ATHENA_AUTOLABEL_BACKEND=disabled` in `.env` (`off` or an empty value also disable it) and restart the
   `web` and `worker` services. The console hides the feature and `manage.py autolabel_worker` exits with
   "Auto-labelling is disabled". You can also simply never start the worker, but the console would still offer the button and queue jobs.
   Manual labelling, review, snapshots, teaching and check screens do not use SAM or CLIP.
3. **Swap the classifier.** Set `ATHENA_CLIP_MODEL` to a Hugging Face model id that loads through `transformers`' `CLIPModel` and
   `CLIPProcessor` (the worker calls exactly those two classes). Athena has not tested any alternative, and a different model needs its
   own licence and quality review. Prefer one that states an explicit weights licence. `ATHENA_SAM_MODEL` works the same way for
   the segmenter. Weights already cached under `/data/models` are not removed by changing the id.
4. Keep `ATHENA_AUTOLABEL_ALLOW_DOWNLOAD=0` after the first download so the weights you reviewed are the ones in use.

Other terms to know (details in the audit): Python dependencies are permissive (certifi and tqdm are MPL-2.0, fine
unmodified; attributions are in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)); if you *distribute* the
Docker image you take on Debian's GPL/LGPL source-availability obligations for the base layer; and Hugging Face's own
terms apply to the downloads (not reviewed).

### Ready-made starting models in the Teach panel

**What people see.** The "Teach the computer" button always starts from a ready-made model that already finds everyday objects in
photos, never from nothing: in a measurement on made-up shapes a model started from nothing found 0 of 144 test boxes
([`docs/guides/model-quality.md`](../docs/guides/model-quality.md#the-one-click-start-pretrained-auto)). On a fresh install **the button
is off**, with the red sentence "No ready-made starting model can be used on this computer right now … Only an administrator can fix
this", because downloads are switched off (the built-in default, or `ATHENA_TRAINING_ALLOW_DOWNLOAD=0` in `.env.example`) and nothing has
been cached. The button turns on once one of the two ready-made models can run, and "Check again" in the panel re-reads the state without
reloading the page.
(An administrator can still start a run from nothing under "Details for your administrator" → "Teach with exactly these settings";
the API accepts it too. That route is not a recommendation.)

**Who and where.** Two ways to allow the download; the first is the intended one.

1. **In the console (an administrator).** Sign in as a superuser, open **Access** (`/access#ready-made-model-downloads`) and use
   **Ready-made model downloads**: choose "Yes, allow downloads", tick that you have read the licence text shown there, write a short
   note saying why (at least 3 characters), and press "Switch downloads on". Switching **off** needs no tick and the note is optional.
   Only a superuser can change it; Trainers and above can read it (`GET /api/console/training-downloads`), and the Access screen itself
   is for administrators only. Every change is kept as an audit record (who, role, before and after, whether the licence tick was
   given, the note, and where the earlier answer had come from). The change happens in the database: the **console never downloads
   anything**. The training helper reads the setting from the same database each time it reports for duty and each time a run starts,
   so there is **nothing to restart**, but the helper and the console must share one database (Docker: the same `athena-data` volume;
   plain install: the same `ATHENA_DATA_HOME`). If the helper cannot read the database, its environment decides, as it did before the
   switch existed.
2. **In the environment** (whoever installed Athena, on the computer that hosts Athena): `ATHENA_TRAINING_ALLOW_DOWNLOAD`, below as
   Option 1.

**Which one wins.** A choice made in the console, on or off, wins over `ATHENA_TRAINING_ALLOW_DOWNLOAD` in both directions; the
environment variable is only used until someone chooses in the console. Without either, downloads are off. **There is no way back to
"environment" from the console:** once a console choice is stored it stays, and only changes between on and off. So an administrator who
switches downloads off in the console is not undone by `ATHENA_TRAINING_ALLOW_DOWNLOAD=1` in `.env`, and the Teach panel's own
sentences say so (precedence is in `web/projects/console_settings.py`, `training_downloads`). `GET …/training/backends` shows who
answered in `downloads.reported_by`: `setting` (the stored console switch), `helper` (the running helper's own report of its
environment) or `console` (neither, so the web process's environment: a guess, and the page says so); see
[`docs/api-reference.md`](../docs/api-reference.md).

**What you are agreeing to.** The two models are the ones torchvision publishes (`FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1`,
about 74 MB, and `MobileNet_V3_Large_Weights.IMAGENET1K_V1`, about 21 MB). They carry their own training-data terms (COCO, ImageNet),
separate from the free software that trains them, and nobody has confirmed whether those terms reach a model taught from them. The
panel shows the licence note and a tick-box the server enforces. Read [`docs/guides/model-quality.md`](../docs/guides/model-quality.md)
and ask a lawyer before commercial use; Athena is not legal advice.

**Option 1: let the helper download once** (from `download.pytorch.org` for the two torchvision models, checked against the checksum in
each file name, kept under `<data home>/models/pretrained/checkpoints/`). Allow it with the console switch above, or, if nobody has
chosen in the console yet, with the environment variable:

- *Console:* nothing else to do on the host; press "Check again" in the Teach panel.
- *Docker Compose, environment variable:* in `web/.env` change `ATHENA_TRAINING_ALLOW_DOWNLOAD=0` to `ATHENA_TRAINING_ALLOW_DOWNLOAD=1`, then run
  `docker compose up -d train_worker` in the `web` folder (every service reads the same `.env` through `env_file`, so they agree). The
  files land in the `athena-data` volume (`/data/models/pretrained/checkpoints/` inside the containers).
- *Plain install, environment variable:* set `ATHENA_TRAINING_ALLOW_DOWNLOAD=1` in the environment of the process you start with `python manage.py train_worker`
  and restart it. The data folder is `ATHENA_DATA_HOME`, by default `~/.athena-os`.
- Then press "Check again" in the Teach panel. To get back to only files you reviewed, switch downloads off (in the console, or set the
  variable back to `0` if no console choice exists; a file already in the folder and matching its checksum is used with downloads off).
- **Firewall allow-list:** the helper (not the web process) needs outbound HTTPS to `download.pytorch.org` for the torchvision models,
  and to `huggingface.co` **and `*.hf.co`** for the D-FINE files below (Hugging Face redirects file downloads to a content-delivery host
  under `hf.co`). A redirect is followed only to those hosts, only over https, never to an address with a user name or password in it,
  and each file is cut off at its pinned size and checked against its SHA-256 (D-FINE) or the checksum prefix in its name (torchvision)
  before it is kept (`redirect_refusal` in `web/projects/training/torchvision_frcnn.py`). A redirect anywhere else stops the download with
  a plain message naming the host, so a proxy that rewrites hosts will break downloads rather than loosen the rule.

**Option 2: no download, copy the files in.** Put these two files into `<data home>/models/pretrained/checkpoints/` (Docker: the
`/data/models/pretrained/checkpoints/` folder of the volume, readable by the container's user, uid 10001):

| File | Source | Used for |
|---|---|---|
| `fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth` | `https://download.pytorch.org/models/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth` | the COCO start (preferred) |
| `mobilenet_v3_large-8738ca79.pth` | `https://download.pytorch.org/models/mobilenet_v3_large-8738ca79.pth` | the ImageNet start (fallback) |

A file is used only when its SHA-256 starts with the eight characters in its name; a wrong or damaged file is ignored. One file is
enough: Athena prefers COCO, then ImageNet, and takes the first one that can run. The file names, folder and checksum rule are the ones
in `PRETRAINED_FILES`, `_weights_path` and `cached_weights_ok` in `web/projects/training/torchvision_frcnn.py`; the exact text the
panel shows is `AUTO_NONE_STEPS` in the same file (it points back to this section by title).

**Optional second tool: D-FINE (`dfine-coco`).** An administrator can pick it in the Teach panel's "Details for your administrator" fold; the
yellow button keeps using the default torchvision tool (`DEFAULT_BACKEND` is unchanged, and the choice of a default ready-made base is the
owner's open licence decision). It is registered in `web/athena_web/settings.py` (`TRAINING["BACKENDS"]`), so the console lists it, but it is
usable only where its extra packages are installed:

- **Install** (worker environment only; the web process never imports torch): `pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-autolabel.txt`.
  The file now ends with one extra block, `scipy==1.18.1`, on top of `torch==2.9.1` and `transformers==5.17.0` (scipy because
  transformers' matcher used by the training loss raises "requires the scipy library" without it). A worker image built before that line
  was added has no scipy, so rebuild it (`docker compose build`); that rebuild has not been run end to end here. On Python 3.14 the
  `numpy==2.1.3` pin may need raising: D-FINE was exercised with numpy 2.3.2 (see the comment in `requirements-autolabel.txt`).
  Until they are installed the backend names the missing packages and the panel says it cannot run.
- **Weights.** Only two pinned checkpoints are ever loaded, never chosen by name: `ustc-community/dfine-nano-coco` (revision `066438d3d8f0…`,
  about 15 MB) and `ustc-community/dfine-small-coco` (revision `f79e65b5fbb3…`, about 41 MB), each file checked against a pinned size and
  SHA-256 (`CHECKPOINTS` in `web/projects/training/dfine.py`). The two Objects365-based families (`*-obj365`, `*-obj2coco`) are refused in code.
  The model cards declare `license: apache-2.0` and `datasets: coco`; that is the model repository's licence, not a statement about the
  COCO photos, and the COCO question is the same as for the torchvision COCO start. See
  [`docs/guides/model-quality.md`](../docs/guides/model-quality.md#d-fine-the-optional-second-tool) and
  [`docs/LICENSE_AUDIT.md`](../docs/LICENSE_AUDIT.md) (engineering read, not legal advice).
- **Same switch.** Downloads use the same console switch / environment variable as above; with it off, nothing is fetched. Allow-list
  `huggingface.co` and `*.hf.co`.
- **Cached-weights alternative (no download).** Put, for the size you want, `config.json` and `model.safetensors` from
  `https://huggingface.co/<repo_id>/resolve/<revision>/<file>` into
  `<data home>/models/pretrained/dfine/<repo_id with / as __>/<revision>/` (for nano:
  `models/pretrained/dfine/ustc-community__dfine-nano-coco/066438d3d8f0da137a37b38fdf3368fd4afceced/`). A file is used only if its size and
  SHA-256 match the pinned values exactly; otherwise it is treated as missing (and replaced if downloads are on).
- **What was run.** A real CPU run of the nano model on made-up shapes (synthetic, not scanner images) and a module test suite in a
  separate virtual environment; the small model was not run, ONNX export did not work, and no run has used real ready-made weights through
  the Teach panel's one-click path. See the numbers, clearly labelled, in `docs/guides/model-quality.md`.

**What has not been tried.** A real run on the one-click path with real ready-made weights through the Teach panel has not been done; the new flow was
exercised with the weights stubbed out and on a machine where none existed. The console switch was exercised by tests and by scripted
browser walks on a disposable instance, not by a real administrator on a deployed one. The Docker steps above follow the compose file and have not
been run end to end. The time shown next to Quick and Better is an estimate worked out from one laptop-class processor (an Apple M3,
with other programs running); a different computer will take longer or shorter.

## Known limits

- Access roles (Viewer, Labeler, Trainer, Approver; see [`docs/guides/access-roles.md`](../docs/guides/access-roles.md)): every signed-in user can **see** every project, but changing things needs a role. Roles are Django groups, optionally overridden per project, assigned by a superuser on the console's **Access** screen or with `manage.py athena_roles`; every change is recorded. People in no role group count as Labelers (`ATHENA_UNASSIGNED_ROLE`) and can never approve a model. Give the Approver role only to people you trust with that decision, and give superuser rights sparingly: a superuser can do everything and is the only kind of account that can change roles.
- Audit trail is partial: model/training transitions (`ModelEvent`), dataset export attempts (`ExportEvent`,
  `GET .../datasets/<id>/exports`) and changes to the download switch (`ConsoleSettingEvent`; the Access screen shows only when and which way
  the last change went, plus who and the note for a change made in that same page visit, and there is no Django admin site, so read the
  records from the database) are append-only. Pulling a check-screen correction into a project leaves one append-only `FeedbackImport` row
  per result (who, their role, which model version and checksum, what was made; read it from the database or via `GET …/feedback/corrections`).
  Project, label and feedback-export changes are not audited.
- Deleting a picture that belongs to a frozen dataset version is refused (HTTP 409) unless the caller passes
  `?remove_from_snapshots=1`; then those versions are permanently marked incomplete and can no longer be exported.
- No encryption at rest by Athena; use disk or volume encryption for sensitive images.
- SQLite on one host. Run one worker. Scale-out needs a different database and queue.
- Rights/licence review is not implemented. Dataset versioning and COCO/YOLO export exist as a JSON API with a "Make a training set" console panel; see [`docs/guides/data-export.md`](../docs/guides/data-export.md).
- Auto-label quality has not been benchmarked against labelled data; treat proposals as a starting point.
