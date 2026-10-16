# Athena OS Open-Source Roadmap

**Release and timing:** no public release date is committed. This roadmap separates the Python developer foundation already present in the tree from the labeling, training, and deployment platform that still needs to be built. A milestone is not complete until its exit gate is demonstrated from a clean install and documented for nontechnical users.

## Product goal

Build one open-source industrial platform where teams can manage many data types, create reviewed datasets with task-appropriate annotation tools, train/evaluate models, and configure web interfaces for real-world inference. A shared multimodal project/data/model lifecycle plus documented modality and connector plugins is the cross-cutting foundation; each milestone still proves one concrete workflow end to end.

The first end-to-end workflow is image object detection, based on the user's scanner and illegal-item example. It must sit inside a shared dashboard that can represent image, video, audio/speech, text/documents, structured and time-series data, depth/point clouds, and community-defined formats. Scanner protocols and later modality order remain open. Model inference belongs in the configured operational UI; manual annotation remains human-authored. Operator true/false corrections are retained as feedback and training candidates, never applied to live model weights automatically.

## What exists in the current source tree

- Versioned Python/Pydantic contracts for observations, assets, models, findings, evidence, policies, and actions.
- SDK interfaces for model adapters, local runtime, domain packs, registries, and evaluation.
- Synthetic image and audio examples, including a third-party YOLO adapter example and a signal-processing example.
- A CLI for contract validation and examples, plus an early standalone local document-library experiment that is not the core product.

The tree includes a Django project registry, a validated **local image-import** slice, and a basic **manual bounding-box** labeling slice: JPEG, PNG, and WebP files are decoded, validated, stored under generated names, and recorded with source filenames and SHA-256 hashes; boxes can be added by dragging, listed, reclassified, and deleted. These remain unreviewed candidates; there is no rights-evidence workflow, complete/accessibility-verified annotation workspace, versioned dataset, training orchestrator, configurable inference UI builder, scanner connector, or production feedback/retraining loop. Do not describe these slices as a complete dataset manager.

## M0 — Open-source release readiness

- [ ] Verify the exact repository contents, ownership, licenses, dependency notices, model/data rights, and trademark/asset boundaries.
- [ ] Confirm that all promised platform workflows can run without a proprietary Athena account or service.
- [ ] Publish maintainer, contribution, issue, code-of-conduct, security-reporting, and release processes.
- [ ] Add automated secret, dependency, license, and package checks to CI.
- [ ] Build a clean source archive and verify installation/tests without development-machine state.

**Exit gate:** publish a reviewed source artifact with complete licenses/notices, safe security reporting, clean-install evidence, and no private datasets, model weights, credentials, or unapproved brand assets.

## M1 — Developer foundation and extension contracts

**Current state: implemented in source; public release and adoption checks remain open.**

- [x] Versioned industrial observation/model/finding/evidence contracts and Python interfaces.
- [x] Reference runtime, adapters, evaluation examples, and synthetic vision/audio pipelines.
- [ ] Document project, dataset, annotation, review, model-run, feedback, inference-UI, and connector boundaries before locking new public contracts.
- [ ] Define compatibility/versioning policy for annotation geometry, modality payloads, task schemas, and model outputs.
- [ ] Publish extension guides for model, training, data-source, modality, and deployment plugins.
- [ ] Verify extension examples from clean environments and add contributor onboarding tests.

**Exit gate:** an external contributor can implement and test one compatible adapter or connector from public documentation without importing proprietary Athena code.

## M2 — First usable labeling-to-inference workflow (image detection)

**Status: in progress.** A sign-in-protected Django console (project registry, validated image import, manual bounding boxes, queued SAM + CLIP auto-labelling with human review) is implemented, tested, and packaged for self-hosted deployment (`web/DEPLOYMENT.md`). Dataset snapshots, downloads, teaching, model decisions and check screens now have console/browser screens (2026-10-16, see `docs/dogfood/2026-10-operator-ux-report.md`); they have been walked through by an automated tester on a local synthetic-data instance only. Rights review, assistive-technology verification, training, and inference remain incomplete.

