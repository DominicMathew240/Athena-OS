# Athena CLI

Command-line interface for the Athena open industrial AI standard.

```bash
athena validate-contracts
athena run-example
athena knowledge add ./company-handbook.md
athena knowledge search "annual leave"
```

`athena knowledge` imports `.txt`, `.md`, and `.faq` files to a local SQLite
library, returns keyword-matched passages with source locations, lists indexed
documents, and removes documents. It stores data on this computer (default:
`~/.athena/knowledge.sqlite3`) and does not upload files or generate AI
answers. Use `athena knowledge --help` for all commands and options.
The SQLite file is local plaintext, not encrypted; protect it and do not use
this early feature for sensitive documents.

The knowledge workflow is an early foundation, not the planned local company
assistant. PDF import, graphical review, semantic retrieval, local model
selection, and source-backed generated answers are not implemented yet.

See the [Athena Open Source README](../README.md) for the full project
overview.
