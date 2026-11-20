"""The document modality plugin: one CSV (or .xlsx, when openpyxl is installed) whose rows are
the project's structured records, plus the FIELD-TYPE system behind document-field validation.

Wave 2's documents slice (OS-DOC-001, extraction slice): a document project's dataset is ONE
table file - CSV strictly, .xlsx only when the openpyxl package is importable in this process
(the check is cheap and honest; a web process without openpyxl refuses the file in plain words
rather than storing something it could not read). The first row is the header; the rows below
it are the records whose cells the FIELDS describe.

What this slice is NOT, stated plainly because the roadmap name is broader: no PDF, no OCR, no
layout analysis (those need detection over page images that does not exist here yet - future
work), and no model training (extraction is rule-based validation and normalization, not
prediction; the tabular-prediction recipe owns table prediction).

The mapping onto the Wave 0 snapshot model is the text slice's pattern (one entry per file):

* Uploading the file creates one ``Asset`` (``modality = "text"``) whose ``metadata`` carries
  the column names, the row count and the file size - parsed with the standard library only
  (openpyxl, when it exists, is imported lazily inside the .xlsx path; the web process never
  imports pandas).
* The project's LABEL LIST is the project's FIELD DEFINITIONS: one label per field, spelled
  ``"<name>:<type>"`` (e.g. ``"invoice_number:text"``) with the type one of
  :data:`FIELD_TYPES` (``text``, ``number``, ``date``). See :func:`parse_fields`.
* The *annotation* is the recipe's whole-dataset confirmation: ``annotation_kind`` is
  ``"document_fields"`` and ``validate_annotation`` accepts
  ``{"label": "<one of the project's field labels>"}``. The label names the field whose values
  the confirmation covers; the generic snapshot's ``primary_label`` convention reads it, and
  one asset + one accepted annotation freezes ONE ``DatasetEntry`` - the whole file, unchanged
  pipeline. Validation of every CELL against its field's type happens at UPLOAD time (and in
  :func:`document_eval.cell_problems`, for comparing two versions).

Only stdlib ``csv`` (and, lazily, openpyxl) are imported here. There is deliberately no
training backend in this slice: a document project's Teach panel reports the task-unavailable
refusal (see ``training_views._task_unavailable_readiness``), which is the honest state - the
slice's "evaluation" is the table comparison in :mod:`projects.training.document_eval`, not a
trained model.
"""

from __future__ import annotations

import csv
import importlib.util
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
#: How many header columns a document table may carry; more is a database, not a document.
MAX_COLUMNS = 200
MIN_DATA_ROWS = 1
MAX_ROWS_COUNTED = 2_000_000  # a defensive stop; the real limit is the byte cap
MAX_SNIFF_BYTES = 64 * 1024

CSV_CONTENT_TYPE = "text/csv"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The field types a project's fields may choose from, in display order.
FIELD_TYPES = ("text", "number", "date")

#: The date formats a date field accepts, tried in this fixed order. They are deliberately
#: unambiguous (four-digit years, numeric months and days) or carry a month NAME, which no
#: other component can be confused with. A value that fits none of them is invalid, not
#: "guessed": "03/04/05" tells nobody whether that is the 3rd of April.
DATE_FORMATS = (
    "%Y-%m-%d",      # 2026-11-19 (ISO)
    "%d %B %Y",      # 19 November 2026
    "%d %b %Y",      # 19 Nov 2026
    "%B %d, %Y",     # November 19, 2026
    "%b %d, %Y",     # Nov 19, 2026
    "%B %d %Y",      # November 19 2026
    "%b %d %Y",      # Nov 19 2026
)

#: How many cell problems a metadata summary (and a comparison mismatch list) lists; the rest
#: are counted only, never itemized.
MAX_CELL_PROBLEMS = 20


def openpyxl_available() -> bool:
    """Whether .xlsx document tables can be read in this process (openpyxl, checked cheaply)."""
    return importlib.util.find_spec("openpyxl") is not None


