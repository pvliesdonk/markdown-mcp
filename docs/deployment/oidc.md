# OIDC Authentication

Optional token-based authentication for HTTP deployments. OIDC activates automatically when all four required environment variables are set.

!!! warning "Transport requirement"
    OIDC requires `--transport http` (or `sse`). It has no effect with `--transport stdio`.

## Required Variables

| Variable | Description |
|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Public base URL of the server (e.g. `https://mcp.example.com`) |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery endpoint (e.g. `https://auth.example.com/.well-known/openid-configuration`) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | OIDC client secret |

## Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | ephemeral | JWT signing key. **Required on Linux/Docker** — the default is ephemeral and invalidates tokens on restart |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | — | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | `openid` | Comma-separated required scopes |

## JWT Signing Key

The FastMCP default signing key is ephemeral (regenerated on startup), which forces clients to re-authenticate after every restart. Set a stable random secret to avoid this:

```bash
# Generate once, store in your .env file
openssl rand -hex 32
```

!!! danger "Linux / Docker"
    On Linux (including Docker), the ephemeral key is especially problematic because it does not persist across process restarts. Always set `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` in production.

## Setup with Authelia

!!! note
    Authelia does not support Dynamic Client Registration (RFC 7591). Clients must be registered manually in `configuration.yml`.

### 1. Register the client in Authelia

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

### 2. Set environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=$(openssl rand -hex 32)
```

See also `examples/obsidian-oidc.env`.

### 3. Start with HTTP transport

```bash
markdown-vault-mcp serve --transport http --port 8000
```

## Architecture

The server uses FastMCP's built-in `OIDCProxy` auth provider (not the external `mcp-auth-proxy` sidecar). The authentication flow:

```
Client → markdown-vault-mcp (with OIDCProxy) → OIDC Provider (Authelia/Keycloak)
```

1. Client connects to the MCP server
2. Server redirects to the OIDC provider for authentication
3. Provider authenticates the user and returns a code
4. Server exchanges the code for tokens
5. Subsequent requests include the JWT token

## Docker Compose with OIDC

```yaml
services:
  markdown-vault-mcp:
    image: ghcr.io/pvliesdonk/markdown-vault-mcp:latest
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
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`)"
      - "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"
    networks:
      - traefik

volumes:
  index-data:
  embeddings-data:

networks:
  traefik:
    external: true
```

With the corresponding `.env`:

```bash
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-stable-hex-key
```
