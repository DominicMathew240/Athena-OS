# The improve loop: operator corrections -> reviewed pictures -> retrain -> compare -> approve

> **For Hermes:** execute with `subagent-driven-development` (fresh child per task, spec review, then quality review; the parent re-runs every acceptance check). Prototype, synthetic data, simulated users only.
> Follows `docs/plans/2026-10-23-no-code-teach-flow.md`. Roadmap items: M4 "Turn approved false-positive/false-negative corrections into a reviewable dataset version", "Add configurable retraining runs and comparisons against the currently approved model".

**Goal:** a nontechnical operator who marks a check-screen result right or wrong creates material a labeler can review, teach again from, and compare with the model in use, without anything touching the live model on its own.

**Rules that never bend (PRODUCT.md):** feedback never changes live weights; corrections become *candidates* a person reviews; provenance (observation -> model version -> picture -> box -> snapshot -> run) stays traceable; unreviewed boxes are never exported or trained on.

**Assumptions (owner defers these; change if wrong):**
1. Only Labeler-and-above can pull corrections into the project; operators on the check screen keep giving feedback as now.
2. A corrected picture is *copied* into the project as a new picture (origin "operator feedback", linked to the observation and model version), once per observation (idempotent).
3. Boxes the operator confirmed or added become **Needs review** suggested boxes (origin "feedback"); a wrongly drawn model box is NOT turned into ground truth (it is simply not copied). A picture whose only feedback is "false alarm" is imported with no boxes and is flagged so the labeler can decide; how snapshots treat box-less pictures is reported, not changed here.
4. The comparison with the model in use is computed at evaluation time on the new model's own held-out test pictures and is marked **not fair** when those pictures were also in the baseline model's training data.
5. No change to approval rules in this phase; the comparison is shown in the approve dialog as information.

## Waves and ownership

