# Docker

This guide walks through three progressive Docker deployments:

1. **Basic** — read-only container with keyword search via HTTP
2. **Git write support** — enable write operations with auto-commit and push
3. **OIDC authentication** — protect HTTP access with Authelia

Each step builds on the previous one.

## Step 1: Basic container with stdio

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

### Verify it works

```bash
# Check the container is running
docker compose ps

# Check logs for successful startup
docker compose logs markdown-vault-mcp
```

You should see log output indicating the index was built successfully (e.g., number of documents indexed). If you see permission errors, check the UID/GID tip below.

!!! tip "UID/GID mismatch"
    If the container can't read your vault files, the container user (UID 1000) may not match your host user. Fix with:

    ```bash
    docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
    ```

    See [Docker deployment](../deployment/docker.md#uidgid-configuration) for more options.

---

## Step 2: Add git write support

**Goal:** Enable write operations that auto-commit and push to a git remote.

**Prerequisites:** Step 1 complete. Your vault must be a git repository with an HTTPS remote.

### Create a Personal Access Token

1. Go to [GitHub Settings > Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)
2. Scope to your vault repository only
3. Grant **Contents: Read and write**
4. Copy the token

### Update the env file

```bash hl_lines="3-7"
# .env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/ObsidianVault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_GIT_TOKEN=github_pat_your_token_here
MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S=30
MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME=markdown-vault-mcp
MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL=noreply@markdown-vault-mcp
MARKDOWN_VAULT_MCP_SERVER_NAME=my-vault
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**
```

**What these do:**

- `READ_ONLY=false` — enables write, edit, delete, rename tools
- `GIT_TOKEN` — enables auto-commit and push via HTTPS
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

## Step 3: Add OIDC authentication

**Goal:** Protect the HTTP endpoint with OIDC authentication using Authelia.

**Prerequisites:** Step 1 (or Step 2) complete. An [Authelia](https://www.authelia.com/) instance running and accessible. A domain name with TLS (OIDC requires HTTPS).

### Register the client in Authelia

Add this to your Authelia `configuration.yml`:

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: markdown-vault-mcp
        client_secret: '$pbkdf2-sha512$...'   # authelia crypto hash generate
        redirect_uris:
          - https://mcp.example.com/auth/callback
        grant_types: [authorization_code]
        response_types: [code]
        pkce_challenge_method: S256
        scopes: [openid, profile, email]
```

Generate the client secret hash:

```bash
docker run --rm authelia/authelia:latest \
  authelia crypto hash generate pbkdf2 --password 'your-client-secret'
```

Use the plain-text secret in your `.env` and the hashed version in Authelia's config.

### Generate a JWT signing key

```bash
openssl rand -hex 32
```

Save the output — you'll need it in the next step.

### Update the env file

```bash hl_lines="3-9"
# .env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/ObsidianVault
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-64-char-hex-key
MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES=openid,profile,email
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_SERVER_NAME=my-vault
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**
```

!!! danger "JWT signing key is required on Linux/Docker"
    Without `OIDC_JWT_SIGNING_KEY`, FastMCP generates an ephemeral key that invalidates all tokens on restart. Always set a stable key in Docker deployments.

### Update Docker Compose for Traefik

Your `compose.yml` should have the Traefik labels and network configured. See the [Docker deployment reference](../deployment/docker.md#traefik-reverse-proxy) for the full compose file, or add these labels:

```yaml
services:
  markdown-vault-mcp:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`)"
      - "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"
    networks:
      - traefik

networks:
  traefik:
    external: true
```

### Restart and verify

```bash
docker compose up -d
```

Test the OIDC flow:

1. Navigate to `https://mcp.example.com` in a browser
2. You should be redirected to your Authelia login page
3. After authentication, you should be redirected back with a valid session

Check the container logs for OIDC initialization:

```bash
docker compose logs markdown-vault-mcp --tail 20
```

You should see no OIDC-related errors. If you see "invalid client" errors, verify the `client_id` and `redirect_uris` match between the `.env` and Authelia config.
