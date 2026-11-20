"""Signal classification backend: scikit-learn window features + a linear classifier.

Wave 2's signals slice (OS-SIG-001, classification only): the dataset is a frozen dataset
version holding ONE CSV (the ``time_series`` modality plugin's documented wide format - a
``series`` column, one value column per time step, a ``label`` column) and one accepted
``signal_dataset`` annotation. The model is deliberately classical: each series row is turned
into a fixed feature vector of *window statistics* (per-half and overall mean, standard
deviation, min/max, slope, and how many times the series crosses its midline - the shape cues
that separate sines from squares from noise), and a regularized logistic regression is fit on
those features. Fast, dependency-light, honest about its limits.

What this backend does, step by step (the text backend's pattern):

* ``build_dataset`` materializes the frozen CSV under the scratch dir (the runner's
  ``export_entries`` / ``entry_ground_truth``) and writes ``target.json`` recording the
  accepted ``signal_dataset`` annotation's label.
* Rows are split 80/10/10 inside ``train``, seeded on the CSV's sha256 prefix, so the same
  frozen version always splits the same way. The test row indices travel in a sidecar JSON
  next to the artifact, so ``evaluate`` scores exactly the series the model was not trained
  on - the same honesty rule the tabular, text and vision backends follow.
* ``evaluate`` reports the share of held-out series classified correctly plus per-class
  precision/recall/F1, in the same shape the audio and text backends use (``available``,
  plain summary, numbers, ``limits``).

Forecasting and anomaly detection are explicitly out of scope for this slice (see
docs/plans/2026-11-20-os-sig-001-signal-classification.md); the constraints say so.

Module import stays light: sklearn, joblib and numpy are imported only inside the worker
methods, and ``check_available`` uses ``find_spec`` so the web process can describe the
backend without importing any of them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import (
    Availability,
    CancelCallback,
    ProgressCallback,
    TrainingBackend,
    TrainingCancelled,
    TrainResult,
)

ARTIFACT_NAME = "model.joblib"
ARTIFACT_FORMAT = "athena.signal-sklearn/1"
SIDECAR_NAME = "training_sidecar.json"
CSV_NAME = "signals.csv"
RANDOM_SEED = 42

METRIC_NOTES = (
    "These numbers come from series the model was not trained on. They are a check, not a promise: "
    "series you collect later can behave in ways that change the answers."
)


def _seed_from_sha(sha256: str) -> int:
    return int(sha256[:8], 16) % (2**31)


@dataclass
class SignalRowItem:
    """One held-out signal row, as ``evaluate`` understands it.

    The signals twin of the text backend's ``TextRowItem``: the CSV the row lives in, the
    row's index in that file, and its ground-truth label. ``asset_id`` is the frozen entry's
    asset, when the caller can name it."""

    path: Path
    row: int
    ground_truth: Any = None
    asset_id: str | None = None


@dataclass
class Sidecar:
    """Everything ``evaluate`` needs that the joblib artifact does not carry comfortably."""

    format: str = ARTIFACT_FORMAT
    csv_path: str = CSV_NAME
    csv_sha256: str = ""
    class_names: list[str] = field(default_factory=list)
    test_rows: list[int] = field(default_factory=list)
    train_rows: int = 0
    feature_names: list[str] = field(default_factory=list)


def _features(values: list[float]) -> list[float]:
    """The window statistics one series row becomes (order matches ``_feature_names``).

    Deliberately simple, deliberately fixed: means/stds/min/max for each half and the whole
    row, a least-squares slope for the whole row and each half, and the midline-crossing
    count. These are the shape cues a linear classifier can actually use; anything fancier
    (spectral features, learned windows) is future work and would need its own slice.
    """
    half = len(values) // 2
    left, right = values[:half], values[half:]

    def stats(window: list[float]) -> list[float]:
        if not window:
            return [0.0, 0.0, 0.0, 0.0, 0.0]
        count = len(window)
        mean = sum(window) / count
        variance = sum((value - mean) ** 2 for value in window) / count
        slope = 0.0
        if count > 1:
            # Least-squares slope against the time index 0..n-1.
            mean_x = (count - 1) / 2
            denominator = sum((index - mean_x) ** 2 for index in range(count))
            slope = (
                sum((index - mean_x) * (value - mean) for index, value in enumerate(window)) / denominator
                if denominator
                else 0.0
            )
        return [mean, variance**0.5, min(window), max(window), slope]

    mid = (min(values) + max(values)) / 2
    crossings = sum(
        1
        for previous, current in zip(values, values[1:])
        if (previous < mid) != (current < mid)
    )
    return [*stats(left), *stats(right), *stats(values), float(crossings)]


FEATURE_NAMES = [
    "left_mean", "left_std", "left_min", "left_max", "left_slope",
    "right_mean", "right_std", "right_min", "right_max", "right_slope",
    "overall_mean", "overall_std", "overall_min", "overall_max", "overall_slope",
    "midline_crossings",
]


class SignalSklearnBackend(TrainingBackend):
    name = "signal-sklearn"
    description = (
        "Signal classification with scikit-learn: window statistics (means, standard deviations, "
        "slope, midline crossings) for each series and a regularized linear classifier "
        "(LogisticRegression), learned from the rows of one signal CSV."
    )
    license_note = (
        "Code: scikit-learn and joblib, both BSD-3-Clause. No pretrained weights are "
        "downloaded or shipped: the model is learned only from the series in your CSV. It is a "
        "window-statistics model, not a forecaster - it knows shape cues, not what comes next."
    )
    dataset_format = ""  # non-vision: build_dataset writes its own files
    constraints = (
        "Needs scikit-learn in the worker environment.",
        "One signal CSV per project: the frozen entry's file is the whole dataset.",
        "Classification only - one label per whole series. Forecasting (predicting the next "
        "values) and anomaly detection (flagging unusual series against a baseline) are not "
        "supported by this backend.",
        "Every series must be the same length (the CSV's value columns); series of different "
        "lengths must be padded or trimmed to one length before upload.",
        "Rows are split 80/10/10 inside training, seeded on the CSV's checksum, so the same "
        "frozen version always splits the same way.",
        "CPU only; thousands of series train in seconds.",
        "A window-statistics model: it generalizes over simple shape cues, so series whose "
        "meaning lies in subtle patterns can be sorted wrongly.",
    )

    # -- availability / config ---------------------------------------------------------
    def check_available(self) -> Availability:
        if importlib.util.find_spec("sklearn") is None:
            return Availability(
                False,
                "scikit-learn is not installed in this environment.",
                "Install the training helper's requirements (scikit-learn) in the environment "
                "that runs `manage.py train_worker`, then start the worker.",
            )
        return Availability(True, "scikit-learn is installed.")

    def default_config(self) -> dict[str, Any]:
        return {}

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - set()
        if unknown:
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        return {}

    def runtime_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {}
        try:
            import sklearn

            info["sklearn"] = sklearn.__version__
        except Exception:  # noqa: BLE001 - runtime_info must never raise
            pass
        return info

    # -- dataset -----------------------------------------------------------------------
    def build_dataset(self, run, dest: Path) -> tuple[list[SignalRowItem], list[str]]:
        """Materialize the frozen signal CSV under ``dest`` and return (eval items, labels).

        Writes ``signals.csv`` (the frozen file verbatim) plus ``target.json`` recording the
        accepted ``signal_dataset`` annotation's label, and returns ``(eval_items, labels)``:
        one :class:`SignalRowItem` per frozen entry with ``row`` filled in later by ``train``
        (once the split is seeded) and ``labels`` = the project's label list (the classes to
        sort series into)."""
        from .runner import entry_ground_truth, export_entries

        ground_truth = entry_ground_truth(run)
        written = export_entries(run, dest)
        if not written or not ground_truth:
            raise ValueError(
                "This dataset version has no frozen signal file to train on. Create a new dataset "
                "version from the project's signal file and label confirmation."
            )
        if len(written) != len(ground_truth):
            raise ValueError("The frozen signal entries and their recorded labels do not match. Try again.")

        items: list[SignalRowItem] = []
        targets: list[str] = []
        for path, entry in zip(written, ground_truth):
            annotation = entry.get("annotation") or {}
            target = annotation.get("label") if isinstance(annotation, dict) else None
            if not isinstance(target, str) or not target:
                raise ValueError(
                    "The frozen signal dataset has no confirmed label. Confirm one in the project, "
                    "then create a new dataset version."
                )
            if targets and target != targets[0]:
                raise ValueError("The frozen entries disagree about the label. Create a new dataset version.")
            targets.append(target)
            csv = dest / CSV_NAME
            if csv.exists() and csv.read_bytes() != path.read_bytes():
                raise ValueError(
                    "This dataset version holds more than one different signal file. Train on one file at a time."
                )
            if not csv.exists():
                csv.write_bytes(path.read_bytes())
            items.append(
                SignalRowItem(
                    path=csv,
                    row=-1,  # filled in by train(), once the row split is known
                    ground_truth=None,
                    asset_id=entry.get("asset_id"),
                )
            )
        (dest / "target.json").write_text(json.dumps({"label": targets[0]}), encoding="utf-8")
        return items, list(run.dataset_version.label_names)

    # -- training ----------------------------------------------------------------------
    def _read_rows(self, path: Path) -> list[tuple[str, list[float], str]]:
        """(series name, values, label) per data row from the CSV, stdlib only; the header and
        shape were validated at upload time and the file is checksum-verified, so a malformed
        row here is a plain failure."""
        import csv as stdlib_csv

        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = stdlib_csv.reader(handle)
            header = next(reader, None)
            names = [name.strip().lower() for name in (header or [])]
            if not names or names[0] != "series" or names[-1] != "label":
                raise ValueError("The frozen signal file no longer has the documented series,...,label header.")
            rows: list[tuple[str, list[float], str]] = []
            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue
                if len(row) != len(names):
                    raise ValueError("The frozen signal file has a row with the wrong number of columns.")
                values: list[float] = []
                for cell in row[1:-1]:
                    try:
                        values.append(float(cell))
                    except ValueError as error:
                        raise ValueError("The frozen signal file has a value cell that is not a number.") from error
                rows.append((row[0].strip(), values, row[-1].strip()))
        return rows

    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import joblib
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        cfg = self.validate_config(config)
        csv = dataset_dir / CSV_NAME
        if not csv.is_file():
            raise ValueError("The dataset directory holds no signal file. Start the run again.")
        _ = cfg  # no settings today; the class list comes from the data

        progress_cb(0.05, "Reading the signal rows")
        rows = self._read_rows(csv)
        if cancel_cb():
            raise TrainingCancelled("Training was cancelled.")
        y_raw = [label for _, _, label in rows]
        class_names = sorted(set(y_raw))
        if len(class_names) < 2:
            raise ValueError("The label column has only one distinct value, so there is nothing to learn.")
        if len(rows) < 10:
            raise ValueError("The signal file needs at least 10 rows to learn from. Add more rows.")

        progress_cb(0.15, f"{len(rows)} series to learn from; sorting into {len(class_names)} labels")
        index_of = {name: i for i, name in enumerate(class_names)}
        y = np.array([index_of[label] for label in y_raw])
        X = np.array([_features(values) for _, values, _ in rows], dtype=float)

        # The row split: 80/10/10, seeded on the CSV's checksum so the same frozen version
        # always splits identically (test indices land in the sidecar for evaluate()).
        seed_source = hashlib.sha256(csv.read_bytes()).hexdigest()
        seed = _seed_from_sha(seed_source)
        order = np.random.RandomState(seed).permutation(len(rows))
        n = len(rows)
        train_rows = order[: int(n * 0.8)]
        val_rows = order[int(n * 0.8) : int(n * 0.9)]
        test_rows = order[int(n * 0.9) :]
        X_train, y_train = X[train_rows], y[train_rows]
        X_val, y_val = X[val_rows], y[val_rows]

        progress_cb(0.3, "Teaching the computer from the training series")
        pipeline = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        max_iter=2000,
                        C=4.0,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        )
        pipeline.fit(X_train, y_train)
        if cancel_cb():
            raise TrainingCancelled("Training was cancelled.")

        progress_cb(0.7, "Checking the first held-back series")
        val_predictions = pipeline.predict(X_val) if len(val_rows) else []
        metrics: dict[str, Any] = {
            "task_type": "classification",
            "train_rows": int(len(train_rows)),
            "validation_rows": int(len(val_rows)),
            "test_rows": int(len(test_rows)),
            "classes": len(class_names),
            "features": len(FEATURE_NAMES),
            "model": "window-features-logreg",
        }
        metrics["validation_accuracy"] = (
            round(float(accuracy_score(y_val, val_predictions)), 4) if len(val_rows) else None
        )

        artifact = dataset_dir.parent / ARTIFACT_NAME
        joblib.dump(pipeline, artifact)
        sidecar = Sidecar(
            csv_path=CSV_NAME,
            csv_sha256=seed_source,
            class_names=class_names,
            test_rows=[int(row) for row in test_rows],
            train_rows=int(len(train_rows)),
            feature_names=list(FEATURE_NAMES),
        )
        (dataset_dir.parent / SIDECAR_NAME).write_text(
            json.dumps({key: value for key, value in sidecar.__dict__.items()}), encoding="utf-8"
        )
        metrics["note"] = METRIC_NOTES
        return TrainResult(artifact_path=artifact, metrics=metrics, label_names=class_names)

    # -- evaluation --------------------------------------------------------------------
    def evaluate(
        self, artifact: Path, items: list[SignalRowItem], labels: list[str], thresholds: dict[str, Any]
    ) -> dict[str, Any]:
        """Score the model on the held-out test rows named in the training sidecar.

        Returns the audio and text backends' documented shape (``available``, plain
        ``summary``, numbers, ``limits``): the share of series classified correctly plus
        per-class precision/recall/F1, so the run's report stays honest and uniform across
        non-vision recipes."""
        import joblib
        import numpy as np

        sidecar_path = artifact.parent / SIDECAR_NAME
        if not sidecar_path.is_file():
            raise ValueError("This model has no record of which rows were held back, so it cannot be scored honestly.")
        sidecar = Sidecar(**json.loads(sidecar_path.read_text(encoding="utf-8")))
        csv = artifact.parent / "dataset" / sidecar.csv_path
        if not csv.is_file():
            raise ValueError("The frozen signal file this model was trained on is no longer available.")
        rows = self._read_rows(csv)
        truth = [rows[int(index)][2] for index in sidecar.test_rows]
        X_test = np.array([_features(rows[int(index)][1]) for index in sidecar.test_rows], dtype=float)
        predictions = joblib.load(artifact).predict(X_test)

        def named(value) -> str:
            try:
                index = int(value)
            except (TypeError, ValueError):
                return str(value)
            return sidecar.class_names[index] if 0 <= index < len(sidecar.class_names) else str(value)

        y_true = [str(value) for value in truth]
        y_pred = [named(value) for value in predictions]
        names = sidecar.class_names or sorted(set(y_true) | set(y_pred))
        from sklearn.metrics import accuracy_score

        accuracy = float(accuracy_score(y_true, y_pred)) if y_true else 0.0
        per_class = {}
        for name in names:
            true_positive = sum(1 for true, pred in zip(y_true, y_pred) if true == name and pred == name)
            actual_total = sum(1 for true in y_true if true == name)
            predicted_total = sum(1 for pred in y_pred if pred == name)
            prec = true_positive / predicted_total if predicted_total else None
            rec = true_positive / actual_total if actual_total else None
            score = 2 * prec * rec / (prec + rec) if prec is not None and rec is not None and prec + rec else None
            per_class[name] = {
                "series": int(actual_total),
                "correct": int(true_positive),
                "wrong": int(actual_total - true_positive),
                "precision": round(prec, 4) if prec is not None else None,
                "recall": round(rec, 4) if rec is not None else None,
                "f1": round(score, 4) if score is not None else None,
            }
        correct = sum(1 for true, pred in zip(y_true, y_pred) if true == pred)
        limits = [METRIC_NOTES]
        if len(y_true) < 20:
            limits.append(
                f"Only {len(y_true)} held-back series were scored. That is a quick check, not a dependable measurement."
            )
        return {
            "available": True,
            "reason": "",
            "eval_kind": "signal_classification",
            "summary": (
                f"On {len(y_true)} series the model had never seen, it sorted {correct} of them "
                f"into the right label ({accuracy:.0%})."
            ),
            "accuracy": round(accuracy, 4),
            "series_correct": int(correct),
            "per_label": per_class,
            "data": {"series": len(y_true)},
            "limits": limits,
        }

    # -- inference (not part of this slice) --------------------------------------------
    def predict(self, artifact, image, thresholds):
        raise NotImplementedError("This backend classifies signal rows, not pictures.")
