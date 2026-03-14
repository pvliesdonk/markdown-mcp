# Deployment Guide

A practical guide to running `markdown-vault-mcp` in a homelab or self-hosted
environment.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 (`docker compose`, not `docker-compose`)
- A directory of Markdown files to serve (the "vault")
- (Optional) Traefik reverse proxy for routing
- (Optional) mcp-auth-proxy for authentication

## Quick Start (Standalone)

Copy one of the example env files that best matches your use case:

| File | Description |
|------|-------------|
| `examples/obsidian-readonly.env` | Obsidian vault, read-only, Ollama embeddings |
| `examples/obsidian-readwrite.env` | Obsidian vault, read-write, git auto-commit |
| `examples/ifcraftcorpus.env` | Strict frontmatter enforcement, read-only corpus |

```bash
cp examples/obsidian-readonly.env .env
```

Edit `.env` to set `MARKDOWN_VAULT_MCP_SOURCE_DIR` to the absolute path of your
vault on the host. Then start the service:

```bash
docker compose up -d
```

The MCP server listens on port 8000. Test it is up:

```bash
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
      FASTMCP_HOME: /data/state/fastmcp
    restart: unless-stopped
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`${MARKDOWN_VAULT_MCP_HOST:-markdown-vault-mcp.local}`)"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"

volumes:
  state-data:
```

**Volume mounts:**

- `/data/vault` — your Markdown vault (bind mount or named volume; pre-created in the image for managed repo mode)
- `/data/state` — all server-managed internal state (Docker-managed named volume): SQLite FTS index, embedding vectors, FastEmbed model cache, and OIDC proxy state

All `/data/*` directories are pre-created and owned by the runtime user in the image. The first startup triggers a full index build; subsequent starts only reindex changed files.

For a full list of environment variables, see the configuration reference in
the README.

## Traefik Reverse Proxy

The `compose.yml` includes Traefik labels out of the box. When Traefik is
running and watching Docker, it picks up these labels and routes traffic
automatically.

**What the labels do:**

- `traefik.enable=true` — opt this service in to Traefik discovery
- `traefik.http.routers.markdown-vault-mcp.rule` — defines the `Host` rule; defaults to `markdown-vault-mcp.local`
- `traefik.http.services.markdown-vault-mcp.loadbalancer.server.port` — tells Traefik the container listens on port 8000

**Prerequisites:**

1. Traefik running in Docker with the Docker provider enabled.
2. Both Traefik and this service on the same Docker network. Add the network to
   `compose.yml`:

   ```yaml
   services:
     markdown-vault-mcp:
       networks:
         - traefik

   networks:
     traefik:
       external: true
   ```

3. A DNS entry (or `/etc/hosts` line) resolving the hostname to your host.

**Customising the Host rule:**

Set `MARKDOWN_VAULT_MCP_HOST` in your `.env`:

```bash
MARKDOWN_VAULT_MCP_HOST=vault.example.com
```

Or edit the label directly in `compose.yml`:

```yaml
- "traefik.http.routers.markdown-vault-mcp.rule=Host(`vault.example.com`)"
```

**Mounting under a subpath (`/vault/mcp`):**

Set the server path directly and match on both host and path prefix:

```yaml
environment:
  MARKDOWN_VAULT_MCP_HTTP_PATH: /vault/mcp
labels:
  - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`) && PathPrefix(`/vault/mcp`)"
  - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"
```

!!! note "OIDC subpath deployments use a different pattern"
    When OIDC is enabled, do **not** include the subpath in `HTTP_PATH`. Instead, put the subpath in `BASE_URL` and configure the reverse proxy to strip the prefix. See the [OIDC subpath deployment guide](deployment/oidc.md#subpath-deployments) for details.

**Example Traefik static config** (`traefik.yml`):

```yaml
providers:
  docker:
    exposedByDefault: false
    network: traefik

entryPoints:
  web:
    address: ":80"
  websecure:
    address: ":443"
