"""The Wave 2 documents slice (OS-DOC-001, table/CSV extraction only), end to end.

Covers the document modality plugin (CSV upload validation for a header-plus-rows table,
XLSX behind an honest openpyxl gate, the field-type system's validation messages and
normalization rules), the document-fields recipe and its by-kind dispatch (a text-classification
project must keep its own plugin), the generic snapshot flow on a real document project, the
table-evaluation module (matches, number tolerance, ambiguous dates as unclear, mismatches,
mismatch caps, extra rows, the checked reading side) and the honest no-training state of the
Teach API for a document project. There is deliberately no training test: this slice trains
nothing (see docs/plans/2026-11-20-os-doc-001-document-fields.md).

Tests that need openpyxl skip cleanly when it is not installed (the tabular tests' pattern).
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import zipfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .datasets import generic as generic_service
from .datasets import splits
from .modalities import get_annotation_plugin, get_plugin
from .modalities.document import (
    DocumentModalityPlugin,
    cell_problems,
    normalize_cell,
    parse_fields,
)
from .models import Asset, AssetAnnotation, DatasetEntry, DatasetVersion, Project
from .tasks import get_recipe
from .tests import SignedInMixin
from .tests_datasets import DatasetBase
from .training import document_eval

OPENPYXL_OK = importlib.util.find_spec("openpyxl") is not None
OPENPYXL_REASON = "openpyxl is needed for XLSX document tables"

#: The fields the fixture project checks: a text field, a number field, a date field and one
#: plain label (a field without a type is text).
FIELDS = ["invoice_number", "amount:number", "issued:date", "supplier"]

HEADER = ["invoice_number", "amount", "issued", "supplier"]


def documents_csv(rows: int = 20, *, corrupt: dict[tuple[int, str], str] | None = None) -> bytes:
    """A deterministic CSV of document records: one header row plus ``rows`` records."""
    return csv_bytes(document_rows(rows))


def document_rows(rows: int = 20) -> list[dict[str, str]]:
    """The same records the CSV writer writes, as dicts (tests build variants from these)."""
    return [
        {
            "invoice_number": f"INV-{i:04d}",
            "amount": f"{1000 + i * 7}.{i % 10}",
            "issued": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "supplier": ["Acme", "Globex", "Umbrella"][i % 3],
        }
        for i in range(rows)
    ]


def csv_bytes(records: list[dict[str, str]], header: list[str] | None = None) -> bytes:
    """A CSV from ``records`` (dicts).

    A custom ``header`` is written verbatim, and the records are then written in THAT order
    (a record whose keys are the custom names carries its own values; a record with the
    standard keys would write empty cells, so callers renaming columns must rename the
    records' keys too - the mismatched-header test does)."""
    columns = header if header is not None else HEADER
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for record in records:
        writer.writerow([str(record.get(name, "")) for name in columns])
    return out.getvalue().encode()


def minimal_xlsx(rows: int = 12) -> bytes:
    """A real (minimal) XLSX workbook of document records, standard library only.

    Same trick as the tabular tests' helper: enough structure for Excel and openpyxl to read
    (inline-string cells, one worksheet), so the document plugin's openpyxl path is exercised
    against a genuine workbook."""
    def cell(value: str, is_number: bool) -> str:
        if is_number:
            return f"<c><v>{value}</v></c>"
        return f'<c t="inlineStr"><is><t>{value}</t></is></c>'

    body = io.StringIO()
    body.write("<row>" + "".join(f'<c t="inlineStr"><is><t>{name}</t></is></c>' for name in HEADER) + "</row>")
    for i in range(rows):
        record = document_rows()[i] if i < len(document_rows(rows)) else document_rows(rows)[-1]
        body.write(
            f"<row>{cell(record['invoice_number'], False)}{cell(record['amount'], True)}"
            f"{cell(record['issued'], False)}{cell(record['supplier'], False)}</row>"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{body.getvalue()}</sheetData></worksheet>",
        )
    return buffer.getvalue()


class DocumentTestBase(DatasetBase):
    """A signed-in user, a media sandbox, an isolated data home and a document project."""

    def setUp(self) -> None:
        super().setUp()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        for override in (
            override_settings(
                DATA_HOME=Path(self.home.name),
                CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
            ),
        ):
            override.enable()
            self.addCleanup(override.disable)
        self.fields = list(FIELDS)
        self.document_project = Project.objects.create(
            name="Check invoice fields", modality="text", task="Check fields in document tables",
            recipe="document-fields", labels=list(self.fields),
        )

    # -- fixtures -----------------------------------------------------------------
    def upload_dataset(self, content: bytes | None = None, filename="documents.csv") -> dict:
        """Upload the document CSV through the API and return the created asset payload."""
        content = content if content is not None else csv_bytes(document_rows())
        upload = SimpleUploadedFile(filename, content, content_type="text/csv")
        response = self.client.post(f"/api/projects/{self.document_project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["assets"][0]

    def confirm_fields(self, asset_id=None, label=None) -> dict:
        """The recipe's whole-dataset confirmation annotation (one accepted annotation freezes the file)."""
        asset_id = asset_id or self.asset_id
        response = self.client.post(
            f"/api/projects/{self.document_project.id}/assets/{asset_id}/annotations",
            data=json.dumps({"label": label or self.fields[0]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["annotation"]

    def make_version(self, records: list[dict[str, str]] | None = None,
                     header: list[str] | None = None) -> DatasetVersion:
        """Upload the CSV, confirm its fields, then freeze a generic dataset version.

        Each call freezes a NEW version: the old asset is deleted first so the project always
        holds exactly the table being frozen (the readiness check refuses an unlabelled
        leftover file), the same swap the console makes between versions."""
        content = csv_bytes(records if records is not None else document_rows(), header=header)
        for asset in Asset.objects.filter(project=self.document_project):
            asset.delete()
        asset = self.upload_dataset(content)
        self.asset_id = asset["id"]
        self.confirm_fields(asset["id"])
        analysis = generic_service.analyse(self.document_project, splits.SplitConfig.default())
        self.assertTrue(analysis.ready, analysis.blockers)
        return generic_service.create_version(analysis, self.user)

    def api(self, suffix: str) -> str:
        return f"/api/projects/{self.document_project.id}{suffix}"

    def post(self, path: str, payload: dict | None = None):
        return self.client.post(path, data=json.dumps(payload or {}), content_type="application/json")


class RecipeAndPluginTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def test_the_document_recipe_is_available(self) -> None:
        recipe = get_recipe("document-fields")
        self.assertIsNotNone(recipe)
        self.assertTrue(recipe.available)
        self.assertEqual(recipe.modality, "text")
        self.assertEqual(recipe.annotation_kind, "document_fields")

    def test_the_plugin_is_registered_by_kind_and_the_text_builtin_stays(self) -> None:
        from .modalities.text import TextModalityPlugin

        # The "text" modality slot keeps the text-classification plugin: the document plugin
        # lives in the by-kind table, exactly like the OCR plugin on image.
        self.assertIs(type(get_plugin("text")), TextModalityPlugin)
        plugin = get_annotation_plugin("document_fields")
        self.assertIs(type(plugin), DocumentModalityPlugin)
        self.assertEqual(plugin.annotation_kind, "document_fields")
        body = self.client.get("/api/recipes").json()["recipes"]
        entry = next(item for item in body if item["key"] == "document-fields")
        self.assertTrue(entry["available"])
        self.assertEqual(entry["unavailable_reason"], "")


class FieldTypeTests(TestCase):
    """The field-type system: parsing, normalization and per-cell messages (pure functions)."""

    def test_field_labels_parse_to_name_type_pairs(self) -> None:
        self.assertEqual(
            parse_fields(["invoice_number", "amount:number", "issued:date", "note:text"]),
            [("invoice_number", "text"), ("amount", "number"), ("issued", "date"), ("note", "text")],
        )

    def test_an_unknown_type_is_text_not_an_error(self) -> None:
        self.assertEqual(parse_fields(["amount:currency"]), [("amount:currency", "text")])

    def test_duplicate_and_empty_labels_are_dropped(self) -> None:
        self.assertEqual(parse_fields(["a", "a", "", "b:number"]), [("a", "text"), ("b", "number")])

    def test_text_normalizes_by_stripping_only(self) -> None:
        self.assertEqual(normalize_cell("  Acme Ltd  ", "text"), "Acme Ltd")

    def test_numbers_normalize_after_thousand_separators(self) -> None:
        self.assertEqual(normalize_cell("1,234.5", "number"), "1234.5")
        self.assertEqual(normalize_cell("1234.500", "number"), "1234.500")
        self.assertIsNone(normalize_cell("twelve", "number"))
        self.assertIsNone(normalize_cell("1.2.3", "number"))

    def test_empty_cells_are_valid_and_empty(self) -> None:
        self.assertEqual(normalize_cell("", "number"), "")
        self.assertEqual(normalize_cell("   ", "date"), "")

    def test_dates_parse_from_the_fixed_format_set_to_iso(self) -> None:
        self.assertEqual(normalize_cell("2026-11-19", "date"), "2026-11-19")
        self.assertEqual(normalize_cell("19 November 2026", "date"), "2026-11-19")
        self.assertEqual(normalize_cell("19 Nov 2026", "date"), "2026-11-19")
        self.assertEqual(normalize_cell("November 19, 2026", "date"), "2026-11-19")

    def test_an_ambiguous_date_is_invalid_never_guessed(self) -> None:
        # 03/04/05 could be three different days; the fixed format set refuses it.
        self.assertIsNone(normalize_cell("03/04/05", "date"))
        self.assertIsNone(normalize_cell("19/11/2026", "date"))
        self.assertIsNone(normalize_cell("2026-13-01", "date"))

    def test_cell_problems_name_the_row_field_and_value(self) -> None:
        rows = [["INV-1", "10.5", "2026-01-01", "Acme"], ["INV-2", "lots", "03/04/05", "Globex"]]
        problems = cell_problems(HEADER, rows, parse_fields(FIELDS))
        self.assertEqual(len(problems), 2)
        self.assertIn("Row 2", problems[0]["message"])
        self.assertIn("amount", problems[0]["message"])
        self.assertIn("not a number", problems[0]["message"])
        self.assertIn("not a date", problems[1]["message"])
        self.assertEqual(problems[1]["row"], 2)
        self.assertEqual(problems[1]["field"], "issued")

    def test_a_field_without_a_column_is_one_problem(self) -> None:
        problems = cell_problems(["a", "b"], [["1", "2"]], [("missing", "text")])
        self.assertEqual(len(problems), 1)
        self.assertIn("no column", problems[0]["message"])

    def test_the_problem_list_is_capped(self) -> None:
        rows = [["x", "bad", "2026-01-01", "Acme"]] * 50
        problems = cell_problems(HEADER, rows, parse_fields(FIELDS))
        self.assertEqual(len(problems), 20)


class UploadValidationTests(DocumentTestBase):
    def post_upload(self, filename: str, content: bytes):
        upload = SimpleUploadedFile(filename, content, content_type="application/octet-stream")
        return self.client.post(f"/api/projects/{self.document_project.id}/assets", {"images": upload})

    def test_a_good_upload_stores_the_column_and_row_summary(self) -> None:
        content = csv_bytes(document_rows(15))
        asset = self.upload_dataset(content)
        self.assertEqual(asset["filename"], "documents.csv")
        self.assertEqual(asset["metadata"]["row_count"], 15)
        self.assertEqual(asset["metadata"]["columns"], HEADER)
        row = Asset.objects.get(id=asset["id"])
        self.assertEqual(row.content_type, "text/csv")
        self.assertEqual(row.metadata["columns"], HEADER)

    def test_an_empty_file_is_refused(self) -> None:
        response = self.post_upload("empty.csv", b"")
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["error"])

    def test_a_header_only_file_is_refused(self) -> None:
        response = self.post_upload("headersonly.csv", csv_bytes([]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one data row", response.json()["error"])

    def test_an_unnamed_or_duplicate_column_is_refused(self) -> None:
        rows = "invoice_number,amount,issued,\n" + "INV-1,10,2026-01-01,x\n"
        response = self.post_upload("blankname.csv", rows.encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs a name", response.json()["error"])
        rows = "amount,amount,issued,supplier\n1,2,2026-01-01,x\n"
        response = self.post_upload("dup.csv", rows.encode())
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be different", response.json()["error"])

    def test_a_ragged_body_is_refused(self) -> None:
        content = csv_bytes(document_rows(5)) + b"INV-9999,5\n"
        response = self.post_upload("ragged.csv", content)
        self.assertEqual(response.status_code, 400)
        self.assertIn("same number of columns", response.json()["error"])

    def test_a_wrong_extension_is_refused(self) -> None:
        response = self.post_upload("documents.json", b'[{"a": 1}]')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only CSV document tables", response.json()["error"])

    def test_binary_garbage_is_refused_plainly(self) -> None:
        response = self.post_upload("bad.csv", bytes(range(256)))
        self.assertEqual(response.status_code, 400)
        self.assertIn("not readable text", response.json()["error"])

    def test_only_one_document_file_per_upload_request(self) -> None:
        plugin = DocumentModalityPlugin()
        self.assertEqual(plugin.max_uploads(), 1)

    def test_an_xlsx_workbook_upload_follows_openpyxl(self) -> None:
        if not OPENPYXL_OK:
            self.skipTest(OPENPYXL_REASON)
        asset = self.upload_dataset(minimal_xlsx(rows=12), filename="documents.xlsx")
        self.assertEqual(asset["metadata"]["row_count"], 12)
        self.assertEqual(asset["metadata"]["columns"], HEADER)
        row = Asset.objects.get(id=asset["id"])
        # The extension went to the storage key (the view pops it); the columns and row count
        # are what the asset's metadata carries.
        self.assertTrue(row.storage_key.endswith(".xlsx"))
        self.assertEqual(row.metadata["row_count"], 12)

    def test_an_xlsx_without_openpyxl_in_this_process_is_refused_plainly(self) -> None:
        # The refusal path is exercised by faking openpyxl's absence, so it runs even where
        # openpyxl IS installed (the lean web process is the real audience of this message).
        from unittest.mock import patch

        with patch("projects.modalities.document.openpyxl_available", return_value=False):
            # The plugin imports openpyxl inside the xlsx path; make that import fail too.
            import builtins

            real_import = builtins.__import__

            def no_openpyxl(name, *args, **kwargs):
                if name == "openpyxl":
                    raise ImportError(name)
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=no_openpyxl):
                response = self.post_upload("documents.xlsx", minimal_xlsx(rows=5))
        self.assertEqual(response.status_code, 400)
        self.assertIn("openpyxl", response.json()["error"])
        self.assertIn("CSV", response.json()["error"])

    def test_a_fake_xlsx_is_refused(self) -> None:
        if not OPENPYXL_OK:
            self.skipTest(OPENPYXL_REASON)
        response = self.post_upload("fake.xlsx", b"this is not a zip")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a readable Excel", response.json()["error"])


class AnnotationTests(DocumentTestBase):
    def annotate(self, payload, asset_id=None):
        asset_id = asset_id or self.upload_dataset()["id"]
        return self.client.post(
            f"/api/projects/{self.document_project.id}/assets/{asset_id}/annotations",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_the_field_confirmation_is_stored_as_the_annotation(self) -> None:
        response = self.annotate({"label": "amount:number"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["annotation"]
        self.assertEqual(body["kind"], "document_fields")
        self.assertEqual(body["data"], {"label": "amount:number"})
        self.assertEqual(body["status"], "accepted")

    def test_a_label_outside_the_project_list_is_refused(self) -> None:
        response = self.annotate({"label": "no_such_field"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not one of this project's fields", response.json()["error"])

    def test_extra_fields_are_refused(self) -> None:
        response = self.annotate({"label": "supplier", "target": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirm one field name", response.json()["error"])


class SnapshotTests(DocumentTestBase):
    def test_one_file_plus_confirmation_freezes_one_entry(self) -> None:
        content = csv_bytes(document_rows(10))
        asset = self.upload_dataset(content)
        self.confirm_fields(asset["id"], label="supplier")
        response = self.post(self.api("/datasets"))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["dataset"]
        self.assertEqual(body["counts"]["entry_count"], 1)
        self.assertEqual(body["label_names"], self.fields)
        row = DatasetEntry.objects.get()
        self.assertEqual(row.label, "supplier")  # the confirmed field, via primary_label
        self.assertEqual(row.annotation, {"label": "supplier"})
        self.assertEqual(row.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(DatasetVersion.objects.get().image_count, 0)

    def test_a_file_without_a_confirmation_is_not_ready(self) -> None:
        self.upload_dataset()
        body = self.client.get(self.api("/datasets/readiness")).json()
        self.assertFalse(body["ready"])
        self.assertIn("no accepted labels", " ".join(body["blockers"]))

    def test_the_annotation_kind_must_be_the_document_one(self) -> None:
        asset = self.upload_dataset()
        AssetAnnotation.objects.create(asset=Asset.objects.get(id=asset["id"]), kind="text_dataset",
                                       data={"label": "supplier"})
        body = self.client.get(self.api("/datasets/readiness")).json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problems"][0]["code"], "annotation_kind_mismatch")

    def test_the_export_streams_the_document_file(self) -> None:
        content = csv_bytes(document_rows(10))
        asset = self.upload_dataset(content)
        self.confirm_fields(asset["id"], label="supplier")
        created = self.post(self.api("/datasets")).json()["dataset"]
        response = self.client.get(self.api(f"/datasets/{created['id']}/export"))
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        entry_names = [name for name in archive.namelist() if name.startswith("entries/")]
        self.assertEqual(len(entry_names), 1)
        self.assertEqual(archive.read(entry_names[0]), content)
        self.assertEqual(archive.read("classes.txt").decode().splitlines(), self.fields)


class ComparisonTests(DocumentTestBase):
    """The table-evaluation module: compare_versions and its reading side."""

    def test_identical_versions_score_every_field_at_100(self) -> None:
        first = self.make_version(document_rows(10))
        second = self.make_version(document_rows(10))
        block = document_eval.compare_versions(first, second, self.document_project)
        self.assertTrue(block["available"])
        self.assertEqual(block["rows_compared"], 10)
        self.assertEqual(block["totals"], {"match": 40, "mismatch": 0, "unclear": 0})
        self.assertEqual(block["accuracy"], 100)
        for row in block["per_field"]:
            self.assertEqual(row["accuracy"], 100, row)
        self.assertEqual(block["mismatches"], [])

    def test_a_changed_cell_is_a_mismatch_that_names_the_row(self) -> None:
        reference = self.make_version(document_rows(6))
        changed = list(document_rows(6))
        changed[3]["supplier"] = "Totally Different Ltd"
        candidate = self.make_version(changed)
        block = document_eval.compare_versions(reference, candidate, self.document_project)
        self.assertEqual(block["totals"], {"match": 23, "mismatch": 1, "unclear": 0})
        self.assertEqual(block["accuracy"], 95)
        self.assertEqual(len(block["mismatches"]), 1)
        self.assertEqual(block["mismatches"][0]["row"], 4)
        self.assertEqual(block["mismatches"][0]["field"], "supplier")
        self.assertEqual(block["mismatches"][0]["candidate"], "Totally Different Ltd")
        supplier = next(row for row in block["per_field"] if row["field"] == "supplier")
        self.assertEqual(supplier["accuracy"], 83)

    def test_numbers_match_within_tolerance(self) -> None:
        # 1234.5 vs 1234.56 is a gap of 1 part in ~22000: within the 1/10000 tolerance? No -
        # 0.06/1234.5 = 4.9e-5 > 1e-4? It is 4.9e-5 < 1e-4, so it MATCHES; a genuinely
        # different amount does not. The boundary is spelled out below.
        self.assertEqual(document_eval.compare_cells("1234.5", "1234.56", "number"), "match")
        self.assertEqual(document_eval.compare_cells("1234.5", "1234.7", "number"), "mismatch")
        self.assertEqual(document_eval.compare_cells("0.1", "0.1000001", "number"), "match")
        self.assertEqual(document_eval.compare_cells("1,234.5", "1234.5", "number"), "match")

    def test_a_non_number_on_either_side_is_unclear_not_a_mismatch(self) -> None:
        self.assertEqual(document_eval.compare_cells("lots", "10", "number"), "unclear")
        self.assertEqual(document_eval.compare_cells("10", "n/a", "number"), "unclear")
        self.assertEqual(document_eval.compare_cells("lots", "n/a", "number"), "unclear")

    def test_blank_number_cells_match_and_a_blank_against_a_number_mismatches(self) -> None:
        # Empty number cells are data, not failed parses: two blanks match, and a blank
        # against a real number is a mismatch. A non-number against a blank cannot be
        # judged, so that pair stays unclear.
        self.assertEqual(document_eval.compare_cells("", "", "number"), "match")
        self.assertEqual(document_eval.compare_cells("   ", "  ", "number"), "match")
        self.assertEqual(document_eval.compare_cells("", "10", "number"), "mismatch")
        self.assertEqual(document_eval.compare_cells("1,234.5", "", "number"), "mismatch")
        self.assertEqual(document_eval.compare_cells("lots", "", "number"), "unclear")
        self.assertEqual(document_eval.compare_cells("", "n/a", "number"), "unclear")

    def test_an_ambiguous_date_is_unclear_never_a_match(self) -> None:
        self.assertEqual(document_eval.compare_cells("03/04/05", "2026-11-19", "date"), "unclear")
        self.assertEqual(document_eval.compare_cells("2026-11-19", "19 Nov 2026", "date"), "match")
        self.assertEqual(document_eval.compare_cells("2026-11-19", "2026-11-20", "date"), "mismatch")

    def test_empty_cells_compare_as_data(self) -> None:
        self.assertEqual(document_eval.compare_cells("", "", "text"), "match")
        self.assertEqual(document_eval.compare_cells("", "Acme", "text"), "mismatch")

    def test_unclear_cells_do_not_change_a_field_s_accuracy(self) -> None:
        reference_rows = list(document_rows(4))
        candidate_rows = list(document_rows(4))
        # Two rows whose dates are ambiguous on BOTH sides: unclear, not mismatches.
        reference_rows[0]["issued"] = candidate_rows[0]["issued"] = "03/04/05"
        reference_rows[1]["issued"] = candidate_rows[1]["issued"] = "19/11/2026"
        # One genuinely wrong date: a mismatch.
        candidate_rows[2]["issued"] = "2020-01-01"
        reference = self.make_version(reference_rows)
        candidate = self.make_version(candidate_rows)
        block = document_eval.compare_versions(reference, candidate, self.document_project)
        issued = next(row for row in block["per_field"] if row["field"] == "issued")
        self.assertEqual(issued["unclear"], 2)
        self.assertEqual(issued["mismatch"], 1)
        self.assertEqual(issued["match"], 1)
        self.assertEqual(issued["accuracy"], 50)  # 1 match of 2 comparable cells

    def test_different_row_counts_compare_the_shared_rows_and_note_the_rest(self) -> None:
        reference = self.make_version(document_rows(8))
        candidate = self.make_version(document_rows(5))
        block = document_eval.compare_versions(reference, candidate, self.document_project)
        self.assertEqual(block["rows_compared"], 5)
        self.assertEqual(block["reference_rows"], 8)
        self.assertEqual(block["candidate_rows"], 5)
        self.assertIn(document_eval.EXTRA_ROWS_NOTE, block["notes"])

    def test_the_mismatch_list_is_capped_but_the_counts_are_exact(self) -> None:
        reference_rows = document_rows(60)
        candidate_rows = [
            {**record, "supplier": "Wrong"} for record in reference_rows
        ]
        reference = self.make_version(reference_rows)
        candidate = self.make_version(candidate_rows)
        block = document_eval.compare_versions(reference, candidate, self.document_project)
        self.assertEqual(block["mismatch_count"], 60)
        self.assertTrue(block["mismatches_truncated"])
        self.assertEqual(len(block["mismatches"]), document_eval.MAX_MISMATCHES)

    def test_different_headers_are_refused_plainly(self) -> None:
        reference = self.make_version(document_rows(4))
        renamed = [
            {"a": record["invoice_number"], "b": record["amount"],
             "c": record["issued"], "d": record["supplier"]}
            for record in document_rows(4)
        ]
        candidate = self.make_version(renamed, header=["a", "b", "c", "d"])
        with self.assertRaises(ValueError) as raised:
            document_eval.compare_versions(reference, candidate, self.document_project)
        self.assertIn("headers", str(raised.exception))

    def test_a_version_of_another_project_is_refused(self) -> None:
        first = self.make_version(document_rows(4))
        with self.assertRaises(ValueError):
            document_eval.compare_versions(first, first, self.document_project)

    def test_the_reading_side_rebuilds_the_sentence_and_refuses_garbage(self) -> None:
        first = self.make_version(document_rows(4))
        changed = [{**record, "amount": "9999"} for record in document_rows(4)]
        second = self.make_version(changed)
        block = document_eval.compare_versions(first, second, self.document_project)
        read = document_eval.plain_comparison(block)
        self.assertTrue(read["available"])
        self.assertIn("match", read["plain"])
        self.assertIn("not a promise", read["plain"])
        # A stored sentence is never trusted: the checked copy rebuilds it from the counts.
        tampered = {**block, "plain": "Everything is perfect, trust me."}
        self.assertNotEqual(document_eval.plain_comparison(tampered)["plain"], "Everything is perfect, trust me.")
        self.assertFalse(document_eval.plain_comparison({"schema": "wrong"})["available"])
        self.assertFalse(document_eval.plain_comparison(None)["available"])
        broken = {**block, "totals": {"match": -1, "mismatch": 0, "unclear": 0}}
        self.assertFalse(document_eval.plain_comparison(broken)["available"])

    def test_the_summary_says_so_when_nothing_could_be_compared(self) -> None:
        # A field that is not a column of the table is unclear for every row.
        project = Project.objects.create(
            name="No such column", modality="text", task="t", recipe="document-fields",
            labels=["ghost:number"],
        )
        from django.core.files.uploadedfile import SimpleUploadedFile as Upload

        versions = []
        for _ in range(2):
            for asset in Asset.objects.filter(project=project):
                asset.delete()
            content = csv_bytes(document_rows(3))
            upload = Upload("documents.csv", content, content_type="text/csv")
            response = self.client.post(f"/api/projects/{project.id}/assets", {"images": upload})
            self.assertEqual(response.status_code, 201, response.content)
            asset = response.json()["assets"][0]
            self.client.post(
                f"/api/projects/{project.id}/assets/{asset['id']}/annotations",
                data=json.dumps({"label": "ghost:number"}), content_type="application/json",
            )
            analysis = generic_service.analyse(project, splits.SplitConfig.default())
            self.assertTrue(analysis.ready, analysis.blockers)
            versions.append(generic_service.create_version(analysis, self.user))
        block = document_eval.compare_versions(versions[0], versions[1], project)
        self.assertEqual(block["totals"]["unclear"], 3)
        self.assertIsNone(block["accuracy"])
        self.assertIn("No cells could be compared", block["plain"])


class TeachApiTests(DocumentTestBase):
    """The honest no-training state: a document project's teach endpoints refuse plainly."""

    def test_training_and_model_routes_answer_404(self) -> None:
        self.assertEqual(self.client.get(self.api("/models")).status_code, 404)
        self.assertEqual(self.client.get(self.api("/training/runs")).status_code, 404)

    def test_training_readiness_says_there_is_nothing_to_teach(self) -> None:
        body = self.client.get(self.api("/training/readiness")).json()
        self.assertTrue(body["task_unavailable"])
        self.assertFalse(body["ready"])
        self.assertIn("no model to teach", " ".join(body["blockers"]))
        self.assertIn("fixed rules", " ".join(body["blockers"]))

    def test_a_run_cannot_be_started_on_a_document_project(self) -> None:
        self.make_version(document_rows(4))
        response = self.client.post(self.api("/training/runs"), data=json.dumps({}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 404)
