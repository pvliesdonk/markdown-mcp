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
      - state-data:/data/state
    environment:
      MARKDOWN_VAULT_MCP_SOURCE_DIR: /data/vault
      MARKDOWN_VAULT_MCP_INDEX_PATH: /data/state/index.db
      MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH: /data/state/embeddings/embeddings
      MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR: /data/state/fastembed
    restart: unless-stopped
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`${MARKDOWN_VAULT_MCP_HOST:-markdown-vault-mcp.local}`)"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"

volumes:
  state-data:
```

### Volume Mounts

| Container Path | Type | Purpose |
|---------------|------|---------|
| `/data/vault` | Bind mount or named volume | Your Markdown vault; pre-created in the image for managed repo mode |
| `/data/state` | Named volume | All server-managed internal state: SQLite FTS index, embedding vectors, FastEmbed model cache, and OIDC proxy state |

All `/data/*` directories are pre-created and owned by the runtime user in the image. For managed repo mode (where the server clones a git repo on first start), `/data/vault` must be writable — this works automatically with named volumes or when UID/GID match the bind-mount owner. The first startup triggers a full index build; subsequent starts only reindex changed files.

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

### Mounting Under a Subpath

To serve MCP at `https://mcp.example.com/vault/mcp`, set:

```bash
MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
```

And use a path-aware Traefik rule:

```yaml
labels:
  - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`) && PathPrefix(`/vault/mcp`)"
  - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"
```

!!! note "OIDC subpath deployments use a different pattern"
    When OIDC is enabled, do **not** include the subpath in `HTTP_PATH`. Instead, put the subpath in `BASE_URL` and configure the reverse proxy to strip the prefix. See the [OIDC subpath deployment guide](oidc.md#subpath-deployments) for details.

### TLS with Let's Encrypt

Add a `certificatesResolvers` block to your Traefik static config and these labels to the service:

```yaml
- "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
- "traefik.http.routers.markdown-vault-mcp.entrypoints=websecure"
```

See the [Traefik ACME documentation](https://doc.traefik.io/traefik/https/acme/) for the full setup.

## Git-Backed Write Support

Git integration supports three modes:

- **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`): clone/pull/commit/push
- **Unmanaged / commit-only** (no `GIT_REPO_URL`, existing git repo): commit only
- **No-git**: no git operations

### Setup

1. For managed mode, set a remote URL and credentials:

    ```bash
    MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/your-org/your-vault.git
    MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
    MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_your_personal_access_token
    ```

2. For unmanaged/commit-only mode, omit `GIT_REPO_URL` and `GIT_TOKEN`.
   If the vault path is a git repo, writes are committed locally only.

3. The vault mount must include `.git` when using managed or unmanaged mode:

    ```yaml
    volumes:
      - /path/to/your/vault:/data/vault
    ```

For managed mode, the token needs `repo` scope (or `contents: write` for fine-grained tokens).

!!! tip "Without auto-push"
    Use unmanaged/commit-only mode: omit `MARKDOWN_VAULT_MCP_GIT_REPO_URL`.
    Writes are committed locally; run `git pull`/`git push` externally.

## UID/GID Configuration

The container runs as a non-root `appuser` (UID 1000 / GID 1000 by default). On startup, the entrypoint automatically fixes ownership of all `/data/*` directories before dropping privileges — so **named volumes work out of the box** regardless of how Docker initialised them.

This is the same entrypoint + `gosu` pattern used by the official PostgreSQL, Redis, and MySQL Docker images.

### Runtime UID/GID override

To match a specific host user (e.g. for bind-mounted vaults), set `PUID` and `PGID`:

```yaml
services:
  markdown-vault-mcp:
    environment:
      PUID: 1001
      PGID: 1001
```

The entrypoint updates `appuser`'s UID/GID to the specified values and chowns `/data` accordingly.

### Build-time UID/GID (alternative for bind mounts)

If you prefer to bake the UID/GID into the image:

```bash
docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
```

### Fix host permissions (bind mounts only)

For bind-mounted vaults where the host user doesn't match, fix host-side:

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
- In unmanaged/commit-only mode, the vault directory is not a git repo — run `git init` on the host first

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
