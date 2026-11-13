from __future__ import annotations

from pathlib import Path

import pytest

from athena_cli.knowledge import KnowledgeLibrary


def test_add_and_search_returns_source_citation(tmp_path: Path) -> None:
    document = tmp_path / "handbook.md"
    document.write_text(
        "# Leave policy\n\nEmployees receive 20 days of annual leave.\n",
        encoding="utf-8",
    )
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")

    added = library.add(document)
    results = library.search("annual leave")

    assert added == 1
    assert len(results) == 1
    assert results[0].text == "Employees receive 20 days of annual leave."
    assert results[0].source == document.resolve()
    assert results[0].start_line == 3
    assert results[0].end_line == 3


def test_readding_changed_file_replaces_old_search_content(tmp_path: Path) -> None:
    document = tmp_path / "policy.txt"
    document.write_text("The old holiday allowance is 10 days.\n", encoding="utf-8")
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")
    library.add(document)

    document.write_text("The new holiday allowance is 25 days.\n", encoding="utf-8")
    library.add(document)

    assert library.search("old") == []
    assert "25 days" in library.search("new holiday")[0].text
    assert len(library.list_sources()) == 1


def test_directory_import_skips_unsupported_and_empty_files(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "guide.md").write_text("Use the service desk for help.\n", encoding="utf-8")
    (folder / "empty.txt").write_text("  \n", encoding="utf-8")
    (folder / "image.png").write_bytes(b"not text")
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")

    assert library.add(folder) == 1
    assert len(library.list_sources()) == 1
    assert library.search("service desk")[0].source.name == "guide.md"


def test_search_is_empty_for_blank_query_and_nonmatching_terms(tmp_path: Path) -> None:
    document = tmp_path / "guide.txt"
    document.write_text("Support hours are Monday through Friday.\n", encoding="utf-8")
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")
    library.add(document)

    assert library.search("   ") == []
    assert library.search("weekend") == []


def test_remove_deletes_source_and_searchable_chunks(tmp_path: Path) -> None:
    document = tmp_path / "guide.txt"
    document.write_text("Contact support for account access.\n", encoding="utf-8")
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")
    library.add(document)

    assert library.remove(document) is True
    assert library.remove(document) is False
    assert library.list_sources() == []
    assert library.search("account access") == []


def test_invalid_document_path_has_actionable_error(tmp_path: Path) -> None:
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")
    unsupported = tmp_path / "handbook.docx"
    unsupported.write_bytes(b"not imported")

    with pytest.raises(ValueError, match="not a supported file or folder"):
        library.add(unsupported)


def test_updating_document_to_empty_removes_stale_passages(tmp_path: Path) -> None:
    document = tmp_path / "faq.txt"
    document.write_text("Reset your password from account settings.\n", encoding="utf-8")
    library = KnowledgeLibrary(tmp_path / "knowledge.sqlite3")
    library.add(document)

    document.write_text(" \n", encoding="utf-8")
    assert library.add(document) == 1

    assert library.list_sources() == []
    assert library.search("password") == []
