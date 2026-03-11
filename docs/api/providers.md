# Embedding Providers

The `providers` module defines an abstract base class for embedding providers and three concrete implementations for Ollama, OpenAI, and Sentence Transformers.

## Quick Start

```python
from markdown_vault_mcp.providers import get_embedding_provider

# Auto-detect based on environment variables
provider = get_embedding_provider()

# Embed a batch of texts
vectors = provider.embed(["hello world", "example text"])
print(f"Dimension: {provider.dimension}")
```

## Provider Selection

The `get_embedding_provider()` function auto-detects the best available provider:

1. **Ollama** — if `OLLAMA_HOST` is reachable
2. **OpenAI** — if `OPENAI_API_KEY` is set
3. **Sentence Transformers** — if the package is installed

Override with `EMBEDDING_PROVIDER=ollama|openai|sentence-transformers`.

## API Reference

::: markdown_vault_mcp.providers.EmbeddingProvider
    options:
      members:
        - embed
        - dimension

::: markdown_vault_mcp.providers.OllamaProvider

::: markdown_vault_mcp.providers.OpenAIProvider

::: markdown_vault_mcp.providers.SentenceTransformersProvider

::: markdown_vault_mcp.providers.get_embedding_provider
