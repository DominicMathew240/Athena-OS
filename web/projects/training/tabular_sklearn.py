"""Tabular backend: scikit-learn ensemble on one frozen table (CSV or XLSX).

Wave 1 port of Athena Forge's ``TabularTrainer`` (``backend/ml/tabular_trainer.py``, PR #79)
onto the Wave 0 snapshot model. The algorithm is the same: pandas loads the file (with the
skiprows=1 retry for CSVs whose first row is a stray title), the target column is
auto-detected when the caller did not choose one, classification vs regression is
auto-detected from the target, and a sklearn Pipeline (SimpleImputer + OrdinalEncoder /
StandardScaler over a ColumnTransformer) feeds a RandomForest + GradientBoosting ensemble
(``VotingClassifier`` soft voting / ``VotingRegressor``); XGBoost joins the ensemble when it
happens to be installed. The fitted pipeline is saved with joblib.

What is different from Forge, and why:

* The dataset is a *frozen dataset version*: the table file plus its accepted
  ``table_target`` annotation (the chosen target column), materialized by the runner's
  ``export_entries`` / ``entry_ground_truth``. ``build_dataset`` writes ``table.csv`` (or
  ``table.xlsx``) plus ``target.json`` into the scratch dir.
* Rows are split inside ``train`` (80/10/10, seeded on the table file's sha256 prefix, so
  the same frozen table always splits the same way). The test row indices travel in a
  sidecar JSON next to the artifact, so ``evaluate`` scores exactly the rows the model was
  not trained on - the same honesty rule the vision backends follow with their test split.
* Metrics are worded for people: classification reports the share of correct rows plus
  per-column precision/recall; regression reports how far off the guesses are on average
  (MAE, RMSE) and R².

Module import stays light: sklearn, pandas and joblib are imported only inside the worker
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
ARTIFACT_FORMAT = "athena.tabular-sklearn/1"
SIDECAR_NAME = "training_sidecar.json"
TABLE_BASENAME = "table"
TARGET_FILENAME = "target.json"
DEFAULT_MODELS = ("ensemble", "forest")
MAX_ESTIMATORS = 500
RANDOM_SEED = 42

METRIC_NOTES = (
    "These numbers come from rows the model was not trained on. They are a check, not a promise: "
    "rows you collect later can be different in ways that change the answers."
)


def _seed_from_sha(sha256: str) -> int:
    return int(sha256[:8], 16) % (2**31)


@dataclass
class RowItem:
    """One held-out table row, as ``evaluate`` understands it.

    The tabular twin of the vision backends' ``EvalItem``: instead of an image path and boxes
    it carries the table file the row lives in, the row's index in that file, and its ground
    truth (the target column's true value). ``asset_id`` is the frozen entry's asset, when the
    caller can name it."""

    path: Path
    row: int
    ground_truth: Any
    asset_id: str | None = None


@dataclass
class Sidecar:
    """Everything ``evaluate`` needs that the joblib artifact does not carry comfortably."""

    format: str = ARTIFACT_FORMAT
    table_path: str = ""
    table_sha256: str = ""
    target_column: str = ""
    task_type: str = ""  # "classification" | "regression"
    class_names: list[str] = field(default_factory=list)
    test_rows: list[int] = field(default_factory=list)
    feature_columns: list[str] = field(default_factory=list)


def _plain_value(value: Any) -> Any:
    """A JSON-safe copy of a metric or ground-truth value (numpy scalars become plain numbers)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if hasattr(value, "item"):  # a numpy scalar
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, (int, float)):
        return value
    return str(value)


