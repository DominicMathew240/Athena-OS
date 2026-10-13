# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse

from athena_cli.knowledge import KnowledgeLibrary


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "knowledge",
        help="Add and search local documents with source citations.",
        description=(
            "Build a local keyword-search library from Markdown, text, and FAQ files. "
            "Documents remain on this computer; this is not an AI-generated answer."
        ),
    )
    parser.add_argument(
        "--database",
        help="Choose where the local library is stored (default: ~/.athena/knowledge.sqlite3).",
    )
    actions = parser.add_subparsers(dest="knowledge_action", required=True)

    add = actions.add_parser("add", help="Add a text file or scan a folder for supported documents.")
    add.add_argument("paths", nargs="+", help="A .txt, .md, or .faq file, or a folder of such files.")

    actions.add_parser("list", help="List documents in the local library.")

    search = actions.add_parser("search", help="Find matching passages and their source locations.")
    search.add_argument("query", help="Words to find in your documents.")
    search.add_argument("--limit", type=int, default=5, help="Maximum passages to show (default: 5).")

    remove = actions.add_parser("remove", help="Remove a document and its indexed passages.")
    remove.add_argument("path", help="The original path used when the document was added.")


def run(args: argparse.Namespace) -> int:
    library = KnowledgeLibrary(args.database)

    try:
        if args.knowledge_action == "add":
            count = sum(library.add(path) for path in args.paths)
            if count:
                noun = "document" if count == 1 else "documents"
                print(f"Added {count} {noun} to your local library.")
            else:
                print("No new documents found. Files that haven't changed were left as-is.")
            print("Supported formats: .txt, .md, .faq. Your documents stay on this computer.")
            return 0

        if args.knowledge_action == "list":
            sources = library.list_sources()
            if not sources:
                print("No documents yet. Add one with: athena knowledge add <file-or-folder>")
                return 0
            print(f"{len(sources)} document(s) in your local library:")
            for source in sources:
                noun = "passage" if source.chunk_count == 1 else "passages"
                print(f"  {source.path} — {source.chunk_count} {noun}")
            return 0

        if args.knowledge_action == "search":
            if args.limit < 1:
                print("Choose a result limit of 1 or more.")
                return 1
            results = library.search(args.query, limit=args.limit)
            if not results:
                print("No matching passages. Try different words or add more documents.")
                return 0
            print("Keyword matches (not AI-generated answers):")
            for result in results:
                if result.start_line == result.end_line:
                    location = f"{result.source}:{result.start_line}"
                else:
                    location = f"{result.source}:{result.start_line}-{result.end_line}"
                print(f"\n[{location}]")
                print(result.text)
            return 0

        if args.knowledge_action == "remove":
            if library.remove(args.path):
                print(f"Removed {args.path} and its indexed passages from your local library.")
            else:
                print(f"That path is not in your local library: {args.path}")
                return 1
            return 0
    except ValueError as exc:
        print(f"Could not update the local library: {exc}")
        return 1

    return 1
