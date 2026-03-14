# OIDC Providers

This guide covers configuring markdown-vault-mcp with specific OIDC providers. For general OIDC setup and architecture, see [OIDC Authentication](../deployment/oidc.md).

!!! note "Transport requirement"
    OIDC requires HTTP transport (`--transport http`). It has no effect with stdio transport.

!!! note "Subpath deployments"
    If your reverse proxy mounts the server under a prefix (for example `https://mcp.example.com/vault`), set `MARKDOWN_VAULT_MCP_BASE_URL` to that prefixed URL and register callback URI `https://mcp.example.com/vault/auth/callback`.

## Authelia

Use Authelia as your OIDC identity provider to authenticate users with local users, LDAP, or upstream providers.

!!! note
    Authelia does not support Dynamic Client Registration (RFC 7591). Register clients manually in `configuration.yml`.

!!! note "Opaque access tokens"
    Authelia issues opaque (non-JWT) access tokens. markdown-vault-mcp handles this automatically by verifying the `id_token` instead, which is always a standard JWT. No extra configuration is needed.

For architecture details and a full Docker Compose deployment, see [OIDC Authentication](../deployment/oidc.md).

### 1. Register client in `configuration.yml`

Add the client and a custom lifespan under `identity_providers.oidc` in your Authelia config:

```yaml
identity_providers:
  oidc:
    # Custom lifespan for long-running MCP sessions.
    # Default Authelia lifespans: access_token=1h, refresh_token=90m.
    # MCP clients do not reliably re-authenticate after token expiry,
    # so longer lifetimes prevent session drops.
    lifespans:
      custom:
        mcp_long_lived:
          access_token: '8h'
          refresh_token: '30d'
    clients:
      - client_id: markdown-vault-mcp
        client_name: markdown-vault-mcp
        client_secret: '$pbkdf2-sha512$...'  # generated in step 2
        public: false
        authorization_policy: two_factor
        lifespan: 'mcp_long_lived'
        redirect_uris:
          - https://mcp.example.com/auth/callback
        grant_types:
          - authorization_code
          - refresh_token
        response_types:
          - code
        scopes:
          - openid
          - profile
          - email
          - offline_access
        userinfo_signed_response_alg: none
        token_endpoint_auth_method: client_secret_post
        pkce_challenge_method: S256
```

