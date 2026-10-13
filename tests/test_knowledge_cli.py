from __future__ import annotations

from pathlib import Path

from athena_cli.main import main


def test_knowledge_cli_add_search_and_remove(tmp_path: Path, capsys) -> None:
    source = tmp_path / "policies"
    source.mkdir()
    handbook = source / "leave-policy.md"
    handbook.write_text(
        "# Leave\n\nStaff have 24 days of annual leave each year.\n",
        encoding="utf-8",
    )
    database = tmp_path / "athena.sqlite3"
    shared = ["knowledge", "--database", str(database)]

    assert main([*shared, "add", str(source)]) == 0
    added_output = capsys.readouterr().out
    assert "Added 1 document" in added_output

    assert main([*shared, "search", "annual leave"]) == 0
    search_output = capsys.readouterr().out
    assert "24 days" in search_output
    assert f"{handbook.resolve()}:3" in search_output
    assert "not AI-generated" in search_output

    assert main([*shared, "remove", str(handbook)]) == 0
    assert "Removed" in capsys.readouterr().out
    assert main([*shared, "search", "annual leave"]) == 0
    assert "No matching passages" in capsys.readouterr().out


def test_knowledge_cli_reports_empty_library_helpfully(tmp_path: Path, capsys) -> None:
    result = main(["knowledge", "--database", str(tmp_path / "empty.sqlite3"), "list"])

    assert result == 0
    assert "No documents yet" in capsys.readouterr().out