| Wave | Task | Owns (may edit) | Must not touch |
|---|---|---|---|
| 1 | **L1** feedback -> reviewable pictures (backend) | `models.py` + one migration (ONLY L1), new `feedback/` or `feedback_import.py`, `views.py`/`urls.py` single additions, new `tests_feedback_import.py` | training/*, console.*, datasets/service.py |
| 1 | **L2** comparison with the model in use (backend) | new `training/comparison.py`, `training/runner.py`, `training/evaluation.py` and `training/report.py` (additive), `results_views.py`, new `tests_comparison.py` | models.py/migrations, lifecycle.py, console.* |
| 1 | **H** hygiene follow-ups | `datasets/service.py`, `datasets/health.py`, `training/lifecycle.py`, `training/torchvision_frcnn.py`, `training/dfine.py`, `console_settings.py`, `permissions.py`, their tests | models.py/migrations, report.py, evaluation.py, console.* |
| 2 | **L3** UI: "Corrections" and "Compared with the model in use" | all `console.*`, `access.*`, `checkscreen*`, UI tests, `browser_smoke.mjs` | backend |
| 3 | spec reviews (L1, L2, H) in parallel with L3; then a persona pass; docs; integration review | | |

## L1 contract (to be confirmed against the code by the child)
- `GET /api/projects/<id>/feedback/corrections` -> counts and a capped list of observations that have operator feedback not yet pulled into the project (`pending`), already pulled (`imported`), and why some cannot be (picture missing, wrong type).
- `POST /api/projects/<id>/feedback/corrections/import` (Labeler+): body optional `{"observation_ids": [...]}` or all pending; creates pictures + Needs-review boxes inside one transaction per observation, append-only audit rows, returns counts and per-observation outcome; idempotent; never touches `ModelVersion`, runs or datasets.
- Provenance stored: observation id, model version id and artifact SHA-256, decider (opaque `u<id>`), feedback kinds, import time/user.

## L2 contract
- New additive `evaluation["comparison"]` for a new candidate: `{baseline:{model_id,version,artifact_sha256}, same_test_pictures:int, fair:bool, unfair_reason, baseline_result:{found,of,wrong_boxes}, candidate_result:{found,of,wrong_boxes}, verdict:"better"|"about_the_same"|"worse"|"cannot_tell", plain}` computed in the worker by running the approved model's backend `predict` on the candidate's test pictures with the same matching code; absent when there is no approved model, no test split, or the baseline backend cannot run; failure never fails the run. `GET .../results/plain` passes it through.

## Parent verification after every wave
`git status`/`git diff --stat`; full suites lean and ml SEQUENTIALLY (never parallel); `makemigrations --check --dry-run` with `DJANGO_DEBUG=1`; `git diff --check`; browser smoke chromium + webkit; one real-page check.

## Status (written 2026-10-24, docs pass; PROTOTYPE, synthetic data, simulated personas only)

Nothing below is evidence for the roadmap gate in M4 (a correction traced from operator decision to approval and rollback by real operators on real imagery). Counts are the lead's own runs, **not re-run by the docs pass**: Django 1410 tests (28 skipped in the lean environment, 18 in the one with torch), the D-FINE module's 81 tests in its own environment, browser smoke 547 (Chromium) / 520 (WebKit) / 520 (iPhone 13 emulation).

| Wave | Task | Status | Where to look |
|---|---|---|---|
| 1 | **L1** feedback -> reviewable pictures (backend) | **Built and tested** (`tests_feedback_import.py`, 49 test methods; `tests_autolabel_feedback.py`, 13). Differs from the contract in places, below | `web/projects/feedback_import.py`, `feedback_views.py`, model `FeedbackImport`, migration `0010` |
| 1 | **L2** comparison with the model in use (backend) | **Built and tested** (`tests_comparison.py`, 70 test methods). Shape differs from the contract, below | `web/projects/training/comparison.py`, hook in `training/runner.py`, `report.py` passes it through |
| 1 | **H** hygiene follow-ups | **Built and tested.** Done: public `content_fingerprint` and `accepted_boxes`, `analyse(file_check=)` with `Analysis.complete` / `IncompleteAnalysis`, readiness `notes` / `files_rechecked`, pinned-host redirects, the decorator's `messages=`, the 300-character note, JSON body limits (`tests_api_common.py`) | `docs/api-reference.md`, "Shared request rules" |
| 2 | **L3** UI: "Corrections" and "Compared with the model in use" | **Built.** Card `#corrections-panel` in the Pictures part, filter `#asset-filter-feedback`, note `#autolabel-feedback-note`; comparison block `trComparisonBlock` on the results card and a sentence in the approve dialog | `console.html`, `console.js`, `console.css`, `tests_training_ui.py`, `browser_smoke.mjs` |
| 3 | spec reviews (L1, L2, H) | Run by the lead; their reports are not part of this docs pass and are not summarised here | |
| 3 | persona pass | **Open at the time of writing**: a separate child was writing `docs/dogfood/2026-10-24-improve-loop.md`. A simulated persona is **not** a usability session, whatever it finds | |
| 3 | docs | **This pass**: API reference, model-quality, access-roles, data-export, DEPLOYMENT, ROADMAP note, new `docs/guides/improve-loop.md`, wiki pages | |
| 3 | integration review | **Open** (the lead's) | |

**Where the build differs from the contracts above** (the code is right, the contracts were the plan):
- Box origin is **`model`** (`status: suggested`, `model_name: "operator feedback (model vN)"`), not a new origin "feedback", because the review controls only know the model-suggestion shape. A comparison in `autolabel/runner.py` (`FEEDBACK_BOX_SOURCE`) must stay equal to `feedback_import.BOX_SOURCE_NAME`; a test checks it.
- "Append-only audit rows" is one append-only `FeedbackImport` row per result; no `ModelEvent` or `ExportEvent` is written.
- The comparison block is `athena.comparison/1` with `test_pictures`, `test_boxes`, `overlap_pictures`, `per_label`, a paired `difference` (`only_candidate_found` / `only_baseline_found`), the baseline's `backend` and `trained_on_pictures`; not the shorter shape in the L2 contract. When the model in use has a missing or changed file, `{available: false, reason}` is stored, the one case that stores a "not available" block.
- Fairness also fails closed when the model in use has no known training pictures or a damaged manifest, not only on overlap.
- Counts of "pending" are taken from the newest 2,000 results still waiting (`SCAN_LIMIT`); `imported` and `total` are exact.

## Open follow-ups (engineering)
- **EXIF-rotated pictures are skipped, not transposed.** Turning them upright (and the check-screen boxes with them) would let those pictures in; today they stay "pending" in the list and are named as skipped by every import attempt.
- **The pending window is 2,000 results**, the list shows 100 entries, and the cheap list check does not decode pictures, so rotation, pixel count and changed bytes show up only when an import is tried.
- **A model that hangs inside the comparison pass has no timeout** (cancel and the run time limit are checked between passes only). Needs a separate process or thread with a deadline.
- **A picture whose only feedback is "false alarm" cannot become a negative example**: it is imported without boxes and snapshots leave out pictures with no accepted box. Supporting "nothing here" needs a decision in the snapshot, the export and the training backends.
- **Imported boxes carry `origin: "model"`**, so exports and the manifest cannot tell a correction box from an auto-suggested one except by `model_name`; the link to the check-screen result is in `FeedbackImport` only, not in the manifest.
- **The auto-suggest guard is computed live** (`kept_for_review_count` asks the link table each time a job is read), not stored on the job; a job read after a picture was unlinked or deleted can show a different count than when it ran.
- **The bulk accept has no server-side guard** against sweeping in correction boxes; only the console warns.
- **Per-label cut-offs and calibration** (carried over from the teach plan): one cut-off applies to every label; scores stay the computer's own numbers.
- **A real usability session** (kit in `docs/usability/`) has never been run; the persona passes are not a substitute.
- **Reset to environment:** once a console choice exists for the download switch, the console cannot go back to "use the environment setting".
- **Trainers cannot see the Access section**, so they have no place to read why downloads are off.
- **D-FINE default decision** and the legal read of the COCO question (teach plan, (b)).
- **Shared ownership questions** (my reading, please correct): the plan's table gives `urls.py`, `models.py` and `console.*` to one writer per wave and the teach plan limits `urls.py` to single-line appends; the plan does not say who owns a shared file after the wave ends. Separately, a project has no owner: any Labeler on the project may pull corrections in, and there is no way to say "only X reviews corrections".

## Owner decisions still open
1. Is **Labeler and above** the right role to pull corrections in (assumption 1)? Or Trainer?
2. Should **the comparison ever gate approval** (today: information only)? And are the margins right: 3 boxes, 10% of the test boxes, 30 test boxes per label (judgements, not measured)?
3. **Transpose or keep skipping** EXIF-rotated pictures?
4. Should **"nothing here" pictures** be supported as negative examples (touches snapshots, exports and trainers)?
5. Should the **bulk accept** treat correction boxes separately (a server-side guard, or a separate accept step)?
6. A **"go back to the environment setting"** action for the download switch (new endpoint and audit row)?
7. Should **Trainers** be able to see the download switch in the console?
8. **D-FINE as the default** ready-made base, and when to ask counsel about the COCO / Hugging Face terms.
9. When to schedule the **real usability session**, and whether to build a timeout for hung model passes before a real pilot.
10. Who **owns** a project's corrections once a project is shared (see above).
