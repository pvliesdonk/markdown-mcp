"""Tests for config.py — env var loading."""

from __future__ import annotations

from pathlib import Path

import logging

import pytest

from markdown_vault_mcp.config import CollectionConfig, get_log_level, load_config


class TestParseHelpers:
    """Test boolean and list parsing edge cases via load_config."""

    def test_bool_true_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "YES", " true "):
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", val)
            config = load_config()
            assert config.read_only is True, f"Expected True for {val!r}"

    def test_bool_false_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "False", "0", "no", "anything"):
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", val)
            config = load_config()
            assert config.read_only is False, f"Expected False for {val!r}"


class TestLoadConfig:
    def test_missing_source_dir_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", raising=False)
        with pytest.raises(ValueError, match="MARKDOWN_VAULT_MCP_SOURCE_DIR"):
            load_config()

    def test_minimal_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        # Clear all optional vars
        for var in (
            "MARKDOWN_VAULT_MCP_READ_ONLY",
            "MARKDOWN_VAULT_MCP_INDEX_PATH",
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
            "MARKDOWN_VAULT_MCP_STATE_PATH",
            "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
            "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
            "MARKDOWN_VAULT_MCP_EXCLUDE",
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL",
            "MARKDOWN_VAULT_MCP_GIT_USERNAME",
            "MARKDOWN_VAULT_MCP_GIT_TOKEN",
            "MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S",
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
        assert config.git_repo_url is None
        assert config.git_username == "x-access-token"
        assert config.git_token is None
        assert config.git_pull_interval_s == 600
        assert config.templates_folder == "_templates"

    def test_full_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/data/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", "/data/index.db")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", "/data/embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", "/data/state.json")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "cluster, topics")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title,cluster")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", ".obsidian/**, .trash/**")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL", "https://github.com/acme/vault.git"
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_USERNAME", "oauth2")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_TOKEN", "ghp_test123")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "300")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates")

        config = load_config()

        assert config.source_dir == Path("/data/vault")
        assert config.read_only is False
        assert config.index_path == Path("/data/index.db")
        assert config.embeddings_path == Path("/data/embeddings")
        assert config.state_path == Path("/data/state.json")
        assert config.indexed_frontmatter_fields == ["cluster", "topics"]
        assert config.required_frontmatter == ["title", "cluster"]
        assert config.exclude_patterns == [".obsidian/**", ".trash/**"]
        assert config.git_repo_url == "https://github.com/acme/vault.git"
        assert config.git_username == "oauth2"
        assert config.git_token == "ghp_test123"
        assert config.git_pull_interval_s == 300
        assert config.templates_folder == "Templates"

    def test_git_username_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_USERNAME", raising=False)
        config = load_config()
        assert config.git_username == "x-access-token"

    def test_templates_folder_trailing_slash_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates/")
        config = load_config()
        assert config.templates_folder == "Templates"

    def test_templates_folder_backslashes_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates\\Notes\\")
        config = load_config()
        assert config.templates_folder == "Templates/Notes"

    def test_templates_folder_slash_only_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "/")
        config = load_config()
        assert config.templates_folder == "_templates"

    def test_token_without_repo_url_logs_deprecation(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_TOKEN", "ghp_legacy")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_REPO_URL", raising=False)
        _ = load_config()
        assert "legacy mode is deprecated" in caplog.text

    def test_invalid_pull_interval_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "nope")
        config = load_config()
        assert config.git_pull_interval_s == 600

    def test_negative_pull_interval_disables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "-5")
        config = load_config()
        assert config.git_pull_interval_s == 0

    def test_comma_separated_strips_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", " a , b , c ")
        config = load_config()
        assert config.indexed_frontmatter_fields == ["a", "b", "c"]

    def test_empty_comma_list_yields_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "")
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
            git_token="ghp_secret",
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
            exclude_patterns=[".obsidian/**"],
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs["source_dir"] == Path("/tmp/vault")
        assert kwargs["read_only"] is False
        assert kwargs["index_path"] == Path("/tmp/index.db")
        assert kwargs["embeddings_path"] == Path("/tmp/emb")
        assert kwargs["state_path"] == Path("/tmp/state.json")
        assert kwargs["indexed_frontmatter_fields"] == ["cluster"]
        assert kwargs["required_frontmatter"] == ["title"]
        assert kwargs["exclude_patterns"] == [".obsidian/**"]
        assert kwargs["attachment_extensions"] is None
        assert kwargs["max_attachment_size_mb"] == 10.0
        assert kwargs["git_pull_interval_s"] == 0
        assert "git_strategy" in kwargs
        assert "on_write" in kwargs

    def test_managed_mode_wires_pull_and_on_write(self, tmp_path: Path) -> None:
        import subprocess

        bare = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )
        source_dir = tmp_path / "vault"
        source_dir.mkdir()

        config = CollectionConfig(
            source_dir=source_dir,
            git_repo_url=str(bare),
            git_token="ghp_secret",
            git_pull_interval_s=123,
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs["git_pull_interval_s"] == 123
        assert "git_strategy" in kwargs
        assert "on_write" in kwargs


class TestGitCommitterConfig:
    """Tests for git committer identity configuration."""

    def test_default_git_commit_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() uses default git_commit_name when env var is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", raising=False)
        config = load_config()
        assert config.git_commit_name == "markdown-vault-mcp"

    def test_default_git_commit_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() uses default git_commit_email when env var is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", raising=False)
        config = load_config()
        assert config.git_commit_email == "noreply@markdown-vault-mcp"

    def test_override_git_commit_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "MyBot")
        config = load_config()
        assert config.git_commit_name == "MyBot"

    def test_override_git_commit_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "bot@example.com")
        config = load_config()
        assert config.git_commit_email == "bot@example.com"

    def test_both_git_committer_vars_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() reads both GIT_COMMIT_NAME and GIT_COMMIT_EMAIL together."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "DeployBot")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "deploy@corp.local")
        config = load_config()
        assert config.git_commit_name == "DeployBot"
        assert config.git_commit_email == "deploy@corp.local"

    def test_empty_git_commit_name_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default when GIT_COMMIT_NAME is empty string."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "")
        config = load_config()
        assert config.git_commit_name == "markdown-vault-mcp"

    def test_empty_git_commit_email_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default when GIT_COMMIT_EMAIL is empty string."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "")
        config = load_config()
        assert config.git_commit_email == "noreply@markdown-vault-mcp"

    def test_config_dataclass_defaults(self) -> None:
        """CollectionConfig has correct default committer values."""
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.git_commit_name == "markdown-vault-mcp"
        assert config.git_commit_email == "noreply@markdown-vault-mcp"

    def test_config_dataclass_custom_values(self) -> None:
        """CollectionConfig accepts custom committer name and email."""
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_commit_name="CI",
            git_commit_email="ci@example.com",
        )
        assert config.git_commit_name == "CI"
        assert config.git_commit_email == "ci@example.com"

    def test_to_collection_kwargs_includes_commit_identity(self) -> None:
        """to_collection_kwargs() passes commit identity to GitWriteStrategy."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_token="ghp_test",
            git_commit_name="TestBot",
            git_commit_email="test@example.com",
        )
        kwargs = config.to_collection_kwargs()

        assert "on_write" in kwargs
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert kwargs["git_strategy"] is strategy
        assert strategy._commit_name == "TestBot"
        assert strategy._commit_email == "test@example.com"

    def test_to_collection_kwargs_with_default_identity(self) -> None:
        """to_collection_kwargs() uses defaults when no custom identity is set."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()

        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._commit_name == "markdown-vault-mcp"
        assert strategy._commit_email == "noreply@markdown-vault-mcp"


