"""The table modality plugin: one CSV or XLSX file is the project's whole dataset.

How a table project maps onto the Wave 0 snapshot model (stated here because it is the one
non-obvious design in the plugin family):

* A tabular project's dataset is ONE table, so the project's *assets* are that single table
  file. Uploading it creates one ``Asset`` (``modality = "table"``) whose ``metadata`` carries
  the column summary: the column names (from the CSV header via the standard library - pandas
  is never imported at validation time), the row count and the file size. pandas, when the
  worker has it, may see more precise types; validation never needs it and never uses it.
* The *annotation* is not per-file: it is the TARGET COLUMN CHOICE. ``annotation_kind`` is
  ``"table_target"`` and ``validate_annotation`` accepts ``{"label": "<column name>"}``.
* The project's LABEL LIST holds the table's COLUMN NAMES (the console fills it from the
  uploaded file's columns), so the label list is the target-choice list. That makes the
  generic snapshot's ``primary_label`` convention (``data["label"]`` is a project label name)
  read the chosen target column: one asset + one accepted annotation freezes as ONE entry
  whose ``DatasetEntry.label`` is the target column - the whole table, unchanged pipeline.

Only stdlib ``csv`` / ``zipfile`` are imported here. pandas lives in
:mod:`projects.training.tabular_sklearn`, in the worker, and is feature-detected there.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

from django.core.exceptions import ValidationError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_TABLE_BYTES = 50 * 1024 * 1024
MIN_COLUMNS = 2
MIN_DATA_ROWS = 3
MAX_SNIFF_BYTES = 64 * 1024
MAX_COLUMNS_SUMMARY = 200
MAX_ROWS_COUNTED = 2_000_000  # a defensive stop; the real limit is the byte cap

CSV_CONTENT_TYPE = "text/csv"
XLSX_CONTENT_TYPE = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)


def safe_filename(upload_name: str) -> str:
    basename = (upload_name or "table.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "table.csv"


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
    """(column names, data-row count) from a CSV, using only the standard library.

    Raises ``ValidationError`` with a plain message when the file is not a table: no header,
    fewer than two columns, fewer than three data rows, or a ragged/unparseable body.
    """
    upload.seek(0)
    sample = upload.read(MAX_SNIFF_BYTES)
    upload.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample.decode("utf-8-sig"), delimiters=",;\t|")
    except (csv.Error, UnicodeDecodeError):
        dialect = csv.excel  # comma-separated is the common case; a ragged body is caught below
    try:
        wrapper = io.TextIOWrapper(upload, encoding="utf-8-sig", newline="")
        reader = csv.reader(wrapper, dialect)
        header = next(reader, None)
        if header is None:
            raise ValidationError("The file is empty. A table needs a header row and at least 3 data rows.")
        columns = [name.strip() for name in header]
        if len(columns) < MIN_COLUMNS:
            raise ValidationError(
                f"A table needs at least {MIN_COLUMNS} columns. This file has only one."
            )
        width = len(header)
        rows = 0
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # a blank line is not a data row
            if len(row) != width:
                raise ValidationError(
                    "The rows in this file do not all have the same number of columns, so it is not a readable table."
                )
            rows += 1
            if rows >= MAX_ROWS_COUNTED:
                break
    except (csv.Error, UnicodeDecodeError) as error:
        raise ValidationError("The file is not a readable text table (CSV).") from error
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
            f"A table needs at least {MIN_DATA_ROWS} data rows below the header. This file has {rows}."
        )
    return columns[:MAX_COLUMNS_SUMMARY], rows


def _xlsx_is_workbook(upload) -> None:
    """Refuse files that are not XLSX workbooks, using only the standard library.

An XLSX file is a ZIP archive with an ``xl/`` folder holding ``workbook.xml`` and at least one
worksheet. Full parsing (and any openpyxl requirement) happens at training time; this is the
cheap structural check that catches renamed files and plain zip archives.
"""
    upload.seek(0)
    try:
        with zipfile.ZipFile(upload) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names or not any(name.startswith("xl/worksheets/") for name in names):
                raise ValidationError(
                    "This file is not a readable Excel workbook (its internal structure is not right)."
                )
    except zipfile.BadZipFile as error:
        raise ValidationError("This file is not a readable Excel workbook (it is not a .xlsx file).") from error
    finally:
        upload.seek(0)


class TableModalityPlugin(ModalityPlugin):
    modality = "table"
    asset_noun = "table"
    annotation_kind = "table_target"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Table files")
        if upload.size > MAX_TABLE_BYTES:
            raise ValidationError("Each table must be 50 MB or smaller.")

        sha256, size_bytes = digest_upload(upload, max_bytes=MAX_TABLE_BYTES,
                                           limit_message="Each table must be 50 MB or smaller.")

        name = (upload.name or "").lower()
        if name.endswith(".csv"):
            if not _decodable(upload):
                raise ValidationError("This CSV file is not readable text. Save it again as UTF-8 and retry.")
            columns, rows = _csv_summary(upload)
            extension, content_type, note = "csv", CSV_CONTENT_TYPE, ""
        elif name.endswith(".xlsx"):
            _xlsx_is_workbook(upload)
            extension, content_type = "xlsx", XLSX_CONTENT_TYPE[0]
            # Column names and row count need a real workbook parser (pandas + openpyxl, in the
            # worker). Say so honestly; training fills in the full picture when it loads the file.
            columns, rows, note = [], 0, (
                "Excel file accepted. Its columns and rows are read when teaching starts "
                "(needs pandas and openpyxl in the training helper)."
            )
        else:
            raise ValidationError("Only CSV and Excel (.xlsx) tables are supported.")

        metadata: dict[str, Any] = {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "extension": extension,
            "content_type": content_type,
            "columns": columns,
            "row_count": rows,
        }
        if note:
            metadata["summary_note"] = note
        return metadata

    def validate_annotation(self, payload, project) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"label"}:
            raise ValidationError("Choose one target column: send the column name as the label.")
        label = payload.get("label")
        if not isinstance(label, str) or not label:
            raise ValidationError("Choose which column Athena should predict.")
        if label not in project.labels:
            raise ValidationError(
                f"“{label}” is not a column of this project's table. Choose one of its columns."
            )
        return {"label": label}

    def asset_view_metadata(self, asset) -> dict[str, Any]:
        return dict(asset.metadata or {})

    def max_uploads(self) -> int:
        return 1

    def max_batch_bytes(self) -> int:
        return MAX_TABLE_BYTES
