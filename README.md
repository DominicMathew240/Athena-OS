# Athena OS

**Current release: 1.4.0** — the version is declared once in [`web/athena_web/settings.py`](web/athena_web/settings.py) (`RELEASE_VERSION`) and shown in the console sidebar footer, the public [`GET /api/version`](web/projects/views.py) endpoint, and the `/healthz` probe. A deployment may set `ATHENA_VERSION` only to *add* a packaging suffix (for example `1.4.0+acme`); it can never mask the released version.

Athena OS is an open-source (Apache-2.0) toolkit for building reviewed datasets and models for industrial
data, starting with image object detection. It is early: see [`ROADMAP.md`](ROADMAP.md) for what is done and
what is not. This README only claims what the source tree and its tests demonstrate. The separate [vision-workflow tracker](docs/plans/2026-11-15-vision-workflows-task-list.md) records locally verified slices and remaining release gates; it is not a production-readiness claim.

The public marketing site is a static React app on the [`landing`](https://github.com/DominicMathew240/Athena-OS/tree/landing) branch (no backend). It deploys to GitHub Pages. This `main` branch is the product source: contracts, SDK, CLI, and the Django console in `web/`.

## Preview: how the system works

The console walks a nontechnical operator through six plain-language steps. Each step only becomes
usable when the ones before it have real content, and nothing is ever approved or published automatically.

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ 1. CREATE      2. ADD           3. LABEL          4. FREEZE                  │
 │  a project     pictures         boxes:            a dataset version          │
 │  (goal,        (JPEG/PNG/       drawn by hand     (immutable, numbered,      │
 │  labels)       WebP, checked    or suggested      hashed manifest,           │
 │                and hashed)      by SAM+CLIP,      COCO/YOLO export)          │
 │                                 always reviewed                             │
 │                                                                              │
 │ 5. TEACH       a model          6. CHECK & IMPROVE                            │
 │  a detector    (CPU training    a check screen runs new pictures              │
 │  from the      worker; the      through the approved model; operators         │
 │  snapshot)     web process      mark results right or wrong; corrections     │
 │                never loads      return as Needs-review pictures for the       │
 │                torch)           next teaching round                           │
 └──────────────────────────────────────────────────────────────────────────────┘
```

**What a first run looks like.** Install, run first-run setup (the first account becomes the
administrator), and the console opens on a plain-language checklist: create a project, add pictures,
draw boxes around the things you want found, save a training set, teach the computer, check the
results. Auto-suggested boxes always arrive as **Needs review** — nothing becomes ground truth until
a person accepts it. A finished teaching run only ever files a *candidate* model; a person must move
it to trial, read its results card, and approve it before anything can use it. Check-screen
corrections are kept as evidence and can be pulled back into the project for the next round — the
live model is never changed by feedback alone.

**Operations (people counting, private, in progress).** A second workflow is being built for
recorded-video operations: private sites and sources, an operation draft with a counting line and
human reference marks, and an immutable, versioned component chain — source view, geometry, recipe,
policy, screen, and an approved-model binding — that can be assembled into an append-only **release
bundle** (a release candidate; publication and runtime are not built yet). The console walks an
operator through this as a guided, resumable stepper, and an environment preflight (`GET
/api/operation-preflight`) says up front whether the server can actually run an operation — video
checking, private storage, the teaching and check-screen helpers, and the picture-reading model —
with the person responsible and one recovery step for each missing part. Today a draft cannot count
people, and the console says so.

### Try it locally

```bash
python -m pip install -r web/requirements.txt numpy
python web/manage.py migrate
DJANGO_DEBUG=1 python web/manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000> and complete first-run setup. Full self-hosting guide (TLS, workers,
Docker, S3): [`web/DEPLOYMENT.md`](web/DEPLOYMENT.md). To try the SDK and synthetic pipelines instead:
[`docs/getting-started.md`](docs/getting-started.md).

### What each part is for