### Project and data setup
- [ ] Create a browser-based dashboard that explains projects, datasets, labels, model status, and next steps in plain language. **UI status (2026-10-16):** the console now has project, label, picture, box, snapshot/download, teaching, model-decision and check-screen panels with plain-language empty/blocked states. One automated first-time-operator walk-through (Chromium, synthetic data, toy model, local instance) completed every step; see `docs/dogfood/2026-10-operator-ux-report.md`. Still open: a usability session with real operators, assistive-technology testing, Firefox/Safari, and wording review by operators. Left unticked.
- [ ] Define a shared project/dataset registry that can list heterogeneous modalities in one workspace while routing each task to its own appropriate tools.
- [ ] Design an extensible modality/connector manifest so community plugins can add new data shapes, import validation, previewers, annotation tools, and inference inputs without changing the core dashboard.
- [ ] Let an operator create an image object-detection project and define/edit label names, definitions, examples, and review rules without editing code.
- [x] Import JPEG, PNG, and WebP images, decode and validate each file, and record the source filename and SHA-256 hash; rights evidence remains unresolved.
- [x] Store project data and imported images in a documented self-hosted location and provide explicit deletion; dataset export is available through the API only (see Dataset, training, and model lifecycle).

### Human annotation and review
- [ ] Complete an accessible image annotation workspace with point-and-drag bounding boxes, class assignment, box selection/move/resize/delete, keyboard alternatives, zoom/pan, undo/redo, and clear save state. **2026-10-16 browser check:** dialog focus is trapped and restored (0 escapes in 60 Tab presses), boxes drawn by mouse and by typed position, suggested vs accepted boxes visually distinct, phone width (390 px) has no horizontal scroll. Not verified: any screen reader, touch devices, large images. Left unticked.
- **Implemented subset:** the local prototype can create boxes by pointer or keyboard percentage entry, view/reclassify/delete them, and move/resize selected boxes with pointer or keyboard controls. Image zoom (50–400%), scroll/trackpad pan, and undo/redo (last 100 changes, in-memory until the dialog closes) are available. Full assistive-technology verification and review/provenance remain open.
- [ ] Support review queues and statuses such as unreviewed, reviewed, and corrected; retain who/when/version provenance where identity is configured. *Partly done:* model proposals carry reviewer and review time, and manual boxes now record their author (`created_by`; exports expose only an opaque `u<id>`). Not done: a review-queue UI, a distinct `corrected` state, and who/when history beyond the latest review.
- [x] Model-suggested boxes (SAM + CLIP, queued in a worker) are stored as `Needs review` with model/job/score provenance and count as ground truth only after a person accepts or edits them; bulk accept/reject is available. Export is now gated on review status (see below). Open: measure proposal accuracy on labelled data, and gate training on review status once training exists.
- [x] Validate annotation geometry and class references before export. Backend only: every accepted box must have positive size inside [0,1], a label that exactly (case-sensitively) matches the project's labels, and an image file that exists with a SHA-256 still matching the upload; any failure blocks the version, with each problem reported by asset id (`GET /api/projects/<id>/datasets/readiness` previews this without creating anything). The console now shows this report as "Is your work ready?" (2026-10-16); still open: the geometry checks are only as strong as the database constraints plus these checks (no check against decoded image dimensions).

