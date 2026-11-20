# Athena OS — Multimodal AI Training Platform Roadmap

**Development review • 17 November 2026**

## 1. Product direction

Athena OS should become a general-purpose, industry-ready AI training operating system, not simply a computer vision platform.

The goal is to let someone without a machine learning background build, train, evaluate, improve and deploy their own AI models using their own data. That includes photographs, videos, acoustic recordings, speech, documents, text, human movements, machine signals and combinations of different data types.

> **Proposed product positioning:**
> **Athena OS** — *Teach AI what matters to your world. No coding required.*

### Findings across existing repositories

- Athena OS already has multimodal ambitions in [`PRODUCT.md`](../../PRODUCT.md) and [`ROADMAP.md`](../../ROADMAP.md), but its verified primary workflow is image object detection, with several training and inference features remaining prototypes tested on synthetic data.
- Athena Forge has progressed further in additional workflows, including spreadsheet training (PR #79), handwriting training/OCR (PR #82), and speech-related foundations.
- Athena Nodes is a separate distributed CPU/GPU computing project that could eventually provide optional training infrastructure.

Therefore, the recommended direction is to expand the existing architecture rather than replacing it, while consolidating overlapping work into one coherent product. The biggest development change is to make the **training task**, rather than the camera or file format, the center of Athena OS.

---

## 2. Current development status

Based on the latest task records available, this is the starting point:

| Component | Verified development status | Recommendation |
|---|---|---|
| **Athena OS Core** | Django console, projects, authentication, image imports, annotation, dataset snapshots and prototype training | Reuse and generalize |
| **Vision workflows** | T01–T04 locally completed; T05 partially completed; T06–T65 remain open | Keep as a vision-specific workstream |
| **OCR** | Handwriting training integrated in Forge (PR #82); document OCR supports inference | Integrate with OS |
| **Audio classification** | Basic WAV classifier and ONNX export exist in Forge | Extend to industrial acoustic tasks |
| **Speech recognition** | Local speech-to-text and Sarawak Malay ASR foundations exist | Complete validated fine-tuning workflow |
| **Spreadsheet AI** | Training workflow merged in Forge PR #79 | Reuse its trainer |
| **Text/NLP training** | Text project category exists, but no verified complete general text-training lifecycle | New development |
| **Hand/body keypoints** | Geometry contracts exist, but no verified complete user-training workflow | New development |
| **Face recognition** | No verified end-to-end trainable biometric workflow | New, separately gated |
| **Multimodal fusion** | Architecture concepts and contracts exist | Longer-term development |
| **Athena Nodes** | Separate CPU/GPU compute platform | Optional compute backend after integration tests |

The [15 November 2026 vision implementation task list](2026-11-15-vision-workflows-task-list.md) remains centered on people counting, parking and camera operations, and its 16 November checkpoint explicitly describes local, partly uncommitted work rather than a released capability. It should become one workstream under this broader platform roadmap rather than the master roadmap for all of Athena OS.

---

## 3. Proposed Athena OS architecture

- **User Workspace (`Athena OS`)**: Projects · Guided Training · Datasets · Models · Deployments
- **Six Modality Pillars**:
  1. **Vision** (Images / Video)
  2. **Audio** (Sounds / Speech)
  3. **Language** (Text / OCR)
  4. **Motion** (Pose / Gestures)
  5. **Signals** (Sensors / Tables)
  6. **Fusion** (Multimodal AI)
- **Shared AI Training Engine**: Data ingestion and quality · Annotation and human review · Training and experiment jobs · Model evaluation and registry · Inference and deployment · Feedback and retraining
- **Compute Targets**: Local CPU/GPU (Self-hosted) · Cloud GPU (Managed compute) · Athena Nodes (Optional distributed network)

### Suggested responsibilities

| System | Responsibility |
|---|---|
| **Athena OS** | Open-source, self-hostable no-code model training platform and shared lifecycle |
| **Athena Forge** | Advanced training engines, experiments, specialized algorithms and existing ML integrations |
| **Athena Lab** | Research, expert review, benchmarking, governance and release qualification |
| **Athena Nodes** | Optional distributed computing and GPU job execution |
| **Domain Packs** | Reusable industry-specific tasks, datasets, evaluation rules and deployment templates |

Keep the current OS open-source functionality independent of proprietary Forge services. Reuse Forge functionality through compatible, versioned interfaces where licensing permits, rather than making a private service mandatory.

**Core resource lifecycle across every modality:**
`Project → Task → Observation → Annotation → Dataset Version → Training Run → Model Version → Evaluation → Deployment → Feedback`

---

## 4. Complete model-training capability roadmap

Recommended priorities are proposed, not existing delivery commitments.

| Modality | What users can train | Priority |
|---|---|---|
| **Computer Vision** | Image classification, detection, segmentation, defects and anomalies | P0 |
| **Acoustic AI** | Sound events, equipment faults, unusual sounds | P0 |
| **OCR** | Handwriting recognition, printed text and document extraction | P0 |
| **Tabular AI** | Classification and prediction from Excel/CSV | P0 |
| **Speech AI** | Transcription, commands, keywords and dialect adaptation | P1 |
| **Text AI** | Document classification, named entities, text extraction | P1 |
| **Sensor AI** | Anomaly detection and forecasting from time-series data | P1 |
| **Human Pose** | Body keypoints, actions, gesture classification | P2 |
| **Hand Tracking** | Hand landmarks, pointing and hand gesture models | P2 |
| **Video AI** | Tracking, event recognition and sequence analysis | P2 |
| **Face AI** | Face detection, verification and controlled recognition | P2 — restricted |
| **Multimodal AI** | Models combining audio, video, text, sensors and other inputs | P3 |

For each domain, Athena OS should deliver a usable training recipe (required data, labeling instructions, supported training method, evaluation procedure, deployment compatibility and minimum evidence required for release), not merely an AI model in a catalogue.

---

## 5. The no-code experience

Instead of asking beginners to choose model architectures, optimizers, epochs, inference frameworks and hyperparameters, Athena starts from an intended outcome (*"What do you want Athena to learn?"*):
- Recognize objects in pictures
- Recognize sounds or equipment faults
- Read handwriting
- Understand documents
- Recognize movements
- Predict from spreadsheets

Once a task is selected, all supported modalities follow six consistent steps:
1. **Choose a goal.** Select an outcome and a recommended starting recipe.
2. **Add examples.** Upload files, capture recordings or connect a supported source. Athena checks compatibility and data quality.
3. **Teach with labels.** Use task-specific tools: boxes, sound timelines, corrected transcripts, keypoints, text spans or spreadsheet columns.
4. **Train the model.** Athena selects compatible settings, shows data readiness and provides recoverable training progress.
5. **Test against reality.** Evaluate on held-out examples and show errors in everyday language, not only technical scores.
6. **Use and improve.** Approve the model, deploy or export it, capture corrections, then retrain and compare safely.

Advanced controls remain available for engineers, while novices can complete supported training tasks without opening them.

---

## 6. Revised engineering roadmap (Waves 0–4)

The timing below is an indicative sequence for a focused engineering team, not a committed release schedule.

### Wave 0 — Shared foundation (0–6 weeks)

| Task ID | Engineering requirement | Dependency |
|---|---|---|
| `OS-CORE-001` | Introduce a task/recipe registry independent of data format | Existing project contracts |
| `OS-CORE-002` | Define modality plugins for import, preview, annotation, training, evaluation and inference | `001` |
| `OS-CORE-003` | Create shared observation, dataset-version and annotation contracts | `001–002` |
| `OS-CORE-004` | Implement consistent training job states, resources, recovery and artifact storage | `003` |
| `OS-CORE-005` | Establish one model registry, evaluation evidence and release lifecycle | `004` |
| `OS-CORE-006` | Build the first universal guided-project workflow | `001–005` |

*Acceptance:* an engineer can register a new modality through a documented adapter without adding modality-specific conditional branches across core OS; self-hosted open-source operation remains independent of proprietary services.

### Wave 1 — Multi-task training MVP (2–4 months)

| Task ID | Required deliverable |
|---|---|
| `OS-VIS-001` | Existing image dataset → annotation → actual training → held-out evaluation → deployment |
| `OS-AUD-001` | Record/upload sound → label clips/events → train classifier → test recordings |
| `OS-OCR-001` | Upload handwriting → correct transcription → fine-tune TrOCR → measure errors |
| `OS-TAB-001` | Upload Excel/CSV → choose target → train → test on unseen rows |

*Acceptance:* each of the four workflows passes a clean-install, real-data training test and a nontechnical user completes it without coding, with reproducible metrics, usable model artifacts and rollback/replacement support.

### Wave 2 — Speech, language and industrial signals (4–7 months)

| Task ID | Requirement |
|---|---|
| `OS-ASR-001` | Speech dataset editor with timestamps, speakers, consent and transcripts |
| `OS-ASR-002` | Genuine ASR fine-tuning and speaker-independent evaluation |
| `OS-NLP-001` | Text classification and named-entity recognition |
| `OS-DOC-001` | Structured document-field extraction and table evaluation |
| `OS-SIG-001` | Time-series classification, forecasting and anomaly detection |
| `OS-AUD-002` | Normal-only/few-shot industrial acoustic anomaly recipes |

### Wave 3 — Human movement, video and industry packs (7–10 months)
- Pose/keypoint annotation, video event labeling, temporal classification and domain-specific training templates using pretrained body/hand landmark estimators.
- Face detection and biometric identity recognition separated with explicit consent, access controls, retention/deletion controls, subgroup testing and safeguards.

### Wave 4 — Multimodal model development (10–15+ months)
- Synchronized cross-modal fusion recipes (acoustics + vibration/temperature, video + pose + equipment state, thermal + RGB + asset metadata, speech + text/terminology), missing-input handling, fusion training, modality ablations and optional Athena Nodes.

---

## 7. Reconciling existing tasks (T01–T65) and Forge LAB tasks

Do not delete `T01–T65`; preserve completed work while reparenting the hierarchy:

| Existing tasks | Updated placement |
|---|---|
| `T01–T04` | Keep as verified local vision foundations |
| `T05` | Complete the missing versioned release-bundle contract |
| `T06–T08` | Generalize guided setup and preflight into OS Core |
| `T09–T31` | Video Operations / Vision Domain Pack |
| `T32–T40` | Extract shared review, training, comparison and approval logic (plus T19 durable workers) |
| `T41–T50` | Industry-specific operations and visual dashboards |
| `T51–T65` | Apply reliability, deployment, security and usability gates across all modalities |

Forge task list items (`LAB-002/003` qualified data, `LAB-006` GPU verification, `LAB-007B` object storage, `LAB-104` multimodal annotation, `LAB-107` benchmarking, `LAB-009` real-data end-to-end qualification) should be reconciled rather than independently reimplemented.

---

## 8. Required platform-wide quality gates

Every supported training task supplies its own evaluation contract:

| Task type | Primary quality checks |
|---|---|
| Object detection | Recall, precision, mAP, false positives |
| Acoustic events | Event F1, missed events, false alarms per hour |
| Speech recognition | Word error rate, character error rate, speaker/dialect generalization |
| OCR | Character/word errors, field accuracy where applicable |
| Text classification | Class F1, confusion analysis, false classifications |
| Pose estimation | Keypoint accuracy, OKS/PCK and visibility failures |
| Time-series anomalies | Event recall, false-alert rate, detection delay |
| Face verification | False acceptance/rejection rates and subgroup testing |

Every training run preserves dataset and license provenance, label-review status, immutable splits, training configuration, hardware details, model version, evaluation evidence and approval history. Training completion alone never implies production readiness.

---

## 9. Immediate execution priorities

1. **Reconcile Athena OS and Forge training contracts** — canonical schemas, deduplication, adapter boundaries.
2. **Finish OS T05 and the shared training-job lifecycle** — versioned release bundles, durable execution, artifact/model provenance.
3. **Create universal task/recipe and annotation plugin interfaces** — make sound, OCR and spreadsheet tasks first-class without duplicating the platform.
4. **Unify the beginner-friendly Teach workflow** — one guided experience with appropriate controls per task.
5. **Validate four real-data vertical slices** — vision, acoustic classification, handwriting OCR and spreadsheets.
6. **Complete evaluation, approval, deployment and recovery gates** — end-to-end quality and operational truth before expanding further.

---

## Execution log

*Added 2026-11-19. This section records what was actually delivered against the waves above, in the roadmap's honesty style: verified means tests and real runs on synthetic data, never real recordings, scans or operators. The plan itself above is unchanged.*

**Wave 0 — complete (OS-CORE-001..006, commit `1b9a2a9`, 2026-11-18).** The shared foundation landed as specified: a task/recipe registry (`GET /api/recipes`; `settings.RECIPES` and `register_recipe()` for third parties), a modality-plugin interface covering upload validation, annotation validation and asset metadata, generic `AssetAnnotation` rows with `Asset.metadata` and a `recipe` field on projects (migration 0025), and generic frozen dataset snapshots — one `DatasetEntry` per asset carrying the accepted annotation verbatim, with the image path's deterministic splits, manifest checksum and export guards reused unchanged (migration 0026). Core views dispatch through the registries rather than modality branches, which was this wave's acceptance test. Verified by 81 new tests (`tests_modal_foundation`, `tests_dataset_generic`); no real data and no real operator has touched any of it.

**Wave 1 — audio, handwriting-OCR and tabular slices merged (2026-11-19).** OS-AUD-001 (merge `3c30296`) and OS-OCR-001 (merge `69ba619`, coexistence fix `267a629`) are on main: WAV clip import and clip labels with the `audio-wav-cnn` backend (ONNX-or-torch artifact), and transcript annotations with the `ocr-trocr` fine-tuning backend (admin-gated TrOCR weight downloads on the D-FINE pattern, CER/word-accuracy evaluation; `predict_text` is backend-level only and the box-shaped check screens refuse it honestly). Both reuse Wave 0's generic snapshots end to end — upload, label, freeze, train, candidate — through the same API and lifecycle as vision. Verified by 1781 green tests in two environments (the ML venv, including a real separable-frequency audio training run; a lean no-torch environment where the ML tests skip cleanly), all on synthetic data; model quality is unmeasured and the OCR suite says so itself. The tabular slice (OS-TAB-001, commit `b949a8f`, on main since 2026-11-19 — after the 1781-test count above, which does not include it) follows the same pattern (CSV/XLSX import, target-column choice, `tabular-sklearn` backend). So this wave's acceptance gate — clean-install, real-data training tests with nontechnical users — is nowhere near met and none of the four Wave 1 items is ticked in [`ROADMAP.md`](../../ROADMAP.md) M3.

**Wave 2 — text-classification slice merged (2026-11-20).** The first OS-NLP-001 half is on main (merge `9d7017f`; slice commits `057ba62` modality plugin + recipe, `65137e8` `text-sklearn` training backend, `edcf838` end-to-end tests + design note, `a85501b` cross-module test-pollution repair): a `text` modality plugin accepts one strict `text,label` CSV per project, a `text_dataset` annotation confirms the label list, and the whole CSV freezes one generic dataset entry through the unchanged Wave 0 pipeline. The backend is word n-gram TF-IDF + regularized logistic regression via scikit-learn with a joblib artifact; rows split 80/10/10 inside `train`, seeded on the CSV's checksum, with the held-out row indices carried in a sidecar so `evaluate` scores exactly the rows the model was not trained on; the web process never imports sklearn (the ML venv carries it, and the ML tests skip with a named reason in the lean venv). **Text classification only — named-entity recognition, the other half of OS-NLP-001, is out of scope and left as future work**, as the backend's own constraints and the design note ([2026-11-19-os-nlp-001-text-classification.md](2026-11-19-os-nlp-001-text-classification.md)) state. Verified by `projects.tests_text` (31 tests: upload refusal paths, label confirmation, freeze, a **real sklearn training run** on synthetic data in the ML venv, candidate → staged → approved lifecycle, predict refusal) and the full suite rerun in **both venvs: 1961 tests, OK** in each. All evidence is synthetic data — deterministic filler text from two fixed word pools — so **model quality is unmeasured and nothing is claimed about real text**; there is no inference/check-screen story yet (the backend's `predict` is a refusal and the results card points at the evaluation report), and no real user has touched any of it. Wave 2's other items (ASR, document extraction, time-series, industrial anomaly recipes) are untouched.

**Wave 2 — time-series classification slice merged (2026-11-20).** The classification third of OS-SIG-001 is on main (merge `e9e0443`; slice commits `db0c32d` modality plugin + recipe, `6fd4c38` `signal-sklearn` training backend, `558c6c7` end-to-end tests + design note + a pre-emptive fix for the cross-module test pollution the tabular merge was bitten by, `3891c87` ruff): a `time_series` modality plugin accepts one strict wide-format CSV per project — the header starts with a `series` column, ends with a `label` column, and every column in between is one time step's numeric value (50 MB / at least 10 rows / at least 8 value columns; long format refused with a plain pointer to the documented shape) — a `signal_dataset` annotation confirms the label list, and the whole CSV freezes one generic dataset entry through the unchanged Wave 0 pipeline (the text slice's one-entry-per-file snapshot, not the tabular per-column target choice). The backend turns each series row into 16 window statistics (per-half and overall mean/std/min/max, least-squares slope, midline crossings) and fits a standardized regularized logistic regression via scikit-learn, joblib artifact; rows split 80/10/10 inside `train`, seeded on the CSV's checksum, with the held-out row indices in a sidecar so `evaluate` scores exactly the series the model was not trained on. The web process never imports sklearn (it is imported only inside worker methods; the ML venv carries it, and the tests skip with a named reason in the lean venv). Verified by `projects.tests_signal` (33 tests: upload refusal paths, label confirmation, freeze, **a real sklearn training run** on synthetic data in the ML venv, candidate → staged → approved lifecycle, predict refusal) with the full suite at **1994 OK in both venvs in the slice's own worktree** (lean 86 skips, ML 2 skips with the real signal-sklearn run executing — the slice author's runs, not re-run by this docs pass), and **2051 OK after the merge** (the merge owner's runs, not re-run by this docs pass). All evidence is synthetic data — deterministic sines, squares and noise — so **model quality is unmeasured and nothing is claimed about real sensor data**; there is no inference/check-screen story yet (the backend's `predict` is a refusal and the results card points at the evaluation report). **Forecasting and anomaly detection — the other two thirds of OS-SIG-001 — are explicitly out of scope**, as the backend's `constraints`, the console's recipe note and the design note ([2026-11-20-os-sig-001-signal-classification.md](2026-11-20-os-sig-001-signal-classification.md)) state. Wave 2's remaining items (ASR, document extraction, industrial anomaly recipes) and the rest of OS-SIG-001 are untouched, and no real user has touched any of it.

