# markdown-vault-mcp

A generic markdown collection [MCP](https://modelcontextprotocol.io/) server with FTS5 full-text search, semantic vector search, frontmatter-aware indexing, and incremental reindexing.

Point it at a directory of Markdown files (an Obsidian vault, a docs folder, a Zettelkasten) and it exposes search, read, write, and edit tools over the Model Context Protocol.

## Features

- **Full-text search** — SQLite FTS5 with BM25 scoring, porter stemming
- **Semantic search** — cosine similarity over embedding vectors (Ollama, OpenAI, or Sentence Transformers)
- **Hybrid search** — Reciprocal Rank Fusion combining FTS5 and vector results
- **Frontmatter-aware** — indexes YAML frontmatter fields, supports required field enforcement
- **Incremental reindexing** — hash-based change detection, only re-processes modified files
- **Write operations** — create, edit, delete, rename documents with automatic index updates
- **Git integration** — optional auto-commit and push on every write via `GIT_ASKPASS`
- **MCP tools** — `search`, `read`, `write`, `edit`, `delete`, `rename`, `list_documents`, `list_folders`, `list_tags`, `reindex`

## Installation

### From PyPI

```bash
pip install markdown-vault-mcp
```

With optional dependencies:

```bash
pip install markdown-vault-mcp[mcp]           # FastMCP server
pip install markdown-vault-mcp[embeddings-api] # Ollama/OpenAI embeddings via HTTP
pip install markdown-vault-mcp[all]            # Everything
```

### From source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
pip install -e ".[all,dev]"
```

### Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

## Quick Start

### As a library

```python
from markdown_vault_mcp import Collection

collection = Collection(source_dir="/path/to/vault")
results = collection.search("query text", limit=10)
```

### As an MCP server

Set the required environment variable and start the server:

```bash
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/vault
markdown-vault-mcp serve
```

### With Docker Compose

1. Copy an example env file:

   ```bash
   cp examples/obsidian-readonly.env .env
   ```

2. Edit `.env` to set `MARKDOWN_VAULT_MCP_SOURCE_DIR` to the absolute path of your vault on the host.

3. Start the service:

   ```bash
   docker compose up -d
   ```

4. Verify it's running:

   ```bash
   curl http://localhost:8000/health
   ```

### Example env files

| File | Description |
|------|-------------|
| `examples/obsidian-readonly.env` | Obsidian vault, read-only, Ollama embeddings |
| `examples/obsidian-readwrite.env` | Obsidian vault, read-write with git auto-commit |
| `examples/ifcraftcorpus.env` | Strict frontmatter enforcement, read-only corpus |

For reverse proxy (Traefik) and authentication (mcp-auth-proxy) setup, see [`docs/deployment.md`](docs/deployment.md).

## Configuration

All configuration is via environment variables with the `MARKDOWN_VAULT_MCP_` prefix.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | — | Yes | Path to the markdown vault directory |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | `<source_dir>/.markdown_vault_mcp/index.db` | No | Path to the SQLite FTS5 index file |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | `<source_dir>/.markdown_vault_mcp/embeddings` | No | Path to the numpy embeddings file |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | `<source_dir>/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | — | No | Comma-separated frontmatter fields to index in FTS5 |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | — | No | Comma-separated frontmatter fields required on every document |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | — | No | Comma-separated glob patterns to exclude (e.g. `.obsidian/**,.trash/**`) |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | — | No | GitHub PAT for auto-commit and push on writes (via `GIT_ASKPASS`) |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | `nomic-embed-text` | No | Ollama embedding model name |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | `false` | No | Force Ollama to use CPU only |

Embedding provider is configured via `EMBEDDING_PROVIDER` (not prefixed): `ollama`, `openai`, or `sentence-transformers`.

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Hybrid full-text + semantic search with optional frontmatter filters |
| `read` | Read a document's content by relative path |
| `write` | Create or overwrite a document (with optional frontmatter) |
| `edit` | Replace a unique text span in a document |
| `delete` | Delete a document and its index entries |
| `rename` | Rename/move a document, updating all index entries |
| `list_documents` | List all indexed document paths (with optional folder filter) |
| `list_folders` | List all folder paths in the vault |
| `list_tags` | List all unique frontmatter tag values |
| `reindex` | Force a full reindex of the vault |

Write tools (`write`, `edit`, `delete`, `rename`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

## Development

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
pip install -e ".[all,dev]"

# Run tests
uv run python -m pytest tests/ -x -q

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/
```

## License

MIT
