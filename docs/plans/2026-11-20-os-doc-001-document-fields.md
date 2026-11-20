# OS-DOC-001 (Wave 2, documents slice): Structured document-field extraction and table evaluation

Date: 2026-11-20 · Branch: `wave2/doc-fields` (planned) · Roadmap: section 6, Wave 2
(`docs/plans/2026-11-17-multimodal-platform-roadmap.md`)

Status: **scope-only plan. Nothing here is implemented; no code, tests or UI exist for this
slice yet.** This note follows the OS-SIG-001 design-record convention
(`docs/plans/2026-11-20-os-sig-001-signal-classification.md`) but is written before the work,
not after it.

## Goal

OS-DOC-001 reads "Structured document-field extraction and table evaluation". The slice's
goal is: given document-like inputs (scanned or photographed forms, or PDFs/images of them),
a person teaches which visual regions hold which field, and Athena learns to read those
fields from new documents and to report **field-level accuracy** (per-field correctness,
character/word errors where applicable) rather than one page-level score. The second half —
table evaluation — means: when a document contains a table, extraction quality is measured
cell-by-cell against a corrected reference table, not eyeballed.

The deliverable mirrors the merged Wave 1/2 slices (OCR OS-OCR-001, text OS-NLP-001,
signals OS-SIG-001): one modality plugin, one recipe, one training backend, one end-to-end
test suite, no new core branches.

## Proposed approach (not yet agreed or built)

* A `documents` recipe (key and modality naming to be decided — see open questions),
  extending the existing image modality (a document is a picture) rather than adding a new
  modality plugin, the same way `handwriting-ocr` is an image project with a different
  annotation kind.
* A new annotation kind for field regions: a bounding box plus a field name and the
  corrected text, reusing the existing box-annotation plumbing for the geometry and the
  transcript pattern from `handwriting-ocr` for the text.
* A `doc-fields` training backend following the OCR slice's shape: crops each labelled
  field region, fine-tunes or prompts a text-reading model, and emits per-field predictions
  with a per-field evaluation report (field found / text exact-match / CER-WER) following
  the shared evaluation contract. Frozen dataset versions, splits and the job/worker model
  come unchanged from the Wave 0 pipeline.
* Table evaluation as a structured-annotation comparison: a reference table annotation is
  compared cell-wise (exact match, normalized numeric/text match to be defined) and the
  evaluation reports per-column and per-cell error rates.
* Console: one recipe-picker entry with honest "what data you need / how it is checked"
  wording, mirroring the text and signals entries.

## Out of scope (future work)

* Full free-form document layout understanding (reading order, headings, multi-page
  structure) — this slice learns named fields only.
* Named-entity extraction from plain text (that is OS-NLP-001's NER half, separate work).
* Table *structure* detection (finding table borders/columns without a taught example).
* Signature, stamp or handwriting-vs-print classification beyond the text reading itself.
* Inference/check-screen serving for document projects (same gap as the tabular, text and
  signal slices; the backend `predict` path story is undecided — see open questions).
* Real-data validation and any model-quality claim; acceptance is synthetic-data plumbing
  only, exactly like the merged slices.

## Open questions

* **Recipe/modality naming:** does a document project stay an `image` project with a
  `doc_fields` annotation kind (like OCR), or become its own modality? Leaning image +
  annotation kind, but the table-evaluation half may push toward structured annotations
  that do not fit the image plugin's single-file model.
* **Table input shape:** how does a person correct a *table* on a scanned page? Per-cell
  box annotations would explode the annotation count; a grid-structured annotation format
  needs design before the annotation contract can be frozen.
* **Backend choice:** fine-tune the existing TrOCR path (inherits OS-OCR-001's unresolved
  base-weight licence question), or pair a detector with a lighter text head? Depends on
  whether field *localization* is taught (boxes) or only field *reading* (given a crop).
* **Evaluation contract:** what counts as a "correct" field — exact string, trimmed string,
  or normalized (case/punctuation/number format)? Must be decided before tests are written
  so the evaluation is not retrofitted.
* **Split unit:** pages per document vs fields per page — avoiding leakage when one
  physical document spans several images, mirroring the T35 recording-grouping rule in the
  vision domain.
* Whether this slice is one merge (like OS-SIG-001) or two (fields, then tables), given the
  table-evaluation design is the least settled part.
