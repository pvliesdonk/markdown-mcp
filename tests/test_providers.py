"""Tests for embedding providers in markdown_vault_mcp.providers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from markdown_vault_mcp.providers import (
    OllamaProvider,
    OpenAIProvider,
    SentenceTransformersProvider,
    get_embedding_provider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_httpx_mock(
    status_code: int = 200,
    json_body: dict | None = None,
    text: str = "",
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_client, mock_response) with context-manager wiring."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text
    mock_response.json.return_value = json_body or {}

    mock_client = MagicMock()
    mock_client.__enter__ = lambda _: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    mock_client.get.return_value = mock_response

    return mock_client, mock_response


# ---------------------------------------------------------------------------
# OllamaProvider tests
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    def test_embed_posts_to_correct_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embed() sends a POST to {OLLAMA_HOST}/api/embed."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1, 0.2, 0.3]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            result = provider.embed(["hello"])

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:11434/api/embed"
        assert result == [[0.1, 0.2, 0.3]]

    def test_embed_payload_includes_model_and_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() includes model and input in POST JSON payload."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "test-model")
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.5, 0.6]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["alpha", "beta"])

        _, call_kwargs = mock_client.post.call_args
        payload = call_kwargs["json"]
        assert payload["model"] == "test-model"
        assert payload["input"] == ["alpha", "beta"]

    def test_embed_cpu_only_includes_num_gpu_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() adds options.num_gpu=0 when OLLAMA_CPU_ONLY=true."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "true")
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[1.0, 2.0]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        payload = call_kwargs["json"]
        assert payload.get("options") == {"num_gpu": 0}

    def test_embed_cpu_only_false_no_options_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() does not include options key when cpu_only is false."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "false")
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[1.0, 2.0]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        payload = call_kwargs["json"]
        assert "options" not in payload

    def test_embed_raises_on_non_200_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() raises RuntimeError when the API returns a non-200 status."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(status_code=503, text="Service Unavailable")
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            with pytest.raises(RuntimeError, match="503"):
                provider.embed(["hello"])

    def test_dimension_triggers_embed_on_first_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dimension property calls embed() if not yet cached."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(
            json_body={"embeddings": [[0.1, 0.2, 0.3, 0.4]]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            dim = provider.dimension

        assert dim == 4
        mock_client.post.assert_called_once()

    def test_dimension_cached_after_first_embed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dimension property uses cached value after the first embed() call."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1, 0.2, 0.3]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["prime"])
            _ = provider.dimension
            _ = provider.dimension

        # embed() was called explicitly once; dimension probe should not add more calls
        assert mock_client.post.call_count == 1

    def test_ollama_host_env_var_changes_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OLLAMA_HOST env var changes the base URL used for POST requests."""
        monkeypatch.setenv("OLLAMA_HOST", "http://remote-host:12345")
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.9]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["x"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://remote-host:12345/api/embed"

    def test_ollama_host_trailing_slash_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OLLAMA_HOST trailing slash is stripped so the URL is clean."""
        monkeypatch.setenv("OLLAMA_HOST", "http://myhost:9999/")
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["y"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://myhost:9999/api/embed"

    def test_ollama_model_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MARKDOWN_VAULT_MCP_OLLAMA_MODEL env var sets the model name."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "my-custom-model")
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.3, 0.4]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"]["model"] == "my-custom-model"

    def test_missing_httpx_raises_import_error(self) -> None:
        """OllamaProvider raises ImportError with helpful message if httpx not found."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "httpx":
                raise ImportError("No module named 'httpx'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(ImportError, match="httpx"),
        ):
            OllamaProvider()


# ---------------------------------------------------------------------------
# OpenAIProvider tests
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def test_init_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """__init__ raises RuntimeError when OPENAI_API_KEY is not set."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with (
            patch("httpx.Client"),
            pytest.raises(RuntimeError, match="OPENAI_API_KEY"),
        ):
            OpenAIProvider()

    def test_embed_sends_bearer_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embed() includes Authorization: Bearer <key> header."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-123")
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            provider.embed(["hello"])

        _, call_kwargs = mock_client.post.call_args
        headers = call_kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-test-key-123"

    def test_embed_sorts_by_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embed() sorts results by index field, preserving input order."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # Return embeddings out-of-order: index 1 first, then index 0
        mock_client, _ = _make_httpx_mock(
            json_body={
                "data": [
                    {"index": 1, "embedding": [9.0, 8.0]},
                    {"index": 0, "embedding": [1.0, 2.0]},
                ]
            }
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            result = provider.embed(["first", "second"])

        assert result[0] == [1.0, 2.0]
        assert result[1] == [9.0, 8.0]

    def test_embed_raises_on_non_200_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() raises RuntimeError when API returns a non-200 status."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client, _ = _make_httpx_mock(status_code=401, text="Unauthorized")
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            with pytest.raises(RuntimeError, match="401"):
                provider.embed(["secret"])

    def test_dimension_caches_after_first_embed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dimension property is cached after the first embed() call."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            # First explicit embed populates cache
            provider.embed(["probe"])
            dim1 = provider.dimension
            dim2 = provider.dimension

        assert dim1 == 3
        assert dim2 == 3
        # Only one HTTP call should have been made (from explicit embed)
        assert mock_client.post.call_count == 1

    def test_dimension_triggers_embed_when_uncached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dimension property calls embed() when cache is cold."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.5, 0.6, 0.7, 0.8]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            dim = provider.dimension

        assert dim == 4
        mock_client.post.assert_called_once()

    def test_embed_posts_to_openai_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed() posts to the official OpenAI embeddings endpoint."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            provider.embed(["hello"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "https://api.openai.com/v1/embeddings"


# ---------------------------------------------------------------------------
# SentenceTransformersProvider tests
# ---------------------------------------------------------------------------


class TestSentenceTransformersProvider:
    def _make_mock_st(self, dim: int = 2) -> MagicMock:
        """Return a mock SentenceTransformer instance."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1] * dim, [0.2] * dim])
        mock_model.get_sentence_embedding_dimension.return_value = dim
        return mock_model

    def _st_module_mock(self, mock_model: MagicMock) -> MagicMock:
        """Return a mock sentence_transformers module that vends mock_model.

        Using patch.dict("sys.modules") rather than patch("sentence_transformers.X")
        means the test works even when the real library is not installed.
        """
        mock_module = MagicMock()
        mock_module.SentenceTransformer.return_value = mock_model
        return mock_module

    def test_embed_returns_list_of_lists(self) -> None:
        """embed() converts numpy output to a list-of-lists."""
        mock_model = self._make_mock_st(dim=3)
        mock_model.encode.return_value = np.array([[1.0, 2.0, 3.0]])

        with patch.dict(
            "sys.modules", {"sentence_transformers": self._st_module_mock(mock_model)}
        ):
            provider = SentenceTransformersProvider()
            result = provider.embed(["single text"])

        assert isinstance(result, list)
        assert isinstance(result[0], list)
        assert result[0] == pytest.approx([1.0, 2.0, 3.0])

    def test_dimension_returns_model_dimension(self) -> None:
        """dimension returns the value from get_sentence_embedding_dimension()."""
        mock_model = self._make_mock_st(dim=7)
        with patch.dict(
            "sys.modules", {"sentence_transformers": self._st_module_mock(mock_model)}
        ):
            provider = SentenceTransformersProvider()
            assert provider.dimension == 7

    def test_missing_sentence_transformers_raises(self) -> None:
        """SentenceTransformersProvider raises ImportError if library not found."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(ImportError, match="sentence-transformers"),
        ):
            SentenceTransformersProvider()

    def test_embed_passes_texts_to_encode(self) -> None:
        """embed() calls model.encode() with the exact input texts."""
        mock_model = self._make_mock_st(dim=2)
        with patch.dict(
            "sys.modules", {"sentence_transformers": self._st_module_mock(mock_model)}
        ):
            provider = SentenceTransformersProvider()
            provider.embed(["foo", "bar", "baz"])

        mock_model.encode.assert_called_once_with(["foo", "bar", "baz"])


# ---------------------------------------------------------------------------
# get_embedding_provider() tests
# ---------------------------------------------------------------------------


class TestGetEmbeddingProvider:
    def _ollama_mock_client(self, reachable: bool = True) -> MagicMock:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda _: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_response = MagicMock()
        mock_response.status_code = 200 if reachable else 503
        mock_client.get.return_value = mock_response
        return mock_client

    def test_explicit_openai_returns_openai_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EMBEDDING_PROVIDER=openai returns OpenAIProvider."""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("httpx.Client"):
            provider = get_embedding_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_explicit_ollama_returns_ollama_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EMBEDDING_PROVIDER=ollama returns OllamaProvider."""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        with patch("httpx.Client"):
            provider = get_embedding_provider()
        assert isinstance(provider, OllamaProvider)

    def test_explicit_sentence_transformers_hyphen(self) -> None:
        """EMBEDDING_PROVIDER=sentence-transformers returns SentenceTransformersProvider."""
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.return_value = mock_model
        with (
            patch.dict(os.environ, {"EMBEDDING_PROVIDER": "sentence-transformers"}),
            patch.dict("sys.modules", {"sentence_transformers": mock_st_module}),
        ):
            provider = get_embedding_provider()
        assert isinstance(provider, SentenceTransformersProvider)

    def test_explicit_sentence_transformers_underscore(self) -> None:
        """EMBEDDING_PROVIDER=sentence_transformers (underscore) also works."""
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.return_value = mock_model
        with (
            patch.dict(os.environ, {"EMBEDDING_PROVIDER": "sentence_transformers"}),
            patch.dict("sys.modules", {"sentence_transformers": mock_st_module}),
        ):
            provider = get_embedding_provider()
        assert isinstance(provider, SentenceTransformersProvider)

    def test_explicit_unknown_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EMBEDDING_PROVIDER=unknown_value raises ValueError."""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "unknown_value")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Unrecognised"):
            get_embedding_provider()

    def test_autodetect_openai_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auto-detect: OPENAI_API_KEY set returns OpenAIProvider."""
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-autodetect")
        with patch("httpx.Client"):
            provider = get_embedding_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_autodetect_ollama_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Auto-detect: Ollama reachable returns OllamaProvider."""
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)

        # First httpx.Client call: GET /api/tags probe (reachable)
        # Second httpx.Client call: OllamaProvider instantiation (no-op)
        probe_client = self._ollama_mock_client(reachable=True)
        ollama_client = MagicMock()
        ollama_client.__enter__ = lambda _: ollama_client
        ollama_client.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", side_effect=[probe_client, ollama_client]):
            provider = get_embedding_provider()

        assert isinstance(provider, OllamaProvider)

    def test_autodetect_ollama_unreachable_falls_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auto-detect: Ollama raises exception → falls through to next provider."""
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.return_value = mock_model

        def raise_on_get(*_args, **_kwargs):
            raise ConnectionError("refused")

        probe_client = MagicMock()
        probe_client.__enter__ = lambda _: probe_client
        probe_client.__exit__ = MagicMock(return_value=False)
        probe_client.get.side_effect = raise_on_get

        with (
            patch("httpx.Client", return_value=probe_client),
            patch.dict("sys.modules", {"sentence_transformers": mock_st_module}),
        ):
            provider = get_embedding_provider()

        assert isinstance(provider, SentenceTransformersProvider)

    def test_autodetect_no_providers_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auto-detect: no providers available raises RuntimeError."""
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)

        probe_client = MagicMock()
        probe_client.__enter__ = lambda _: probe_client
        probe_client.__exit__ = MagicMock(return_value=False)
        probe_client.get.side_effect = ConnectionError("refused")

        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *args, **kwargs)

        with (
            patch("httpx.Client", return_value=probe_client),
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(RuntimeError, match="No embedding provider"),
        ):
            get_embedding_provider()