| Piece | What it does |
|---|---|
| **Console** (`web/`) | The product: sign-in, projects, labelling, review, dataset versions, teaching, check screens, four access roles (Viewer, Labeler, Trainer, Approver) enforced server-side on every write. |
| **Contracts and SDK** (`sdk/python`, `contracts/`) | Versioned Pydantic contracts for observations, assets, models, findings, evidence, policies and actions; generated JSON Schema checked in. Needs Pydantic 2.11+. |
| **CLI and examples** (`cli/`, `examples/`) | Contract validation and synthetic image/audio pipelines. The YOLO example adapter needs `ultralytics` (**AGPL-3.0**) — read the warning in [`docs/getting-started.md`](docs/getting-started.md) first. |
| **Reference connectors** | Folder-drop and HTTP-push connectors with retries, backpressure and an idempotency ledger: [`docs/guides/writing-a-connector.md`](docs/guides/writing-a-connector.md). |

## Versioning

The console follows **semantic versioning** starting at `1.0.0`. The version lives in exactly one
place (`RELEASE_VERSION` in [`web/athena_web/settings.py`](web/athena_web/settings.py)) and is read
through [`athena_web/version.py`](web/athena_web/version.py):

- `GET /api/version` → `{"name": "athena-console", "version": "1.4.0"}` (public; a bug report
  should not need an account)
- `GET /healthz` → the version alongside the database/media readiness checks
- the console sidebar footer → `Athena OS 1.4.0` on every page

The SDK and CLI packages (`athena-sdk`, `athena-cli`) are versioned independently at `0.1.0` in
their own `pyproject.toml` files; they will track the console's major version when their public
contracts stabilise.

## What exists today

- **Athena console** (`web/`, Django): sign-in, project registry, a plain-language console, JPEG/PNG/WebP image
  import with checksums, a label editor, manual bounding boxes, and optional SAM + CLIP auto-suggested boxes that
  stay **Needs review** until a person accepts them. Self-hosting guide: [`web/DEPLOYMENT.md`](web/DEPLOYMENT.md);
  developer notes: [`web/README.md`](web/README.md).
- **Dataset versioning and export (JSON API plus a "Make a training set" console panel)**: immutable numbered versions of the
  accepted boxes, deterministic train/val/test splits, a hashed manifest, and COCO or YOLO zip export.
  See [`docs/guides/data-export.md`](docs/guides/data-export.md).
- **Teaching, models and check screens (prototype)**: a one-button "Teach the computer" part that trains a CPU detector from a
  ready-made starting model, a picture check, plain-language results, model stages with an approval note, check screens that run an
  image through the approved model, and four access roles. Operator corrections from check screens can be pulled back into a project as pictures with
  **Needs review** boxes and a new model compared with the one in use ([`docs/guides/improve-loop.md`](docs/guides/improve-loop.md)); nothing changes a model by
  itself. Tested on synthetic data and a toy model only; a usability session with
  real operators has not been run. See [`docs/guides/model-quality.md`](docs/guides/model-quality.md) for what was measured and what was not.
- **Operation drafts and release bundles (API only)**: private sites/sources/operations, recorded-video drafts with a counting
  line and reference marks, and the immutable component-version chain described above. Verified by
  tests and an end-to-end API walkthrough; no console UI, no runtime, nothing published.

## What does not exist yet

Rights/evidence review, a real-operator trace of the correction-to-new-model path (the pieces exist as a prototype: see
[`docs/guides/improve-loop.md`](docs/guides/improve-loop.md)), connectors for any real scanner (only reference connectors exist),
any measured accuracy on real imagery for the trained detector or for auto-labelling, real-operator usability evidence, and any
runtime that executes an operation release bundle. Do not treat the console as a complete dataset manager.

## Licences

Code is Apache-2.0 ([`LICENSE`](LICENSE)); docs are CC-BY-4.0 ([`docs/LICENSE`](docs/LICENSE)). Third-party
notices: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Model weights that the auto-labeller downloads have
their own terms, including a caveat on CLIP: see the licence audit and the "Licences and model terms" section of
[`web/DEPLOYMENT.md`](web/DEPLOYMENT.md). The audit is an engineering review, not legal advice.

## Where to start

[`docs/getting-started.md`](docs/getting-started.md), [`docs/architecture-overview.md`](docs/architecture-overview.md),
[`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md).
