"""Integration tests for mcp_server.py using FastMCP test client.

Tests exercise all MCP tools via the in-memory Client transport,
verifying end-to-end behaviour through the full Collection stack.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.mcp_server import _build_oidc_auth, create_server, get_collection

if TYPE_CHECKING:
    from pathlib import Path


def _parse_tool_data(result: Any) -> Any:
    """Extract data from a CallToolResult, handling FastMCP v2 serialization.

    FastMCP v2 serializes list[dict] as a single JSON TextContent blob.
    ``result.data`` works for simple types (dict, str, list[str]) but
    returns opaque ``Root()`` objects for list[dict].  This helper falls
    back to parsing the raw text content when needed.
    """
    data = result.data
    if isinstance(data, list) and data and not isinstance(data[0], (dict, str)):
        # Opaque Root objects — parse from raw text content.
        raw = result.content[0].text if result.content else "[]"
        return json.loads(raw)
    return data


_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
    "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
    "MARKDOWN_VAULT_MCP_EXCLUDE",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    # OIDC vars — ensure non-OIDC tests run unauthenticated
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
)


@pytest.fixture
def _mcp_env(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal env vars for create_server (read_only=true default)."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _mcp_env_writable(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars with read_only=false."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _mcp_env_with_fields(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars with indexed frontmatter fields."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    # Set after clearing so it's not wiped by _CLEAR_VARS.
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "cluster,tags")


# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------


class TestServerIdentity:
    """Verify SERVER_NAME and INSTRUCTIONS env vars are respected."""

    @pytest.mark.usefixtures("_mcp_env")
    def test_defaults_read_only(self) -> None:
        server = create_server()
        assert server.name == "markdown-vault-mcp"
        assert "READ-ONLY" in server.instructions
        assert "not available" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_defaults_read_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        server = create_server()
        assert "READ-WRITE" in server.instructions
        assert "'write'" in server.instructions
        assert "'edit'" in server.instructions
        assert "'rename'" in server.instructions
        assert "'delete'" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_default_instructions_content(self) -> None:
        server = create_server()
        assert "relative" in server.instructions
        assert "'search'" in server.instructions
        assert "'stats'" in server.instructions
        assert "MARKDOWN_VAULT_MCP_INSTRUCTIONS" in server.instructions

    @pytest.mark.usefixtures("_mcp_env")
    def test_custom_server_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "my-vault")
        server = create_server()
        assert server.name == "my-vault"

    @pytest.mark.usefixtures("_mcp_env")
    def test_custom_instructions_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
            "Personal notes vault. Read-only.",
        )
        server = create_server()
        assert server.instructions == "Personal notes vault. Read-only."


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestToolListing:
    """Verify correct tools are registered based on read_only setting."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_write_tools_absent_when_readonly(self) -> None:
        server = create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}

        # Read-only tools present
        assert "search" in names
        assert "read" in names
        assert "list_documents" in names
        assert "list_folders" in names
        assert "list_tags" in names
        assert "stats" in names
        assert "embeddings_status" in names
        assert "reindex" in names
        assert "build_embeddings" in names
        # Write tools absent when read_only=true (default)
        assert "write" not in names
        assert "edit" not in names
        assert "delete" not in names
        assert "rename" not in names

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_tools_present_when_writable(self) -> None:
        server = create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}

        # Write tools present when read_only=false
        assert "write" in names
        assert "edit" in names
        assert "delete" in names
        assert "rename" in names


class TestToolAnnotations:
    """Verify ToolAnnotations are set correctly per tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_annotations(self) -> None:
        server = create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            by_name = {t.name: t for t in tools}

        # Read-only tools
        for name in (
            "search",
            "read",
            "list_documents",
            "list_folders",
            "list_tags",
            "stats",
            "embeddings_status",
        ):
            ann = by_name[name].annotations
            assert ann is not None, f"{name} missing annotations"
            assert ann.readOnlyHint is True, f"{name} readOnlyHint"
            assert ann.destructiveHint is False, f"{name} destructiveHint"

        # Index management tools — not readOnly
        for name in ("reindex", "build_embeddings"):
            ann = by_name[name].annotations
            assert ann is not None
            assert ann.readOnlyHint is False, f"{name} readOnlyHint"

        # Write tools — not readOnly
        for name in ("write", "edit", "rename"):
            ann = by_name[name].annotations
            assert ann is not None
            assert ann.readOnlyHint is False, f"{name} readOnlyHint"
            assert ann.destructiveHint is False, f"{name} destructiveHint"

        # Delete is destructive
        ann = by_name["delete"].annotations
        assert ann is not None
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


class TestSearchTool:
    """Test the search MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_keyword_search(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "search", {"query": "simple document", "limit": 5}
            )
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        paths = {r["path"] for r in data}
        assert "simple.md" in paths

    @pytest.mark.usefixtures("_mcp_env")
    async def test_search_with_folder_filter(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "search",
                {"query": "subfolder nested", "folder": "subfolder"},
            )
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0, (
            "expected at least one result for 'subfolder nested' in subfolder"
        )
        for r in data:
            assert r["path"].startswith("subfolder/")


