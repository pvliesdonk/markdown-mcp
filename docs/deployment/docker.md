# Docker Deployment

## Quick Start

```bash
# Pull the image
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest

# Copy an example env file
cp examples/obsidian-readonly.env .env

# Edit .env — set MARKDOWN_VAULT_MCP_SOURCE_DIR to the vault path on the host
# Then start the service
docker compose up -d

# Check it's running
curl http://localhost:8000/health
```

## Docker Compose Configuration

The `compose.yml` defines a single service:

```yaml
services:
  markdown-vault-mcp:
    image: ghcr.io/pvliesdonk/markdown-vault-mcp:latest
    build: .
    env_file: .env
    volumes:
      - ${MARKDOWN_VAULT_MCP_SOURCE_DIR:?Set MARKDOWN_VAULT_MCP_SOURCE_DIR}:/data/vault
      - index-data:/data/index
      - embeddings-data:/data/embeddings
    environment:
      MARKDOWN_VAULT_MCP_SOURCE_DIR: /data/vault
      MARKDOWN_VAULT_MCP_INDEX_PATH: /data/index/index.db
      MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH: /data/embeddings/embeddings
    restart: unless-stopped
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`${MARKDOWN_VAULT_MCP_HOST:-markdown-vault-mcp.local}`)"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"

volumes:
  index-data:
  embeddings-data:
```

### Volume Mounts

| Container Path | Type | Purpose |
|---------------|------|---------|
| `/data/vault` | Bind mount | Your Markdown vault (from `MARKDOWN_VAULT_MCP_SOURCE_DIR`) |
| `/data/index` | Named volume | SQLite FTS5 index (persists across restarts) |
| `/data/embeddings` | Named volume | Numpy embedding vectors |

The index and embeddings volumes are automatically created on first run. The first startup triggers a full index build; subsequent starts only reindex changed files.

## Traefik Reverse Proxy

The `compose.yml` includes Traefik labels out of the box. When Traefik is running and watching Docker, it picks up these labels and routes traffic automatically.

**What the labels do:**

- `traefik.enable=true` — opt this service in to Traefik discovery
- `traefik.http.routers.markdown-vault-mcp.rule` — defines the `Host` rule; defaults to `markdown-vault-mcp.local`
- `traefik.http.services.markdown-vault-mcp.loadbalancer.server.port` — tells Traefik the container listens on port 8000

### Prerequisites

1. Traefik running in Docker with the Docker provider enabled
2. Both Traefik and this service on the same Docker network:

    ```yaml
    services:
      markdown-vault-mcp:
        networks:
          - traefik

    networks:
      traefik:
        external: true
    ```

3. A DNS entry (or `/etc/hosts` line) resolving the hostname to your host

### Custom Hostname

Set `MARKDOWN_VAULT_MCP_HOST` in your `.env`:

```bash
MARKDOWN_VAULT_MCP_HOST=vault.example.com
```

### TLS with Let's Encrypt

Add a `certificatesResolvers` block to your Traefik static config and these labels to the service:

```yaml
- "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
- "traefik.http.routers.markdown-vault-mcp.entrypoints=websecure"
```

See the [Traefik ACME documentation](https://doc.traefik.io/traefik/https/acme/) for the full setup.

## Git-Backed Write Support

When `MARKDOWN_VAULT_MCP_GIT_TOKEN` is set, every write operation automatically stages, commits, and pushes to the configured remote.

### Setup

1. The vault directory must be a git repository with a configured remote
2. Mount the vault so the `.git` directory is accessible:

    ```yaml
    volumes:
      - /path/to/your/vault:/data/vault
    ```

3. Set the token in `.env`:

    ```bash
    MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_your_personal_access_token
    ```

The token needs `repo` scope (or `contents: write` for fine-grained tokens).

!!! tip "Without auto-push"
    Omit `MARKDOWN_VAULT_MCP_GIT_TOKEN`. Writes persist to disk; run `git add + commit + push` from a cron job or git hook.

## UID/GID Configuration

The container runs as a non-root `appuser` (UID 1000 / GID 1000 by default). If the vault is owned by a different UID, reads will fail.

=== "Build-time (recommended)"

    ```bash
    docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
    ```

=== "Runtime override"

    ```yaml
    services:
      markdown-vault-mcp:
        user: "1001:1001"   # or "${APP_UID}:${APP_GID}" with .env
    ```

=== "Fix host permissions"

    ```bash
    chown -R 1000:1000 /path/to/vault
    ```

## Troubleshooting

### Traefik network not found

```
network traefik declared as external, but could not be found
```

Create the network first: `docker network create traefik`

### Git push failures

Check logs: `docker compose logs markdown-vault-mcp`

Common causes:

- Token lacks `repo` scope — regenerate with the right permissions
- Remote URL is SSH-based — the PAT strategy only works with HTTPS remotes. Convert: `git remote set-url origin https://github.com/user/repo.git`
- The vault directory is not a git repo — run `git init && git remote add origin ...` on the host first

### Stale index after adding files outside the server

The server reindexes on startup. Restart the container:

```bash
docker compose restart markdown-vault-mcp
```

For continuous sync, use the MCP `reindex` tool instead of restarting.

### Ollama on Linux without Docker Desktop

Add `extra_hosts` to `compose.yml` for `host.docker.internal` to resolve:

```yaml
services:
  markdown-vault-mcp:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```
