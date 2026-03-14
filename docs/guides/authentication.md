# Authentication

This guide covers how to protect your markdown-vault-mcp server with authentication. Choose the mode that fits your deployment.

!!! warning "Transport requirement"
    Authentication only works with HTTP transport (`--transport http` or `sse`). It has no effect with `--transport stdio`.

## Auth modes

The server supports three authentication modes, resolved in order of precedence:

| Priority | Mode | When to use | Configuration |
|----------|------|-------------|---------------|
| 1 | **Bearer token** | Simple deployments behind a VPN, Docker compose stacks, development | Set `MARKDOWN_VAULT_MCP_BEARER_TOKEN` |
| 2 | **OIDC** | Production with user identity, SSO, multi-user access | Set all four OIDC variables |
| 3 | **No auth** | Local stdio usage, trusted networks | Default (nothing to configure) |

If both bearer token and OIDC are configured, bearer token wins and a warning is logged.

---

## Bearer token

The simplest way to protect your server. A single static token shared between server and clients.

### Setup

1. Generate a random token:

    ```bash
    openssl rand -hex 32
    ```

2. Set the environment variable:

    ```bash
    MARKDOWN_VAULT_MCP_BEARER_TOKEN=your-generated-token
    ```

3. Start the server with HTTP transport:

    ```bash
    markdown-vault-mcp serve --transport http --port 8000
    ```

### Client usage

Clients must include the token in every request:

```
Authorization: Bearer your-generated-token
```

### When to use bearer token

- Deployments behind a VPN or firewall
- Docker compose stacks where services communicate internally
- Development and testing environments
- Any scenario where full OIDC is overkill

See also: [`examples/bearer-auth.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/bearer-auth.env) for a ready-to-use example.

---

## OIDC

Full OAuth 2.1 authentication using an external identity provider. Supports user login flows, SSO, and multi-user access control.

### How it works

The server uses FastMCP's built-in `OIDCProxy` — no external auth sidecar needed:

```
Client → markdown-vault-mcp (OIDCProxy) → OIDC Provider
```

1. Client connects to the server
2. Server redirects to the OIDC provider for login
3. Provider authenticates the user and returns a code
4. Server exchanges the code for tokens
5. Subsequent requests include the JWT

### Required variables

| Variable | Description |
|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Public base URL (e.g. `https://mcp.example.com`) |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery endpoint |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | Client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | Client secret |

### Optional variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | ephemeral | JWT signing key — **required on Linux/Docker** |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | — | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | `openid` | Comma-separated required scopes |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | `false` | Set `true` to verify the access token as a JWT instead of the id token; useful for audience-claim validation on JWT access tokens |

!!! danger "JWT signing key on Linux/Docker"
    Without `OIDC_JWT_SIGNING_KEY`, FastMCP generates an ephemeral key that invalidates all tokens on restart. Always set a stable key in production:

    ```bash
    openssl rand -hex 32
    ```

!!! tip "Long-running sessions"
    Configure `offline_access` scope on your identity provider to enable refresh tokens. Combined with long token lifetimes, this prevents session drops during extended use. See [Session drops after token expiry](#session-drops-after-token-expiry) in Troubleshooting.

### Provider guides

For step-by-step setup with specific providers:

- [Authelia](oidc-providers.md#authelia)
- [Keycloak](oidc-providers.md#keycloak)
- [Google](oidc-providers.md#google)
- [GitHub (via Keycloak broker)](oidc-providers.md#github)

For the full OIDC reference (env vars, Docker Compose, subpath deployments, architecture):

- [OIDC Authentication reference](../deployment/oidc.md)

---

## Troubleshooting

### "invalid client" error

The `client_id` and/or `redirect_uris` in your OIDC provider config don't match the values in your `.env` file. Verify both sides match exactly.

### Tokens invalidated after restart

You're missing `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY`. Without it, FastMCP generates an ephemeral key on each startup. Generate and set a stable key:

```bash
openssl rand -hex 32
```

### Auth has no effect

Authentication only works with HTTP transport. If you're using `--transport stdio`, auth is silently ignored. Switch to `--transport http`.

### Bearer token not working

- Verify the env var is set and non-empty (whitespace-only values are ignored)
- Check that clients send `Authorization: Bearer <token>` (not `Basic` or other schemes)
- If OIDC is also configured, bearer token takes precedence — check logs for the warning

### OIDC redirect fails

- Verify `BASE_URL` matches your public URL exactly (including any subpath prefix)
- For subpath deployments, see the [subpath deployment guide](../deployment/oidc.md#subpath-deployments) — `BASE_URL` must include the prefix, `HTTP_PATH` must not
- Check that `redirect_uris` in your provider config includes your callback URL (e.g., `https://mcp.example.com/auth/callback`)

### Session drops after token expiry

**Symptom:** the MCP client works for a few hours, then starts returning 401 errors or stops responding. Restarting the client fixes it temporarily.

**Root cause:** MCP clients (Claude.ai, Claude Code) do not reliably re-authenticate when both access and refresh tokens expire. This is a known ecosystem-wide issue:

- Claude Code ignores `scopes_supported` and never requests `offline_access`, so no refresh token is issued ([claude-code#7744](https://github.com/anthropics/claude-code/issues/7744))
- The MCP Python SDK can deadlock during token refresh inside an active SSE stream ([python-sdk#1326](https://github.com/modelcontextprotocol/python-sdk/issues/1326))

**Workaround:** configure long token lifetimes on your identity provider so tokens outlast a typical session. For example, set access tokens to 8 hours and refresh tokens to 30 days. Also include `offline_access` in the scopes configured on the provider side — even though current clients may not request it, future client updates may use it.

See the [Authelia provider guide](oidc-providers.md#authelia) for specific configuration.

### Opaque access tokens (Authelia)

Authelia issues opaque (non-JWT) access tokens. This is handled automatically — the server verifies the `id_token` instead. No extra configuration needed. See the [Authelia guide](oidc-providers.md#authelia) for details.
