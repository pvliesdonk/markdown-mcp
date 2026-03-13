# Docker

This guide walks through three progressive Docker deployments:

1. **Basic** — read-only container with keyword search via HTTP
2. **Git write support** — enable write operations with auto-commit and push
3. **Bearer token authentication** — simple static token auth
4. **OIDC authentication** — protect HTTP access with Authelia

Each step builds on the previous one.

## Step 1: Basic container via HTTP

**Goal:** Run markdown-vault-mcp in a Docker container with your vault mounted as a volume.

**Prerequisites:** Docker and Docker Compose installed.

### Pull the image

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

### Create an env file

Create a `.env` file:

```bash
# .env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/ObsidianVault
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_SERVER_NAME=my-vault
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**
```

Replace `/home/user/ObsidianVault` with the path to your vault **on the host**. Inside the container, the vault is always mounted at `/data/vault` — the `compose.yml` handles this mapping automatically.

### Start with Docker Compose

The repository includes a `compose.yml`. If you cloned the repo, just run:

```bash
docker compose up -d
```

This mounts your vault at `/data/vault` inside the container and creates named volumes for the index and embeddings.

### Optional: run under a reverse-proxy subpath

If you want a public MCP URL like `https://mcp.example.com/vault/mcp`, add:

```bash
MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
```

Then configure Traefik with a matching `PathPrefix` rule (shown in Step 3).

### Verify it works

```bash
# Check the container is running
docker compose ps

# Check logs for successful startup
docker compose logs markdown-vault-mcp
```

You should see log output indicating the index was built successfully (e.g., number of documents indexed). If you see permission errors, check the UID/GID tip below.

!!! tip "UID/GID handling"
    Named volumes work out of the box — the entrypoint automatically fixes ownership on startup. For **bind-mounted vaults** where the host user doesn't match the container user (UID 1000), either set `PUID`/`PGID` environment variables or rebuild:

    ```bash
    docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
    ```

    See [Docker deployment](../deployment/docker.md#uidgid-configuration) for more options.

---

## Step 2: Add git write support

**Goal:** Enable managed git mode so writes auto-commit and push to a git remote.

**Prerequisites:** Step 1 complete. Your vault must be a git repository with an HTTPS remote.

### Create a Personal Access Token

1. Go to [GitHub Settings > Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)
2. Scope to your vault repository only
3. Grant **Contents: Read and write**
4. Copy the token

### Update the env file

```bash hl_lines="3-9"
# .env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/ObsidianVault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/your-org/your-vault.git
MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
MARKDOWN_VAULT_MCP_GIT_TOKEN=github_pat_your_token_here
MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S=30
MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME=markdown-vault-mcp
MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL=noreply@markdown-vault-mcp
MARKDOWN_VAULT_MCP_SERVER_NAME=my-vault
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**
```

**What these do:**

- `READ_ONLY=false` — enables write, edit, delete, rename tools
- `GIT_REPO_URL` — enables managed mode (clone/remote validation)
- `GIT_USERNAME` / `GIT_TOKEN` — HTTPS auth for pull/push
- `GIT_PUSH_DELAY_S=30` — push after 30 seconds of write-idle time
- `GIT_COMMIT_NAME` / `GIT_COMMIT_EMAIL` — required in Docker where `git config user.name` is unset

!!! warning "HTTPS remotes only"
    The git integration uses `GIT_ASKPASS` for authentication, which only works with HTTPS remotes. If your remote URL starts with `git@`, convert it:

    ```bash
    git -C /path/to/vault remote set-url origin https://github.com/user/repo.git
    ```

### Restart and verify

```bash
docker compose restart markdown-vault-mcp
```

Check logs for successful git initialization:

```bash
docker compose logs markdown-vault-mcp --tail 20
```

You should see no git errors. Write a test note via the MCP `write` tool and check the git log on the host:

```bash
git -C /path/to/vault log --oneline -3
```

---

## Step 3: Add bearer token authentication

**Goal:** Protect the HTTP endpoint with a simple static bearer token.

**Prerequisites:** Step 1 (or Step 2) complete.

Add `MARKDOWN_VAULT_MCP_BEARER_TOKEN` to your `.env` file:

```bash
MARKDOWN_VAULT_MCP_BEARER_TOKEN=your-secret-token
```

Clients must include the `Authorization: Bearer your-secret-token` header in every request. No external identity provider needed.

> **Tip:** Generate a random token with `openssl rand -hex 32`.

For more details on bearer token auth (client usage, when to use it), see the [Authentication guide](authentication.md#bearer-token).

---

## Step 4: Add OIDC authentication

**Goal:** Protect the HTTP endpoint with OIDC authentication (e.g., Authelia, Keycloak).

**Prerequisites:** Step 1 (or Step 2) complete. An OIDC provider running and accessible. A domain name with TLS (OIDC requires HTTPS).

### Overview

OIDC requires four environment variables added to your `.env`:

```bash hl_lines="3-6"
# .env (add to your existing config)
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-64-char-hex-key
```

!!! danger "JWT signing key is required on Linux/Docker"
    Without `OIDC_JWT_SIGNING_KEY`, FastMCP generates an ephemeral key that invalidates all tokens on restart. Generate one with `openssl rand -hex 32`.

### Detailed setup

For the full OIDC setup including provider registration, Traefik configuration, subpath deployments, and troubleshooting:

- [Authentication guide — OIDC section](authentication.md#oidc) — overview and variable reference
- [OIDC provider setup](oidc-providers.md) — step-by-step for Authelia, Keycloak, Google, GitHub
- [OIDC deployment reference](../deployment/oidc.md) — Docker Compose, subpath config, architecture

### Verify

```bash
docker compose up -d
docker compose logs markdown-vault-mcp --tail 20
```

You should see no OIDC-related errors. Navigate to your server URL in a browser — you should be redirected to your OIDC provider's login page.
