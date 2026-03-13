"""Embedding providers for markdown-vault-mcp.

Provides an :class:`EmbeddingProvider` ABC and three concrete implementations:

- :class:`OllamaProvider` — HTTP client to Ollama REST API.
- :class:`OpenAIProvider` — HTTP client to OpenAI Embeddings API.
- :class:`FastEmbedProvider` — local fastembed/ONNX runtime embeddings.

Use :func:`get_embedding_provider` to auto-detect and return the best
available provider based on environment variables.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from markdown_vault_mcp.config import _ENV_PREFIX

logger = logging.getLogger(__name__)

# Maximum texts per ONNX inference call inside FastEmbed.  The default (256)
# creates attention matrices that can require >192 GB with long chunks from
# models like nomic-embed-text-v1.5 (8192-token context).
_FASTEMBED_ONNX_BATCH_SIZE = 4


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding dimension size.

        Returns:
            Integer dimension of each embedding vector.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier for index compatibility metadata."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable model identifier for index compatibility metadata."""
        ...


class OllamaProvider(EmbeddingProvider):
    """Embedding provider backed by the Ollama REST API.

    Configuration via environment variables:

    - ``OLLAMA_HOST``: base URL of the Ollama server
      (default: ``http://localhost:11434``).
    - ``MARKDOWN_VAULT_MCP_OLLAMA_MODEL``: model name to use
      (default: ``nomic-embed-text``).
    - ``MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY``: set to ``true`` to force CPU-only
      inference (default: ``false``).
    """

    def __init__(self) -> None:
        """Initialise OllamaProvider from environment variables.

        Raises:
            ImportError: If ``httpx`` is not installed.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "OllamaProvider requires 'httpx'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings-api]'"
            ) from exc

        self._httpx = httpx
        self._host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self._model = os.environ.get(f"{_ENV_PREFIX}_OLLAMA_MODEL", "nomic-embed-text")
        cpu_only_raw = os.environ.get(f"{_ENV_PREFIX}_OLLAMA_CPU_ONLY", "false").lower()
        self._cpu_only = cpu_only_raw in ("1", "true", "yes")
        self._dimension: int | None = None

        logger.debug(
            "OllamaProvider initialised: host=%s model=%s cpu_only=%s",
            self._host,
            self._model,
            self._cpu_only,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the Ollama REST API.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            RuntimeError: If the Ollama API returns an error response.
        """
        payload: dict[str, object] = {"model": self._model, "input": texts}
        if self._cpu_only:
            payload["options"] = {"num_gpu": 0}

        url = f"{self._host}/api/embed"
        logger.debug("POST %s model=%s texts=%d", url, self._model, len(texts))

        with self._httpx.Client() as client:
            response = client.post(url, json=payload, timeout=30.0)

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama API error {response.status_code}: {response.text}"
            )

        data = response.json()
        embeddings: list[list[float]] = data["embeddings"]

        # Cache dimension from first successful call.
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])

        return embeddings

    @property
    def dimension(self) -> int:
        """Embedding dimension size.

        Embeds a test string on first access to determine the dimension.

        Returns:
            Integer dimension of each embedding vector.
        """
        if self._dimension is None:
            self.embed(["dimension probe"])
        if self._dimension is None:
            raise RuntimeError(
                "OllamaProvider.embed() returned no embeddings; "
                "cannot determine dimension."
            )
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model


class OpenAIProvider(EmbeddingProvider):
    """Embedding provider backed by the OpenAI Embeddings API.

    Configuration via environment variables:

    - ``OPENAI_API_KEY``: required API key.

    Uses the ``text-embedding-3-small`` model.
    """

    _MODEL = "text-embedding-3-small"
    _ENDPOINT = "https://api.openai.com/v1/embeddings"

    def __init__(self) -> None:
        """Initialise OpenAIProvider from environment variables.

        Raises:
            ImportError: If ``httpx`` is not installed.
            RuntimeError: If ``OPENAI_API_KEY`` is not set.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "OpenAIProvider requires 'httpx'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings-api]'"
            ) from exc

        self._httpx = httpx
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OpenAIProvider requires the OPENAI_API_KEY environment variable."
            )
        self._api_key = api_key
        self._dimension: int | None = None

        logger.debug("OpenAIProvider initialised: model=%s", self._MODEL)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the OpenAI Embeddings API.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors in input order.

        Raises:
            RuntimeError: If the OpenAI API returns an error response.
        """
        payload = {"input": texts, "model": self._MODEL}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "POST %s model=%s texts=%d", self._ENDPOINT, self._MODEL, len(texts)
        )

        with self._httpx.Client() as client:
            response = client.post(
                self._ENDPOINT, json=payload, headers=headers, timeout=30.0
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )

        data = response.json()
        # Sort by index to guarantee input order is preserved.
        items: list[dict] = sorted(data["data"], key=lambda d: d["index"])
        embeddings: list[list[float]] = [item["embedding"] for item in items]

        # Cache dimension from first successful call.
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])

        return embeddings

    @property
    def dimension(self) -> int:
        """Embedding dimension size.

        Embeds a test string on first access to determine the dimension.

        Returns:
            Integer dimension of each embedding vector.
        """
        if self._dimension is None:
            self.embed(["dimension probe"])
        if self._dimension is None:
            raise RuntimeError(
                "OpenAIProvider.embed() returned no embeddings; "
                "cannot determine dimension."
            )
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._MODEL


