# Embedding Providers

This guide covers configuring each supported embedding provider for semantic search. You only need one provider — choose based on your requirements:

| Provider | Runs locally | Requires GPU | Internet required | Install size | RAM during embedding |
|----------|-------------|-------------|-------------------|-------------|---------------------|
| [Ollama](#ollama) | Yes | No (CPU works fine) | No | ~2 GB (model) | ~2–4 GB (separate process) |
| [FastEmbed](#fastembed) | Yes | No | First run only (model download) | Small runtime + model | ~1–2 GB (in-process) |
| [OpenAI](#openai) | No (API call) | N/A | Yes | Minimal | Negligible |

All three providers produce embeddings that enable the `semantic` and `hybrid` search modes in the `search` tool.

## Ollama

[Ollama](https://ollama.com) runs embedding models locally. It's the recommended option for local, private embeddings — easy to set up and works well on CPU.

### Install Ollama

=== "macOS"

    ```bash
    brew install ollama
    ```

=== "Linux"

    ```bash
    curl -fsSL https://ollama.com/install.sh | sh
    ```

=== "Docker"

    If your vault server runs in Docker and Ollama runs on the host, no Ollama install inside the container is needed — just point to the host.

### Pull the embedding model

```bash
ollama pull nomic-embed-text
```

Verify it's available:

```bash
ollama list
```

You should see `nomic-embed-text` in the list.

### Configure

```bash
EMBEDDING_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
MARKDOWN_VAULT_MCP_OLLAMA_MODEL=nomic-embed-text
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

**CPU-only mode** — if you have a GPU but want to force CPU-only (e.g., to reserve the GPU for inference):

```bash
MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY=true
```

**Docker-to-host networking** — if Ollama runs on the host and the vault server runs in Docker:

=== "Docker Desktop (macOS/Windows)"

    ```bash
    OLLAMA_HOST=http://host.docker.internal:11434
    ```

=== "Linux (without Docker Desktop)"

    Add to your `compose.yml`:

    ```yaml
    services:
      markdown-vault-mcp:
        extra_hosts:
          - "host.docker.internal:host-gateway"
    ```

    Then use:

    ```bash
    OLLAMA_HOST=http://host.docker.internal:11434
    ```

### Verify

```bash
# Test Ollama is reachable
curl http://localhost:11434/api/tags

# Test embedding generation
curl http://localhost:11434/api/embeddings -d '{
  "model": "nomic-embed-text",
  "prompt": "test embedding"
}'
```

You should get a JSON response with an `embedding` array. After starting the vault server, use hybrid search:

> Search for "project planning" using hybrid mode

If embeddings are working, hybrid and semantic search modes will return results ranked by conceptual similarity.

---

## FastEmbed

[FastEmbed](https://github.com/qdrant/fastembed) runs ONNX embedding models directly in Python — no separate server needed.

### Install

```bash
pip install markdown-vault-mcp[embeddings]
```

Or with uv:

```bash
uv pip install markdown-vault-mcp[embeddings]
```

The `[all]` extra includes FastEmbed as well.

### Configure

```bash
EMBEDDING_PROVIDER=fastembed
MARKDOWN_VAULT_MCP_FASTEMBED_MODEL=nomic-ai/nomic-embed-text-v1.5
MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR=/path/to/store/fastembed-cache
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

That's it — no host URL or API key needed. The model downloads automatically on first use and is reused from cache after that.

!!! note "First startup downloads the model"
    Set `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` to a persistent location. In Docker, mount it as a named volume (for example `/data/fastembed`) to avoid re-downloading on container recreation.

!!! info "Memory usage — in-process vs out-of-process"
    FastEmbed runs the ONNX model **inside the Python process**, so the container itself bears the full inference memory cost. To keep this bounded, the server limits the ONNX-level batch size to 4 chunks per inference call (tunable via the `_FASTEMBED_ONNX_BATCH_SIZE` constant in `providers.py`).

    By contrast, Ollama runs inference in a **separate server process** — the Python container only sends HTTP requests and receives float vectors, so its own memory footprint stays low. If memory is tight (e.g., a small VPS), Ollama may be a better fit since its memory is isolated from the MCP server.

### Verify

Start the server and test with a search:

> Search for "meeting notes" using semantic mode

If FastEmbed is working, you'll get results ranked by semantic similarity even if the exact phrase doesn't appear in the documents.

---

## OpenAI

Uses the [OpenAI Embeddings API](https://platform.openai.com/docs/guides/embeddings) (`text-embedding-3-small` by default). Requires an API key and internet access. Lowest local resource usage, but sends document content to OpenAI.

### Get an API key

1. Go to [OpenAI API Keys](https://platform.openai.com/api-keys)
2. Create a new secret key
3. Copy it

### Configure

```bash
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-your-api-key-here
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

!!! warning "Privacy"
    Document content (titles, headings, body text) is sent to OpenAI for embedding. Do not use this provider if your vault contains sensitive data you don't want to share with OpenAI. Use Ollama or FastEmbed for fully local, private embeddings.

!!! tip "Cost"
    OpenAI embeddings are inexpensive. `text-embedding-3-small` costs $0.02 per million tokens. A vault of 1,000 notes (~500K tokens) costs about $0.01 to embed. Reindexing only processes changed documents.

### Verify

```bash
# Test your API key (replace $OPENAI_API_KEY with your key, or export it first)
curl https://api.openai.com/v1/embeddings \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "test", "model": "text-embedding-3-small"}'
```

You should get a JSON response with an embedding array. After starting the server, test hybrid search:

> Search for "project ideas" using hybrid mode

---

## Auto-detection

If you don't set `EMBEDDING_PROVIDER`, the server tries providers in this order:

1. **OpenAI** — if `OPENAI_API_KEY` is set
2. **Ollama** — if `OLLAMA_HOST` is reachable
3. **FastEmbed** — if the package is installed

Set `EMBEDDING_PROVIDER` explicitly to avoid surprises when your environment changes (e.g., setting `OPENAI_API_KEY` for another tool will cause the server to switch from Ollama to OpenAI).

## Common to all providers

Regardless of which provider you choose:

- **`MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` is required** to enable semantic search. Without it, only keyword search is available.
- Embeddings are built automatically on first startup when a provider is configured. Subsequent starts load the persisted index from disk and only process changed files.
- Use `mode="hybrid"` in search for best results — it combines keyword (BM25) and semantic (cosine similarity) scores using Reciprocal Rank Fusion.

!!! note "Large vaults"
    The initial embedding build uses two levels of batching to keep memory bounded:

    1. **Collection level** — 64 chunks per provider call (`_EMBEDDING_BATCH_SIZE` in `collection.py`)
    2. **ONNX level** (FastEmbed only) — 4 chunks per inference call (`_FASTEMBED_ONNX_BATCH_SIZE` in `providers.py`)

    The current ONNX batch size of 4 was chosen to prevent OOM on long-context models (e.g., `nomic-embed-text-v1.5` with 8192-token context). Larger batch sizes improve throughput but increase peak memory proportionally; smaller values have negligible impact on build time since the model computation dominates.

    For very large vaults (thousands of notes), the first startup may take several minutes. If the process is interrupted mid-build, it will rebuild from scratch on the next startup — partial indices are never persisted.