class TestAttachmentConfig:
    """Tests for attachment extension and size limit configuration."""

    def test_default_attachment_extensions_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() returns None attachment_extensions when env var not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", raising=False)
        config = load_config()
        assert config.attachment_extensions is None

    def test_attachment_extensions_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses ATTACHMENT_EXTENSIONS as comma-separated list."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png,docx")
        config = load_config()
        assert config.attachment_extensions == ["pdf", "png", "docx"]

    def test_attachment_extensions_wildcard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses ATTACHMENT_EXTENSIONS=* as ['*']."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "*")
        config = load_config()
        assert config.attachment_extensions == ["*"]

    def test_attachment_extensions_empty_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() returns None when ATTACHMENT_EXTENSIONS is empty."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "")
        config = load_config()
        assert config.attachment_extensions is None

    def test_default_max_attachment_size_mb(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() defaults max_attachment_size_mb to 10.0."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", raising=False)
        config = load_config()
        assert config.max_attachment_size_mb == 10.0

    def test_max_attachment_size_mb_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses MAX_ATTACHMENT_SIZE_MB from env var."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "25.5")
        config = load_config()
        assert config.max_attachment_size_mb == 25.5

    def test_max_attachment_size_mb_zero_disables_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() accepts 0 as a valid value for MAX_ATTACHMENT_SIZE_MB."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0")
        config = load_config()
        assert config.max_attachment_size_mb == 0.0

    def test_max_attachment_size_mb_invalid_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to 10.0 for invalid MAX_ATTACHMENT_SIZE_MB."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "not-a-number")
        config = load_config()
        assert config.max_attachment_size_mb == 10.0

    def test_max_attachment_size_mb_negative_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() resets negative MAX_ATTACHMENT_SIZE_MB to 10.0."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "-5")
        config = load_config()
        assert config.max_attachment_size_mb == 10.0

    def test_attachment_config_passed_through_to_collection_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """to_collection_kwargs() includes attachment_extensions and max_attachment_size_mb."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "5.0")
        config = load_config()
        kwargs = config.to_collection_kwargs()
        assert kwargs["attachment_extensions"] == ["pdf", "png"]
        assert kwargs["max_attachment_size_mb"] == 5.0


class TestGitLfsConfig:
    """Tests for GIT_LFS env var parsing."""

    def test_git_lfs_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults git_lfs to True when GIT_LFS is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_LFS", raising=False)
        config = load_config()
        assert config.git_lfs is True

    def test_git_lfs_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() parses GIT_LFS=false as False."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "false")
        config = load_config()
        assert config.git_lfs is False

    def test_git_lfs_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() parses GIT_LFS=true as True."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "true")
        config = load_config()
        assert config.git_lfs is True

    def test_git_lfs_passed_to_strategy(self, tmp_path: Path) -> None:
        """to_collection_kwargs() passes git_lfs to GitWriteStrategy."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=tmp_path,
            git_token="ghp_test",
            git_lfs=False,
        )
        kwargs = config.to_collection_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._git_lfs is False

    def test_git_lfs_default_true_in_strategy(self, tmp_path: Path) -> None:
        """to_collection_kwargs() passes git_lfs=True to strategy by default."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=tmp_path,
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._git_lfs is True


class TestGetLogLevel:
    """Tests for get_log_level() env var parsing."""

    def test_default_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_LOG_LEVEL", raising=False)
        assert get_log_level() == logging.INFO

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("DEBUG", 10),
            ("debug", 10),
            ("INFO", 20),
            ("WARNING", 30),
            ("ERROR", 40),
        ],
    )
    def test_valid_levels(
        self, monkeypatch: pytest.MonkeyPatch, value: str, expected: int
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_LOG_LEVEL", value)
        assert get_log_level() == expected

    def test_invalid_falls_back_to_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_LOG_LEVEL", "BOGUS")
        with caplog.at_level(logging.WARNING):
            level = get_log_level()
        assert level == logging.INFO
        assert "BOGUS" in caplog.text

    def test_empty_string_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_LOG_LEVEL", "")
        assert get_log_level() == logging.INFO

    def test_whitespace_only_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_LOG_LEVEL", "  ")
        assert get_log_level() == logging.INFO
