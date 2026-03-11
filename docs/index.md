# markdown-vault-mcp

A generic markdown collection [MCP](https://modelcontextprotocol.io/) server with FTS5 full-text search, semantic vector search, frontmatter-aware indexing, incremental reindexing, and non-markdown attachment support.

Point it at a directory of Markdown files — an Obsidian vault, a docs folder, a Zettelkasten — and it exposes search, read, write, and edit tools over the [Model Context Protocol](https://modelcontextprotocol.io/).

## Features

- **Full-text search** — SQLite FTS5 with BM25 scoring, porter stemming
- **Semantic search** — cosine similarity over embedding vectors (Ollama, OpenAI, or Sentence Transformers)
- **Hybrid search** — Reciprocal Rank Fusion combining FTS5 and vector results
- **Frontmatter-aware** — indexes YAML frontmatter fields, supports required field enforcement
- **Incremental reindexing** — hash-based change detection, only re-processes modified files
- **Write operations** — create, edit, delete, rename documents with automatic index updates
- **Attachment support** — read, write, delete, and list non-markdown files (PDFs, images, etc.)
- **Git integration** — optional auto-commit and push on every write via `GIT_ASKPASS`
- **OIDC authentication** — optional token-based auth for HTTP deployments
- **13 MCP tools** — search, read, write, edit, delete, rename, and admin operations
- **6 MCP resources** — vault configuration, statistics, tags, folders, and document outlines
- **5 MCP prompts** — summarize, research, discuss, compare, and find related notes

## Quick Start

### As a library

```python
from pathlib import Path
from markdown_vault_mcp import Collection

collection = Collection(source_dir=Path("/path/to/vault"))
results = collection.search("query text", limit=10)
```

### As an MCP server

```bash
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/vault
markdown-vault-mcp serve
```

### With Docker Compose

```bash
cp examples/obsidian-readonly.env .env
# Edit .env to set MARKDOWN_VAULT_MCP_SOURCE_DIR
docker compose up -d
```

See [Installation](installation.md) for detailed setup instructions and [Configuration](configuration.md) for all available options.

## Architecture

The library is fully synchronous — no asyncio in core modules. The MCP server layer uses `asyncio.to_thread()` to bridge to the async FastMCP framework.

```
┌──────────────┐
│  MCP Server   │  ← FastMCP, asyncio.to_thread()
├──────────────┤
│  Collection   │  ← Thin facade / public API
├──────────────┤
│  Scanner      │  ← File discovery, frontmatter parsing, chunking
│  FTS Index    │  ← SQLite FTS5, BM25 scoring
│  Vector Index │  ← numpy embeddings, cosine similarity
│  Tracker      │  ← Hash-based change detection
│  Providers    │  ← Embedding provider ABC + implementations
│  Git          │  ← Auto-commit/push strategy
├──────────────┤
│  Config       │  ← Environment variable loading
└──────────────┘
```

## License

[MIT](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/LICENSE)