```

**TLS / HTTPS with Let's Encrypt:**

Add a `certificatesResolvers` block to your Traefik static config and a
`tls.certresolver` label to the service. See the
[Traefik ACME documentation](https://doc.traefik.io/traefik/https/acme/) for
the full setup. The minimal addition to the service labels:

```yaml
- "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
- "traefik.http.routers.markdown-vault-mcp.entrypoints=websecure"
```

## mcp-auth-proxy Integration

[mcp-auth-proxy](https://github.com/wrale/mcp-auth-proxy) adds an OAuth2/OIDC
authentication layer in front of any MCP server. Clients authenticate via the
proxy; only authenticated requests reach `markdown-vault-mcp`.

The proxy runs as a sidecar alongside the MCP server. Traefik routes external
traffic to the proxy; the proxy forwards authenticated requests to the MCP
server on its internal port.

**Example compose snippet:**

```yaml
services:
  markdown-vault-mcp:
    image: ghcr.io/pvliesdonk/markdown-vault-mcp:latest
    env_file: .env
    volumes:
      - ${MARKDOWN_VAULT_MCP_SOURCE_DIR:?Set MARKDOWN_VAULT_MCP_SOURCE_DIR}:/data/vault
      - state-data:/data/state
    environment:
      MARKDOWN_VAULT_MCP_SOURCE_DIR: /data/vault
      MARKDOWN_VAULT_MCP_INDEX_PATH: /data/state/index.db
      MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH: /data/state/embeddings/embeddings
      MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR: /data/state/fastembed
    networks:
      - internal
    restart: unless-stopped
    # No Traefik labels here — proxy is the public face.

  mcp-auth-proxy:
    image: ghcr.io/wrale/mcp-auth-proxy:latest
    env_file: mcp-auth-proxy.env   # OIDC_CLIENT_SECRET goes here, not in compose.yml
    environment:
      # Upstream MCP server
      MCP_SERVER_URL: http://markdown-vault-mcp:8000
      # OAuth2/OIDC provider (replace with your provider's values)
      OIDC_ISSUER: https://auth.example.com
      OIDC_CLIENT_ID: your-client-id
      # OIDC_CLIENT_SECRET loaded from env_file (avoid putting secrets in compose.yml)
      # Proxy listen port
      PROXY_PORT: "9000"
    restart: unless-stopped
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.mcp-auth-proxy.rule=Host(`vault.example.com`)"
      - "traefik.http.services.mcp-auth-proxy.loadbalancer.server.port=9000"
    networks:
      - traefik
      - internal
    depends_on:
      - markdown-vault-mcp

volumes:
  state-data:

networks:
  traefik:
    external: true
  internal:
    internal: true   # not routable from outside
```

**Middleware chain:**

```
client → Traefik → mcp-auth-proxy:9000 → markdown-vault-mcp:8000
```

The Traefik labels move to the proxy service. `markdown-vault-mcp` stays off
the public network entirely.

**Notes:**

- The exact environment variable names depend on your mcp-auth-proxy version.
  Check its documentation for the current config schema.
- If your OIDC provider requires PKCE, set the appropriate flag in the proxy
  config.
- FastMCP has built-in OAuth support under evaluation as a future alternative
  to mcp-auth-proxy (see `docs/design.md`).

## Git-Backed Write Support

Git integration has three modes:

- **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`): clone/pull/commit/push
- **Unmanaged / commit-only** (no `GIT_REPO_URL`, existing git repo): commit only
- **No-git**: plain directory, no git operations

**Setup:**

1. For managed mode, set:

   ```bash
   MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/your-org/your-vault.git
   MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
   MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_your_personal_access_token
   ```

2. For unmanaged/commit-only mode, omit `GIT_REPO_URL` and `GIT_TOKEN`.
   If `SOURCE_DIR` is a git repo, writes are committed locally.

3. Mount the vault so the `.git` directory is accessible inside the container —
   the default bind mount covers this:

   ```yaml
   volumes:
     - /path/to/your/vault:/data/vault
   ```

For managed mode, the token needs `repo` scope (or `contents: write` for fine-grained tokens) on
the target repository.

**Example read-write env file** (`examples/obsidian-readwrite.env`):

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**,_templates/**
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/your-org/your-vault.git
MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_your_token_here
EMBEDDING_PROVIDER=ollama
OLLAMA_HOST=http://host.docker.internal:11434  # use host.docker.internal inside Docker
MARKDOWN_VAULT_MCP_OLLAMA_MODEL=nomic-embed-text
```

**Note:** On Linux without Docker Desktop, you may need to add
`extra_hosts: ["host.docker.internal:host-gateway"]` to the service in
`compose.yml` for `host.docker.internal` to resolve.

**If you prefer commit-only mode:** omit `MARKDOWN_VAULT_MCP_GIT_REPO_URL`.
Writes are still committed locally (when `SOURCE_DIR` is a git repo); run
`git pull`/`git push` from cron/hooks or another process.

## Troubleshooting

**Permission denied on vault directory**

Named volumes are handled automatically — the entrypoint fixes ownership on
startup. For **bind-mounted vaults** where the host user doesn't match the
container user (UID 1000 / GID 1000 by default), use one of these options:

**Option 1: Set PUID/PGID** (recommended — no rebuild needed):

```yaml
services:
  markdown-vault-mcp:
    environment:
      PUID: 1001
      PGID: 1001
```

**Option 2: Build with matching UID/GID** (baked into the image):

```bash
docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
```

**Option 3: Fix host permissions** to match the default container user:

```bash
chown -R 1000:1000 /path/to/vault
```

**Traefik network not found**

```
network traefik declared as external, but could not be found
```

Create the network before starting the stack:

```bash
docker network create traefik
```

Then start Traefik attached to that network before starting this stack.

**Git push failures**

Check the container logs:

```bash
docker compose logs markdown-vault-mcp
```

Git errors are logged at ERROR level. Common causes:

- Token lacks `repo` scope — regenerate with the right permissions.
- Remote URL is SSH-based — the PAT strategy only works with HTTPS remotes.
  Convert: `git remote set-url origin https://github.com/user/repo.git`
- In unmanaged/commit-only mode, the vault directory is not a git repo —
  run `git init` on the host first.

**Index feels stale after adding files outside the server**

The server reindexes on startup. Restart the container to pick up external
changes:

```bash
docker compose restart markdown-vault-mcp
```

For continuous sync, trigger reindex via the MCP `reindex` tool instead of
restarting.
