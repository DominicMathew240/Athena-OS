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

**Status: in progress.** A local Django project registry, validated image import, and basic manual bounding-box create/list/reclassify/delete path are implemented and tested. Rights review, full annotation editing/accessibility, dataset versioning/export, training, and inference remain incomplete.

### Project and data setup
- [ ] Create a browser-based dashboard that explains projects, datasets, labels, model status, and next steps in plain language.
- [ ] Define a shared project/dataset registry that can list heterogeneous modalities in one workspace while routing each task to its own appropriate tools.
- [ ] Design an extensible modality/connector manifest so community plugins can add new data shapes, import validation, previewers, annotation tools, and inference inputs without changing the core dashboard.
- [ ] Let an operator create an image object-detection project and define/edit label names, definitions, examples, and review rules without editing code.
- [x] Import JPEG, PNG, and WebP images, decode and validate each file, and record the source filename and SHA-256 hash; rights evidence remains unresolved.
- [x] Store project data and imported images in a documented self-hosted location and provide explicit deletion; dataset export remains unimplemented.

### Human annotation and review
- [ ] Complete an accessible image annotation workspace with point-and-drag bounding boxes, class assignment, box selection/move/resize/delete, keyboard alternatives, zoom/pan, undo/redo, and clear save state.
- **Implemented subset:** the local prototype can create boxes by pointer or keyboard percentage entry, view/reclassify/delete them, and move/resize selected boxes with pointer or keyboard controls. Image zoom (50–400%), scroll/trackpad pan, and undo/redo (last 100 changes, in-memory until the dialog closes) are available. Full assistive-technology verification and review/provenance remain open.
- [ ] Support review queues and statuses such as unreviewed, reviewed, and corrected; retain who/when/version provenance where identity is configured.
- [ ] Keep annotation human-authored. Do not use model-generated labels as a substitute for ground truth; an optional model suggestion feature requires a separate decision and human approval.
- [ ] Validate annotation geometry and class references before export.

### Dataset, training, and model lifecycle
- [ ] Version datasets and export at least one documented interchange format after format/license review (candidate: COCO or YOLO; not yet selected).
- [ ] Add one opt-in training backend behind a replaceable interface; document hardware, runtime, model-license, and dependency constraints.
- [ ] Track immutable training configuration, dataset version, model checksum, evaluation results, and failure logs.
- [ ] Provide a human-readable evaluation report and a staging step; never promote a model solely because training completed.

### Custom real-world inference interface
- [ ] Let an operator configure and preview a web interface for a selected model: task name, accepted inputs, displayed classes, thresholds, status text, and human decision controls.
- [ ] Add an initial safe test input path (local image upload/sample) and a connector interface for scanners or operational data sources; do not hard-code a vendor protocol before one is selected.
- [ ] Show detections, uncertainty, and model/version provenance in the configured UI. A model result is not a final legal determination; make human review/override explicit for high-impact outcomes.
- [ ] Record true/false feedback against the exact observation, model version, and operator decision. Make it exportable as a reviewed training candidate.
- [ ] Keep the live model unchanged until an operator reviews the feedback, starts a new training run, inspects its evaluation, and explicitly approves the replacement.

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
- [ ] Require explicit human approval for promotion; retain rollback to the previous model.
- [ ] Add audit records for project changes, model promotion, feedback disposition, and export.
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
