"""Integration tests for mcp_server.py using FastMCP test client.

Tests exercise all MCP tools via the in-memory Client transport,
verifying end-to-end behaviour through the full Collection stack.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from markdown_mcp.mcp_server import create_server

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
    "MARKDOWN_MCP_INDEX_PATH",
    "MARKDOWN_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_MCP_STATE_PATH",
    "MARKDOWN_MCP_INDEXED_FIELDS",
    "MARKDOWN_MCP_REQUIRED_FIELDS",
    "MARKDOWN_MCP_EXCLUDE",
    "MARKDOWN_MCP_GIT_TOKEN",
)


@pytest.fixture
def _mcp_env(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal env vars for create_server (read_only=true default)."""
    monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _mcp_env_writable(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars with read_only=false."""
    monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.setenv("MARKDOWN_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _mcp_env_with_fields(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars with indexed frontmatter fields."""
    monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    # Set after clearing so it's not wiped by _CLEAR_VARS.
    monkeypatch.setenv("MARKDOWN_MCP_INDEXED_FIELDS", "cluster,tags")


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
    """Test that MARKDOWN_MCP_EXCLUDE env var is respected by the MCP server."""

    async def test_exclude_patterns_hides_subfolder_docs(
        self,
        vault_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """list_documents does not return docs matching MARKDOWN_MCP_EXCLUDE."""
        monkeypatch.setenv("MARKDOWN_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_MCP_EXCLUDE", "subfolder/**")
        monkeypatch.delenv("MARKDOWN_MCP_READ_ONLY", raising=False)
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_MCP_EXCLUDE":
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