class TestReadTool:
    """Test the read MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_existing(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "simple.md"})
        data = result.data
        assert isinstance(data, dict)
        assert data["path"] == "simple.md"
        assert "Simple Document" in data["content"]

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_nonexistent(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("read", {"path": "nonexistent.md"})
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env")
    async def test_read_with_frontmatter(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "full_frontmatter.md"})
        data = result.data
        assert data["title"] == "Full Frontmatter Note"
        assert data["frontmatter"]["cluster"] == "fiction"


class TestListDocumentsTool:
    """Test the list_documents MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_all(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {})
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        paths = {d["path"] for d in data}
        assert "simple.md" in paths

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_by_folder(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {"folder": "subfolder"})
        data = _parse_tool_data(result)
        assert isinstance(data, list)
        assert len(data) > 0
        for doc in data:
            assert doc["folder"] == "subfolder" or doc["folder"].startswith(
                "subfolder/"
            )


class TestListFoldersTool:
    """Test the list_folders MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_list_folders(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_folders", {})
        folders = result.data
        assert isinstance(folders, list)
        assert "subfolder" in folders


class TestListTagsTool:
    """Test the list_tags MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_with_fields")
    async def test_list_tags(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_tags", {"field": "cluster"})
        tags = result.data
        assert isinstance(tags, list)
        assert "fiction" in tags


class TestStatsTool:
    """Test the stats MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_stats(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("stats", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["document_count"] > 0
        assert data["chunk_count"] > 0
        assert "semantic_search_available" in data


class TestEmbeddingsStatusTool:
    """Test the embeddings_status MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_embeddings_status_no_provider(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("embeddings_status", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["provider"] is None


class TestReindexTool:
    """Test the reindex MCP tool."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_reindex_no_changes(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("reindex", {})
        data = result.data
        assert isinstance(data, dict)
        assert data["added"] == 0
        assert data["modified"] == 0
        assert data["deleted"] == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test structured error responses for invalid operations."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_semantic_search_without_embeddings_returns_error(self) -> None:
        """search with mode='semantic' when no embeddings configured returns error."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "search", {"query": "test", "mode": "semantic"}
            )
        assert result.isError is True


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


class TestWriteTool:
    """Test the write MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_creates_document(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write", {"path": "new_note.md", "content": "# New\n\nBody.\n"}
            )
        data = result.data
        assert isinstance(data, dict)
        assert data["path"] == "new_note.md"
        assert data["created"] is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_overwrites_existing(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write", {"path": "simple.md", "content": "# Replaced\n"}
            )
        data = result.data
        assert data["created"] is False

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_write_with_frontmatter(self) -> None:
        """write tool with frontmatter parameter creates document and returns created=True."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write",
                {
                    "path": "fm_note.md",
                    "content": "# Frontmatter Note\n\nBody.\n",
                    "frontmatter": {"title": "Frontmatter Note", "tags": ["x", "y"]},
                },
            )
        data = result.data
        assert isinstance(data, dict)
        assert data["created"] is True
        assert data["path"] == "fm_note.md"


class TestEditTool:
    """Test the edit MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_patches_document(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "edit",
                {
                    "path": "simple.md",
                    "old_text": "Simple Document",
                    "new_text": "Updated Document",
                },
            )
        data = result.data
        assert data["path"] == "simple.md"
        assert data["replacements"] == 1

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_nonexistent_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {"path": "nonexistent.md", "old_text": "a", "new_text": "b"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_edit_conflict_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "edit",
                {"path": "simple.md", "old_text": "missing text", "new_text": "b"},
            )
        assert result.isError is True


class TestDeleteTool:
    """Test the delete MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_removes_document(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("delete", {"path": "simple.md"})
        data = result.data
        assert data["path"] == "simple.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_delete_nonexistent_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp("delete", {"path": "nonexistent.md"})
        assert result.isError is True


class TestRenameTool:
    """Test the rename MCP tool."""

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_moves_document(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "rename", {"old_path": "simple.md", "new_path": "renamed.md"}
            )
        data = result.data
        assert data["old_path"] == "simple.md"
        assert data["new_path"] == "renamed.md"

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_nonexistent_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "nonexistent.md", "new_path": "target.md"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_target_exists_returns_error(self) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "simple.md", "new_path": "no_frontmatter.md"},
            )
        assert result.isError is True

    @pytest.mark.usefixtures("_mcp_env_writable")
    async def test_rename_to_same_path_returns_error(self) -> None:
        """rename to same old_path and new_path should return an error."""
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool_mcp(
                "rename",
                {"old_path": "simple.md", "new_path": "simple.md"},
            )
        assert result.isError is True


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


