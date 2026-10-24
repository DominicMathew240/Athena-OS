# No-code "Teach" flow: implementation and task-allocation plan

> **For Hermes:** execute with the `subagent-driven-development` skill (fresh child per task, spec review then quality review, parent re-verifies every claim).
> **Status:** PROTOTYPE. Local, synthetic data, simulated personas. Nothing here is evidence for the roadmap gate that needs real operators (`docs/usability/`).

**Goal:** a non-technical operator presses one "Teach" action and gets a model that is not doomed, with results in plain words and a clear next step.

**Why (evidence, all in-tree):**
- `docs/guides/model-quality.md`: from-scratch start found 0 of 144 test boxes; COCO start recall 0.986, ImageNet-backbone start 0.889 (synthetic shapes, 8 epochs, CPU, two seeds; says nothing about real scanner images).
- **At the start of this plan (since replaced by the one-click start, see Next steps (a)):** the UI hid pretrained starts under "Advanced" with the copy "Most people can leave this closed and start from scratch" (`web/projects/templates/projects/console.html`, `#tr-start-from`), and `DEFAULTS["pretrained"] == "none"` (`web/projects/training/torchvision_frcnn.py`; the raw API default is still `none`, the console's start uses `auto`).
- Teach needs snapshot -> tool picker -> two Advanced drawers -> licence tick-box before one primary button.
- Results are AP50/precision/recall; the operator needs "found X of Y, Z false alarms" plus the pictures that were wrong.

**Assumptions (decisions set aside, change if wrong):**
1. Licence: keep the existing torchvision `coco` / `imagenet_backbone` starts. A licence-clean replacement base is researched (W0-C) but not swapped in this plan. The server-side acknowledgement stays enforced.
2. No new heavy dependency in the web process. scikit-learn is used only in a scratch spike (W1-S) until the owner decides.
3. No commits, pushes, stashes or checkouts. The tree is already dirty (9 modified files, see `git status`), so children read `git diff` of a file before editing it.
4. No schema change unless a child reports it is unavoidable; W1-B1 is the only agent allowed to touch `models.py` / migrations.

**Test commands (environment quirk):** host `python3` is 3.15 without Django. W0-A builds a scratch venv (Django 6.1.2, Pillow 12.3.0, numpy; torch only in a second venv). All children use the venv path recorded in `scratch/athena-qa/ENV.md` and never `pip install` into the host Python.
Django: `<venv>/bin/python web/manage.py test projects` (baseline 610 tests per `docs/dogfood/2026-10-persona-rosa-layout-pass.md`).
Browser smoke: `bash web/scripts/run_browser_smoke.sh [webkit]`.

## Waves and ownership

| Wave | Task | Agent role | Owns (may edit) | Must not touch |
|---|---|---|---|---|
| 0 | **W0-A** QA harness + baseline measurements | implementer (scratch only) | `~/.hermes/profiles/athena/cache/scratch/athena-qa/**` | the repo tree |
| 0 | **W0-B** code wiki of `web/` | read-only writer | `~/.hermes/wikis/athena-os/**` | the repo tree |
| 0 | **W0-C** pretrained-base licence research | read-only researcher | `scratch/athena-qa/base-models.md` | the repo tree |
| 1 | **W1-B1** auto-config + one-click start | backend | `web/projects/training/{base,lifecycle,runner,torchvision_frcnn}.py`, `training_views.py`, new `tests_training_auto.py`; `models.py`/migrations only if unavoidable | `console.*`, `datasets/`, `report.py`, `evaluation.py` |
| 1 | **W1-B2** data-health check | backend | new `datasets/health.py`, new `tests_data_health.py`, one new route in `dataset_views.py` + one line in `urls.py` | everything else |
| 1 | **W1-B3** plain-language results + misses list | backend | `training/report.py`, `training/evaluation.py`, new `results_views.py`, new `tests_results_plain.py`, one line in `urls.py` | `training_views.py`, `console.*` |
| 1 | **W1-S** calibration + threshold spike (sklearn) | read-only spike | `scratch/athena-qa/calibration/**` | the repo tree |
| 2 | **W2-U** Teach flow UI (single owner) | UI | `console.html`, `console.js`, `console.css`, `tests_console.py`, `tests_training_ui.py`, `tests_pretrained_ui.py`, `scripts/browser_smoke.mjs`, cache-busters | backend modules |
| 3 | **W3-F** persona + dogfood re-run | read-only finder | `docs/dogfood/<date>-teach-flow.md`, screenshots | product code |
| 3 | **W3-D** docs truth pass | docs | `ROADMAP.md`, `PRODUCT.md`, `docs/guides/model-quality.md`, `web/README.md` | code |

Wave 1 tasks run in parallel (disjoint files). Wave 2 starts after the parent has verified the three backend JSON contracts. Wave 3 after wave 2 is verified. `urls.py`: only single-line appends, re-read before editing.

## Wave 0

### W0-A QA harness and baseline (measure before changing anything)
1. Build venvs under scratch: lean (Django, Pillow, numpy, whitenoise) and ML (adds torch 2.9.1, torchvision 0.24.1 per `web/requirements-autolabel.txt`). Record paths in `ENV.md`.
2. Run `manage.py test projects` in both venvs; record counts, failures, skips. Do not fix anything.
3. Write `seed_teach_qa.py` (Django shell script): a project "Dented cans" with 3 labels, 60 synthetic pictures with boxes, a snapshot, and (ML venv) one tiny trained model. Disposable SQLite, own data dir, loopback port.
4. Write `teach_baseline.mjs` (Playwright; reuse `web/scripts/.smoke` or `PLAYWRIGHT_NODE_MODULES`): on a cold and a seeded instance, log in, open the Teach part, and measure: clicks from "project open" to a started run, form controls and drawers visible before the primary button, count of jargon terms from a list (snapshot, epoch, batch, backbone, ImageNet, COCO, AP50, IoU, validation, candidate), page height at 1440x900 and 390x844, primary buttons per part, console errors. Save JSON + screenshots.
5. Report: baseline numbers and the exact commands. Stop rule: if the venv cannot be built, report the exact error; do not substitute mocked output.

### W0-B Code wiki (skill `code-wiki`)
Scope: the `web/` Django app (`projects/`: models, training, datasets, inference, autolabel, views/urls/permissions, console static files) plus `sdk/python/athena_sdk` connectors. Output to `~/.hermes/wikis/athena-os/` only. Every diagram node must exist in source; parse every Mermaid block. Purpose: shared reference so wave-1 children and later reviewers start oriented.

### W0-C Base-model licence research
Table of candidate detector bases usable as an owned-model starting point (code licence, weights licence, training-data terms, size, whether torchvision/ONNX friendly), each row with a source URL and "not legal advice". Compare with the current torchvision COCO/ImageNet weights. Recommendation only.

## Wave 1 (parallel after W0-A is verified)

### W1-B1 Auto settings and one-click start
- `pretrained: "auto"` resolves to the best start that is available (coco, then imagenet_backbone). It never silently resolves to `none` when a ready-made start could run; if none is available the API answers 409 with a plain reason and the administrator step instead of starting a doomed run. The resolved value is stored in the write-once config.
- `epochs`, `batch_size`, `image_size` derived from the number of training pictures and a `quality: "quick" | "better"` choice; the derivation is a pure function with a test table.
- `POST .../training/runs` accepts `{"snapshot": "latest"}`; if there is no snapshot or the current work differs from the latest one, it creates a snapshot first (reuse `datasets.service`), inside one transaction, refusing with the existing readiness reasons.
- Keep existing explicit-config requests working (tests must still pass). Acknowledgement of pretrained terms stays server-enforced.
- Report the exact request/response JSON the UI must render.

### W1-B2 Data health
Pure read-only `GET /api/projects/<id>/datasets/health` returning per-label counts with a verdict (`enough` / `few` / `none`; thresholds named constants, documented), whether a held-out group would be non-empty, near-duplicate groups (difference hash with Pillow, Hamming threshold named constant, groups reported by picture id), and an overall `ready_to_teach` with plain reasons. No DB writes, no new dependency. Test with generated images (near-copy with noise, crop, unrelated).

### W1-B3 Plain-language results
For a finished evaluation, add a `plain` block: per label "found X of Y", "wrong boxes Z", overall verdict (`good_enough_to_try` / `needs_more_examples` / `not_ready`, thresholds as named constants with the reason text), plus a capped list of the worst misses and false alarms as `{asset_id, kind, label, score, box}` taken from the held-out pictures only. New read-only endpoint in `results_views.py`; do not change stored evaluation rows. Every number comes from the existing evaluation code, never invented.

### W1-S Calibration and threshold spike
In a scratch venv with scikit-learn 1.9.1: take predictions from the W0-A tiny model (and the benchmark script's synthetic set if it runs), fit isotonic and Platt calibration on the validation split, report ECE/reliability on the test split before and after, and choose a threshold for a stated miss rate from the precision-recall curve. Output: a table, the scripts, and a recommendation on whether to put this in the worker. Cite the honest limit: synthetic data and a toy model.

### Review (per backend task)
Spec reviewer first, then quality reviewer, read-only. Adversarial probes: malformed config, a project with no pictures, two simultaneous start requests (one run, no duplicate snapshot), a viewer/labeler role starting a run (403), snapshot creation failing midway.

## Wave 2

### W2-U Teach flow UI (one owner for `console.*`)
Inputs: contracts from B1, B2, B3.
1. Teach part shows: a data-health checklist (B2), one primary button "Teach the computer" with a Quick / Better choice, and a one-line plan sentence stating the start model, the expected time and anything dangerous (for example "no ready-made start available"). The tool picker, snapshot picker and all numeric settings move to a closed "Details for your administrator" fold.
2. Pretrained acknowledgement: shown once, in plain words, only when a ready-made start is used; server still enforces.
3. Results card in task terms (B3 `plain` block) with the misses/false-alarm picture list and a link to open the picture in the label editor. The AP50 table moves under "Details".
4. Wording: "snapshot" is described at first use as "a saved copy of your labelled pictures"; keep the product's status words unless the owner renames them.
5. Update tests pinned to old wording by grepping the test tree; update `scripts/browser_smoke.mjs` for the new controls; bump `?v=` cache-busters by script.
Acceptance: re-run `teach_baseline.mjs`; clicks to a started run, controls before the primary button and primary buttons per part must all go down; axe 0 violations at 1440 and 390; console errors 0; full Django suite green.

## Wave 3

### W3-F Persona and dogfood re-run
Persona "Rosa" again (supervisor, Excel and WhatsApp), same task as the baseline, plus the adversarial moves from the `dogfood` skill (double-click Start, refresh during a run, two tabs, back button, no-ready-made-start state, empty test group, 390 px). Pragmatism filter mandatory; label everything "simulated user, local prototype, synthetic data".

### W3-D Docs truth pass
Update only what the evidence supports. Roadmap boxes stay unticked unless their stated gate is met. State what was not tested: real operators, real scanner images, Firefox/Safari, screen reader.

## Parent verification (after every wave)
`git status` and `git diff --stat`; run the full suite in both venvs; run the browser smoke; `makemigrations --check --dry-run`; `git diff --check`; open one real page in Chromium and confirm zero page errors. A child's "all green" is a claim until the parent has reproduced it.

## External gates (not closable by this plan)
Real-operator usability session (kit exists), legal review of pretrained-weight terms, real scanner imagery accuracy, real screen-reader pass.

## Next steps and open decisions

Written 2026-10-23 after waves 0-3. **Status of this round: PROTOTYPE.** Local, synthetic data, simulated personas; nothing below is evidence for the roadmap gate that needs real operators. What was built and measured is summarised in the `ROADMAP.md` M2 note (2026-10-23) and `docs/dogfood/2026-10-23-teach-flow.md`. Items marked **[OWNER]** need a decision from the owner; the rest are engineering follow-ups.

### (a) DONE (2026-10-23, later in the round): how an administrator allows the ready-made weights
**Built: a switch in the console.** Access > "Ready-made model downloads" (`/access#ready-made-model-downloads`), superuser only; `GET`/`PUT /api/console/training-downloads` (Trainer and above can read, only a superuser can change); code `web/projects/console_settings.py`, `settings_views.py`, models `ConsoleSetting` and `ConsoleSettingEvent`, migration `0009`; tests `web/projects/tests_console_settings.py` (64 test methods). Switching on needs the administrator's own licence tick and a note of at least 3 characters; every change writes an append-only audit row. **The web process never fetches weights**: it stores the choice and the teaching helper reads it from the same database before each run and when it reports for duty (no restart). The per-run licence tick (`acknowledge_pretrained_terms`) is unchanged. Details and real request/response bodies: `docs/api-reference.md`; operator steps: `web/DEPLOYMENT.md`.

**Precedence caveat (state it plainly):** a stored console choice, on or off, beats `ATHENA_TRAINING_ALLOW_DOWNLOAD`; with none, the environment variable is used; with neither, off. **There is no way back to "environment" once a console choice exists**: the console only moves between on and off, so an administrator who switches downloads off cannot be undone by editing `.env`. Whether that is the right rule is a small **[OWNER]** call (a "go back to the environment setting" action would be a new endpoint and an audit row).

**Checked how:** unit tests, the browser smoke test (Chromium and WebKit, the Access walk-through with the tick, the note and one request) and axe on the new section. **Not checked:** a real administrator on a real install, and a real helper downloading real weights through it.

**Open follow-ups from this item** (status updated 2026-10-24):
- **Trainers cannot see the downloads section.** *Still open.* `/access` is administrator-only, so a Trainer who reads "ask an administrator" in the Teach panel has no place to look; they can only read the state through the API or the Teach panel's sentence.
- ~~`permissions.access` has no per-view message option~~ **Done 2026-10-24:** `access(..., messages={level: sentence})` (`web/projects/permissions.py`); `settings_views.py` uses it and the wrapper re-wording is gone.
- ~~The Access note box allows 500 characters but the server accepts 300~~ **Done:** both are 300 (`NOTE_MAX_CHARS` in `console_settings.py`, `maxlength="300"` in `access.html`; tests pin both).
- ~~Downloads follow redirects to other hosts~~ **Done 2026-10-24:** a redirect is followed only over https, to the backend's pinned hosts (`download.pytorch.org`; `huggingface.co` and `*.hf.co`), never to an address with a user name or password, and each file is cut off at its size and checked against its checksum (`redirect_refusal` in `torchvision_frcnn.py`, see `docs/api-reference.md`, "Shared request rules"). *Still open:* the rule is tested without the network (stubs); no real download through a real redirect was run here.
- Nothing lists the audit rows except the Access screen's one-line "last change"; there is no Django admin site. *Still open.*

Options that were considered at the start of the round (the first was built, in a smaller form than the table says: the console stores the choice and the helper fetches):

| Option | What it takes | Cost / risk |
|---|---|---|
| Browser button for administrators | An admin-only endpoint that records the choice and lets the helper act on it; an audit row; the downloaded file is checked against its published checksum as now | The web process (or a helper told by it) would fetch third-party weights on a click; needs a place to store the setting and a decision on who may click (superuser only is the simplest). Interacts with the licence tick and the pending legal review |
| Bundle the weights with Athena | Ship the two files (about 95 MB together) in the image or release | Redistributing third-party weights needs the legal review first; makes every install carry the COCO / ImageNet terms |
| One command for the person who installed Athena | A `manage.py` command that downloads, verifies and reports, run once on the host (Docker: `docker compose exec`) | Smallest change and no new web surface; still needs shell access, so a floor supervisor cannot do it; pairs well with a copyable "note for my administrator" in the panel (idea T9 of the persona report) |

### (b) DONE as an opt-in tool (2026-10-23, later in the round): a licence-clean base model, first candidate only
**Built: the D-FINE backend (`dfine-coco`)**, rank 1 below: `web/projects/training/dfine.py`, registered in `settings.TRAINING["BACKENDS"]`; tests `web/projects/tests_training_dfine.py` (81 test methods; run in a separate environment, not in the main suite). Nano and small, COCO-only checkpoints at pinned revisions with pinned SHA-256s, Objects365 checkpoints refused in code, `scipy==1.18.1` added to `web/requirements-autolabel.txt`. A real CPU run on **synthetic shapes** (nano): 2 passes found 0 of 144 test boxes, 8 passes recall 0.833, precision 0.615, mAP50 0.792, 12 passes 0.903 / 0.730 / 0.907; roughly 37 to 65 seconds per pass over 200 pictures. Those numbers are not comparable with the torchvision ones and say nothing about real scanner images (`docs/guides/model-quality.md`, "D-FINE: the optional second tool").

**Open items:** (1) **the default stays `torchvision-fasterrcnn`**: making D-FINE the default ready-made base is an owner decision, below; (2) the **small** checkpoint was never run, so it has no figure and no time estimate; (3) **ONNX export did not work** for the trained model; (4) the licence read stands as written in `LICENSE_AUDIT.md`: Apache-2.0 is declared on the model cards, that is not a statement about the COCO photos, the licensor of the converted weights is unverified, and nothing here is legal advice; (5) the pinned numpy on Python 3.14 (below); (6) no run has used it through the Teach panel with real downloaded weights. RT-DETRv2 (rank 2) and RF-DETR (rank 3) were researched only and are not built.

The research recommendation that led here, kept as written:
The current ready-made starts are torchvision's COCO detector and ImageNet backbone; torchvision states no licence for the weights and both carry dataset terms nobody here has resolved (`docs/guides/model-quality.md`, "Licence"). The research file (`base-models.md` in the QA scratch folder, 2026-10-23; engineering read, **not legal advice**; accuracy was **not** measured, only CPU step time and ONNX export) recommends, in this order:

1. **D-FINE-N / D-FINE-S, COCO-only checkpoints** (`ustc-community/dfine-nano-coco`, `dfine-small-coco`) via Hugging Face transformers: Apache-2.0 stated on card and repo, no new dependency, fastest of the DETRs in the probe (nano 0.75-0.97 s, small 1.7-2.7 s per batch-4 step at 640 px on an M3, against about 2 s for today's model at 320 px). Researcher's integration estimate: a new backend of 350-450 lines, **about 2-3 engineer-days** including tests and the licence note, plus a 1-day spike on the benchmark script and on at least one real scanner dataset (DETR heads can be slow to adapt on tiny sets). ONNX export did not match PyTorch (legacy exporter) or failed (dynamo) in the probe.
2. **RT-DETRv2-R18vd, COCO-only** (`PekingU/rtdetr_v2_r18vd`): Apache-2.0; the only DETR whose ONNX export matched PyTorch (dynamo exporter, opset 18); 2-4x slower than D-FINE on CPU. About +0.5 day if built beside rank 1 in one module, about 2 days alone.
3. **RF-DETR Nano / Small / Medium / Large** (the Apache-designated sizes only): the clearest explicit weights statement, but PML-1.0 sizes sit next to them (needs an allow-list in code), ONNX failed in the probe, and whether its checkpoints were pretrained on Objects365 is unverified. About 2 days plus 0.5 day for the allow-list.

**Avoid** checkpoints pretrained on Objects365 (`*_obj365`, `*_obj2coco`, `rtdetr_*_coco_o365`, Grounding DINO): Objects365 says "available for the academic purpose only", and D-FINE's README warns they "should not be assumed to be commercially cleared"; the Hugging Face `apache-2.0` tag alone does not show this. No candidate removes the COCO question (all were trained on COCO, mostly from ImageNet-seeded backbones); only pretraining on permissive data yourself changes the lineage, at GPU-days to GPU-weeks (researcher's unsourced estimate). The researcher's list of ten questions for a lawyer is section 6 of that file. Decision needed: whether to commission a spike, and when to ask counsel.

### (c) DONE as a prototype (2026-10-23, later in the round): threshold selection and the confidence wording; calibration still open
**Built:** `web/projects/training/operating_points.py` (tests `web/projects/tests_operating_points.py`, 73 test methods): for 80, 90 and 95% of the drawn boxes found, a suggested cut-off from the Checking pictures, what it gave on the Final test pictures, a 90% range from a seeded picture-level re-draw, and, per score range, how often a score was right on the Final test pictures. "Enough data" means at least 400 drawn boxes on the Checking pictures and at least 30 per label and a Final test split with boxes; a cut-off is `recommended` only if the target was also reached in at least 90% of the re-draws. Rounding never flatters (shares and low ends down, high ends up). The results card has a closed "How far to trust the scores" disclosure; the check screen words a score from its measured band (or shows the raw "score 0.xx" with "the computer's own number, not checked"); the builder offers "Use the suggested setting" only when a value is recommended and never applies it by itself. **The fixed "Very sure / Fairly sure / Somewhat sure / Not very sure" phrases were removed.** Details: `docs/guides/model-quality.md` ("Operating points and measured wording"), shape: `docs/api-reference.md`.

**Still open:** Platt or isotonic **calibration** (the spike showed it works mechanically on its synthetic data and changes only the words; it is not built, and scores are shown as the computer's own numbers); **per-label cut-offs** (one cut-off applies to every label); everything on **real imagery** (all numbers so far are from generated boxes or drawn shapes); the "400 boxes" and "20 predictions per range" minimums are judgements from one spike; the comparison against the currently approved model has since been built as a different thing (found boxes on the same pictures; see (e)), not a comparison of operating points. The original note follows, as written before this was built.

From the calibration spike (`docs/guides/model-quality.md`, "What was measured"; synthetic shapes, one weak from-scratch model, one machine): the check screen's old "Somewhat sure" band was right 40.1% of the time, and recall targets 0.90 and 0.95 were unreachable for that model. The spike's recommendation, which I am repeating and have not tested further: (1) choose a threshold from the validation split's precision-recall curve (a sort and a cumulative sum, no new dependency) only when the fit split has about 400 or more boxes, and always show the recall ceiling; (2) stop printing the raw score as "NN%" and derive the wording bands from measured per-band precision, or print "model score 0.97, not a probability"; (3) calibration (Platt, two numbers stored with the evaluation) later and optional. Decision needed: whether to change the wording now (cheap, independent of any library) and whether threshold selection comes before a second base model. Any wording change must be re-measured on a stronger model and on real pictures first. *(Points 1 and 2 are built; point 3 and the re-measurement on real pictures are not.)*

### (d) Code follow-ups from the reviews (status updated 2026-10-24)
- ~~A **public fingerprint helper** in `datasets.service`~~ **Done:** `content_fingerprint(project)` is public (`_fingerprint` stays as an alias); `training/lifecycle.py` calls the public name.
- ~~A **time budget / skip-files parameter on `analyse`**~~ **Done:** `analyse(project, config, file_check="full" | "skip" | <seconds>)`; the readiness preview uses a 2-second budget (`READINESS_FILE_CHECK_SECONDS`) and says when it did not re-check every file (`one_click.notes`, `files_rechecked`); `Analysis.complete` and `IncompleteAnalysis` stop a version from being frozen from a partial check. The start itself always checks every file. See `docs/api-reference.md`.
- ~~A **shared "accepted boxes" helper**~~ **Done:** `datasets.service.accepted_boxes(project)` is used by the fingerprint, `analyse` and `datasets/health.py`. (The review counted four places; this was checked for the three named here, not re-counted elsewhere.)
- The **real usability session** kit exists (`docs/usability/m2-usability-session.md`) and has never been run. **[OWNER]** to schedule it; the persona report recommends confirming its tickets T1-T4 with two or three real non-technical people before treating the fixes as settled. *Still open.*
- Not covered by anything in this round: Firefox and Safari on the new flow, a screen-reader pass, a real helper run with real ready-made weights, and the Docker steps in `web/DEPLOYMENT.md` run end to end. *Still open.*

### (e) What came next (2026-10-24)
The improve loop (pulling operator corrections into a project, and comparing a new model with the one in use) was planned and built in [`2026-10-23-improve-loop.md`](2026-10-23-improve-loop.md); its status, open follow-ups and owner decisions are there. The comparison with the currently approved model, listed above as "no comparison against the currently approved model", now exists as information in the results card and approve dialog (synthetic checks only).
