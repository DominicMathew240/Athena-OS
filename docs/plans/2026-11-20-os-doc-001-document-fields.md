# OS-DOC-001 — Document fields: table/CSV extraction and table evaluation (Wave 2, slice 1)

Date: 2026-11-20
Branch: `wave2/documents`
Roadmap: `docs/plans/2026-11-17-multimodal-platform-roadmap.md` (section 6, Wave 2, OS-DOC-001)

## Working-tree continuation (2026-11-20; not complete)

An uncommitted continuation adds a read-only comparison API and a console panel, and fixes numeric blank-cell comparison. The API and numerical fix have focused tests; the panel has only a general console smoke check, not a document-specific browser journey. The existing console does not yet provide a complete document upload-and-snapshot journey, so the comparison flow and this roadmap task remain open. The historical statements below describe the original slice, before this continuation.

## What this slice is

The roadmap line is "Structured document-field extraction and table evaluation". This slice
implements the **table-shaped half** of it, and nothing else:

* A `document-fields` recipe (available today) on the `text` modality. The console's project
  picker has a "Check fields in document tables" button for it.
* A document modality plugin (`projects/modalities/document.py`) that validates one
  document-shaped table file per project: **CSV strictly** (UTF-8, a header row of unique
  column names, at least one data row, ragged rows refused); **XLSX only when openpyxl is
  importable in the web process** (the ML venv has it, the lean web venv does not — where it
  is missing the upload is refused in plain words telling the operator to save as CSV, never
  stored unread).
* A **field-type system**: the project's label list is its field definitions, one label per
  field, spelled `name:type` with the type one of `text`, `number`, `date`. A label without a
  type is a text field. Validation and normalization are rule-based (`normalize_cell`): text
  strips; numbers accept thousand separators and normalize to plain decimal text; dates parse
  from a **fixed format set** (ISO, `19 November 2026`, `Nov 19, 2026` and siblings) to ISO.
  An ambiguous date (`03/04/05`, `19/11/2026`) is **invalid, never guessed**. `cell_problems`
  lists the cells that fail their field's type (row, field, value, plain message), capped at
  20.
* **Table evaluation** (`projects/training/document_eval.py`): comparing two dataset versions
  of the same document project, cell by cell, under the field types. This is the slice's
  "extraction accuracy": the reference version's values are treated as correct, the
  candidate's are scored against them — per-field accuracy (matches over comparable cells), a
  capped mismatch list (50 rows, counts always exact), and **unclear** as a third verdict for
  pairs that cannot be judged honestly (an ambiguous date or non-number on either side; a
  field with no column). Unclear cells never help and never hurt accuracy; they are counted.
  Numbers compare with a one-part-in-ten-thousand tolerance. Rows pair by position; differing
  row counts compare the shared rows and note the rest. `plain_comparison` is the checked
  reading side: a stored sentence is never trusted, it is rebuilt from the counts.

The snapshot path is the unchanged Wave 0 generic pipeline (the text/audio/signals slices'
pattern): one asset + one accepted `document_fields` annotation freezes one `DatasetEntry`.
The annotation is the whole-dataset field confirmation (`{"label": "<field label>"}`).

## What is deliberately NOT in this slice (honest limits)

* **No PDF, no OCR, no layout analysis.** PDF/table detection does not exist in this codebase
  yet. Reading a scanned page is future work (it needs detection over page images, a Wave 2+
  item of its own). This slice's documents are tables whose text is already data: CSV, or
  XLSX where openpyxl can read it.
* **No model training.** Extraction here is rule-based validation and normalization, not
  prediction. There is no `document` training backend, on purpose: registering a fake
  `document-rules` backend whose `train()` refuses would add a backend that can never run —
  instead the recipe is listed in `NON_TRAINING_RECIPES` (`training_views.py`), so a document
  project's Teach panel gets the honest "there is no model to teach; compare two saved
  versions instead" refusal (`_task_unavailable_readiness`), and its `/models` and
  `/training/runs` endpoints answer 404 like every not-yet-teachable project. This is NOT the
  tabular-prediction slice: that recipe (CSV upload → target choice → trained classifier)
  already exists and owns table prediction; this slice owns field checking and version
  comparison.
