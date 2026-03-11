# OIDC Providers

This guide covers configuring markdown-vault-mcp with specific OIDC providers. For general OIDC setup and architecture, see [OIDC Authentication](../deployment/oidc.md).

!!! note "Transport requirement"
    OIDC requires HTTP transport (`--transport http`). It has no effect with stdio transport.

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

Or in Docker — see [Docker OIDC setup](docker.md#step-3-add-oidc-authentication).

### Verify

1. Open `https://mcp.example.com` in a browser
2. You should be redirected to Google's sign-in page
3. After signing in, you should be redirected back to the server

Check server logs for successful OIDC initialization. If you see errors:

- **"invalid_client"** — verify the Client ID and Client Secret match the Google console
- **"redirect_uri_mismatch"** — the `BASE_URL` + `/auth/callback` must exactly match the authorized redirect URI in Google console (including the scheme and trailing path)

---

## GitHub

Use GitHub as your OIDC identity provider to authenticate users with their GitHub accounts.

!!! warning "GitHub OAuth is not standard OIDC"
    GitHub OAuth Apps implement OAuth 2.0 but do **not** provide a standard OIDC discovery endpoint (`.well-known/openid-configuration`). This means GitHub cannot be used directly with markdown-vault-mcp's OIDC integration, which requires a compliant OIDC provider.

    **Recommended approach:** Use an OIDC-compliant identity broker that supports GitHub as a social login backend:

    - **[Authelia](https://www.authelia.com/)** — configure GitHub as an upstream identity provider
    - **[Keycloak](https://www.keycloak.org/)** — add GitHub as an Identity Provider under "Social" in the admin console
    - **[Authentik](https://goauthentik.io/)** — add a GitHub OAuth Source

    These brokers provide a compliant OIDC discovery endpoint that markdown-vault-mcp can use, while delegating actual authentication to GitHub.

### Example: Keycloak with GitHub social login

This example uses Keycloak as the OIDC broker with GitHub as the authentication backend.

#### 1. Create a GitHub OAuth App

1. Go to [GitHub Settings > Developer settings > OAuth Apps](https://github.com/settings/developers)
2. Click **New OAuth App**
3. Fill in:
    - **Application name:** `markdown-vault-mcp` (or any name)
    - **Homepage URL:** `https://auth.example.com`
    - **Authorization callback URL:** `https://auth.example.com/realms/your-realm/broker/github/endpoint`
4. Click **Register application**
5. Note the **Client ID** and generate a **Client Secret**

#### 2. Configure GitHub in Keycloak

1. In the Keycloak admin console, go to **Identity Providers** > **Add provider** > **GitHub**
2. Enter the GitHub OAuth App Client ID and Client Secret
3. Save

#### 3. Register a client for markdown-vault-mcp in Keycloak

1. Go to **Clients** > **Create client**
2. Set **Client ID** to `markdown-vault-mcp`
3. Set **Valid redirect URIs** to `https://mcp.example.com/auth/callback`
4. Enable **Client authentication** and note the client secret from the Credentials tab

#### 4. Configure environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/realms/your-realm/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-keycloak-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-64-char-hex-key
MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES=openid,email
```

Generate the JWT signing key:

```bash
openssl rand -hex 32
```

#### 5. Start the server

```bash
markdown-vault-mcp serve --transport http --port 8000
```

Or in Docker — see [Docker OIDC setup](docker.md#step-3-add-oidc-authentication).

### Verify

1. Open `https://mcp.example.com` in a browser
2. You should be redirected to Keycloak's login page, which shows a "GitHub" social login button
3. Click GitHub, authorize the app, and you should be redirected back to the server

Check server logs for successful authentication. If you see errors:

- **"invalid_client"** — verify the Client ID and Secret match the Keycloak client, not the GitHub OAuth App
- **"redirect_uri_mismatch"** — the callback URL in Keycloak must exactly match `BASE_URL` + `/auth/callback`

---

## General tips

These apply to all OIDC providers:

- **Always set `OIDC_JWT_SIGNING_KEY`** on Linux/Docker. The default ephemeral key invalidates all tokens on restart.
- **Test with a browser first.** The OIDC flow is easiest to debug in a browser where you can see redirects and error pages.
- **Check the discovery URL.** Visit `OIDC_CONFIG_URL` in a browser — it should return a JSON document with `authorization_endpoint`, `token_endpoint`, and other fields.
- **Redirect URI must match exactly.** The `BASE_URL` + `/auth/callback` must match the redirect URI registered with the provider, including scheme (`https://`), domain, port, and path.