class TestMCPExcludePatterns:
    """Test that MARKDOWN_VAULT_MCP_EXCLUDE env var is respected by the MCP server."""

    async def test_exclude_patterns_hides_subfolder_docs(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """list_documents does not return docs matching MARKDOWN_VAULT_MCP_EXCLUDE."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", "subfolder/**")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_VAULT_MCP_EXCLUDE":
                monkeypatch.delenv(var, raising=False)

        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {})

        data = _parse_tool_data(result)
        assert isinstance(data, list)
        paths = [d["path"] for d in data]

        # Root-level docs should be present.
        assert "simple.md" in paths
        # Subfolder docs should be excluded.
        assert not any(p.startswith("subfolder/") for p in paths)


# ---------------------------------------------------------------------------
# OIDC auth configuration
# ---------------------------------------------------------------------------

_OIDC_VARS = (
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
)

_OIDC_REQUIRED = {
    "MARKDOWN_VAULT_MCP_BASE_URL": "https://mcp.example.com",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL": "https://auth.example.com/.well-known/openid-configuration",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID": "test-client",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET": "test-secret",
}


class TestBuildOidcAuth:
    """Unit tests for _build_oidc_auth()."""

    @pytest.fixture(autouse=True)
    def _clear_oidc_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ensure OIDC env vars are absent before each test."""
        for var in _OIDC_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_returns_none_when_no_vars_set(self) -> None:
        assert _build_oidc_auth() is None

    @pytest.mark.parametrize(
        "missing_var",
        [
            "MARKDOWN_VAULT_MCP_BASE_URL",
            "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
            "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
            "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
        ],
    )
    def test_returns_none_when_one_required_var_missing(
        self, monkeypatch: pytest.MonkeyPatch, missing_var: str
    ) -> None:
        """Any one missing required var disables auth."""
        for var, val in _OIDC_REQUIRED.items():
            if var != missing_var:
                monkeypatch.setenv(var, val)
        assert _build_oidc_auth() is None

    def test_returns_non_none_when_all_required_vars_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            result = _build_oidc_auth()

        assert result is not None
        mock_cls.assert_called_once()

    def test_passes_required_kwargs_to_oidc_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        kw = mock_cls.call_args.kwargs
        assert kw["base_url"] == "https://mcp.example.com"
        assert (
            kw["config_url"]
            == "https://auth.example.com/.well-known/openid-configuration"
        )
        assert kw["client_id"] == "test-client"
        assert kw["client_secret"] == "test-secret"

    def test_default_required_scopes_is_openid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid"]

    def test_empty_required_scopes_falls_back_to_openid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicitly empty REQUIRED_SCOPES falls back to ['openid'], not []."""
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES", "")

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid"]

    def test_custom_required_scopes_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES", "openid, profile, email"
        )

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["required_scopes"] == [
            "openid",
            "profile",
            "email",
        ]

    def test_audience_forwarded_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_AUDIENCE", "my-api")

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["audience"] == "my-api"

    def test_audience_is_none_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["audience"] is None

    def test_jwt_signing_key_forwarded_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY", "deadbeef1234")

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["jwt_signing_key"] == "deadbeef1234"

    def test_jwt_signing_key_is_none_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            _build_oidc_auth()

        assert mock_cls.call_args.kwargs["jwt_signing_key"] is None

    def test_linux_warning_when_jwt_key_absent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.mcp_server.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            _build_oidc_auth()

        assert any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_no_warning_when_jwt_key_present_on_linux(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY", "some-key")

        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.mcp_server.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            _build_oidc_auth()

        assert not any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_no_warning_on_non_linux_without_jwt_key(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from unittest.mock import MagicMock, patch

        for var, val in _OIDC_REQUIRED.items():
            monkeypatch.setenv(var, val)

        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.mcp_server.sys") as mock_sys,
        ):
            mock_sys.platform = "darwin"
            _build_oidc_auth()

        assert not any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# MCP attachment tool tests
# ---------------------------------------------------------------------------


@pytest.fixture
def _mcp_env_writable_with_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Writable vault with a PDF and PNG attachment pre-created."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nSome content.\n", encoding="utf-8")
    (vault / "assets").mkdir()
    (vault / "assets" / "report.pdf").write_bytes(b"%PDF-1.4 fake content")
    (vault / "assets" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)

    return vault


class TestMCPReadAttachment:
    """MCP read() tool dispatches to attachment path for non-.md files."""

    async def test_read_attachment_returns_base64_content(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        import base64

        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/report.pdf"})
        data = result.data
        assert data["path"] == "assets/report.pdf"
        assert data["mime_type"] == "application/pdf"
        assert "size_bytes" in data
        assert "content_base64" in data
        decoded = base64.b64decode(data["content_base64"])
        assert decoded == b"%PDF-1.4 fake content"

    async def test_read_attachment_returns_mime_type(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("read", {"path": "assets/image.png"})
        assert result.data["mime_type"] == "image/png"

    async def test_read_attachment_missing_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read", {"path": "assets/missing.pdf"})


class TestMCPWriteAttachment:
    """MCP write() tool dispatches to attachment path for non-.md files."""

    async def test_write_attachment_creates_file(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        import base64

        raw = b"new pdf binary content"
        b64 = base64.b64encode(raw).decode("ascii")
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "write",
                {"path": "assets/new.pdf", "content_base64": b64},
            )
        data = result.data
        assert data["path"] == "assets/new.pdf"
        assert data["created"] is True
        assert (
            _mcp_env_writable_with_attachments / "assets" / "new.pdf"
        ).read_bytes() == raw

    async def test_write_attachment_missing_base64_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("write", {"path": "assets/new.pdf"})

    async def test_write_attachment_invalid_base64_raises(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "write",
                    {"path": "assets/new.pdf", "content_base64": "!!!invalid!!!"},
                )


class TestMCPListDocumentsAttachments:
    """MCP list_documents() with include_attachments flag."""

    async def test_list_documents_default_excludes_attachments(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("list_documents", {})
        items = _parse_tool_data(result)
        paths = [item["path"] for item in items]
        assert not any(p.endswith(".pdf") or p.endswith(".png") for p in paths)

    async def test_list_documents_include_attachments_returns_both(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "list_documents", {"include_attachments": True}
            )
        items = _parse_tool_data(result)
        kinds = {item.get("kind") for item in items}
        # All entries must carry a kind field
        assert "note" in kinds
        assert "attachment" in kinds
        paths = [item["path"] for item in items]
        assert any(p.endswith(".pdf") for p in paths)
        assert any(p.endswith(".png") for p in paths)
        assert "note.md" in paths

    async def test_list_documents_attachments_have_mime_type(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "list_documents", {"include_attachments": True}
            )
        items = _parse_tool_data(result)
        pdf_items = [i for i in items if i.get("path", "").endswith(".pdf")]
        assert len(pdf_items) >= 1
        assert pdf_items[0].get("mime_type") == "application/pdf"
        assert pdf_items[0].get("kind") == "attachment"


class TestMCPStatsAttachmentExtensions:
    """MCP stats() includes attachment_extensions field."""

    async def test_stats_includes_attachment_extensions(
        self, _mcp_env_writable_with_attachments: Path
    ) -> None:
        server = create_server()
        async with Client(server) as client:
            result = await client.call_tool("stats", {})
        data = result.data
        assert "attachment_extensions" in data
        assert isinstance(data["attachment_extensions"], list)
        assert "pdf" in data["attachment_extensions"]
