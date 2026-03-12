# Claude Desktop

This guide walks through three progressive setups for using markdown-vault-mcp with [Claude Desktop](https://claude.ai/download):

1. **Basic** — read-only keyword search, no external services
2. **Git write support** — enable write/edit/delete with auto-commit
3. **Semantic search** — add embedding-based search for better results

Each step builds on the previous one. Start with Step 1 and add features as needed.

## Step 1: Basic read-only setup

**Goal:** Connect a local Obsidian vault to Claude Desktop with keyword search.

**Prerequisites:** Python 3.10+, Claude Desktop installed.

### Install

```bash
pip install markdown-vault-mcp[all]
```

Or with uv:

```bash
uv tool install markdown-vault-mcp[all]
```

### Configure Claude Desktop

Edit your Claude Desktop configuration file:

=== "macOS"

    `~/Library/Application Support/Claude/claude_desktop_config.json`

=== "Windows"

    `%APPDATA%\Claude\claude_desktop_config.json`

=== "Linux"

    `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/path/to/store/index.db"
      }
    }
  }
}
```

Replace `/path/to/your/ObsidianVault` with the actual path to your vault.

!!! tip "Persist the index"
    Setting `MARKDOWN_VAULT_MCP_INDEX_PATH` stores the FTS5 index on disk. Without it, the index is built in memory on every startup. With it, only changed files are reindexed.

!!! tip "Exclude Obsidian internals"
    `MARKDOWN_VAULT_MCP_EXCLUDE` keeps `.obsidian/` config files and `.trash/` out of search results. Add any other directories you want to skip (e.g., `_templates/**`).

### Restart Claude Desktop

Quit and reopen Claude Desktop. The server tools should appear in Claude's tool list.

### Verify it works

In a Claude Desktop conversation, ask:

> Search my vault for "meeting notes"

Claude should use the `search` tool and return matching documents from your vault. If you get no results, verify that `MARKDOWN_VAULT_MCP_SOURCE_DIR` points to a directory containing `.md` files.

---

## Step 2: Enable git write support

**Goal:** Allow Claude to create, edit, delete, and rename notes in managed git mode — with every change auto-committed and pushed.

**Prerequisites:** Step 1 complete. Your vault directory must be a git repository with an HTTPS remote configured.

### Create a GitHub Personal Access Token

1. Go to [GitHub Settings > Developer settings > Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)
2. Set repository access to your vault repository only
3. Grant **Contents: Read and write** permission
4. Copy the token (starts with `github_pat_`)

### Update the configuration

Add the highlighted lines to your existing config:

```json hl_lines="8-12"
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/ObsidianVault",
        "MARKDOWN_VAULT_MCP_READ_ONLY": "false",
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL": "https://github.com/your-org/your-vault.git",
        "MARKDOWN_VAULT_MCP_GIT_USERNAME": "x-access-token",
        "MARKDOWN_VAULT_MCP_GIT_TOKEN": "github_pat_your_token_here",
        "MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S": "60",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/path/to/store/index.db"
      }
    }
  }
}
```

**What these do:**

- `READ_ONLY=false` — enables the write, edit, delete, and rename tools
- `GIT_REPO_URL` — enables managed mode (clone/remote validation)
- `GIT_USERNAME` / `GIT_TOKEN` — HTTPS auth for pull/push
- `GIT_PUSH_DELAY_S=60` — batches rapid writes, pushing after 60 seconds of idle time

!!! warning "Token security"
    The token is stored in plain text in your Claude Desktop config. Use a fine-grained token scoped to the single vault repository with minimal permissions.

### Restart and verify

Restart Claude Desktop, then ask:

> Create a new note at "test/hello.md" with the content "Hello from Claude!"

Claude should use the `write` tool. Check your git log to confirm the commit:

```bash
cd /path/to/your/ObsidianVault
git log --oneline -3
```

You should see a commit from `markdown-vault-mcp`. Delete the test note when done:

> Delete the note at "test/hello.md"

---

## Step 3: Add semantic search

**Goal:** Enable embedding-based search alongside keyword search for better recall on conceptual queries.

**Prerequisites:** Step 1 complete. One of: [Ollama](https://ollama.com) running locally, an OpenAI API key, or `sentence-transformers` installed (see [Embeddings guide](embeddings.md) for details on each).

This example uses Ollama — the easiest option for local, private embeddings.

### Install and start Ollama

```bash
# Install Ollama (macOS)
brew install ollama

# Pull the embedding model
ollama pull nomic-embed-text

# Ollama runs automatically after install; verify:
curl http://localhost:11434/api/tags
```

### Update the configuration

Add the highlighted lines:

```json hl_lines="9-12"
{
  "mcpServers": {
    "my-vault": {
      "command": "markdown-vault-mcp",
      "args": ["serve"],
      "env": {
        "MARKDOWN_VAULT_MCP_SOURCE_DIR": "/path/to/your/ObsidianVault",
        "MARKDOWN_VAULT_MCP_SERVER_NAME": "my-vault",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH": "/path/to/store/embeddings",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_HOST": "http://localhost:11434",
        "MARKDOWN_VAULT_MCP_OLLAMA_MODEL": "nomic-embed-text",
        "MARKDOWN_VAULT_MCP_EXCLUDE": ".obsidian/**,.trash/**",
        "MARKDOWN_VAULT_MCP_INDEX_PATH": "/path/to/store/index.db"
      }
    }
  }
}
```

**What these do:**

- `EMBEDDINGS_PATH` — where to persist embedding vectors on disk (required to enable semantic search)
- `EMBEDDING_PROVIDER=ollama` — use Ollama for embeddings (auto-detected if omitted, but explicit is clearer)
- `OLLAMA_HOST` — Ollama server URL (default `http://localhost:11434`)
- `OLLAMA_MODEL` — embedding model (default `nomic-embed-text`)

!!! note "First startup is slower"
    The first startup with embeddings builds vectors for every document. Subsequent starts only process changed files.

### Restart and verify

Restart Claude Desktop, then ask:

> Search my vault for notes about "project planning and task management" using hybrid mode

Claude should use the `search` tool with `mode="hybrid"`. Hybrid search combines keyword (BM25) and semantic (cosine similarity) results using Reciprocal Rank Fusion, giving better results for conceptual queries.

Compare with keyword-only:

> Search my vault for "project planning" using keyword mode

Hybrid mode should return more conceptually related notes, even if they don't contain the exact phrase.