### Dataset, training, and model lifecycle
- [x] Version datasets and export at least one documented interchange format. **Format decision: both COCO (instances JSON, absolute-pixel `[x,y,w,h]`) and YOLO (txt, normalized `class cx cy w h`, `classes.txt`, `data.yaml`) are implemented**, selected by `?format=coco|yolo`. Backend only (`/api/projects/<id>/datasets`): immutable numbered versions with frozen image/box snapshots, accepted boxes only (`Needs review` boxes are never included and the response reports how many were excluded), deterministic hash-based 80/10/10 train/val/test split (seed and ratios configurable, byte-identical images always share a split), class-balance and duplicate summary, `manifest.json` with per-file SHA-256 plus a recorded `manifest_sha256`, streamed path-safe zip with a size cap, and round-trip tests for both formats. Not done: no console UI; formal licence review of the COCO/YOLO format specifications and of the `info` block conventions has not been done here; near-duplicate (non byte-identical) leakage is not detected; exports are audited (`ExportEvent`); frozen versions keep metadata and checksums but not copies of the image bytes, so deleting a picture that is part of a version is refused with HTTP 409 unless the caller passes `?remove_from_snapshots=1`, in which case the picture is deleted and each affected version is permanently marked incomplete (`incomplete_reason`, the only field of a version that can ever change; its manifest and checksum stay verifiable, but export is refused with a count of the missing pictures); the snapshot cannot yet be re-imported (no import/migration tooling).
- [x] Add one opt-in training backend behind a replaceable interface; document hardware, runtime, model-license, and dependency constraints. Backend only (`web/projects/training/`, `GET /api/projects/<id>/training/{backends,readiness}`, `POST/GET .../training/runs`): `TrainingBackend` (`name`, `license_note`, `check_available()`, `train()`, `evaluate()`, `predict()`) with a registry that also loads third-party backends from the `athena.training_backends` entry-point group or `settings.TRAINING["BACKENDS"]`; the web process never imports torch (`manage.py train_worker` does the work, single instance, heartbeat, cancel, stuck-run recovery). Reference backend `torchvision-fasterrcnn` (torchvision/PyTorch BSD-3; MobileNetV3-Large 320 FPN, CPU, `weights=None` so nothing is downloaded and the model starts from random weights). **Verified:** unit tests plus a real 1-epoch CPU train → evaluate → predict run in a separate venv with torch 2.9.1 + torchvision 0.24.1 (pins added to `web/requirements-autolabel.txt`). **Not verified / open:** model quality (from-scratch training on small datasets will be weak; no accuracy claim is made), GPU, long runs, other OS/arch, pretrained-weight options and their licences, and written hardware/runtime documentation outside the code (`constraints`/`license_note` strings are served by the API; DEPLOYMENT.md has not been updated); no console UI.
- [x] Track immutable training configuration, dataset version, model checksum, evaluation results, and failure logs. Backend only: a run stores its normalized config + `config_sha256` + the dataset version and its `manifest_sha256` (write-once; enforced in `save()` and bulk `update()`), runtime/hardware info captured at start (platform, Python, framework versions, device; no host names or paths), capped logs and a sanitized error text; each `ModelVersion` stores the artifact SHA-256 (re-checked before approval and before every prediction), label snapshot and evaluation. An append-only `ModelEvent` log records run creation/outcome and every stage change with actor and note. Open: no UI; the audit log covers runs/models only, not other project changes; artifacts are single files on local disk (no remote storage, no backup/retention policy).
- [x] Provide a human-readable evaluation report and a staging step; never promote a model solely because training completed. Backend only (`GET .../models/<id>/evaluation`, `POST .../models/<id>/{stage,approve,rollback}`): training success creates a `candidate` and nothing more; `staged` requires a held-out evaluation (an empty test split gives "Evaluation not available" and staging is refused); `approved` requires a signed-in user's explicit POST with a note, only from `staged`, and in one transaction retires the previously approved model and keeps a rollback pointer; rollback restores it. Metrics (precision/recall at IoU 0.5 and a stated confidence, AP50, per label) come from our own numpy function with unit tests and are reported with image/box counts and plain-language limits. Open: no UI; comparison against the currently approved model is not computed; calibration is not measured; near-duplicate leakage is not detected; any signed-in user may approve (there are no roles yet, and no separate-approver rule); the numbers say nothing about field accuracy.

