# Git Integration

Use this guide to choose and configure the right git mode for your deployment.

## Modes

1. **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`)
   The server owns clone, periodic pull, commit, and deferred push.
2. **Unmanaged / commit-only** (no `GIT_REPO_URL`, existing git repo)
   The server stages and commits writes, but never pulls or pushes.
3. **No-git** (default)
   The vault is treated as a plain directory with no git operations.

## Managed Mode (Recommended For Containerized Deployments)

Use managed mode when the server should fully own git synchronization.

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/your-org/your-vault.git
MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
MARKDOWN_VAULT_MCP_GIT_TOKEN=github_pat_xxx
MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S=600
MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S=30
```

Behavior:

- If `SOURCE_DIR` is empty at startup, the server clones `GIT_REPO_URL` into it.
- If `SOURCE_DIR` is already a git repo, the server verifies `origin` matches `GIT_REPO_URL`.
- Writes are committed and pushed after the configured idle delay.
- Periodic pull uses fast-forward-only updates.

## Unmanaged / Commit-Only Mode

Use unmanaged mode when another process controls pull/push, but you still want MCP writes committed locally.

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
# No GIT_REPO_URL
# No GIT_TOKEN required
MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME=markdown-vault-mcp
MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL=noreply@markdown-vault-mcp
```

Behavior:

- If `SOURCE_DIR` is a git repo, writes are committed locally.
- No periodic pull.
- No push.

## No-Git Mode

Use no-git mode when you only need file persistence.

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
# No git env vars required
```

Behavior:

- Files are written to disk.
- No staging, commits, pulls, or pushes.

## Provider Username Reference

`MARKDOWN_VAULT_MCP_GIT_USERNAME` controls the HTTPS username prompt:

- GitHub: `x-access-token`
- GitLab: `oauth2`
- Bitbucket: account username

## Legacy Compatibility

`GIT_TOKEN` without `GIT_REPO_URL` still works for backward compatibility and logs a deprecation warning.
