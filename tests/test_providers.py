"""Tests for embedding providers in markdown_vault_mcp.providers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp.providers import (
    FastEmbedProvider,
    OllamaProvider,
    OpenAIProvider,
    get_embedding_provider,
)


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


class TestOllamaProvider:
    def test_embed_posts_to_correct_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1, 0.2, 0.3]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            result = provider.embed(["hello"])

        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["model"] == "nomic-embed-text"
        assert result == [[0.1, 0.2, 0.3]]
        assert provider.provider_name == "ollama"
        assert provider.model_name == "nomic-embed-text"

    def test_embed_raises_on_non_200_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(status_code=503, text="Service Unavailable")
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            with pytest.raises(RuntimeError, match="503"):
                provider.embed(["hello"])

    def test_dimension_triggers_embed_on_first_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        mock_client, _ = _make_httpx_mock(
            json_body={"embeddings": [[0.1, 0.2, 0.3, 0.4]]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            assert provider.dimension == 4

    def test_ollama_model_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "my-custom-model")
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.3, 0.4]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider()
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"]["model"] == "my-custom-model"
        assert provider.model_name == "my-custom-model"


class TestOpenAIProvider:
    def test_init_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with (
            patch("httpx.Client"),
            pytest.raises(RuntimeError, match="OPENAI_API_KEY"),
        ):
            OpenAIProvider()

    def test_embed_sends_bearer_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-123")
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider()
            provider.embed(["hello"])

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-key-123"
        assert provider.provider_name == "openai"
        assert provider.model_name == "text-embedding-3-small"

    def test_embed_sorts_by_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
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
        assert result == [[1.0, 2.0], [9.0, 8.0]]


class TestFastEmbedProvider:
    def test_embed_uses_fastembed_model_and_cache_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5"
        )
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR", "/tmp/fastembed-cache"
        )

        vec = MagicMock()
        vec.tolist.return_value = [0.1, 0.2, 0.3]
        model_instance = MagicMock()
        model_instance.embed.return_value = [vec]
        module = MagicMock()
        module.TextEmbedding.return_value = model_instance

        with patch.dict("sys.modules", {"fastembed": module}):
            provider = FastEmbedProvider()
            result = provider.embed(["hello"])

        module.TextEmbedding.assert_called_once_with(
            model_name="nomic-ai/nomic-embed-text-v1.5",
            cache_dir="/tmp/fastembed-cache",
        )
        assert result == [[0.1, 0.2, 0.3]]
        assert provider.dimension == 3
        assert provider.provider_name == "fastembed"
        assert provider.model_name == "nomic-ai/nomic-embed-text-v1.5"

    def test_missing_fastembed_raises(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fastembed":
                raise ImportError("No module named 'fastembed'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(ImportError, match="fastembed"),
        ):
            FastEmbedProvider()

    def test_dimension_raises_on_empty_embeddings(self) -> None:
        model_instance = MagicMock()
        model_instance.embed.return_value = []
        module = MagicMock()
        module.TextEmbedding.return_value = model_instance

        with patch.dict("sys.modules", {"fastembed": module}):
            provider = FastEmbedProvider()
            with pytest.raises(RuntimeError, match="cannot determine dimension"):
                _ = provider.dimension


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
        monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("httpx.Client"):
            provider = get_embedding_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_explicit_ollama_returns_ollama_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        with patch("httpx.Client"):
            provider = get_embedding_provider()
        assert isinstance(provider, OllamaProvider)

    def test_explicit_fastembed_returns_fastembed_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
        module = MagicMock()
        module.TextEmbedding.return_value = MagicMock(embed=lambda *_: [])
        with patch.dict("sys.modules", {"fastembed": module}):
            provider = get_embedding_provider()
        assert isinstance(provider, FastEmbedProvider)

    def test_explicit_unknown_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "unknown_value")
        with pytest.raises(ValueError, match="Valid values: 'openai', 'ollama', 'fastembed'"):
            get_embedding_provider()

    def test_autodetect_openai_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-autodetect")
        with patch("httpx.Client"):
            provider = get_embedding_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_autodetect_ollama_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        probe_client = self._ollama_mock_client(reachable=True)
        ollama_client = MagicMock()
        ollama_client.__enter__ = lambda _: ollama_client
        ollama_client.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", side_effect=[probe_client, ollama_client]):
            provider = get_embedding_provider()
        assert isinstance(provider, OllamaProvider)

    def test_autodetect_fastembed_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)

        probe_client = MagicMock()
        probe_client.__enter__ = lambda _: probe_client
        probe_client.__exit__ = MagicMock(return_value=False)
        probe_client.get.side_effect = ConnectionError("refused")

        module = MagicMock()
        module.TextEmbedding.return_value = MagicMock(embed=lambda *_: [])

        with (
            patch("httpx.Client", return_value=probe_client),
            patch.dict("sys.modules", {"fastembed": module}),
        ):
            provider = get_embedding_provider()
        assert isinstance(provider, FastEmbedProvider)

    def test_autodetect_no_providers_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
            if name == "fastembed":
                raise ImportError("No module named 'fastembed'")
            return real_import(name, *args, **kwargs)

        with (
            patch("httpx.Client", return_value=probe_client),
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(RuntimeError, match="No embedding provider"),
        ):
            get_embedding_provider()