!!! tip "Token lifetimes"
    MCP clients (Claude.ai, Claude Code) do not reliably re-authenticate when tokens expire. The `mcp_long_lived` custom lifespan sets `access_token` to 8 hours and `refresh_token` to 30 days so tokens outlast a typical session. See [Authelia OIDC Provider — Lifespans](https://www.authelia.com/configuration/identity-providers/openid-connect/provider/#lifespans) for the full reference.

### 2. Generate and hash a client secret

Generate a secret (example):

```bash
openssl rand -base64 32
```

Hash the secret for Authelia:

```bash
authelia crypto hash generate pbkdf2
```

Enter the secret at the prompt. Set the hashed value in `configuration.yml` as `client_secret`. Keep the plain-text secret for `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET`.

### 3. Generate JWT signing key

```bash
openssl rand -hex 32
```

### 4. Configure environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-64-char-hex-key
MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES=openid,profile,email,offline_access
```

### 5. Start the server

```bash
markdown-vault-mcp serve --transport http --port 8000
```

Or in Docker — see [Docker OIDC setup](docker.md#step-4-add-oidc-authentication).

### Verify

1. Open `https://mcp.example.com` in a browser
2. Confirm redirect to Authelia login
3. Sign in and confirm redirect back to markdown-vault-mcp
4. Confirm authenticated requests succeed from your MCP client

If login fails:

- **"invalid_client"**: Check client ID/secret values and confirm the Authelia hash was generated from the same plain-text secret.
- **Redirect mismatch**: Ensure redirect URI matches exactly (`BASE_URL` + `/auth/callback`).
- **Session drops after hours**: Check that the `mcp_long_lived` lifespan is configured and referenced by the client. See the [token lifetime troubleshooting](authentication.md#session-drops-after-token-expiry).

---

## Keycloak

Use Keycloak directly as your OIDC provider for username/password (or federated) authentication.

### 1. Create a realm

1. Open the Keycloak admin console
2. Create a new realm (for example `vault`)
3. Switch to the new realm before creating clients

### 2. Create a client with redirect URI

1. Go to **Clients** and click **Create client**
2. Set **Client ID** to `markdown-vault-mcp`
3. Set **Valid redirect URIs** to `https://mcp.example.com/auth/callback`
4. Enable **Client authentication** for a confidential client
5. Save and copy the client secret from the **Credentials** tab

Keycloak discovery URL format:

```text
https://auth.example.com/realms/{realm}/.well-known/openid-configuration
```

### 3. Configure environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/realms/vault/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-keycloak-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-64-char-hex-key
MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES=openid,profile,email
```

Generate the JWT signing key:

```bash
openssl rand -hex 32
```

### 4. Start the server

```bash
markdown-vault-mcp serve --transport http --port 8000
```

Or in Docker — see [Docker OIDC setup](docker.md#step-4-add-oidc-authentication).

### Verify

1. Open `https://mcp.example.com` in a browser
2. Confirm redirect to the Keycloak login page
3. Sign in and confirm redirect back to markdown-vault-mcp
4. Confirm your MCP client can access tools after login

If login fails:

- **"invalid_client"**: Verify Client ID/secret from the Keycloak client.
- **Redirect mismatch**: Ensure `BASE_URL` + `/auth/callback` exactly matches Keycloak client settings.

---

## Google

Use Google as your OIDC identity provider to authenticate users with their Google accounts.

### 1. Create OAuth credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Select or create a project
3. Click **Create Credentials** > **OAuth client ID**
4. Choose **Web application**
5. Set the **Authorized redirect URI** to:

    ```
    https://mcp.example.com/auth/callback
    ```

    Replace `mcp.example.com` with your server's public domain.

6. Click **Create** and note the **Client ID** and **Client Secret**

!!! tip "Consent screen"
    If this is a new project, Google will prompt you to configure the OAuth consent screen first. For internal use, choose **Internal** (Google Workspace) or **External** with test users.

### 2. Configure environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://accounts.google.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=123456789-abcdef.apps.googleusercontent.com
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=GOCSPX-your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-64-char-hex-key
MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES=openid,email
```

Generate the JWT signing key:

```bash
openssl rand -hex 32
```

### 3. Start the server

```bash
markdown-vault-mcp serve --transport http --port 8000
```

Or in Docker — see [Docker OIDC setup](docker.md#step-4-add-oidc-authentication).

### Verify

1. Open `https://mcp.example.com` in a browser
2. You should be redirected to Google's sign-in page
3. After signing in, you should be redirected back to the server

Check server logs for successful OIDC initialization. If you see errors:

- **"invalid_client"** — verify the Client ID and Client Secret match the Google console
- **"redirect_uri_mismatch"** — the `BASE_URL` + `/auth/callback` must exactly match the authorized redirect URI in Google console (including the scheme and trailing path)
- For prefixed deployments, this means `BASE_URL` should include the prefix, e.g. `https://mcp.example.com/vault`.

---

## GitHub

Use GitHub as an authentication backend through an OIDC-compliant broker.

!!! warning "GitHub OAuth is not standard OIDC"
    GitHub OAuth Apps implement OAuth 2.0 but do **not** provide a standard OIDC discovery endpoint (`.well-known/openid-configuration`). GitHub cannot be used directly with markdown-vault-mcp's OIDC integration.

### Recommended approach

Use Keycloak as the OIDC broker and GitHub as a social login provider:

1. Complete [Keycloak](#keycloak) setup first (realm, client, env vars, server startup)
2. In Keycloak, add **Identity Providers** > **GitHub** with your GitHub OAuth App credentials
3. Login at Keycloak and choose GitHub as the upstream identity provider

This keeps Keycloak as the OIDC provider for markdown-vault-mcp while delegating user auth to GitHub.

Other compatible brokers also work, including [Authelia](https://www.authelia.com/) and [Authentik](https://goauthentik.io/). Keycloak is shown here because the setup flow is already documented in this guide.

### Verify

1. Open `https://mcp.example.com` in a browser
2. You should be redirected to Keycloak's login page with a GitHub option
3. Click GitHub, authorize, and confirm redirect back to markdown-vault-mcp

---

## General tips

These apply to all OIDC providers:

- **Always set `OIDC_JWT_SIGNING_KEY`** on Linux/Docker. The default ephemeral key invalidates all tokens on restart.
- **Test with a browser first.** The OIDC flow is easiest to debug in a browser where you can see redirects and error pages.
- **Check the discovery URL.** Visit `OIDC_CONFIG_URL` in a browser — it should return a JSON document with `authorization_endpoint`, `token_endpoint`, and other fields.
- **Redirect URI must match exactly.** The `BASE_URL` + `/auth/callback` must match the redirect URI registered with the provider, including scheme (`https://`), domain, port, and path.

### JWT vs opaque access tokens

OIDC providers issue two tokens after login: an **access token** and an **id token**. The id token is always a standard JWT. The access token may be a JWT (Keycloak, Google) or an opaque string (Authelia).

By default, markdown-vault-mcp verifies the **id token**, which works with all providers. If your provider issues JWT access tokens and you need audience-claim validation on that token, set:

```bash
MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN=true
```

Most deployments should leave this unset.