* **No API route for running a comparison.** The comparison module is a library
  (`compare_versions`, `plain_comparison`) with full test coverage; wiring it into the console
  UI (a "compare versions" panel) is the natural next slice. No new URL routes were added
  (the roles test's DECLARED table is unchanged, on purpose).
* **No upload-time field validation of cells.** The upload validates the table's shape
  (header, rows, encoding); `cell_problems` exists as a pure function (tested) for callers,
  but the upload endpoint does not yet run it against the project's fields — a re-upload with
  bad cells is accepted as a table and the problems surface in the comparison, not the
  upload. This keeps upload independent of label edits (fields change after upload in the
  normal flow). A follow-up can wire it in.

## The field-type system, precisely

| Type | Accepts | Normalizes to | Ambiguity rule |
|---|---|---|---|
| `text` | anything | stripped text | — |
| `number` | plain numbers; `,`/space/nbsp thousand separators | plain decimal text (`1,234.5` → `1234.5`) | a non-number is invalid, never coerced |
| `date` | the fixed format set only | ISO `YYYY-MM-DD` | an ambiguous date is invalid, never guessed |

Empty cells are valid and normalize to `""` (a missing value is data, not an error). The
comparison treats two blanks as a match and a blank against a value as a mismatch.

## Evaluation semantics, precisely

* `compare_versions(reference, candidate, project)` — raises `ValueError` with a plain
  message for non-comparable inputs (different projects, same version, different headers,
  a non-CSV (xlsx) file, a missing file, a version that is not exactly one table).
* Verdicts per cell: `match` / `mismatch` / `unclear`. Accuracy per field and overall =
  matches ÷ (matches + mismatches), rounded DOWN to a whole percent (never overstates).
* Numbers: equal as decimals, or within 1/10000 relative tolerance.
* The mismatch list is capped at 50; `mismatch_count` and `mismatches_truncated` are exact.
* `plain_comparison(block)` re-validates a stored block (shape, counts, caps) and rebuilds
  the summary sentence from the counts; anything malformed reads as "not available".

## Where things live

* `web/projects/modalities/document.py` — plugin, field types, `normalize_cell`,
  `cell_problems`, the openpyxl gate. Registered **by annotation kind**
  (`document_fields`) like the OCR plugin, so the `text` modality slot keeps the
  text-classification plugin. `project_assets` now dispatches uploads through
  `annotation_plugin(project)` first (a recipe may ask the same modality for a different
  file shape).
* `web/projects/training/document_eval.py` — the comparison module (pure Python: csv,
  decimal, fractions; the lean web process can run it without pandas/openpyxl).
* `web/projects/tasks/__init__.py` — the `document-fields` recipe.
* `web/projects/training_views.py` — `NON_TRAINING_RECIPES` and the honest readiness
  sentence for document projects.
* `web/projects/tests_document.py` — 47 tests: recipe/dispatch, field types, upload
  validation matrix (CSV + XLSX both paths), annotation, snapshot freeze + export,
  the comparison matrix (matches, tolerance, ambiguous dates, caps, extra rows, reading
  side), and the no-training API state. XLSX tests skip cleanly without openpyxl; the
  no-openpyxl refusal is exercised everywhere via a mocked import.

## Test results (2026-11-20)

* Lean web venv (no torch/sklearn/pandas/openpyxl): **2098 tests, OK, 88 skipped** (the
  document suite: 47 tests, 2 skipped — the two real-openpyxl uploads).
* ML venv (sklearn/pandas/openpyxl): **2098 tests, OK, 2 skipped** (the document suite: 47
  tests, 0 skipped — XLSX uploads run for real).
