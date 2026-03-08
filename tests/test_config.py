"""Tests for config.py — env var loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from markdown_mcp.config import CollectionConfig, load_config


class TestParseHelpers:
    """Test boolean and list parsing edge cases via load_config."""

    def test_bool_true_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "YES", " true "):
            monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_MCP_READ_ONLY", val)
            config = load_config()
            assert config.read_only is True, f"Expected True for {val!r}"

    def test_bool_false_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "False", "0", "no", "anything"):
            monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_MCP_READ_ONLY", val)
            config = load_config()
            assert config.read_only is False, f"Expected False for {val!r}"


class TestLoadConfig:
    def test_missing_source_dir_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARKDOWN_MCP_SOURCE_DIR", raising=False)
        with pytest.raises(ValueError, match="MARKDOWN_MCP_SOURCE_DIR"):
            load_config()

    def test_minimal_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", "/tmp/vault")
        # Clear all optional vars
        for var in (
            "MARKDOWN_MCP_READ_ONLY",
            "MARKDOWN_MCP_INDEX_PATH",
            "MARKDOWN_MCP_EMBEDDINGS_PATH",
            "MARKDOWN_MCP_STATE_PATH",
            "MARKDOWN_MCP_INDEXED_FIELDS",
            "MARKDOWN_MCP_REQUIRED_FIELDS",
            "MARKDOWN_MCP_EXCLUDE",
            "MARKDOWN_MCP_GIT_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)

        config = load_config()

        assert config.source_dir == Path("/tmp/vault")
        assert config.read_only is True  # default
        assert config.index_path is None
        assert config.embeddings_path is None
        assert config.state_path is None
        assert config.indexed_frontmatter_fields is None
        assert config.required_frontmatter is None
        assert config.exclude_patterns is None
        assert config.git_token is None

    def test_full_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", "/data/vault")
        monkeypatch.setenv("MARKDOWN_MCP_READ_ONLY", "false")
        monkeypatch.setenv("MARKDOWN_MCP_INDEX_PATH", "/data/index.db")
        monkeypatch.setenv("MARKDOWN_MCP_EMBEDDINGS_PATH", "/data/embeddings")
        monkeypatch.setenv("MARKDOWN_MCP_STATE_PATH", "/data/state.json")
        monkeypatch.setenv("MARKDOWN_MCP_INDEXED_FIELDS", "cluster, topics")
        monkeypatch.setenv("MARKDOWN_MCP_REQUIRED_FIELDS", "title,cluster")
        monkeypatch.setenv("MARKDOWN_MCP_EXCLUDE", ".obsidian/**, .trash/**")
        monkeypatch.setenv("MARKDOWN_MCP_GIT_TOKEN", "ghp_test123")

        config = load_config()

        assert config.source_dir == Path("/data/vault")
        assert config.read_only is False
        assert config.index_path == Path("/data/index.db")
        assert config.embeddings_path == Path("/data/embeddings")
        assert config.state_path == Path("/data/state.json")
        assert config.indexed_frontmatter_fields == ["cluster", "topics"]
        assert config.required_frontmatter == ["title", "cluster"]
        assert config.exclude_patterns == [".obsidian/**", ".trash/**"]
        assert config.git_token == "ghp_test123"

    def test_comma_separated_strips_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_MCP_INDEXED_FIELDS", " a , b , c ")
        config = load_config()
        assert config.indexed_frontmatter_fields == ["a", "b", "c"]

    def test_empty_comma_list_yields_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_MCP_INDEXED_FIELDS", "")
        config = load_config()
        assert config.indexed_frontmatter_fields is None


class TestToCollectionKwargs:
    def test_includes_exclude_patterns(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            exclude_patterns=[".obsidian/**"],
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs["exclude_patterns"] == [".obsidian/**"]
        assert kwargs["source_dir"] == Path("/tmp/vault")

    def test_excludes_git_token(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()
        assert "git_token" not in kwargs

    def test_includes_all_collection_params(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            read_only=False,
            index_path=Path("/tmp/index.db"),
            embeddings_path=Path("/tmp/emb"),
            state_path=Path("/tmp/state.json"),
            indexed_frontmatter_fields=["cluster"],
            required_frontmatter=["title"],
            exclude_patterns=[".trash/**"],
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs == {
            "source_dir": Path("/tmp/vault"),
            "read_only": False,
            "index_path": Path("/tmp/index.db"),
            "embeddings_path": Path("/tmp/emb"),
            "state_path": Path("/tmp/state.json"),
            "indexed_frontmatter_fields": ["cluster"],
            "required_frontmatter": ["title"],
            "exclude_patterns": [".trash/**"],
        }
