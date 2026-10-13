# SPDX-License-Identifier: Apache-2.0
"""Private, local-only document library for the first assistant milestone.

This is a lexical retrieval foundation, not a chatbot or an LLM integration.
Source text stays in the SQLite database on this machine.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

_SUPPORTED_SUFFIXES = {".faq", ".md", ".txt"}
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class KnowledgeSource:
    path: Path
    chunk_count: int


@dataclass(frozen=True)
class SearchResult:
    source: Path
    start_line: int
    end_line: int
    text: str
    score: float


class KnowledgeLibrary:
    """A small SQLite-backed local library for text, Markdown, and FAQ files."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(
            database_path or Path.home() / ".athena" / "knowledge.sqlite3"
        ).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        try:
            os.chmod(self.database_path, 0o600)
        except OSError:
            pass  # Windows and managed filesystems may not support POSIX modes.
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_source_id_idx ON chunks(source_id);
                """
            )

    @staticmethod
    def _chunks(content: str) -> list[tuple[int, int, str]]:
        chunks: list[tuple[int, int, str]] = []
        lines = content.splitlines()
        block: list[tuple[int, str]] = []

        def flush() -> None:
            if not block:
                return
            visible = [
                (line_no, line.strip())
                for line_no, line in block
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if visible:
                chunks.append(
                    (
                        visible[0][0],
                        visible[-1][0],
                        "\n".join(text for _, text in visible),
                    )
                )
            block.clear()

        for line_no, line in enumerate(lines, start=1):
            if line.strip():
                block.append((line_no, line))
            else:
                flush()
        flush()
        return chunks

    def add(self, path: str | Path) -> int:
        """Import supported files, or recursively import supported files in a folder.

        Returns the number of new or changed source documents; unchanged files
        are skipped. Raises ValueError with actionable guidance for bad input.
        """
        target = Path(path).expanduser()
        if not target.exists():
            raise ValueError(f"Path does not exist: {target}")

        if target.is_file():
            paths = [target] if target.suffix.casefold() in _SUPPORTED_SUFFIXES else []
        elif target.is_dir():
            paths = sorted(
                candidate
                for candidate in target.rglob("*")
                if candidate.is_file() and candidate.suffix.casefold() in _SUPPORTED_SUFFIXES
            )
        else:
            paths = []
        if not paths:
            raise ValueError(
                f"{target} is not a supported file or folder. Choose .txt, .md, or .faq files."
            )

        changed = 0
        for source_path in paths:
            resolved = source_path.resolve()
            try:
                raw = resolved.read_bytes()
            except OSError as exc:
                raise ValueError(f"Could not read {resolved}: {exc}") from exc
            if len(raw) > _MAX_DOCUMENT_BYTES:
                raise ValueError(f"{resolved} is larger than the 10 MB per-file limit.")
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{resolved} is not valid UTF-8 text.") from exc
            content_hash = hashlib.sha256(raw).hexdigest()
            chunks = self._chunks(content)
            with closing(self._connect()) as connection:
                existing = connection.execute(
                    "SELECT id, content_hash FROM sources WHERE path = ?", (str(resolved),)
                ).fetchone()
                if existing and existing["content_hash"] == content_hash:
                    continue
                if not chunks:
                    if existing:
                        connection.execute("DELETE FROM sources WHERE id = ?", (existing["id"],))
                        connection.commit()
                        changed += 1
                    continue
                if existing:
                    connection.execute("DELETE FROM sources WHERE id = ?", (existing["id"],))
                cursor = connection.execute(
                    "INSERT INTO sources(path, content_hash) VALUES (?, ?)",
                    (str(resolved), content_hash),
                )
                connection.executemany(
                    """INSERT INTO chunks(source_id, ordinal, start_line, end_line, text)
                       VALUES (?, ?, ?, ?, ?)""",
                    [
                        (cursor.lastrowid, ordinal, start_line, end_line, text)
                        for ordinal, (start_line, end_line, text) in enumerate(chunks)
                    ],
                )
                connection.commit()
            changed += 1
        return changed

    def list_sources(self) -> list[KnowledgeSource]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT sources.path, COUNT(chunks.id) AS chunk_count
                   FROM sources LEFT JOIN chunks ON chunks.source_id = sources.id
                   GROUP BY sources.id ORDER BY sources.path COLLATE NOCASE"""
            ).fetchall()
        return [KnowledgeSource(Path(row["path"]), row["chunk_count"]) for row in rows]

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        terms = {term.casefold() for term in _TOKEN_PATTERN.findall(query)}
        if not terms or limit <= 0:
            return []
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT sources.path, chunks.start_line, chunks.end_line, chunks.text
                   FROM chunks JOIN sources ON sources.id = chunks.source_id"""
            ).fetchall()
        matches: list[SearchResult] = []
        for row in rows:
            words = [word.casefold() for word in _TOKEN_PATTERN.findall(row["text"])]
            matched = terms.intersection(words)
            if not matched:
                continue
            score = len(matched) / len(terms)
            matches.append(
                SearchResult(
                    Path(row["path"]), row["start_line"], row["end_line"], row["text"], score
                )
            )
        matches.sort(
            key=lambda result: (-result.score, str(result.source).casefold(), result.start_line)
        )
        return matches[:limit]

    def remove(self, path: str | Path) -> bool:
        resolved = Path(path).expanduser().resolve()
        with closing(self._connect()) as connection:
            cursor = connection.execute("DELETE FROM sources WHERE path = ?", (str(resolved),))
            connection.commit()
        return cursor.rowcount > 0


__all__ = ["KnowledgeLibrary", "KnowledgeSource", "SearchResult"]
