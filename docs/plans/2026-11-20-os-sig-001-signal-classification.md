# OS-SIG-001 (Wave 2, signals slice): Time-series classification

Date: 2026-11-20 · Branch: `wave2/signals` · Roadmap: section 6, Wave 2
(`docs/plans/2026-11-17-multimodal-platform-roadmap.md`)

## Scope

Wave 2's OS-SIG-001 reads "Time-series classification, forecasting and anomaly detection".
**This slice implements time-series classification only.** Forecasting (predicting the next
values of a series) and anomaly detection (flagging unusual series against a normal baseline)
are explicitly **out of scope** and left as future work; the backend's `constraints` say so in
plain words, and the console's recipe note does too.

What landed, mirroring the Wave 2 text slice (OS-NLP-001, commits `057ba62`/`65137e8`/
`edcf838`) step for step:

* A `time_series` modality plugin (`projects/modalities/signal.py`): upload validation for
  one signal dataset CSV, annotation validation, asset metadata, one-file-per-project limit.
* A `signal-classification` recipe (`available: true`, annotation kind `signal_dataset`),
  registered in `projects/tasks/__init__.py` and wired through
  `RECIPE_DEFAULT_BACKENDS` (`projects/training_views.py`) to the new backend.
* A `signal-sklearn` training backend (`projects/training/signal_sklearn.py`): window
  statistics per series row (per-half and overall mean/std/min/max, least-squares slope,
  midline crossings) + a regularized logistic regression via scikit-learn, a joblib
  artifact, and a held-out evaluation (accuracy + per-class precision/recall/F1) following
  the tabular/text/audio evaluation contract. Training runs through the existing worker
  model; the web process never imports sklearn (the ML venv carries it; tests skip cleanly
  in the lean no-torch/no-sklearn venv).
* End-to-end tests (`projects/tests_signal.py`): upload → label confirmation → freeze
  dataset version → train (real small synthetic run in the ML venv; skipped with a named
  reason in lean) → candidate → staged → approved lifecycle, all through the same API as the
  other modalities. No new URL routes were needed — the generic layer covers everything.
* The console recipe picker gained a "Tell signal patterns apart" button
  (`console.html` + `console.js`), mirroring the text slice's picker entry.

## Format decision

* **One primary format: wide CSV — one series per row.** The header starts with a `series`
  column (a name or id for that series), ends with a `label` column (the class the whole
  series belongs to), and every column in between is one time step's numeric value. All rows
  must have the same length (series of different lengths must be padded or trimmed before
  upload). Long format (a timestamp column plus one value column, series stacked
  vertically) is **refused** with a plain pointer to the documented shape: one strict
  documented format beats two loose ones, and the operator can always reshape their export
  as the documented CSV.
* Size/shape limits: 50 MB per file, at least 8 value columns (time steps), at least 10
  data rows, every value cell must be a number, unique column names. The upload summary
  records the row count, the value-column count and the distinct label values (the console
  fills the project's label list from them).
* **The snapshot pattern is the text one, not the tabular one**: one asset (the CSV) plus
  one accepted `signal_dataset` annotation (a label-name confirmation) freezes ONE generic
  `DatasetEntry` — the whole CSV — through the unchanged Wave 0 pipeline. The tabular
  slice's per-column target choice does not apply: in signal classification the "target" is
  always the label column, so the annotation merely confirms the label list that the frozen
  rows carry.
* Training-side split: rows are split 80/10/10 inside `train`, seeded on the CSV's checksum,
  so the same frozen version always splits identically; the test row indices travel in a
  sidecar JSON so `evaluate` scores exactly the series the model was not trained on (the
  same honesty rule as the tabular and text backends).

## Model choice, honestly

Each series becomes a fixed 16-number feature vector: per-half and overall mean, standard
deviation, min, max and least-squares slope, plus the count of midline crossings. A
standardized regularized logistic regression is fit on those. This separates obviously
different shapes (a sine from a square from noise) and nothing more. It is *not* a
forecasting model (it never predicts the next value) and *not* an anomaly detector (it has
no notion of a normal baseline beyond the labelled classes it was taught).

## Honest limits

* **Synthetic data says nothing about model quality.** The tests' series are deterministic
  sines, squares and noise; they only prove the plumbing works. A window-statistics model
  scoring 100% on them means nothing about real sensor data.
* The features are hand-chosen shape cues. Series whose meaning lies in subtle patterns
  (slow drifts, rare spikes at unknown positions, frequency content the 16 numbers do not
  capture) can be sorted wrongly; the backend's `constraints` and the evaluation's `limits`
  say so.
* Classification only, one label per whole series. No forecasting, no anomaly detection, no
  variable-length series, no multi-label.
* No inference/check-screen story yet (same as the tabular and text slices): the backend's
  `predict` is a refusal, and the results card points at the evaluation report (the generic
  non-vision wording).

## Out of scope (future work)

* Forecasting recipes (predict the next N values of a series).
* Anomaly detection recipes (normal-only / few-shot baselines — overlaps with the planned
  OS-AUD-002 acoustic anomaly recipes).
* Variable-length series, timestamps-as-data (the `series` column is a name today, not a
  clock; irregular sampling is not modeled), spectral or learned feature extractors.
* Real-data validation and model-quality claims.
* Signal inference screens (score a pasted series against an approved model).
