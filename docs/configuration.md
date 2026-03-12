# Configuration

All configuration is via environment variables. Most use the `MARKDOWN_VAULT_MCP_` prefix; embedding provider settings use their own conventions.

## Core

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | path | — | **Yes** | Path to the markdown vault directory |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | bool | `true` | No | Set to `false` to enable write operations |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | path | in-memory | No | Path to the SQLite FTS5 index file; set for persistence across restarts |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | path | disabled | No | Path to the numpy embeddings file; required to enable semantic search |
| `MARKDOWN_VAULT_MCP_STATE_PATH` | path | `{SOURCE_DIR}/.markdown_vault_mcp/state.json` | No | Path to the change-tracking state file |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | csv | — | No | Comma-separated frontmatter fields to promote to the tag index for structured filtering |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | csv | — | No | Comma-separated frontmatter fields required on every document; documents missing any are excluded from the index |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | csv | — | No | Comma-separated glob patterns to exclude from scanning (e.g. `.obsidian/**,.trash/**`) |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | string | `_templates` | No | Relative folder path used by the `create_from_template` prompt to discover/read template files |

## Server Identity

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | string | `markdown-vault-mcp` | MCP server name shown to clients; useful for multi-instance setups |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | string | (auto) | System-level instructions injected into LLM context; defaults to a description that reflects read-only vs read-write state |
| `MARKDOWN_VAULT_MCP_HTTP_PATH` | path | `/mcp` | HTTP endpoint path for streamable HTTP transport (`serve --transport http`) |

## Search and Embeddings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `EMBEDDING_PROVIDER` | string | auto-detect | Embedding provider: `openai`, `ollama`, or `fastembed`. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `OLLAMA_HOST` | url | `http://localhost:11434` | Ollama server URL. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `OPENAI_API_KEY` | string | — | OpenAI API key for the OpenAI embedding provider. **Not** `MARKDOWN_VAULT_MCP_`-prefixed |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | string | `nomic-embed-text` | Ollama embedding model name |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | bool | `false` | Force Ollama to use CPU only |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | string | `nomic-ai/nomic-embed-text-v1.5` | FastEmbed model name |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | path | FastEmbed default | FastEmbed model cache directory (set this to a persistent Docker volume) |

!!! note "Embedding provider auto-detection"
    When `EMBEDDING_PROVIDER` is not set, the server tries providers in this order:

    1. **OpenAI** — if `OPENAI_API_KEY` is set
    2. **Ollama** — if `OLLAMA_HOST` is reachable
    3. **FastEmbed** — if the `fastembed` package is installed

## Git Integration

Git integration has three modes:

- **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`): server manages clone, pull, commit, and push.
- **Unmanaged / commit-only** (no `GIT_REPO_URL`, repo already exists): server commits writes locally only; you manage pull/push externally.
- **No-git** (default): plain directory; no git operations.

Backward compatibility: `GIT_TOKEN` without `GIT_REPO_URL` still works (legacy behavior) and logs a deprecation warning.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | string | — | HTTPS repo URL for managed mode. On startup, empty `SOURCE_DIR` is cloned from this URL |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | string | `x-access-token` | Username for HTTPS auth prompts (`x-access-token` GitHub, `oauth2` GitLab, account name Bitbucket) |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | string | — | Token/password for HTTPS auth via `GIT_ASKPASS` |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | int | `600` | Seconds between `git fetch` + ff-only update attempts; `0` disables periodic pull |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | float | `30` | Seconds of write-idle time before pushing; `0` = push only on shutdown |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | string | `markdown-vault-mcp` | Git committer name for auto-commits; **set this in Docker** where `git config user.name` is empty |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | string | `noreply@markdown-vault-mcp` | Git committer email for auto-commits |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | bool | `true` | Run `git lfs pull` on startup to resolve LFS pointers; set to `false` if git-lfs is not installed |

!!! tip "Push delay"
    The push delay batches rapid writes into a single push. Set to `0` to disable automatic pushing — the server will push only on shutdown via `close()`.

!!! warning "HTTPS remotes only with token auth"
    When `GIT_TOKEN` is used, SSH remotes are rejected. Use an HTTPS URL for `origin` or `GIT_REPO_URL`.

## Attachments

Non-markdown file support for PDFs, images, spreadsheets, and more.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | csv | (built-in list) | Comma-separated allowed extensions without dot (e.g. `pdf,png,jpg`); use `*` to allow all non-`.md` files |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | float | `10.0` | Maximum attachment size in MB for reads and writes; `0` disables the limit |

**Default allowed extensions:** `pdf`, `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`, `png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`, `bmp`, `tiff`, `zip`, `tar`, `gz`, `mp3`, `mp4`, `wav`, `ogg`, `txt`, `csv`, `tsv`, `json`, `yaml`, `toml`, `xml`, `html`, `css`, `js`, `ts`

!!! warning "Hidden directories"
    Attachments inside hidden directories (`.git/`, `.obsidian/`, `.markdown_vault_mcp/`, etc.) are never listed, regardless of extension settings. `MARKDOWN_VAULT_MCP_EXCLUDE` patterns are also applied to attachments.

## OIDC Authentication

Optional token-based authentication for HTTP deployments. OIDC activates when all four required variables are set. See [OIDC deployment](deployment/oidc.md) for setup details.

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | url | Yes | Public base URL of the server (e.g. `https://mcp.example.com` or `https://mcp.example.com/vault` when mounted under a prefix) |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | url | Yes | OIDC discovery endpoint (e.g. `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | string | Yes | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | string | Yes | OIDC client secret |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | string | No | JWT signing key; **required on Linux/Docker** — the default is ephemeral and invalidates tokens on restart. Generate with `openssl rand -hex 32` |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | string | No | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | csv | `openid` | Comma-separated required scopes |

## Boolean Parsing

Boolean environment variables accept `true`, `1`, or `yes` (case-insensitive) as truthy. Everything else is treated as `false`.

## Example .env Files

| File | Description |
|------|-------------|
| [`examples/obsidian-readonly.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/obsidian-readonly.env) | Obsidian vault, read-only, Ollama embeddings |
| [`examples/obsidian-readwrite.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/obsidian-readwrite.env) | Obsidian vault, read-write with git auto-commit |
| [`examples/obsidian-oidc.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/obsidian-oidc.env) | Obsidian vault, read-only, OIDC authentication (Authelia) |
| [`examples/ifcraftcorpus.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/ifcraftcorpus.env) | Strict frontmatter enforcement, read-only corpus |