### Custom real-world inference interface
- [ ] Let an operator configure and preview a web interface for a selected model: task name, accepted inputs, displayed classes, thresholds, status text, and human decision controls. *Backend done, no UI:* `InferenceApp` configs are strictly validated (unknown keys rejected; JPEG/PNG/WebP only), can only point at an `approved` model version, and `GET .../inference/apps/<id>/preview` returns the exact render model (including the three result states with the operator's wording). Not done: the configuration/preview screens themselves. **UI update (2026-10-16):** the builder (`/projects/<id>/check-screen/setup`) and a "Check screens" list on the project page now exist; verified in Chromium (create, edit, switch model, retired-model warning, preview, unsaved-edit warning). Not verified: operators' own understanding of the options, Firefox/Safari. Left unticked.
- [ ] Add an initial safe test input path (local image upload/sample) and a connector interface for scanners or operational data sources; do not hard-code a vendor protocol before one is selected. *Partly done (backend):* `POST .../inference/apps/<id>/run` accepts one image upload, validated with the console's existing image checks (type, size, pixels), stored with its SHA-256, and predicted by `manage.py inference_worker` via a DB queue (same pattern as auto-labelling; 503 with how-to-enable text if the backend cannot run). Not done: sample images, any connector interface, an upload UI. **UI update (2026-10-16):** the operator screen (`/check/<slug>`) accepts one upload, refuses wrong file types with a plain message, and ran real predictions on the CPU worker (toy model). Still no sample images and no scanner connector. Left unticked.
- [ ] Show detections, uncertainty, and model/version provenance in the configured UI. A model result is not a final legal determination; make human review/override explicit for high-impact outcomes. *Backend render model done, no UI:* every result carries the exact model version + artifact SHA-256 + evaluation summary, scores, hidden-detection counts and the text that output is not a final determination; with `human_review_required` (default on) a result is `needs_review` until a person records confirm/reject/second-look, and overrides can require a note. Not done: the screen that renders it; calibration of scores (they are raw model scores, not probabilities). **UI update (2026-10-16):** the operator screen renders boxes with numbered tags, 'Very sure · 99%' wording, the not-a-final-decision banner and the model version; per-finding right/wrong, missed-object and whole-result decisions were exercised. Scores are raw model scores of a toy model, not calibrated. Left unticked pending an operator session.
- [x] Record true/false feedback against the exact observation, model version, and operator decision. Make it exportable as a reviewed training candidate. Backend only: immutable `FeedbackRecord` (true_positive / false_positive per shown detection, or a missed object with label and optional box), `ReviewDecision` per observation, and `GET .../inference/apps/<id>/feedback/export?format=json|jsonl` with the image SHA-256, model version/artifact checksum/training-run/dataset-manifest lineage and the decider (opaque `u<id>`). Open: no UI; feedback is not turned into a dataset version (see M4); later verdicts supersede earlier ones in the export rather than editing them.
- [ ] Keep the live model unchanged until an operator reviews the feedback, starts a new training run, inspects its evaluation, and explicitly approves the replacement. *Backend enforced, flow incomplete:* feedback never touches a `ModelVersion` or any dataset/training run (tested); an app stays pinned to the exact version it was set up with and refuses to run once that version is no longer approved until an operator re-points it; approving or rolling back a model does not silently re-point apps. Missing: the reviewed-feedback → dataset-version step (M4) and any UI. **2026-10-16 check:** after 23 operator runs, right/wrong feedback and picture removal, both models' recorded `artifact_sha256` still equalled the hash of their files, and the rows were identical before and after a last run with feedback. The reviewed-feedback → dataset step and any UI for it are still missing. Left unticked.

*Gate status (2026-10-16): NOT met. The mechanical flow has been run end to end once by an automated tester; the "observed in a usability session" condition needs real operators and is untested.*

**Exit gate:** from a clean self-hosted install, a nontechnical operator can use one dashboard to create a project, add and manually label images, review and export a versioned dataset, train or connect a compatible model, configure a task-specific web UI, run an image through it, submit true/false feedback, and verify that the correction is preserved without changing live model weights. The project registry/connector boundary must be modality-extensible, but only the image-detection path is claimed complete at this gate. The flow must be covered by tests and observed in a usability session; production scanner integration remains gated on selecting real hardware/protocol.

## M3 — Multimodal data and annotation

- [ ] Add reusable modality and task contracts beyond RGB-image detection.
- [ ] Prioritize audio tasks with explicit time ranges, clip labels, and transcription/event review as a separate workflow.
- [ ] Add video frame/timeline annotation and synchronized multimodal inputs where a real use case justifies them.
- [ ] Evaluate thermal, depth, point-cloud, and sensor-timeseries data through adapter/renderer plugins rather than forcing them into image boxes.
- [ ] Validate round-trip import/export against published dataset formats and provide migration tooling for schema changes.
- [ ] Publish synthetic, clearly labeled example projects for each supported workflow.

**Exit gate:** each modality has a task-specific annotation UI, a tested import/export path, a model adapter path, and provenance-preserving evaluation; unsupported formats fail clearly rather than being silently coerced.

## M4 — Safe feedback, retraining, and model quality

- [ ] Turn approved false-positive/false-negative corrections into a reviewable dataset version with exact source/model lineage.
- [ ] Add dataset quality checks, duplicate detection, class imbalance summaries, and evaluation splits that prevent leakage.
- [ ] Add configurable retraining runs and comparisons against the currently approved model.
- [x] Require explicit human approval for promotion; retain rollback to the previous model. Backend only — see the staging item above (noted POST required, only from `staged`, atomic retire of the previous model, `rollback` endpoint, DB constraint allowing at most one approved model per project). Open: no UI, no approver roles.
- [x] Add audit records for project changes, model promotion, feedback disposition, and export. *Done for what exists:* append-only `ModelEvent` rows for training-run creation/outcome and every model stage transition (actor, time, from/to, note); feedback and review decisions are immutable rows with author and time; append-only `ExportEvent` rows for every dataset-version export attempt, successful, refused or abandoned (version, user, format, bytes streamed, status, error, time), listed at `GET /api/projects/<id>/datasets/<dataset_id>/exports`. **Not done (still open, annotated rather than silently dropped):** project and label changes are not audited, and the inference feedback export (`.../feedback/export`) writes no audit row; audit rows are readable through the API only (no console screen, no filtering or retention policy).
- [ ] Measure performance on representative held-out data; publish limits and calibration evidence rather than inferred accuracy claims.

**Exit gate:** a correction can be traced from operator decision to reviewed training example, new run, evaluation, approval, and rollback. No correction silently alters the model used by a live interface.

## M5 — Field-ready self-hosting and data-source connectors

- [ ] Package the dashboard and runtime for the selected operating systems and deployment modes (workstation, edge, or self-hosted server after discovery).
- [ ] Add connector SDKs, health/status views, retries, backpressure, and clear recovery for scanners and industrial feeds.
- [ ] Define secure transport, credentials handling, encryption, retention, backup/restore, update, and deletion requirements before production deployments.
- [ ] Add configurable access roles, audit exports, and operator-friendly recovery for shared deployments.
- [ ] Test disconnected/limited-network operation where the chosen deployment requires it.
- [ ] Document regulatory, safety, and model-license responsibilities without claiming the platform itself certifies detections.

**Exit gate:** a supported deployment can connect to a selected real data source, sustain documented operational conditions, recover from interruption without losing provenance, and pass security and operator acceptance reviews.

## M6 — Open ecosystem and community operations

- [ ] Publish stable plugin APIs for modalities, annotation tools, training backends, model adapters, connectors, and inference-interface components.
- [ ] Provide plugin validation, compatibility matrices, example templates, and sandboxed integration tests.
- [ ] Add localization, accessibility reviews with disabled users, and industry-specific starter configurations that do not hard-code proprietary workflows.
- [ ] Build community governance for roadmap proposals, security response, releases, and deprecation.
- [ ] Publish reproducible release artifacts, checksums, SBOMs, migration notes, and measured support matrices.

**Exit gate:** independent contributors can maintain and distribute compatible plugins; the core platform remains usable without paid or proprietary dependencies.

## Optional future work (not a prerequisite for the open-source platform)

Managed hosting, multi-tenant cloud processing, enterprise identity integrations, hosted model serving, billing, and premium support may be considered separately. Their absence must not disable self-hosted labeling, training integrations, or configured inference UIs in the open-source product.

## How to help shape the roadmap

Open a focused proposal with the user problem, intended workflow, data modality, safety/privacy constraints, and a testable outcome. Breaking public contracts require an RFC and migration plan. Use synthetic data in examples and public issues; report vulnerabilities privately through the verified channel in `SECURITY.md`. Roadmap checkboxes reflect demonstrated code and tests, not intent or prototype screenshots.
