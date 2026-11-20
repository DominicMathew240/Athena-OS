# OS-NLP-001 (Wave 2, first slice): Text classification

Date: 2026-11-19 · Branch: `wave2/nlp` · Roadmap: section 6, Wave 2 (`docs/plans/2026-11-17-multimodal-platform-roadmap.md`)

## Scope

Wave 2's OS-NLP-001 reads "Text classification and named-entity recognition". **This slice
implements text classification only.** Named-entity recognition (finding spans inside text),
span annotation tools, sequence-labeling models and entity-level evaluation are explicitly
**out of scope** and left as future work; the backend's `constraints` say so in plain words,
and the console's recipe note does too.

What landed, mirroring the Wave 1 tabular slice (OS-TAB-001, commit `b949a8f`) step for step:

* A `text` modality plugin (`projects/modalities/text.py`): upload validation for one text
  dataset CSV, annotation validation, asset metadata, one-file-per-project limit.
* A `text-classification` recipe (`available: true`, annotation kind `text_dataset`),
  registered in `projects/tasks/__init__.py` and wired through
  `RECIPE_DEFAULT_BACKENDS` (`projects/training_views.py`) to the new backend.
* A `text-sklearn` training backend (`projects/training/text_sklearn.py`): word n-gram
  TF-IDF + regularized logistic regression via scikit-learn, a joblib artifact, and a
  held-out evaluation (accuracy + per-class precision/recall/F1) following the tabular and
  audio backends' evaluation contract. Training runs through the existing worker model; the
  web process never imports sklearn (the ML venv carries it; tests skip cleanly in the lean
  no-torch/no-sklearn venv).
* End-to-end tests (`projects/tests_text.py`): upload → label confirmation → freeze dataset
  version → train (real small synthetic run in the ML venv; skipped with a named reason in
  lean) → candidate → staged → approved lifecycle, all through the same API as the other
  modalities. No new URL routes were needed — the generic layer covers everything.

## Format decisions

* **One primary format: a CSV with a header of exactly `text,label`** (those names, that
  order, comma-separated). Everything else — JSON, TSV, renamed or reordered columns, extra
  columns — is refused with a plain message. One strict documented shape beats three loose
  ones; the operator can always re-save their data as the documented CSV.
* Size/shape limits: 50 MB per file, at least 10 data rows, every row needs non-empty text
  and a non-empty label. The upload summary records the column names, row count and the
  distinct label values (the console fills the project's label list from them).
* **The snapshot pattern is the audio one, not the tabular one**: one asset (the CSV) plus
  one accepted `text_dataset` annotation (a label-name confirmation) freezes ONE generic
  `DatasetEntry` — the whole CSV — through the unchanged Wave 0 pipeline. The tabular slice's
  per-column target choice does not apply: in text classification the "target" is always the
  label column, so the annotation merely confirms the label list that the frozen rows carry.
* Training-side split: rows are split 80/10/10 inside `train`, seeded on the CSV's checksum,
  so the same frozen version always splits identically; the test row indices travel in a
  sidecar JSON so `evaluate` scores exactly the rows the model was not trained on (the same
  honesty rule as the tabular backend).

## Honest limits

* **Synthetic data says nothing about model quality.** The tests' texts are deterministic
  filler built from two fixed word pools; they only prove the plumbing works. A TF-IDF model
  scoring 100% on them means nothing about real text.
* The model is word statistics, not a language model: no pretrained weights, no semantics
  beyond shared word shapes. Texts worded differently from the training rows can be sorted
  wrongly; the backend's `constraints` and the evaluation's `limits` say so.
* Classification only, one label per text. No NER, no span tools, no multi-label.
* No inference/check-screen story yet (same as the tabular slice): the backend's `predict`
  is a refusal, and the results card points at the evaluation report (the generic
  non-vision wording).

## Out of scope (future work)

* Named-entity recognition (the other half of OS-NLP-001).
* Real-data validation, model-quality claims, longer-document handling.
* Text inference screens (classify a pasted text against an approved model).
