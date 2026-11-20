"""The time-series modality plugin: one CSV file whose rows are labelled signal windows.

Wave 2's signals slice (OS-SIG-001, classification only): a signal project's dataset is ONE
CSV file in *wide* format - one series per row. The first column is ``series`` (a name or
id for that signal) and the LAST column is ``label`` (the class that whole series belongs
to); every column in between must be a number (the series' values, in time order, one column
per time step). Long format (a timestamp column plus one value column, series stacked
vertically) is explicitly refused with a plain pointer to this shape, on purpose: one strict,
documented format beats two loose ones, and the operator can always reshape their export as
the documented CSV. Forecasting and anomaly detection are out of scope for this slice (see
docs/plans/2026-11-20-os-sig-001-signal-classification.md); this plugin only carries
classification data.

How a signal project maps onto the Wave 0 snapshot model (the text and tabular slices'
pattern, one entry per file, not the tabular slice's target-column choice):

* Uploading the CSV creates one ``Asset`` (``modality = "time_series"``) whose ``metadata``
  carries the column names, the row count, the value-column count and the file size - parsed
  with the standard library only (the web process never imports pandas).
* The project's LABEL LIST is the set of distinct values in the file's ``label`` column (the
  console fills it from the upload's summary; the operator may then edit it like any label
  list, e.g. to drop a one-example class).
* The *annotation* is the recipe's whole-dataset confirmation: ``annotation_kind`` is
  ``"signal_dataset"`` and ``validate_annotation`` accepts ``{"label": "<a project label>"}``.
  The label names the classification target the frozen rows carry; the generic snapshot's
  ``primary_label`` convention then reads it, and one asset + one accepted annotation freezes
  ONE ``DatasetEntry`` - the whole CSV, unchanged pipeline.

Only stdlib ``csv`` is imported here. sklearn lives in
:mod:`projects.training.signal_sklearn`, in the worker, and is feature-detected there.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from django.core.exceptions import ValidationError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_SIGNAL_BYTES = 50 * 1024 * 1024
#: The two fixed column names at the ends of the header; the value columns sit between them.
FIRST_COLUMN = "series"
LAST_COLUMN = "label"
MIN_VALUE_COLUMNS = 8  # a series shorter than this has nothing to compute features from
MIN_DATA_ROWS = 10
MAX_ROWS_COUNTED = 2_000_000  # a defensive stop; the real limit is the byte cap
MAX_SNIFF_BYTES = 64 * 1024
#: How many distinct label values the upload summary lists (the rest are counted only).
MAX_LABELS_SUMMARY = 200

CSV_CONTENT_TYPE = "text/csv"


def safe_filename(upload_name: str) -> str:
    basename = (upload_name or "signals.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "signals.csv"


def _decodable(upload) -> bool:
    """True when the file is text the CSV reader can parse (UTF-8, with or without a BOM)."""
    try:
        upload.seek(0)
        sample = upload.read(MAX_SNIFF_BYTES)
        sample.decode("utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return False
    finally:
        upload.seek(0)
    return True


def _number(cell: str) -> float | None:
    """The cell as a float, or ``None`` when it is not a plain number (``int``/``float`` text)."""
    text = cell.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _csv_summary(upload) -> tuple[list[str], int, int]:
    """(distinct label values in row order, data-row count, value-column count) from the CSV.

    Raises ``ValidationError`` with a plain message when the file is not the documented
    shape: wrong header, ragged body, non-numeric value cells, or too few rows or columns.
    """
    upload.seek(0)
    wrapper = io.TextIOWrapper(upload, encoding="utf-8-sig", newline="")
    labels: list[str] = []
    seen: set[str] = set()
    rows = 0
    width = 0
    try:
        reader = csv.reader(wrapper)
        header = next(reader, None)
        if header is None:
            raise ValidationError(
                "The file is empty. A signal dataset needs a header row and at least 10 data rows."
            )
        names = [name.strip().lower() for name in header]
        if not names or names[0] != FIRST_COLUMN or names[-1] != LAST_COLUMN:
            raise ValidationError(
                "The first row must start with a series column and end with a label column: "
                "series, then one numbered column per time step, then label. "
                "Save your data as a CSV with that shape and try again."
            )
        width = len(names)
        if width - 2 < MIN_VALUE_COLUMNS:
            raise ValidationError(
                f"A signal needs at least {MIN_VALUE_COLUMNS} value columns (one per time step), "
                f"between the series and label columns. This file has {width - 2}."
            )
        if len(set(names)) != width:
            raise ValidationError(
                "Every column name must be different, so each time step has its own column."
            )
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # a blank line is not a data row
            if len(row) != width:
                raise ValidationError(
                    "Every data row must have the same number of columns as the header. "
                    "Some rows in this file have a different number of columns."
                )
            series = row[0].strip()
            label = row[-1].strip()
            if not series:
                raise ValidationError(
                    "Every row needs a name in the series column. Some rows are empty there."
                )
            if not label:
                raise ValidationError("Every row needs a label in the label column. Some rows are empty there.")
            for cell in row[1:-1]:
                if _number(cell) is None:
                    raise ValidationError(
                        "Every value column must be a number (one time step per column). "
                        "Some cells in this file are not numbers."
                    )
            if label not in seen:
                seen.add(label)
                labels.append(label)
            rows += 1
            if rows >= MAX_ROWS_COUNTED:
                break
    except (csv.Error, UnicodeDecodeError) as error:
        raise ValidationError("The file is not a readable signal dataset (CSV).") from error
    finally:
        # TextIOWrapper closes the file it wraps when it is garbage collected, and the view
        # reads the upload again after validation - detach it so the upload stays open.
        try:
            wrapper.detach()
        except (ValueError, AttributeError, UnboundLocalError):
            pass
        upload.seek(0)
    if rows < MIN_DATA_ROWS:
        raise ValidationError(
            f"A signal dataset needs at least {MIN_DATA_ROWS} data rows below the header. This file has {rows}."
        )
    return labels, rows, width - 2


class SignalModalityPlugin(ModalityPlugin):
    modality = "time_series"
    asset_noun = "signal dataset"
    annotation_kind = "signal_dataset"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Signal dataset files")
        if upload.size > MAX_SIGNAL_BYTES:
            raise ValidationError("Each signal dataset must be 50 MB or smaller.")

        sha256, size_bytes = digest_upload(upload, max_bytes=MAX_SIGNAL_BYTES,
                                           limit_message="Each signal dataset must be 50 MB or smaller.")

        name = (upload.name or "").lower()
        if not name.endswith(".csv"):
            raise ValidationError(
                "Only CSV signal datasets are supported (a series column, value columns and a label column)."
            )
        if not _decodable(upload):
            raise ValidationError("This CSV file is not readable text. Save it again as UTF-8 and retry.")
        labels, rows, value_columns = _csv_summary(upload)

        return {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "extension": "csv",
            "content_type": CSV_CONTENT_TYPE,
            "columns": ["series", "…", "label"],  # a value-column count, not thousands of names
            "row_count": rows,
            "value_column_count": value_columns,
            "label_values": labels[:MAX_LABELS_SUMMARY],
            "label_value_count": len(labels),
        }

    def validate_annotation(self, payload, project) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"label"}:
            raise ValidationError("Confirm one label name: send it as the annotation's label.")
        label = payload.get("label")
        if not isinstance(label, str) or not label:
            raise ValidationError("Choose a label from this project's label list.")
        if label not in project.labels:
            raise ValidationError(
                f"“{label}” is not one of this project's labels. Choose one of its labels."
            )
        return {"label": label}

    def asset_view_metadata(self, asset) -> dict[str, Any]:
        return dict(asset.metadata or {})

    def max_uploads(self) -> int:
        return 1

    def max_batch_bytes(self) -> int:
        return MAX_SIGNAL_BYTES