class FastEmbedProvider(EmbeddingProvider):
    """Embedding provider backed by the local fastembed library.

    The ``fastembed`` package is imported lazily at instantiation
    time so that it does not need to be installed unless this provider is used.
    """

    def __init__(
        self,
        model_name: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        """Initialise FastEmbed model.

        Args:
            model_name: FastEmbed model identifier.
            cache_dir: Optional model cache directory.

        Raises:
            ImportError: If ``fastembed`` is not installed.
        """
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise ImportError(
                "FastEmbedProvider requires 'fastembed'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            ) from exc

        self._model_name = model_name or os.environ.get(
            f"{_ENV_PREFIX}_FASTEMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5"
        )
        self._cache_dir = cache_dir or os.environ.get(
            f"{_ENV_PREFIX}_FASTEMBED_CACHE_DIR"
        )
        kwargs: dict[str, object] = {"model_name": self._model_name}
        if self._cache_dir:
            kwargs["cache_dir"] = self._cache_dir
        self._model = TextEmbedding(**kwargs)
        self._dimension: int | None = None
        logger.debug(
            "FastEmbedProvider initialised: model=%s cache_dir=%s",
            self._model_name,
            self._cache_dir,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the local fastembed model.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        vectors = [
            vector.tolist()
            for vector in self._model.embed(
                texts, batch_size=_FASTEMBED_ONNX_BATCH_SIZE
            )
        ]
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors

    @property
    def dimension(self) -> int:
        """Embedding dimension size from the loaded model.

        Returns:
            Integer dimension of each embedding vector.
        """
        if self._dimension is None:
            self.embed(["dimension probe"])
        if self._dimension is None:
            raise RuntimeError(
                "FastEmbedProvider.embed() returned no embeddings; "
                "cannot determine dimension."
            )
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "fastembed"

    @property
    def model_name(self) -> str:
        return self._model_name


def get_embedding_provider() -> EmbeddingProvider:
    """Auto-detect and return an embedding provider.

    Checks the ``EMBEDDING_PROVIDER`` environment variable first. When that
    variable is not set, probes for available providers in this order:

    1. If ``OPENAI_API_KEY`` is set → :class:`OpenAIProvider`.
    2. If Ollama is reachable at ``OLLAMA_HOST`` → :class:`OllamaProvider`.
    3. If ``fastembed`` can be imported →
       :class:`FastEmbedProvider`.
    4. Raises :class:`RuntimeError` with installation instructions.

    Returns:
        An initialised :class:`EmbeddingProvider` instance.

    Raises:
        RuntimeError: If no provider is available and ``EMBEDDING_PROVIDER``
            is not set, or if the explicitly requested provider cannot be
            initialised.
        ValueError: If ``EMBEDDING_PROVIDER`` is set to an unrecognised value.
    """
    explicit = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()

    if explicit == "openai":
        logger.info("Using OpenAIProvider (EMBEDDING_PROVIDER=openai)")
        return OpenAIProvider()

    if explicit == "ollama":
        logger.info("Using OllamaProvider (EMBEDDING_PROVIDER=ollama)")
        return OllamaProvider()

    if explicit == "fastembed":
        logger.info(
            "Using FastEmbedProvider (EMBEDDING_PROVIDER=%s)",
            explicit,
        )
        return FastEmbedProvider()

    if explicit:
        raise ValueError(
            f"Unrecognised EMBEDDING_PROVIDER value: {explicit!r}. "
            "Valid values: 'openai', 'ollama', 'fastembed'."
        )

    # Auto-detect: OpenAI API key present?
    if os.environ.get("OPENAI_API_KEY"):
        logger.info("Auto-detected OpenAIProvider (OPENAI_API_KEY is set)")
        return OpenAIProvider()

    # Auto-detect: Ollama reachable?
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{host}/api/tags")
        if response.status_code == 200:
            logger.info("Auto-detected OllamaProvider (Ollama reachable at %s)", host)
            return OllamaProvider()
    except Exception:
        logger.debug("Ollama not reachable at %s, skipping", host)

    # Auto-detect: fastembed importable?
    try:
        import fastembed  # noqa: F401

        logger.info("Auto-detected FastEmbedProvider")
        return FastEmbedProvider()
    except ImportError:
        logger.debug("fastembed not available, skipping")

    raise RuntimeError(
        "No embedding provider is available. Install one of:\n"
        "  pip install 'markdown-vault-mcp[embeddings-api]'  # httpx for Ollama or OpenAI\n"
        "  pip install 'markdown-vault-mcp[embeddings]'       # fastembed (local)\n"
        "Or set OPENAI_API_KEY for the OpenAI provider, "
        "or start an Ollama server for the Ollama provider."
    )
