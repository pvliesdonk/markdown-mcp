# Deployment

This section covers deploying markdown-vault-mcp in various environments.

## Deployment Options

| Method | Best For | Guide |
|--------|----------|-------|
| [Docker Compose](docker.md) | Self-hosted, homelab, production | Full compose setup with volumes, Traefik, and troubleshooting |
| [Claude Desktop](claude-desktop.md) | Local development, personal use | Direct stdio integration with Claude Desktop |
| [OIDC Authentication](oidc.md) | Multi-user, HTTP deployments | Token-based auth with Authelia, Keycloak, etc. |

## Prerequisites

- **Docker deployments:** Docker Engine 24+ and Docker Compose v2
- **Claude Desktop:** Python 3.10+ with `markdown-vault-mcp[mcp]` installed
- **All deployments:** A directory of Markdown files to serve

## Transport Modes

The MCP server supports three transport modes:

| Transport | Flag | Use Case |
|-----------|------|----------|
| `stdio` | `--transport stdio` (default) | Claude Desktop and other stdio-based clients |
| `sse` | `--transport sse` | Server-Sent Events for web clients |
| `http` | `--transport http` | Streamable HTTP — required for Docker and OIDC |

```bash
# Default (stdio) — for Claude Desktop
markdown-vault-mcp serve

# HTTP — for Docker/reverse proxy/OIDC
markdown-vault-mcp serve --transport http --host 0.0.0.0 --port 8000
```

## Example .env Files

| File | Description |
|------|-------------|
| `examples/obsidian-readonly.env` | Obsidian vault, read-only, Ollama embeddings |
| `examples/obsidian-readwrite.env` | Obsidian vault, read-write with managed git mode (clone/pull/commit/push) |
| `examples/obsidian-oidc.env` | Obsidian vault, read-only, OIDC authentication |
| `examples/ifcraftcorpus.env` | Strict frontmatter enforcement, read-only corpus |
