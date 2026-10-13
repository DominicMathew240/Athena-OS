# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Product Category

Open-source multimodal industrial data platform for human labeling, dataset curation, model training/evaluation, and customizable real-world inference interfaces. Operators configure projects from one browser-based dashboard; modality-specific task tools and connectors plug into shared project, dataset, model, and deployment workflows.

## Stack

The web application uses Django 6.1.2 and Pillow 12.3.0 and is requested to run on Python 3.15. The project registry and local image-import slice have been runtime-tested on Python 3.15, although Django's current official compatibility matrix lists Python 3.12–3.14 only. The existing SDK and CLI retain their separate Python requirements. Training backends, deployment targets, and scanner protocols remain open; do not carry forward the former FastAPI/Ollama company-assistant proposal.

## Users

- Industry operators and domain experts who know what should be detected but should not need to write code to define labels, review examples, or configure the resulting interface.
- ML engineers and system integrators who connect data sources, choose or train models, validate results, and deploy or embed the configured interface in a real workflow.
- Annotators and reviewers who create dependable ground truth and correct model feedback.

## Product Purpose

Let industrial teams turn real-world observations into reviewed training data, train and evaluate task-specific models, and configure a web interface that uses an approved model against connected operational data. For a scanner workflow, an operator should be able to define what counts as an illegal item, label examples, train/evaluate a detector, configure the output UI, connect a scanner data source, and review true/false decisions without editing application code.

The platform itself—including annotation, data management, model integration, and the configurable inference UI—is intended to be open source. The repository includes a Django project registry, validated local image imports with hashes, and basic manual bounding-box labeling with normalized coordinates and limited pointer/keyboard geometry editing; rights review, full annotation accessibility, dataset versioning/export, model workflows, and configurable inference remain unimplemented.

## Core Workflow

1. Create an industry project from the shared dashboard and choose a modality/task, label schema, and human review rules.
2. Bring in observations through a file import or documented connector. The current web slice only imports JPEG, PNG, and WebP files for image projects; the intended project workspace must accommodate image, video, audio/speech, text/documents, structured/time-series, depth/point-cloud, and new community-defined modalities without forcing them into one annotation tool.
3. Annotate manually with the task-appropriate tool. The current web slice can add, reclassify, move, resize, and delete image boxes, with keyboard coordinate entry, arrow-key editing, bounded zoom, scroll/trackpad pan, and in-memory undo/redo for the active dialog. Review and full accessibility verification are still open. The first complete vertical slice is image object detection from the scanner/illegal-item example, but the platform's project/data/connector architecture is modality-extensible from the start.
4. Version the reviewed dataset and train/evaluate a compatible model through an explicit backend integration.
5. Use the dashboard to configure and preview a task-specific web interface for operators, including the input source, output labels, thresholds, and human decision controls.
6. Record operator corrections as auditable feedback and training candidates. Feedback never changes the live model automatically: a human must review the corrected examples, start retraining, evaluate the new model, and approve promotion.

The trained AI is for inference in the configured real-world UI, not for generating annotation labels. No AI-generated annotation suggestions are assumed in the product scope.

## Positioning

An open, extensible industrial labeling-to-inference platform—not a chatbot and not a proprietary hosted-only control plane. Core workflows should work in a self-hosted installation; optional managed services must remain optional and must not be prerequisites for labeling, training, or deployment.

## Operating Context

Industrial teams may work with RGB/thermal images, video, audio, point clouds, depth, and sensor time series. Scanner or data-source protocols vary by site and remain a connector decision. The first implementation should prove one task end to end, keep provenance from source observation through annotation, dataset version, model, inference result, and operator feedback, and leave extension points for additional modalities.

## Capabilities and Constraints

- Existing foundation: versioned industrial observation/model/finding/evidence contracts, Python SDK interfaces, evaluation interfaces, CLI, and synthetic image/audio examples.
- Platform requirement: one shared multimodal workspace with modality-appropriate annotation and inference tools; formats and modalities are extensible through documented plugins rather than a finite hard-coded list.
- Available web slice: graphical project metadata management, validated local image import with SHA-256 hashes, and basic manual image bounding-box create/list/reclassify/delete. Not implemented: rights review, full dataset/media management, complete annotation/review workflow, model training orchestration, no-code inference UI configuration, scanner connectors, model feedback/retraining workflows, or production packaging.
- The existing `athena knowledge` command is an early standalone local document-library experiment; it is not the product's core workflow or roadmap milestone.
- Use only real or explicitly synthetic sample data. Do not imply model accuracy, scan coverage, compliance, or legal classification is guaranteed.
- Model outputs are indications, not final legal determinations. Provide a human review/override path and retain uncertainty, confidence, and provenance.
- Store true/false corrections with model and input provenance. Corrections become candidates for a human-approved dataset/retraining cycle; never silently update production weights.
- Keep the platform open-source and self-hostable without Athena Business. Third-party model packages, weights, datasets, scanner SDKs, and generated data retain their own licenses and notices.
- Deployment target, identity/access model, retention/encryption requirements, training framework, and scanner protocols need explicit design decisions before production claims.

## Brand Commitments

The user previously approved the Athena mark and wordmark from
`athena-forge/frontend/src/assets/athena-mark.png` and
`athena-forge/frontend/src/assets/athena-wordmark.png`. Retain those assets if licensing and distribution boundaries permit; derive a distinct accessible palette rather than copying the sibling product's palette. The user also asked for a bolder Japanese design influence for the web UI. Translate that into considered information hierarchy and graphic discipline; do not use faux scripts, clichés, or cramped text.

## Product Principles

- Make common project, annotation, review, and inference setup possible without code.
- Preserve human control over labels, model promotion, and high-impact decisions.
- Make provenance and uncertainty visible at every handoff.
- Keep modality-specific behavior behind stable, documented interfaces.
- Treat every customer scanner, feed, model, and task schema as configurable input—not as a hard-coded illegal-items product.
- Explain failures and recovery steps in plain language.

## Accessibility & Inclusion

Use plain language, keyboard-operable tools, visible focus, semantic HTML, and responsive layouts. Annotation interactions must not rely on color alone or mouse-only controls. Target WCAG 2.2 AA for the web interface and verify the full keyboard and assistive-technology path for each annotation tool.

## Open Decisions

- Confirm the first task and modality; image object detection is the first end-to-end slice based on the user's scanner/illegal-item example, while the dashboard and data contracts must be extensible to other modalities.
- Select an initial dataset interchange format and training backend after license and compatibility review.
- Define which scanner/data-source integration should be proven first; retain a connector interface rather than guessing a vendor protocol.
- Specify the deployment target for the customizable inference UI (local workstation, edge device, self-hosted server, or more than one).
- Define project roles, access control, encryption, retention, and feedback retention before real sensitive/regulated data is used.
- Define how far operators may customize screen layout and branding, versus selecting safe approved templates and controls.
