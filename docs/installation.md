# Installation

## From PyPI

```bash
pip install markdown-vault-mcp
```

With optional dependencies:

=== "MCP server"

    ```bash
    pip install markdown-vault-mcp[mcp]
    ```
    Adds FastMCP for running as an MCP server.

=== "API embeddings"

    ```bash
    pip install markdown-vault-mcp[embeddings-api]
    ```
    Adds httpx + numpy for Ollama/OpenAI embeddings via HTTP.

=== "All (recommended)"

    ```bash
    pip install markdown-vault-mcp[all]
    ```
    MCP + API embeddings. Lightweight — no PyTorch.

=== "All + local models"

    ```bash
    pip install markdown-vault-mcp[all-local]
    ```
    Includes sentence-transformers + PyTorch for local CPU/GPU embeddings without Ollama.

!!! info "`[all]` vs `[all-local]`"
    The `[all]` extra is lightweight and does **not** include `sentence-transformers` or PyTorch. Use `[all-local]` if you want local CPU/GPU embeddings without an Ollama server. The Docker image uses `[all]`.

## Using uv

```bash
uv pip install markdown-vault-mcp[all]
```

## From Source

```bash
git clone https://github.com/pvliesdonk/markdown-vault-mcp.git
cd markdown-vault-mcp
pip install -e ".[all,dev]"
```

## Docker

```bash
docker pull ghcr.io/pvliesdonk/markdown-vault-mcp:latest
```

The Docker image uses `[all]` (MCP + API embeddings). It does **not** include `sentence-transformers` or PyTorch — use Ollama or OpenAI for embeddings. For local sentence-transformers, build from source with `[all-local]`.

See [Docker deployment](deployment/docker.md) for compose setup and volume configuration.

## Verify Installation

```bash
# Check the CLI is available
markdown-vault-mcp --help

# Quick test with a local vault
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/markdown/files
markdown-vault-mcp search "hello world"
```