class TabularSklearnBackend(TrainingBackend):
    name = "tabular-sklearn"
    description = (
        "Random forest + gradient boosting ensemble (scikit-learn) on the rows of one table. "
        "Classification or regression is chosen from the target column."
    )
    license_note = (
        "Code: scikit-learn, pandas and joblib, all BSD-3-Clause (joblib carries an extra "
        "licence note about the bundled Loky/Layer tests). No pretrained weights are "
        "downloaded or shipped: the model is learned only from the rows in your table. "
        "When XGBoost happens to be installed it joins the ensemble; XGBoost is Apache-2.0."
    )
    dataset_format = ""  # non-vision: build_dataset writes its own files
    constraints = (
        "Needs scikit-learn and pandas in the worker environment; .xlsx tables also need openpyxl.",
        "One table per project: the frozen entry's file is the whole dataset.",
        "Rows are split 80/10/10 inside training, seeded on the table's checksum, so the same "
        "frozen version always splits the same way.",
        "CPU only; a few hundred rows train in seconds, tens of thousands in minutes.",
    )

    # -- availability / config ---------------------------------------------------------
    def check_available(self) -> Availability:
        missing = [name for name in ("sklearn", "pandas") if importlib.util.find_spec(name) is None]
        if not missing:
            return Availability(True, "scikit-learn and pandas are installed.")
        return Availability(
            False,
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed in this environment.",
            "Install the training helper's requirements (scikit-learn and pandas) in the "
            "environment that runs `manage.py train_worker`, then start the worker.",
        )

    def default_config(self) -> dict[str, Any]:
        return {}

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        unknown = set(config) - {"target_column", "model"}
        if unknown:
            raise ValueError(f"Unsupported training setting: {', '.join(sorted(map(str, unknown)))}.")
        normalized: dict[str, Any] = {}
        if "target_column" in config:
            target = config["target_column"]
            if not isinstance(target, str) or not target.strip():
                raise ValueError("target_column must be the name of one column of the table.")
            normalized["target_column"] = target.strip()
        if "model" in config:
            model = config["model"]
            if model not in DEFAULT_MODELS:
                raise ValueError("model must be one of: " + ", ".join(f"'{m}'" for m in DEFAULT_MODELS) + ".")
            normalized["model"] = model
        return normalized

    def runtime_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {}
        try:
            import sklearn

            info["sklearn"] = sklearn.__version__
        except Exception:  # noqa: BLE001 - runtime_info must never raise
            pass
        try:
            import pandas

            info["pandas"] = pandas.__version__
        except Exception:  # noqa: BLE001
            pass
        return info

    # -- dataset -----------------------------------------------------------------------
    def build_dataset(self, run, dest: Path) -> tuple[list[RowItem], list[str]]:
        """Materialize the frozen table and its target choice under ``dest``.

        Writes ``table.csv`` (or ``table.xlsx``) plus ``target.json`` (the chosen target
        column, from the entry's frozen annotation) and returns ``(eval_items, labels)``:
        one :class:`RowItem` per held-out row - determined in ``train`` once the split is
        seeded and written to the sidecar, so until then every row of the test split's file
        is listed and ``train`` narrows it - and ``labels = [target_column]``.
        """
        from .runner import entry_ground_truth, export_entries

        ground_truth = entry_ground_truth(run)
        written = export_entries(run, dest)
        if not written or not ground_truth:
            raise ValueError(
                "This dataset version has no frozen table to train on. Create a new dataset "
                "version from the project's table and target choice."
            )
        if len(written) != len(ground_truth):
            raise ValueError("The frozen table entries and their recorded choices do not match. Try again.")

        items: list[RowItem] = []
        targets: list[str] = []
        for path, entry in zip(written, ground_truth):
            annotation = entry.get("annotation") or {}
            target = annotation.get("label") if isinstance(annotation, dict) else None
            if not isinstance(target, str) or not target:
                raise ValueError(
                    "The frozen table has no chosen target column. Choose one in the project, "
                    "then create a new dataset version."
                )
            if targets and target != targets[0]:
                raise ValueError("The frozen entries disagree about which column to predict. Create a new dataset version.")
            targets.append(target)
            table = dest / f"{TABLE_BASENAME}{path.suffix.lower()}"
            if table.exists() and table.read_bytes() != path.read_bytes():
                raise ValueError("This dataset version holds more than one different table. Train on one table at a time.")
            if not table.exists():
                table.write_bytes(path.read_bytes())
            items.append(
                RowItem(
                    path=table,
                    row=-1,  # filled in by train(), once the row split is known
                    ground_truth=None,
                    asset_id=entry.get("asset_id"),
                )
            )
        (dest / TARGET_FILENAME).write_text(json.dumps({"target_column": targets[0]}), encoding="utf-8")
        return items, [targets[0]]

    # -- training ----------------------------------------------------------------------
    def _load_table(self, path: Path):
        """Load the table with pandas, keeping Forge's single-column retry for CSVs."""
        import pandas as pd

        if path.suffix.lower() == ".xlsx":
            try:
                return pd.read_excel(path)
            except ImportError as error:
                raise ValueError(
                    "Reading Excel (.xlsx) tables needs the openpyxl package in the training "
                    "helper's environment. Install it there, or upload the table as a CSV."
                ) from error
        frame = pd.read_csv(path)
        try:
            if frame.shape[1] == 1:  # a stray title row above the header is common
                retry = pd.read_csv(path, skiprows=1)
                if retry.shape[1] > 1:
                    return retry
        except Exception:  # noqa: BLE001 - the retry is best-effort; the first read stands
            pass
        return frame

    def _auto_target(self, frame) -> str:
        """Forge's rule: the last text column, else the last column."""
        text_columns = [column for column in frame.columns if frame[column].dtype == object]
        if text_columns:
            return str(text_columns[-1])
        return str(frame.columns[-1])

    def train(
        self,
        dataset_dir: Path,
        config: dict[str, Any],
        progress_cb: ProgressCallback,
        cancel_cb: CancelCallback,
    ) -> TrainResult:
        import joblib
        import numpy as np
        import pandas as pd
        from pandas.api import types as pdtypes
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import (
            GradientBoostingClassifier,
            GradientBoostingRegressor,
            RandomForestClassifier,
            RandomForestRegressor,
            VotingClassifier,
            VotingRegressor,
        )
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import accuracy_score
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import LabelEncoder, OrdinalEncoder, StandardScaler

        cfg = self.validate_config(config)
        target_json = dataset_dir / TARGET_FILENAME
        annotation_target = ""
        if target_json.is_file():
            annotation_target = str(json.loads(target_json.read_text(encoding="utf-8")).get("target_column", ""))
        table_files = sorted(
            path for path in dataset_dir.glob(f"{TABLE_BASENAME}.*") if path.suffix.lower() in (".csv", ".xlsx")
        )
        if not table_files:
            raise ValueError("The dataset directory holds no table file. Start the run again.")
        table = table_files[0]

        progress_cb(0.05, "Loading the table")
        frame = self._load_table(table)
        if cancel_cb():
            raise TrainingCancelled("Training was cancelled.")

        target = cfg.get("target_column") or annotation_target or self._auto_target(frame)
        if target not in [str(column) for column in frame.columns]:
            raise ValueError(
                f"The column to predict (“{target}”) is not in this table. Check the target choice."
            )
        frame = frame.dropna(subset=[target])
        feature_columns = [str(column) for column in frame.columns if str(column) != target]
        if not feature_columns:
            raise ValueError(
                "The table has no columns left to learn from once the target is set aside. "
                "A table needs at least two columns."
            )
        if len(frame) < 10:
            raise ValueError("The table needs at least 10 complete rows to learn from. Add more rows.")

        progress_cb(0.15, f"{len(frame)} rows to learn from; predicting the column “{target}”")
        y_raw = frame[target]
        numeric_target = pdtypes.is_numeric_dtype(y_raw)
        # Forge's rule: numbers with many distinct values are a regression target; anything
        # else (including text, and numbers with few distinct values) is classification.
        task_type = "regression" if numeric_target and y_raw.nunique(dropna=True) > 20 else "classification"

        class_names: list[str] = []
        if task_type == "classification":
            encoder = LabelEncoder()
            y = encoder.fit_transform(y_raw.astype(str))
            class_names = [str(name) for name in encoder.classes_]
            if len(class_names) < 2:
                raise ValueError("The column to predict has only one distinct value, so there is nothing to learn.")
        else:
            y = pd.to_numeric(y_raw, errors="coerce").fillna(float(np.nanmedian(pd.to_numeric(y_raw, errors="coerce")))).values

        numeric_features = [column for column in feature_columns if pdtypes.is_numeric_dtype(frame[column])]
        categorical_features = [column for column in feature_columns if column not in numeric_features]
        for column in categorical_features:  # avoid mixed-type cells breaking the encoder
            frame[column] = frame[column].astype(str).fillna("unknown")
        X = frame[feature_columns]

        numeric_transformer = Pipeline(
            steps=[("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]
        )
        categorical_transformer = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="constant", fill_value="unknown")),
                ("encoder", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)),
            ]
        )
        preprocessor = ColumnTransformer(
            transformers=[
                ("num", numeric_transformer, numeric_features),
                ("cat", categorical_transformer, categorical_features),
            ],
            remainder="drop",
        )

        # The row split: 80/10/10, seeded on the table's checksum so the same frozen version
        # always splits identically (test indices land in the sidecar for evaluate()).
        seed_source = hashlib.sha256(table.read_bytes()).hexdigest()
        seed = _seed_from_sha(seed_source)
        order = np.random.RandomState(seed).permutation(len(frame))
        n = len(frame)
        train_rows = order[: int(n * 0.8)]
        val_rows = order[int(n * 0.8) : int(n * 0.9)]
        test_rows = order[int(n * 0.9) :]
        X_train, y_train = X.iloc[train_rows], y[train_rows]
        X_val, y_val = X.iloc[val_rows], y[val_rows]

        progress_cb(0.3, "Teaching the computer from the training rows")
        n_estimators = 100
        max_depth = 6
        chosen = cfg.get("model", "ensemble")
        try:
            import xgboost  # noqa: F401 - optional ensemble member

            has_xgb = True
        except ImportError:
            has_xgb = False
        if task_type == "classification":
            estimators = [
                ("rf", RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, random_state=RANDOM_SEED)),
                ("gb", GradientBoostingClassifier(n_estimators=n_estimators, max_depth=max_depth, random_state=RANDOM_SEED)),
            ]
            if has_xgb and chosen == "ensemble":
                estimators.append(("xgb", _xgb_classifier(len(class_names))))
            base_model = (
                VotingClassifier(estimators=estimators, voting="soft")
                if chosen == "ensemble"
                else estimators[0][1]
            )
        else:
            estimators = [
                ("rf", RandomForestRegressor(n_estimators=n_estimators, max_depth=max_depth, random_state=RANDOM_SEED)),
                ("gb", GradientBoostingRegressor(n_estimators=n_estimators, max_depth=max_depth, random_state=RANDOM_SEED)),
            ]
            if has_xgb and chosen == "ensemble":
                estimators.append(("xgb", _xgb_regressor()))
            base_model = VotingRegressor(estimators=estimators) if chosen == "ensemble" else estimators[0][1]
        pipeline = Pipeline(steps=[("preprocessor", preprocessor), ("model", base_model)])
        pipeline.fit(X_train, y_train)
        if cancel_cb():
            raise TrainingCancelled("Training was cancelled.")

        progress_cb(0.7, "Checking the first held-back rows")
        val_predictions = pipeline.predict(X_val) if len(val_rows) else []
        metrics: dict[str, Any] = {
            "task_type": task_type,
            "target_column": target,
            "train_rows": int(len(train_rows)),
            "validation_rows": int(len(val_rows)),
            "test_rows": int(len(test_rows)),
            "features": len(feature_columns),
            "model": "ensemble" if chosen == "ensemble" else "forest",
            "classes": len(class_names),
        }
        if task_type == "classification":
            metrics["validation_accuracy"] = round(float(accuracy_score(y_val, val_predictions)), 4) if len(val_rows) else None
        else:
            metrics["validation_mae"] = round(float(np.mean(np.abs(np.asarray(y_val) - val_predictions))), 4) if len(val_rows) else None

        artifact = dataset_dir.parent / ARTIFACT_NAME
        joblib.dump(pipeline, artifact)
        sidecar = Sidecar(
            table_path=str(table.relative_to(dataset_dir)),
            table_sha256=seed_source,
            target_column=target,
            task_type=task_type,
            class_names=class_names,
            test_rows=[int(row) for row in test_rows],
            feature_columns=feature_columns,
        )
        (dataset_dir.parent / SIDECAR_NAME).write_text(
            json.dumps({key: value for key, value in sidecar.__dict__.items()}), encoding="utf-8"
        )
        metrics["note"] = METRIC_NOTES
        return TrainResult(artifact_path=artifact, metrics=metrics, label_names=[target])

    # -- evaluation --------------------------------------------------------------------
    def evaluate(self, artifact: Path, items: list[RowItem], labels: list[str], thresholds: dict[str, Any]) -> dict[str, Any]:
        """Score the model on the held-out test rows named in the training sidecar.

        Returns the same shape the vision evaluation uses (``available``, plain ``summary``,
        numbers, ``limits``) so the run's report stays honest and uniform: classification
        reports the share of rows predicted correctly plus per-class precision/recall;
        regression reports how far off the guesses are (MAE, RMSE) and R².
        """
        import joblib
        import numpy as np

        sidecar_path = artifact.parent / SIDECAR_NAME
        if not sidecar_path.is_file():
            raise ValueError("This model has no record of which rows were held back, so it cannot be scored honestly.")
        sidecar = Sidecar(**json.loads(sidecar_path.read_text(encoding="utf-8")))
        table = artifact.parent / "dataset" / sidecar.table_path
        if not table.is_file():
            raise ValueError("The frozen table this model was trained on is no longer available.")
        frame = self._load_table(table)
        target = sidecar.target_column
        if target not in [str(column) for column in frame.columns]:
            raise ValueError("The frozen table no longer has the column this model predicts.")
        test_frame = frame.iloc[sidecar.test_rows]
        X = test_frame[[column for column in sidecar.feature_columns if column in test_frame.columns]]
        truth = test_frame[target]
        predictions = joblib.load(artifact).predict(X)
        limits = [METRIC_NOTES]
        if len(test_frame) < 20:
            limits.append(
                f"Only {len(test_frame)} held-back rows were scored. That is a quick check, not a dependable measurement."
            )
        if sidecar.task_type == "classification":
            from sklearn.metrics import accuracy_score, precision_recall_fscore_support

            # The pipeline predicts the label-encoded integers; map them back through the
            # class names recorded at training time so truth and predictions speak the same words.
            def named(value) -> str:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    return str(value)
                return sidecar.class_names[index] if 0 <= index < len(sidecar.class_names) else str(value)

            y_true = [str(value) for value in truth]
            y_pred = [named(value) for value in predictions]
            names = sidecar.class_names or sorted(set(y_true) | set(y_pred))
            accuracy = float(accuracy_score(y_true, y_pred))
            precision, recall, f1, support = precision_recall_fscore_support(
                y_true, y_pred, labels=names, zero_division=0
            )
            per_class = {
                name: {
                    "rows": int(count),
                    "share_correct": None if not count else round(
                        float(sum(1 for true, pred in zip(y_true, y_pred) if true == name and pred == name)) / count, 4
                    ),
                    "precision": None if not count else round(float(precision[i]), 4),
                    "recall": None if not count else round(float(recall[i]), 4),
                }
                for i, (name, count) in enumerate(zip(names, support))
            }
            correct = sum(1 for true, pred in zip(y_true, y_pred) if true == pred)
            return {
                "available": True,
                "kind": "tabular-classification",
                "summary": (
                    f"On {len(test_frame)} rows the model had never seen, it predicted the "
                    f"“{target}” column correctly for {correct} of them "
                    f"({accuracy:.0%})."
                ),
                "metrics": {
                    "target_column": target,
                    "test_rows": int(len(test_frame)),
                    "accuracy": round(accuracy, 4),
                    "per_class": per_class,
                },
                "limits": limits,
            }
        # Regression
        y_true = np.asarray([float(value) for value in pd_to_numeric(truth)], dtype=float)
        y_pred = np.asarray([float(value) for value in predictions], dtype=float)
        errors = np.abs(y_true - y_pred)
        mae = float(np.mean(errors))
        rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
        spread = float(np.std(y_true))
        r2 = float(1 - np.sum((y_true - y_pred) ** 2) / np.sum((y_true - np.mean(y_true)) ** 2)) if spread else None
        return {
            "available": True,
            "kind": "tabular-regression",
            "summary": (
                f"On {len(test_frame)} rows the model had never seen, its guesses for the "
                f"“{target}” column were off by {mae:.3g} on average (typical size of the "
                f"column's values: {spread:.3g})."
            ),
            "metrics": {
                "target_column": target,
                "test_rows": int(len(test_frame)),
                "mean_absolute_error": round(mae, 6),
                "root_mean_squared_error": round(rmse, 6),
                "r2": None if r2 is None else round(r2, 4),
            },
            "limits": limits,
        }

    # -- inference (not part of this slice) --------------------------------------------
    def predict(self, artifact, image, thresholds):
        raise NotImplementedError("This backend predicts table rows, not pictures.")


def pd_to_numeric(series):
    """``pd.to_numeric`` without importing pandas at module import time."""
    import pandas as pd

    return pd.to_numeric(series, errors="raise")


def _xgb_classifier(n_classes: int):
    import xgboost

    return xgboost.XGBClassifier(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        objective="binary:logistic" if n_classes == 2 else "multi:softmax",
        eval_metric="logloss",
        random_state=RANDOM_SEED,
        verbosity=0,
    )


def _xgb_regressor():
    import xgboost

    return xgboost.XGBRegressor(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        objective="reg:squarederror",
        eval_metric="rmse",
        random_state=RANDOM_SEED,
        verbosity=0,
    )
