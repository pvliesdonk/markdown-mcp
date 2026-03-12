# markdown-vault-mcp

[![CI](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pvliesdonk/markdown-vault-mcp/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp/graph/badge.svg)](https://codecov.io/gh/pvliesdonk/markdown-vault-mcp) [![PyPI](https://img.shields.io/pypi/v/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![Python](https://img.shields.io/pypi/pyversions/markdown-vault-mcp)](https://pypi.org/project/markdown-vault-mcp/) [![License](https://img.shields.io/github/license/pvliesdonk/markdown-vault-mcp)](LICENSE) [![Docker](https://img.shields.io/github/v/release/pvliesdonk/markdown-vault-mcp?label=ghcr.io&logo=docker)](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp) [![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pvliesdonk.github.io/markdown-vault-mcp/) [![llms.txt](https://img.shields.io/badge/llms.txt-available-brightgreen)](https://pvliesdonk.github.io/markdown-vault-mcp/llms.txt)

A generic markdown collection [MCP](https://modelcontextprotocol.io/) server with FTS5 full-text search, semantic vector search, frontmatter-aware indexing, incremental reindexing, and non-markdown attachment support.

**[Documentation](https://pvliesdonk.github.io/markdown-vault-mcp/)** | **[PyPI](https://pypi.org/project/markdown-vault-mcp/)** | **[Docker](https://github.com/pvliesdonk/markdown-vault-mcp/pkgs/container/markdown-vault-mcp)**

Point it at a directory of Markdown files (an Obsidian vault, a docs folder, a Zettelkasten) and it exposes search, read, write, and edit tools over the Model Context Protocol.

## Features

- **Full-text search** — SQLite FTS5 with BM25 scoring, porter stemming
- **Semantic search** — cosine similarity over embedding vectors (Ollama, OpenAI, or Sentence Transformers)
- **Hybrid search** — Reciprocal Rank Fusion combining FTS5 and vector results
- **Frontmatter-aware** — indexes YAML frontmatter fields, supports required field enforcement
- **Incremental reindexing** — hash-based change detection, only re-processes modified files
- **Write operations** — create, edit, delete, rename documents with automatic index updates
- **Attachment support** — read, write, delete, and list non-markdown files (PDFs, images, etc.)
- **Git integration** — optional auto-commit and push on every write via `GIT_ASKPASS`
- **OIDC authentication** — optional token-based auth for HTTP deployments (Authelia, Keycloak, etc.)
- **MCP tools** — 13 tools including search, read, write, edit, delete, rename, and admin operations
- **MCP resources** — 6 resources exposing vault configuration, statistics, tags, folders, and document outlines
- **MCP prompts** — 5 prompt templates for summarizing, researching, discussing, comparing, and finding related notes

## Installation

### From PyPI

```bash
pip install markdown-vault-mcp
```

With optional dependencies:

```bash
pip install markdown-vault-mcp[mcp]            # FastMCP server
pip install markdown-vault-mcp[embeddings-api]  # Ollama/OpenAI embeddings via HTTP
pip install markdown-vault-mcp[all]             # MCP + API embeddings (lightweight, no PyTorch)
pip install markdown-vault-mcp[all-local]       # + sentence-transformers + PyTorch (large)
```

> **`[all]` vs `[all-local]`:** The `[all]` extra is lightweight and does **not** include `sentence-transformers` or PyTorch. Use `[all-local]` if you want local CPU/GPU embeddings without Ollama. The Docker image uses `[all]`.

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

The Docker image uses `[all]` (MCP + API embeddings). It does **not** include `sentence-transformers` or PyTorch — use Ollama or OpenAI for embeddings. For local sentence-transformers, build from source with `[all-local]`.

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

1. Copy an example env file:

   ```bash
   cp examples/obsidian-readonly.env .env
   ```

2. Edit `.env` to set `MARKDOWN_VAULT_MCP_SOURCE_DIR` to the absolute path of your vault on the host.

3. Start the service:

   ```bash
   docker compose up -d
   ```

4. Check the logs:

   ```bash
   docker compose logs -f markdown-vault-mcp
   ```

### Example env files

| File | Description |
|------|-------------|
| `examples/obsidian-readonly.env` | Obsidian vault, read-only, Ollama embeddings |
| `examples/obsidian-readwrite.env` | Obsidian vault, read-write with git auto-commit |
| `examples/obsidian-oidc.env` | Obsidian vault, read-only, OIDC authentication (Authelia) |
| `examples/ifcraftcorpus.env` | Strict frontmatter enforcement, read-only corpus |

For reverse proxy (Traefik) and deployment setup, see [`docs/deployment.md`](docs/deployment.md).

## Configuration

All configuration is via environment variables with the `MARKDOWN_VAULT_MCP_` prefix (except embedding provider settings, which use their own conventions).

### Core

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | — | **Yes** | Path to the markdown vault directory |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | in-memory | No | Path to the SQLite FTS5 index file; set for persistence across restarts |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | disabled | No | Path to the numpy embeddings file; required to enable semantic search |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | `{SOURCE_DIR}/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | — | No | Comma-separated frontmatter fields to promote to the tag index for structured filtering |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | — | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | — | No | Comma-separated glob patterns to exclude from scanning (e.g. `.obsidian/**,.trash/**`) |

### Server identity

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | `markdown-vault-mcp` | MCP server name shown to clients; useful for multi-instance setups |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | (auto) | System-level instructions injected into LLM context; defaults to a description that reflects read-only vs read-write state |
| `MARKDOWN_VAULT_MCP_HTTP_PATH` | `/mcp` | HTTP endpoint path for streamable HTTP transport (used by `serve --transport http`) |

### Search and embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | auto-detect | Embedding provider: `ollama`, `openai`, or `sentence-transformers` (**not** `MARKDOWN_VAULT_MCP_`-prefixed) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL (**not** `MARKDOWN_VAULT_MCP_`-prefixed) |
| `OPENAI_API_KEY` | — | OpenAI API key for the OpenAI embedding provider (**not** `MARKDOWN_VAULT_MCP_`-prefixed) |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | `nomic-embed-text` | Ollama embedding model name |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | `false` | Force Ollama to use CPU only |

### Git integration

Git integration has three modes:

- **Managed mode** (`MARKDOWN_VAULT_MCP_GIT_REPO_URL` set): server owns repo setup.
  On startup it clones into `SOURCE_DIR` when empty, or validates existing `origin`.
  Pull loop + auto-commit + deferred push are enabled.
- **Unmanaged / commit-only mode** (no `GIT_REPO_URL`): writes are committed to a local git repo if `SOURCE_DIR` is already a git checkout. No pull, no push.
- **No-git mode**: if `SOURCE_DIR` is not a git repo, git callbacks are no-ops.

When token auth is used (`MARKDOWN_VAULT_MCP_GIT_TOKEN`), remotes must be HTTPS.
SSH remotes (for example `git@github.com:owner/repo.git`) are rejected with a startup error.
Fix with: `git -C /path/to/vault remote set-url origin https://github.com/owner/repo.git`

Backward compatibility: `MARKDOWN_VAULT_MCP_GIT_TOKEN` without `GIT_REPO_URL` still works (legacy mode) but logs a deprecation warning.

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | — | HTTPS remote URL for managed mode; enables clone/remote validation on startup |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | `x-access-token` | Username for HTTPS auth prompts (`x-access-token` for GitHub, `oauth2` for GitLab, account name for Bitbucket) |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | — | Token/password for HTTPS auth (`GIT_ASKPASS`) |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | `600` | Seconds between `git fetch` + ff-only update attempts; `0` disables periodic pull |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | `30` | Seconds of write-idle time before pushing; `0` = push only on shutdown |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | `markdown-vault-mcp` | Git committer name for auto-commits; **set this in Docker** where `git config user.name` is empty |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | `noreply@markdown-vault-mcp` | Git committer email for auto-commits |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | `true` | Enable Git LFS — runs `git lfs pull` on startup to fetch LFS-tracked attachments (PDFs, images). Set to `false` for repos without LFS. |

### Attachments

Non-markdown file support. See [Attachments](#attachments) for details.

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | (built-in list) | Comma-separated allowed extensions without dot (e.g. `pdf,png,jpg`); use `*` to allow all non-`.md` files |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | `10.0` | Maximum attachment size in MB for reads and writes; `0` disables the limit |

### OIDC authentication

Optional token-based authentication for HTTP deployments. OIDC activates when all four required variables are set. See [Authentication](#authentication) for setup details.

| Variable | Required | Description |
|----------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Yes | Public base URL of the server (e.g. `https://mcp.example.com`; include prefix if mounted under subpath, e.g. `https://mcp.example.com/vault`) |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | Yes | OIDC discovery endpoint (e.g. `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | Yes | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | Yes | OIDC client secret |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | No | JWT signing key; **required on Linux/Docker** — the default is ephemeral and invalidates tokens on restart. Generate with `openssl rand -hex 32` |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | No | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | No | Comma-separated required scopes; default `openid` |

## CLI Reference

```
markdown-vault-mcp <command> [options]
```

### `serve`

Start the MCP server.

```bash
markdown-vault-mcp serve [--transport {stdio|sse|http}] [--host HOST] [--port PORT] [--path PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `stdio` | MCP transport: `stdio` (stdin/stdout, default), `sse` (Server-Sent Events), `http` (streamable-HTTP). Use `http` for Docker with a reverse proxy or when OIDC is enabled. |
| `--host` | `0.0.0.0` | Bind host for the `http` transport (ignored for `stdio` and `sse`) |
| `--port` | `8000` | Port for the `http` transport (ignored for `stdio` and `sse`) |
| `--path` | env `MARKDOWN_VAULT_MCP_HTTP_PATH` or `/mcp` | MCP HTTP path for `http` transport; useful for reverse-proxy subpath mounting (e.g. `/vault/mcp`) |

### Reverse Proxy Subpath Mounts

By default, HTTP transport serves MCP on `/mcp`. You can run it under a subpath:

```bash
markdown-vault-mcp serve --transport http --path /vault/mcp
```

Equivalent env-based config:

```bash
MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
```

For reverse proxies, you can either:

- Keep app path at `/mcp` and use proxy rewrite/strip-prefix middleware.
- Set app path directly to the public path (`/vault/mcp`) and route without rewrite.

When OIDC is enabled and you deploy under a prefix, include that prefix in `MARKDOWN_VAULT_MCP_BASE_URL`. Example:

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com/vault
```

Then your redirect URI is:

```text
https://mcp.example.com/vault/auth/callback
```

### `index`

Build the full-text search index.

```bash
markdown-vault-mcp index [--source-dir PATH] [--index-path PATH] [--force]
```

### `search`

Search the collection from the CLI.

```bash
markdown-vault-mcp search <query> [-n LIMIT] [-m {keyword|semantic|hybrid}] [--folder PATH] [--json]
```

### `reindex`

Incrementally reindex the vault (only processes changed files).

```bash
markdown-vault-mcp reindex [--source-dir PATH] [--index-path PATH]
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Hybrid full-text + semantic search with optional frontmatter filters |
| `read` | Read a document or attachment by relative path |
| `write` | Create or overwrite a document or attachment |
| `edit` | Replace a unique text span in a document (notes only) |
| `delete` | Delete a document or attachment and its index entries |
| `rename` | Rename/move a document or attachment, updating all index entries |
| `list_documents` | List indexed documents; pass `include_attachments=true` to also list non-markdown files |
| `list_folders` | List all folder paths in the vault |
| `list_tags` | List all unique frontmatter tag values |
| `reindex` | Force a full reindex of the vault |
| `stats` | Get collection statistics (document count, chunk count, etc.) |
| `build_embeddings` | Build or rebuild vector embeddings for semantic search |
| `embeddings_status` | Check embedding provider and index status |

Write tools (`write`, `edit`, `delete`, `rename`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

### Resources

MCP resources expose vault metadata as structured JSON that clients can read directly without invoking tools.

| URI | Description |
|-----|-------------|
| `config://vault` | Current collection configuration (source dir, indexed fields, read-only state, etc.) |
| `stats://vault` | Collection statistics (document count, chunk count, embedding count, etc.) |
| `tags://vault` | All frontmatter tag values grouped by indexed field |
| `tags://vault/{field}` | Tag values for a specific indexed frontmatter field (template) |
| `folders://vault` | All folder paths in the vault |
| `toc://vault/{path}` | Table of contents (heading outline) for a specific document (template) |

### Prompts

Prompt templates guide the LLM through multi-step workflows using the vault tools.

| Prompt | Parameters | Description |
|--------|------------|-------------|
| `summarize` | `path` | Read a document and produce a structured summary with key themes and takeaways |
| `research` | `topic` | Search for a topic, synthesize findings, and create a new note at `research/{topic}.md` |
| `discuss` | `path` | Analyze a document and suggest improvements using `edit` (not `write`) |
| `related` | `path` | Find related notes via search and suggest cross-references as markdown links |
| `compare` | `path1`, `path2` | Read two documents and produce a side-by-side comparison |

Write prompts (`research`, `discuss`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

## Attachments

In addition to Markdown notes, the server can read, write, delete, rename, and list non-markdown files (PDFs, images, spreadsheets, etc.). All existing tools are overloaded — no new tool names.

### How it works

Path dispatch is extension-based: a path ending in `.md` is treated as a note; any other path is treated as an attachment if the extension is in the allowlist. The `kind` field on returned objects distinguishes the two: `"note"` or `"attachment"`.

### Reading attachments

`read` returns base64-encoded content for binary attachments:

```json
{
  "path": "assets/diagram.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 12345,
  "content_base64": "<base64 string>",
  "modified_at": 1741564800.0
}
```

### Writing attachments

`write` accepts a `content_base64` parameter for binary content:

```json
{ "path": "assets/diagram.pdf", "content_base64": "<base64 string>" }
```

### Listing attachments

`list_documents` with `include_attachments=true` returns both notes and attachments:

```json
[
  { "path": "notes/intro.md", "kind": "note", "title": "Intro", "folder": "notes", "frontmatter": {}, "modified_at": 1741564800.0 },
  { "path": "assets/diagram.pdf", "kind": "attachment", "folder": "assets", "mime_type": "application/pdf", "size_bytes": 12345, "modified_at": 1741564800.0 }
]
```

### Default allowed extensions

`pdf`, `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`, `png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `tiff`, `zip`, `tar`, `gz`, `mp3`, `mp4`, `wav`, `ogg`, `txt`, `csv`, `tsv`, `json`, `yaml`, `toml`, `xml`, `html`, `css`, `js`, `ts`

Override with `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS`. Use `*` to allow all non-`.md` files.

> **Hidden directories:** Attachments inside hidden directories (`.git/`, `.obsidian/`, `.markdown_vault_mcp/`, etc.) are never listed, regardless of extension settings. `MARKDOWN_VAULT_MCP_EXCLUDE` patterns are also applied to attachments.

## Authentication

OIDC authentication is optional and activates automatically when all four required variables (`BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`) are set.

**OIDC requires `--transport http` (or `sse`).** It has no effect with `--transport stdio`.

### Setup with Authelia

> **Note:** Authelia does not support Dynamic Client Registration (RFC 7591). Clients must be registered manually in `configuration.yml`.

1. Register the client in Authelia:

   ```yaml
   identity_providers:
     oidc:
       clients:
         - client_id: markdown-vault-mcp
           client_secret: '$pbkdf2-sha512$...'   # authelia crypto hash generate
           redirect_uris:
             - https://mcp.example.com/auth/callback
             - https://mcp.example.com/vault/auth/callback   # when mounted under /vault
           grant_types: [authorization_code]
           response_types: [code]
           pkce_challenge_method: S256
           scopes: [openid, profile, email]
   ```

2. Set the environment variables (see also `examples/obsidian-oidc.env`):

   ```bash
   MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
   MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
   MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
   MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
   MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=$(openssl rand -hex 32)
   ```

   For subpath deployments (example MCP endpoint `/vault/mcp`):

   ```bash
   MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
   MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com/vault
   ```

3. Start with HTTP transport:

   ```bash
   markdown-vault-mcp serve --transport http --port 8000
   ```

### JWT signing key

The FastMCP default signing key is ephemeral (regenerated on startup), which forces clients to re-authenticate after every restart. Set `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` to a stable random secret to avoid this:

```bash
# Generate once, store in your .env file
openssl rand -hex 32
```

## Development

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
uv pip install -e ".[all,dev]"

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
