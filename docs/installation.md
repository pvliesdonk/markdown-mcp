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

=== "Local embeddings"

    ```bash
    pip install markdown-vault-mcp[embeddings]
    ```
    Adds FastEmbed + numpy for local embeddings.

=== "All (recommended)"

    ```bash
    pip install markdown-vault-mcp[all]
    ```
    MCP + FastEmbed + API embeddings.

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

The Docker image uses `[all]` (MCP + FastEmbed + API embeddings). Semantic search is available by default with FastEmbed and can switch to Ollama/OpenAI when configured.

See [Docker deployment](deployment/docker.md) for compose setup and volume configuration.

## Verify Installation

```bash
# Check the CLI is available
markdown-vault-mcp --help

# Quick test with a local vault
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/your/markdown/files
markdown-vault-mcp search "hello world"
```
