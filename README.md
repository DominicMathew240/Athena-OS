# Athena OS

Athena OS is an open-source (Apache-2.0) toolkit for building reviewed datasets and models for industrial
data, starting with image object detection. It is early: see [`ROADMAP.md`](ROADMAP.md) for what is done and
what is not. This README only claims what the source tree and its tests demonstrate.

The public marketing site is a static React app on the [`landing`](https://github.com/DominicMathew240/Athena-OS/tree/landing) branch (no backend). It deploys to GitHub Pages. This `main` branch is the product source: contracts, SDK, CLI, and the Django console in `web/`.

## What exists today

- **Contracts and SDK** (`sdk/python`, `contracts/`): versioned Pydantic contracts for observations, assets,
  models, findings, evidence, policies, and actions, with generated JSON Schema checked in.
  Needs Pydantic 2.11 or newer.
- **CLI and examples** (`cli/`, `examples/`): contract validation and synthetic image/audio example pipelines.
  The YOLO example adapter needs `ultralytics`, which is **AGPL-3.0**; read the warning in
  [`docs/getting-started.md`](docs/getting-started.md) and [`docs/LICENSE_AUDIT.md`](docs/LICENSE_AUDIT.md) first.
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

## What does not exist yet

Rights/evidence review, a real-operator trace of the correction-to-new-model path (the pieces exist as a prototype: see
[`docs/guides/improve-loop.md`](docs/guides/improve-loop.md)), connectors for any real scanner (only reference connectors exist),
any measured accuracy on real imagery for the trained detector or for auto-labelling, and real-operator usability evidence. Do not
treat the console as a complete dataset manager.

## Licences

Code is Apache-2.0 ([`LICENSE`](LICENSE)); docs are CC-BY-4.0 ([`docs/LICENSE`](docs/LICENSE)). Third-party
notices: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Model weights that the auto-labeller downloads have
their own terms, including a caveat on CLIP: see the licence audit and the "Licences and model terms" section of
[`web/DEPLOYMENT.md`](web/DEPLOYMENT.md). The audit is an engineering review, not legal advice.

## Where to start

[`docs/getting-started.md`](docs/getting-started.md), [`docs/architecture-overview.md`](docs/architecture-overview.md),
[`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md).
