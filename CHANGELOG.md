# Changelog

## 1.0.0 (2026-03-08)

First stable release.

### Features

- **Full-text search** with SQLite FTS5, BM25 scoring, and porter stemming
- **Semantic search** with cosine similarity over embedding vectors (Ollama, OpenAI, Sentence Transformers)
- **Hybrid search** combining FTS5 and vector results via Reciprocal Rank Fusion
- **Frontmatter indexing** with optional required-field enforcement
- **Incremental reindexing** via hash-based change detection
- **Write operations**: create, edit, delete, rename with automatic FTS + vector index updates
- **Git integration**: auto-commit and push on writes via `GIT_ASKPASS` (token never in argv)
- **Thread-safe writes** with `threading.Lock` serialization
- **MCP server** with 13 tools: `search`, `read`, `write`, `edit`, `delete`, `rename`, `list_documents`, `list_folders`, `list_tags`, `reindex`, `stats`, `build_embeddings`, `embeddings_status`
- **Docker support** with multi-arch images (amd64, arm64) on GHCR
- **Configuration** via `MARKDOWN_VAULT_MCP_*` environment variables

### Deployment

- Docker Compose with named volumes for index and embeddings persistence
- Traefik reverse proxy integration with TLS/Let's Encrypt
- mcp-auth-proxy sidecar for OAuth2/OIDC authentication
- Example env files for Obsidian (read-only, read-write) and structured corpus use cases
