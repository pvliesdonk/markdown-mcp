"""Shared test fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from markdown_vault_mcp.providers import EmbeddingProvider


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic provider for testing. Returns hash-based vectors."""

    def __init__(self, dim: int = 32) -> None:
        """Initialise with a fixed vector dimension.

        Args:
            dim: Embedding dimension. Defaults to 32.
        """
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic hash-based vectors for each text.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        vectors = []
        for text in texts:
            seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % 2**31
            rng = np.random.RandomState(seed)
            vec = rng.randn(self._dim).tolist()
            vectors.append(vec)
        return vectors

    @property
    def dimension(self) -> int:
        """Embedding dimension size.

        Returns:
            Integer dimension of each embedding vector.
        """
        return self._dim


@pytest.fixture
def fixtures_path() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_provider() -> MockEmbeddingProvider:
    """Mock embedding provider for testing."""
    return MockEmbeddingProvider()


@pytest.fixture
def vault_path(tmp_path: Path, fixtures_path: Path) -> Path:
    """Copy fixtures into a temp directory.

    Excludes ``invalid_utf8.md`` (non-UTF-8 bytes) so that Collection tests
    can scan the vault without errors.  ``malformed_yaml.md`` is included
    because ``scan_directory`` handles YAML parse errors gracefully.

    Returns:
        Path to the vault root inside ``tmp_path``.
    """
    import shutil

    vault = tmp_path / "vault"
    vault.mkdir()

    _EXCLUDED = {"invalid_utf8.md"}

    for src in fixtures_path.rglob("*.md"):
        if src.name in _EXCLUDED:
            continue
        rel = src.relative_to(fixtures_path)
        dest = vault / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    return vault
