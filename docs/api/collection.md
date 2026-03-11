# Collection

The `Collection` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class.

## Quick Start

```python
from pathlib import Path
from markdown_vault_mcp import Collection

# Basic read-only collection
collection = Collection(source_dir=Path("/path/to/vault"))
stats = collection.build_index()
print(f"Indexed {stats.documents_indexed} documents")

# Search
results = collection.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document
note = collection.read("Journal/note.md")
print(note.content)
```

## API Reference

::: markdown_vault_mcp.collection.Collection
    options:
      members:
        - __init__
        - pause_writes
        - sync_from_remote_before_index
        - start
        - stop
        - build_index
        - search
        - read
        - write
        - edit
        - delete
        - rename
        - list
        - list_folders
        - list_tags
        - stats
        - reindex
        - build_embeddings
        - embeddings_status
        - get_toc
        - read_attachment
        - write_attachment
        - delete_attachment
        - rename_attachment
        - list_attachments
        - close
