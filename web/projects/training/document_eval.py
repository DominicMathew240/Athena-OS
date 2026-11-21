"""The OS-DOC-001 table evaluation: compare two dataset versions of a document project, field
by field, cell by cell.

This is the "table evaluation" half of the documents slice, and it is deliberately NOT a model
score. There is no training and no held-out split in this slice (extraction is rule-based
validation and normalization - see :mod:`projects.modalities.document`); the
document-extraction equivalent of a held-out score is comparing two VERSIONS of the same
document project: a REFERENCE (the version whose values are treated as correct, typically the
accepted one) and a CANDIDATE (the version whose extracted values are being checked, typically
a re-upload or the next revision). Every cell that both versions carry is compared under the
project's field types; per-field accuracy is the share of the reference's cells the candidate
reproduces.

The matching rules, stated once (they are the whole semantics of the score):

* **Row pairing is by position.** Two versions of the same document table are the same records
  in the same order; a shifted row would silently pair the wrong cells, so when the row counts
  differ only the first ``min(reference, candidate)`` rows are compared and the extra rows are
  reported as a difference note, never scored as wrong cells.
* **A cell matches when its normalized value is equal.** TEXT cells compare after stripping;
  NUMBER cells compare with :data:`NUMBER_TOLERANCE` (values within one part in ten thousand
  of each other are equal, so ``0.1`` and ``0.1000001`` do not disagree); DATE cells compare
  as ISO dates after :func:`normalize_cell` (both sides must parse under the fixed format set).
* **An ambiguous date is UNCLEAR, not a match.** A date that fits none of the fixed formats on
  either side (``"03/04/05"``) is never guessed; the cell is counted ``unclear`` and excluded
  from both matches and mismatches. The same happens to a NUMBER cell that is not a plain
  number on either side. Unclear cells never help and never hurt a field's accuracy - but they
  are counted, because a candidate full of unclear cells is not extracting anything.
* **The mismatch list is capped** (:data:`MAX_MISMATCHES`); the counts are always exact.

Pure Python only (stdlib csv, decimal, datetime): the web process may call
:func:`plain_comparison` for a stored block, and it must never need pandas or openpyxl (a
version's file is read as CSV; an .xlsx-sourced version compares as unclear rows with a plain
reason, because its bytes are not CSV - see :func:`_read_version_table`).
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from ..modalities.document import normalize_cell, parse_fields
from ..models import DatasetVersion, Project

SCHEMA = "athena.document-comparison/1"
#: Two numbers closer than one part in ten thousand are the same number (float noise, not a
#: different value). Anything looser would call genuinely different amounts equal.
NUMBER_TOLERANCE = Fraction(1, 10000)
#: How many mismatched cells the comparison lists; the counts are always exact.
MAX_MISMATCHES = 50

MATCH = "match"
MISMATCH = "mismatch"
UNCLEAR = "unclear"

EXTRA_ROWS_NOTE = (
    "The two versions have different numbers of rows. Only the rows both carry were compared; "
    "the extra rows are a difference to resolve by hand, not a score."
)


def _plain_pct(part: int, total: int) -> int | None:
    """The share of matching cells, rounded DOWN to a whole percent (never overstates)."""
    if total <= 0:
        return None
    return (100 * part) // total


def number_equal(reference: str, candidate: str, tolerance: Fraction = NUMBER_TOLERANCE) -> bool | None:
    """Whether two number cells are equal after normalization, with ``tolerance``.

    ``True`` / ``False`` when both cells are valid numbers (compared as decimals: equal, or
    within ``tolerance`` of each other relative to the reference); ``None`` when either cell
    is not a plain number - the pair is UNCLEAR, never a mismatch."""
    try:
        left = Decimal(reference)
        right = Decimal(candidate)
    except InvalidOperation:
        return None
    if left == right:
        return True
    scale = abs(left) if left != 0 else Decimal(1)
    gap = abs(left - right)
    if scale == 0:
        return right == 0 or gap <= Decimal(tolerance.numerator) / Decimal(tolerance.denominator)
    return bool(gap / scale <= Decimal(tolerance.numerator) / Decimal(tolerance.denominator))


def compare_cells(reference: str, candidate: str, field_type: str) -> str:
    """One cell pair under one field type: ``MATCH``, ``MISMATCH`` or ``UNCLEAR``.

    UNCLEAR means the pair cannot be judged honestly: a number or date cell that does not
    parse on either side (an ambiguous date is never guessed), or a field that is not a column
    of one of the tables. Empty cells compare as empty: two blanks match, a blank against a
    value is a mismatch (a missing value is data)."""
    if field_type == "number":
        left = normalize_cell(reference, "number")
        right = normalize_cell(candidate, "number")
        # A non-number is unclear. A blank is valid data (""), so it is judged below
        # rather than here: number_equal cannot parse "" and returns None.
        if left is None or right is None:
            return UNCLEAR
        if left == "" or right == "":
            return MATCH if left == right else MISMATCH
        equal = number_equal(left, right)
        # None means the pair cannot be judged; it is never a mismatch.
        if equal is None:
            return UNCLEAR
        return MATCH if equal else MISMATCH
    left = normalize_cell(reference, field_type)
    right = normalize_cell(candidate, field_type)
    if left is None or right is None:
        return UNCLEAR
    return MATCH if left == right else MISMATCH


def _read_version_table(version: DatasetVersion) -> tuple[list[str], list[list[str]]]:
    """(header, data rows) of a frozen document version's file, from storage.

    Only CSV bytes can be read here (the standard library's csv module); an .xlsx-sourced
    version raises ``ValueError`` with a plain reason rather than importing openpyxl, because
    this module may run in the lean web process. The caller turns that into the comparison's
    ``available: False`` answer."""
    entries = list(version.entries.all())
    if len(entries) != 1:
        raise ValueError(
            "A document comparison needs a dataset version that freezes exactly one document table."
        )
    row = entries[0]
    if (row.filename or "").lower().endswith(".xlsx"):
        raise ValueError(
            "This version's file is an Excel (.xlsx) table. The comparison reads CSV; save the "
            "table as a CSV, upload it and freeze a new version to compare."
        )
    from django.core.files.storage import default_storage

    if not default_storage.exists(row.storage_key):
        raise ValueError("This version's document table file is missing from storage.")
    with default_storage.open(row.storage_key, "rb") as handle:
        wrapper = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
        try:
            reader = csv.reader(wrapper)
            header = next(reader, None)
            if header is None:
                raise ValueError("This version's document table file is empty.")
            rows = [line for line in reader if any(cell.strip() for cell in line)]
        finally:
            try:
                wrapper.detach()
            except (ValueError, AttributeError, UnboundLocalError):
                pass
    return [name.strip() for name in header], rows


def compare_versions(reference: DatasetVersion, candidate: DatasetVersion, project: Project) -> dict[str, Any]:
    """The full table comparison of two document versions of one project.

    Returns the block :func:`plain_comparison` reads: per-field counts (match / mismatch /
    unclear / total), per-field accuracy (matching cells over comparable cells, i.e. matches
    plus mismatches - unclear cells are excluded from the denominator), a capped mismatch list
    with row numbers, and the plain summary sentence. Raises ``ValueError`` with a plain
    message when the two versions are not comparable (different projects, non-CSV files, a
    missing file, a version that is not one document table)."""
    if reference.project_id != project.id or candidate.project_id != project.id:
        raise ValueError("Both dataset versions must belong to the same document project.")
    if reference.id == candidate.id:
        raise ValueError("Choose two different dataset versions to compare.")
    ref_header, ref_rows = _read_version_table(reference)
    cand_header, cand_rows = _read_version_table(candidate)
    if ref_header != cand_header:
        raise ValueError(
            "The two versions have different column headers, so their cells cannot be paired. "
            "Compare versions of the same document shape."
        )

    fields = parse_fields(project.labels)
    columns = {name: index for index, name in enumerate(ref_header)}
    notes: list[str] = []
    if len(ref_rows) != len(cand_rows):
        notes.append(EXTRA_ROWS_NOTE)
    compared = min(len(ref_rows), len(cand_rows))

    per_field: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    totals = {"match": 0, "mismatch": 0, "unclear": 0}
    for name, field_type in fields:
        index = columns.get(name)
        counts = {"match": 0, "mismatch": 0, "unclear": 0}
        if index is None:
            # The field is not a column of this table: nothing can be compared, and that is
            # UNCLEAR (an extraction that cannot even find the field), never zero-accuracy.
            counts["unclear"] = compared
            per_field.append({
                "field": name, "type": field_type, **counts,
                "accuracy": None, "note": "This field has no column in the table.",
            })
            totals["unclear"] += compared
            continue
        for row_number in range(compared):
            verdict = compare_cells(ref_rows[row_number][index], cand_rows[row_number][index], field_type)
            counts[verdict] += 1
            totals[verdict] += 1
            if verdict == MISMATCH and len(mismatches) < MAX_MISMATCHES:
                mismatches.append({
                    "row": row_number + 1, "field": name, "type": field_type,
                    "reference": ref_rows[row_number][index],
                    "candidate": cand_rows[row_number][index],
                })
        comparable = counts[MATCH] + counts[MISMATCH]
        per_field.append({
            "field": name, "type": field_type, **counts,
            "accuracy": _plain_pct(counts[MATCH], comparable) if comparable else None,
        })

    comparable_total = totals[MATCH] + totals[MISMATCH]
    block: dict[str, Any] = {
        "schema": SCHEMA,
        "available": True,
        "reference_version": reference.version,
        "candidate_version": candidate.version,
        "rows_compared": compared,
        "reference_rows": len(ref_rows),
        "candidate_rows": len(cand_rows),
        "notes": notes,
        "per_field": per_field,
        "totals": totals,
        "accuracy": _plain_pct(totals[MATCH], comparable_total) if comparable_total else None,
        "mismatches": mismatches,
        "mismatch_count": totals[MISMATCH],
        "mismatches_truncated": totals[MISMATCH] > len(mismatches),
    }
    block["plain"] = summary_sentence(block)
    return block


def summary_sentence(block: dict[str, Any]) -> str:
    """The plain summary of a comparison block (all numbers from the block, none re-derived)."""
    accuracy = block.get("accuracy")
    cells = block["totals"]["match"] + block["totals"]["mismatch"]
    if cells <= 0:
        return "No cells could be compared: every field was unclear or the tables carry no rows."
    lead = (
        f"{block['totals']['match']} of {cells} compared cells match"
        + (f" ({accuracy}%)" if accuracy is not None else "")
        + f"; {block['totals']['mismatch']} do not."
    )
    if block["totals"]["unclear"]:
        lead += f" {block['totals']['unclear']} cell(s) could not be judged (unclear values, not guessed)."
    if block["mismatches_truncated"]:
        lead += f" Only the first {len(block['mismatches'])} mismatches are listed."
    worst = [
        row for row in block["per_field"]
        if row["accuracy"] is not None and row["accuracy"] < 100
    ]
    if worst:
        named = ", ".join(f"{row['field']} ({row['accuracy']}%)" for row in worst[:5])
        lead += f" Fields below 100%: {named}."
    lead += (
        " This compares two saved versions of the table; it is not a promise about future documents."
    )
    return lead


# -- reading side (pure; never trusts a stored sentence) -------------------------------------------

def plain_comparison(block: Any) -> dict[str, Any]:
    """The checked form of a stored comparison block for display: ``{available: True, ...}``
    or ``{available: False, reason}``. Never raises."""
    if not isinstance(block, dict) or block.get("schema") != SCHEMA:
        return {"available": False, "reason": "No readable document comparison was saved here."}
    if block.get("available") is not True:
        reason = block.get("reason")
        return {
            "available": False,
            "reason": reason if isinstance(reason, str) and reason else "No document comparison is available.",
        }
    try:
        return {"available": True, **_validate(block)}
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"available": False, "reason": "The saved document comparison could not be read, so it is not shown."}


def _validate(block: dict[str, Any]) -> dict[str, Any]:
    """A checked copy of the block with its sentence derived again from its counts."""
    totals = block["totals"]
    if not isinstance(totals, dict) or any(
        not isinstance(totals.get(key), int) or isinstance(totals.get(key), bool) or totals[key] < 0
        for key in (MATCH, MISMATCH, UNCLEAR)
    ):
        raise ValueError("totals")
    rows = [block["rows_compared"], block["reference_rows"], block["candidate_rows"]]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in rows):
        raise ValueError("rows")
    if block["reference_rows"] != block["candidate_rows"] and EXTRA_ROWS_NOTE not in (block.get("notes") or []):
        raise ValueError("notes")
    per_field = block["per_field"]
    if not isinstance(per_field, list):
        raise ValueError("per_field")
    seen: set[str] = set()
    checked_fields: list[dict[str, Any]] = []
    for row in per_field:
        if not isinstance(row, dict) or not isinstance(row.get("field"), str) or row["field"] in seen:
            raise ValueError("field row")
        seen.add(row["field"])
        counts = {key: row.get(key) for key in (MATCH, MISMATCH, UNCLEAR)}
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
            raise ValueError("field counts")
        if counts[MATCH] + counts[MISMATCH] + counts[UNCLEAR] > max(rows[1], rows[2]):
            raise ValueError("field counts do not add up")
        comparable = counts[MATCH] + counts[MISMATCH]
        checked = dict(row)
        checked["accuracy"] = _plain_pct(counts[MATCH], comparable) if comparable else None
        checked_fields.append(checked)
    mismatch_rows = block["mismatches"]
    if not isinstance(mismatch_rows, list) or len(mismatch_rows) > MAX_MISMATCHES:
        raise ValueError("mismatches")
    for row in mismatch_rows:
        if not isinstance(row, dict) or not isinstance(row.get("field"), str):
            raise ValueError("mismatch row")
    out = {
        "reference_version": block["reference_version"],
        "candidate_version": block["candidate_version"],
        "rows_compared": block["rows_compared"],
        "reference_rows": block["reference_rows"],
        "candidate_rows": block["candidate_rows"],
        "notes": list(block.get("notes") or []),
        "per_field": checked_fields,
        "totals": {key: totals[key] for key in (MATCH, MISMATCH, UNCLEAR)},
        "accuracy": _plain_pct(totals[MATCH], totals[MATCH] + totals[MISMATCH]) if totals[MATCH] + totals[MISMATCH] else None,
        "mismatches": mismatch_rows,
        "mismatch_count": totals[MISMATCH],
        "mismatches_truncated": totals[MISMATCH] > len(mismatch_rows),
    }
    out["plain"] = summary_sentence(out)
    return out