def safe_filename(upload_name: str) -> str:
    basename = (upload_name or "documents.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "documents.csv"


# -- the field-type system (pure, shared with the comparison module) --------------------------------

def parse_fields(labels: list[str]) -> list[tuple[str, str]]:
    """The project's field definitions from its label list: ``[(name, type), ...]``.

    Each label is ``"<name>:<type>"`` with the type one of :data:`FIELD_TYPES`; a label
    without a type separator is a TEXT field (the common case, and the honest default when the
    operator just lists the fields they care about). An unknown type is kept as ``"text"``
    rather than refused: the label list is free-text editing surface, and a field that stops
    parsing is a text field with a longer name, not a broken project.
    """
    fields: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label in labels or []:
        text = str(label).strip()
        if not text:
            continue
        name, _, declared = text.rpartition(":")
        if name and declared.strip().lower() in FIELD_TYPES:
            kind = declared.strip().lower()
        else:
            name, kind = text, "text"
        if name not in seen:
            seen.add(name)
            fields.append((name, kind))
    return fields


def normalize_cell(value: str, field_type: str) -> str | None:
    """The cell's normalized text for its field type, or ``None`` when it is not valid.

    A TEXT cell is stripped and returned as is. A NUMBER cell must be a plain number; commas
    and spaces as thousand separators are accepted and dropped, and the result is normalized
    to the number's plain ``Decimal`` text (``1,234.5`` and ``1234.500`` both become
    ``"1234.5"``; see :func:`document_eval.number_equal` for the tolerance rule used in
    comparisons). A DATE cell must fit one of :data:`DATE_FORMATS` and is normalized to ISO
    (``2026-11-19``). An empty (or whitespace-only) cell is valid and normalizes to ``""``:
    a missing value is data, not an error; the comparison counts it as a cell like any other."""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if field_type == "number":
        cleaned = text.replace(",", "").replace(" ", "").replace("\u00a0", "")
        try:
            number = Decimal(cleaned)
        except InvalidOperation:
            return None
        return str(number.normalize()) if number == number.to_integral_value() else str(number)
    if field_type == "date":
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None
    return text


def cell_problems(header: list[str], rows: list[list[str]], fields: list[tuple[str, str]],
                  limit: int = MAX_CELL_PROBLEMS) -> list[dict[str, Any]]:
    """Every cell whose value does not validate against its field's type, capped at ``limit``.

    Returns ``[{"row": <data-row number, 1 = first row under the header>, "field": <name>,
    "type": <field type>, "value": <the cell as uploaded>, "message": <plain sentence>}, ...]``
    in row order. A field name that is not a column of the table is itself one problem (the
    field can never be extracted); only the first such problem per field is listed. The list is
    capped so one bad file cannot flood the upload response; callers that need every problem
    pass a bigger ``limit`` (the comparison module does not - its own cap applies)."""
    problems: list[dict[str, Any]] = []
    columns = {name: index for index, name in enumerate(header)}
    reported_missing: set[str] = set()
    for name, field_type in fields:
        index = columns.get(name)
        if index is None:
            if name not in reported_missing:
                reported_missing.add(name)
                problems.append({
                    "row": 0, "field": name, "type": field_type, "value": "",
                    "message": f"The field “{name}” has no column in this file.",
                })
            continue
        for number, row in enumerate(rows, start=1):
            if index >= len(row):
                continue  # a ragged row is refused by the upload check before this runs
            value = row[index]
            if normalize_cell(value, field_type) is None:
                what = {"number": "a number", "date": "a date"}.get(field_type, "text")
                problems.append({
                    "row": number, "field": name, "type": field_type, "value": value,
                    "message": f"Row {number}: the cell under “{name}” is not {what} ({value!r}).",
                })
                if len(problems) >= limit:
                    return problems
    return problems


# -- file reading (CSV with the standard library; XLSX with openpyxl, lazily) -----------------------

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


def _csv_rows(upload) -> tuple[list[str], list[list[str]]]:
    """(header, data rows) from a CSV, using only the standard library.

    Raises ``ValidationError`` with a plain message when the file is not a readable
    single-comma CSV table: empty, a ragged body, or a header with too many columns. Blank
    lines are not data rows. Only the first :data:`MAX_ROWS_COUNTED` rows are read; the byte
    cap is the real limit."""
    upload.seek(0)
    wrapper = io.TextIOWrapper(upload, encoding="utf-8-sig", newline="")
    rows: list[list[str]] = []
    try:
        reader = csv.reader(wrapper)
        header = next(reader, None)
        if header is None:
            raise ValidationError("The file is empty. A document table needs a header row and at least one data row.")
        names = [name.strip() for name in header]
        if len(names) > MAX_COLUMNS:
            raise ValidationError(
                f"A document table can have at most {MAX_COLUMNS} columns. This file has {len(names)}."
            )
        if any(not name for name in names):
            raise ValidationError("Every column in the header row needs a name. Some are empty.")
        if len(set(names)) != len(names):
            raise ValidationError("Every column name must be different, so each field has its own column.")
        width = len(names)
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # a blank line is not a data row
            if len(row) != width:
                raise ValidationError(
                    "Every data row must have the same number of columns as the header. "
                    "Some rows in this file have a different number of columns."
                )
            rows.append(row)
            if len(rows) >= MAX_ROWS_COUNTED:
                break
    except (csv.Error, UnicodeDecodeError) as error:
        raise ValidationError("The file is not a readable document table (CSV).") from error
    finally:
        # TextIOWrapper closes the file it wraps when it is garbage collected, and the view
        # reads the upload again after validation - detach it so the upload stays open.
        try:
            wrapper.detach()
        except (ValueError, AttributeError, UnboundLocalError):
            pass
        upload.seek(0)
    if not rows:
        raise ValidationError("A document table needs at least one data row below the header.")
    return names, rows


def _xlsx_rows(upload) -> tuple[list[str], list[list[str]]]:
    """(header, data rows) from an .xlsx workbook, via openpyxl (imported lazily).

    Same rules as the CSV path, in the workbook's own words where they differ: only the FIRST
    worksheet is read (a document is one table, not a workbook of them). Raises
    ``ValidationError`` when openpyxl is missing or the workbook is not readable."""
    try:
        import openpyxl
    except ImportError:
        raise ValidationError(
            "Excel (.xlsx) document tables need the openpyxl package, which is not installed here. "
            "Save the file as a CSV and upload that instead."
        ) from None
    upload.seek(0)
    try:
        workbook = openpyxl.load_workbook(upload, read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            names = [str(cell.value).strip() if cell.value is not None else "" for cell in next(sheet.iter_rows())]
            if not names or all(name == "" for name in names):
                raise ValidationError("A document table needs a header row with column names.")
            if len(names) > MAX_COLUMNS:
                raise ValidationError(
                    f"A document table can have at most {MAX_COLUMNS} columns. This file has {len(names)}."
                )
            if any(not name for name in names):
                raise ValidationError("Every column in the header row needs a name. Some are empty.")
            if len(set(names)) != len(names):
                raise ValidationError("Every column name must be different, so each field has its own column.")
            width = len(names)
            rows: list[list[str]] = []
            for values in sheet.iter_rows(min_row=2, values_only=True):
                row = [("" if value is None else str(value).strip()) for value in values]
                if not any(cell for cell in row):
                    continue  # an empty sheet row is not a data row
                if len(row) != width:
                    raise ValidationError(
                        "Every data row must have the same number of columns as the header. "
                        "Some rows in this file have a different number of columns."
                    )
                rows.append(row)
                if len(rows) >= MAX_ROWS_COUNTED:
                    break
        finally:
            workbook.close()
    except ValidationError:
        raise
    except Exception as error:  # noqa: BLE001 - openpyxl raises a zoo of parser errors
        raise ValidationError("This file is not a readable Excel (.xlsx) document table.") from error
    finally:
        upload.seek(0)
    if not rows:
        raise ValidationError("A document table needs at least one data row below the header.")
    return names, rows


class DocumentModalityPlugin(ModalityPlugin):
    modality = "text"
    asset_noun = "document table"
    annotation_kind = "document_fields"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Document table files")
        if upload.size > MAX_DOCUMENT_BYTES:
            raise ValidationError("Each document table must be 50 MB or smaller.")

        sha256, size_bytes = digest_upload(upload, max_bytes=MAX_DOCUMENT_BYTES,
                                           limit_message="Each document table must be 50 MB or smaller.")

        name = (upload.name or "").lower()
        if name.endswith(".csv"):
            if not _decodable(upload):
                raise ValidationError("This CSV file is not readable text. Save it again as UTF-8 and retry.")
            columns, rows = _csv_rows(upload)
            extension, content_type = "csv", CSV_CONTENT_TYPE
        elif name.endswith(".xlsx"):
            # One format (CSV) is always read the same way; .xlsx is optional on purpose and
            # the refusal is plain when openpyxl is missing, so the operator can save as CSV.
            columns, rows = _xlsx_rows(upload)
            extension, content_type = "xlsx", XLSX_CONTENT_TYPE
        else:
            raise ValidationError(
                "Only CSV document tables are always supported (Excel .xlsx when this console can read it). "
                "Save your document data as a CSV with a header row and try again."
            )

        return {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "extension": extension,
            "content_type": content_type,
            "columns": columns,
            "row_count": len(rows),
        }

    def validate_annotation(self, payload, project) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"label"}:
            raise ValidationError("Confirm one field name: send it as the annotation's label.")
        label = payload.get("label")
        if not isinstance(label, str) or not label:
            raise ValidationError("Choose a field from this project's field list.")
        if label not in project.labels:
            raise ValidationError(
                f"“{label}” is not one of this project's fields. Choose one of its fields."
            )
        return {"label": label}

    def asset_view_metadata(self, asset) -> dict[str, Any]:
        return dict(asset.metadata or {})

    def max_uploads(self) -> int:
        return 1

    def max_batch_bytes(self) -> int:
        return MAX_DOCUMENT_BYTES


# Importing this module is what turns the Wave 2 documents slice on: the plugin gets its
# by-kind registry entry (the "text" *modality* stays the builtin text plugin - a
# text-classification project uploads and annotates exactly as before; a document-fields
# project's recipe names this kind, so ``annotations.annotation_plugin`` finds it here).
# Deliberately last, after the class.
from .registry import register_annotation_plugin as _register_annotation_plugin  # noqa: E402

_register_annotation_plugin(DocumentModalityPlugin)


#: Kept for callers that want the fixed date format set without importing it by path.
__all__ = [
    "DATE_FORMATS",
    "FIELD_TYPES",
    "DocumentModalityPlugin",
    "cell_problems",
    "normalize_cell",
    "openpyxl_available",
    "parse_fields",
]
