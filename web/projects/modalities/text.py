"""The text modality plugin: one CSV file whose rows are text-classification examples.

Wave 2's text slice (OS-NLP-001, classification only): a text project's dataset is ONE CSV
file with a ``text`` column and a ``label`` column (in that order, those names). Every other
format - JSON, TSV, renaming the columns, extra columns, reordering them - is refused with a
plain message, on purpose: one strict, documented shape beats three loose ones, and the
operator can always save their data as the documented CSV.

How a text project maps onto the Wave 0 snapshot model (the audio slice's pattern, one entry
per file, not the tabular slice's target-column choice):

* Uploading the CSV creates one ``Asset`` (``modality = "text"``) whose ``metadata`` carries
  the column names, the row count and the file size - parsed with the standard library only,
  exactly like the table plugin's CSV path (the web process never imports pandas).
* The project's LABEL LIST is the set of distinct values in the file's ``label`` column (the
  console fills it from the upload's summary; the operator may then edit it like any label
  list, e.g. to drop a one-example class).
* The *annotation* is the recipe's whole-dataset confirmation, the same "one accepted
  annotation freezes the file" move the tabular slice uses: ``annotation_kind`` is
  ``"text_dataset"`` and ``validate_annotation`` accepts ``{"label": "<a project label>"}``.
  The label names the classification target the frozen rows carry; the generic snapshot's
  ``primary_label`` convention then reads it, and one asset + one accepted annotation freezes
  ONE ``DatasetEntry`` - the whole CSV, unchanged pipeline.

Only stdlib ``csv`` is imported here. sklearn lives in
:mod:`projects.training.text_sklearn`, in the worker, and is feature-detected there.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from django.core.exceptions import ValidationError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_TEXT_BYTES = 50 * 1024 * 1024
#: The one accepted header, exactly. Column order and names are both fixed.
HEADER = ["text", "label"]
MIN_DATA_ROWS = 10
MAX_ROWS_COUNTED = 2_000_000  # a defensive stop; the real limit is the byte cap
MAX_SNIFF_BYTES = 64 * 1024
#: How many distinct label values the upload summary lists (the rest are counted only).
MAX_LABELS_SUMMARY = 200

CSV_CONTENT_TYPE = "text/csv"


def safe_filename(upload_name: str) -> str:
    basename = (upload_name or "texts.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "texts.csv"


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


def _csv_summary(upload) -> tuple[list[str], int]:
    """(distinct label values in row order, data-row count) from the CSV, stdlib only.

    Raises ``ValidationError`` with a plain message when the file is not the documented
    shape: wrong header, ragged body, or fewer than ten data rows.
    """
    upload.seek(0)
    wrapper = io.TextIOWrapper(upload, encoding="utf-8-sig", newline="")
    labels: list[str] = []
    seen: set[str] = set()
    rows = 0
    try:
        reader = csv.reader(wrapper)
        header = next(reader, None)
        if header is None:
            raise ValidationError("The file is empty. A text dataset needs a header row and at least 10 data rows.")
        if [name.strip().lower() for name in header] != HEADER:
            raise ValidationError(
                "The first row must be exactly: text,label (the text to learn from, then its label). "
                "Save your data as a CSV with those two columns and try again."
            )
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # a blank line is not a data row
            if len(row) != 2:
                raise ValidationError(
                    "Every data row must have exactly two columns: text,label. "
                    "Some rows in this file have a different number of columns."
                )
            text, label = row[0].strip(), row[1].strip()
            if not text:
                raise ValidationError("Every row needs some text in the text column. Some rows are empty there.")
            if not label:
                raise ValidationError("Every row needs a label in the label column. Some rows are empty there.")
            if label not in seen:
                seen.add(label)
                labels.append(label)
            rows += 1
            if rows >= MAX_ROWS_COUNTED:
                break
    except (csv.Error, UnicodeDecodeError) as error:
        raise ValidationError("The file is not a readable text dataset (CSV).") from error
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
            f"A text dataset needs at least {MIN_DATA_ROWS} data rows below the header. This file has {rows}."
        )
    return labels, rows


class TextModalityPlugin(ModalityPlugin):
    modality = "text"
    asset_noun = "text dataset"
    annotation_kind = "text_dataset"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Text dataset files")
        if upload.size > MAX_TEXT_BYTES:
            raise ValidationError("Each text dataset must be 50 MB or smaller.")

        sha256, size_bytes = digest_upload(upload, max_bytes=MAX_TEXT_BYTES,
                                           limit_message="Each text dataset must be 50 MB or smaller.")

        name = (upload.name or "").lower()
        if not name.endswith(".csv"):
            raise ValidationError("Only CSV text datasets are supported (a text column and a label column).")
        if not _decodable(upload):
            raise ValidationError("This CSV file is not readable text. Save it again as UTF-8 and retry.")
        labels, rows = _csv_summary(upload)

        return {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "extension": "csv",
            "content_type": CSV_CONTENT_TYPE,
            "columns": list(HEADER),
            "row_count": rows,
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
        return MAX_TEXT_BYTES
