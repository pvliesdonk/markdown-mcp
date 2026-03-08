"""Embedding providers for markdown-vault-mcp.

Provides an :class:`EmbeddingProvider` ABC and three concrete implementations:

- :class:`OllamaProvider` — HTTP client to Ollama REST API.
- :class:`OpenAIProvider` — HTTP client to OpenAI Embeddings API.
- :class:`SentenceTransformersProvider` — local sentence-transformers library.

Use :func:`get_embedding_provider` to auto-detect and return the best
available provider based on environment variables.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from markdown_vault_mcp.config import _ENV_PREFIX

logger = logging.getLogger(__name__)


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


class SentenceTransformersProvider(EmbeddingProvider):
    """Embedding provider backed by the local sentence-transformers library.

    The ``sentence_transformers`` package is imported lazily at instantiation
    time so that it does not need to be installed unless this provider is used.

    Default model: ``all-MiniLM-L6-v2``.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        """Load the sentence-transformers model.

        Args:
            model_name: Hugging Face model identifier to load.
                Defaults to ``all-MiniLM-L6-v2``.

        Raises:
            ImportError: If ``sentence_transformers`` is not installed.
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "SentenceTransformersProvider requires 'sentence-transformers'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            ) from exc

        self._model = SentenceTransformer(model_name)
        logger.debug("SentenceTransformersProvider initialised: model=%s", model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the local sentence-transformers model.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        return self._model.encode(texts).tolist()  # type: ignore[return-value]

    @property
    def dimension(self) -> int:
        """Embedding dimension size from the loaded model.

        Returns:
            Integer dimension of each embedding vector.
        """
        dim: int | None = self._model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(
                "SentenceTransformer.get_sentence_embedding_dimension() returned None; "
                "the model may not have been loaded correctly."
            )
        return dim


def get_embedding_provider() -> EmbeddingProvider:
    """Auto-detect and return an embedding provider.

    Checks the ``EMBEDDING_PROVIDER`` environment variable first. When that
    variable is not set, probes for available providers in this order:

    1. If ``OPENAI_API_KEY`` is set → :class:`OpenAIProvider`.
    2. If Ollama is reachable at ``OLLAMA_HOST`` → :class:`OllamaProvider`.
    3. If ``sentence_transformers`` can be imported →
       :class:`SentenceTransformersProvider`.
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

    if explicit in ("sentence-transformers", "sentence_transformers"):
        logger.info(
            "Using SentenceTransformersProvider (EMBEDDING_PROVIDER=%s)",
            explicit,
        )
        return SentenceTransformersProvider()

    if explicit:
        raise ValueError(
            f"Unrecognised EMBEDDING_PROVIDER value: {explicit!r}. "
            "Valid values: 'openai', 'ollama', 'sentence-transformers'."
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

    # Auto-detect: sentence_transformers importable?
    try:
        import sentence_transformers  # noqa: F401

        logger.info("Auto-detected SentenceTransformersProvider")
        return SentenceTransformersProvider()
    except ImportError:
        logger.debug("sentence_transformers not available, skipping")

    raise RuntimeError(
        "No embedding provider is available. Install one of:\n"
        "  pip install 'markdown-vault-mcp[embeddings-api]'  # httpx for Ollama or OpenAI\n"
        "  pip install 'markdown-vault-mcp[embeddings]'       # sentence-transformers (local)\n"
        "Or set OPENAI_API_KEY for the OpenAI provider, "
        "or start an Ollama server for the Ollama provider."
    )
