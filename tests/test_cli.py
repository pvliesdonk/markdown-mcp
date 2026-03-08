"""Tests for cli.py — argument parsing and subcommand dispatch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp.cli import _build_parser, main


class TestBuildParser:
    """Test argument parser construction."""

    def test_no_command_exits(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_serve_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"
        assert args.transport == "stdio"

    def test_serve_sse_transport(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve", "--transport", "sse"])
        assert args.transport == "sse"

    def test_index_command(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["index"])
        assert args.command == "index"
        assert args.force is False

    def test_index_force_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["index", "--force"])
        assert args.force is True

    def test_search_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["search", "hello world"])
        assert args.command == "search"
        assert args.query == "hello world"
        assert args.limit == 10
        assert args.mode == "keyword"
        assert args.folder is None
        assert args.json is False

    def test_search_all_options(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            [
                "search",
                "test query",
                "-n",
                "5",
                "-m",
                "hybrid",
                "--folder",
                "Journal",
                "--json",
            ]
        )
        assert args.query == "test query"
        assert args.limit == 5
        assert args.mode == "hybrid"
        assert args.folder == "Journal"
        assert args.json is True

    def test_index_source_dir_and_index_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["index", "--source-dir", "/data/vault", "--index-path", "/data/idx.db"]
        )
        assert args.source_dir == "/data/vault"
        assert args.index_path == "/data/idx.db"

    def test_search_source_dir(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["search", "query", "--source-dir", "/data/vault"])
        assert args.source_dir == "/data/vault"

    def test_reindex_command(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reindex"])
        assert args.command == "reindex"

    def test_reindex_source_dir_and_index_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["reindex", "--source-dir", "/data/vault", "--index-path", "/data/idx.db"]
        )
        assert args.source_dir == "/data/vault"
        assert args.index_path == "/data/idx.db"

    def test_verbose_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["-v", "index"])
        assert args.verbose is True


class TestMainDispatch:
    """Test main() dispatches to the correct subcommand handler."""

    def test_no_command_exits(self) -> None:
        with (
            patch("sys.argv", ["markdown-vault-mcp"]),
            pytest.raises(SystemExit, match="2"),
        ):
            main()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_index_dispatch(self, mock_commands: MagicMock) -> None:
        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with patch("sys.argv", ["markdown-vault-mcp", "index"]):
            main()
        mock_commands.__getitem__.assert_called_once_with("index")
        mock_handler.assert_called_once()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_valueerror_exits_with_message(self, mock_commands: MagicMock) -> None:
        mock_handler = MagicMock(side_effect=ValueError("SOURCE_DIR not set"))
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with (
            patch("sys.argv", ["markdown-vault-mcp", "index"]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_serve_dispatch(self, mock_commands: MagicMock) -> None:
        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with patch("sys.argv", ["markdown-vault-mcp", "serve"]):
            main()
        mock_commands.__getitem__.assert_called_once_with("serve")
        mock_handler.assert_called_once()


class TestCmdIndex:
    """Test the index subcommand."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_prints_stats(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 42
        mock_stats.chunks_indexed = 128
        mock_collection.build_index.return_value = mock_stats
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index"]):
            main()

        mock_collection.build_index.assert_called_once_with(force=False)
        captured = capsys.readouterr()
        assert "42 documents" in captured.out
        assert "128 chunks" in captured.out
        mock_collection.build_index.assert_called_once_with(force=False)

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_valueerror_exits_with_message(
        self,
        mock_build: MagicMock,
    ) -> None:
        mock_build.side_effect = ValueError("MARKDOWN_VAULT_MCP_SOURCE_DIR is required")

        with (
            patch("sys.argv", ["markdown-vault-mcp", "index"]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_force_propagates(
        self,
        mock_build: MagicMock,
    ) -> None:
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 10
        mock_stats.chunks_indexed = 30
        mock_collection.build_index.return_value = mock_stats
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index", "--force"]):
            main()

        mock_collection.build_index.assert_called_once_with(force=True)


class TestCmdSearch:
    """Test the search subcommand."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_search_text_output(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_result = MagicMock()
        mock_result.path = "notes/test.md"
        mock_result.title = "Test Note"
        mock_result.score = 0.9876

        mock_collection = MagicMock()
        mock_collection.search.return_value = [mock_result]
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "test"]):
            main()

        captured = capsys.readouterr()
        assert "notes/test.md" in captured.out
        assert "0.9876" in captured.out
        assert "Test Note" in captured.out

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_search_json_output(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from markdown_vault_mcp.types import SearchResult

        result = SearchResult(
            path="a.md",
            title="Note A",
            folder="",
            heading=None,
            content="hello",
            score=1.0,
            search_type="keyword",
            frontmatter={},
        )
        mock_collection = MagicMock()
        mock_collection.search.return_value = [result]
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "test", "--json"]):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["path"] == "a.md"
        assert data[0]["score"] == 1.0

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_search_passes_options(self, mock_build: MagicMock) -> None:
        mock_collection = MagicMock()
        mock_collection.search.return_value = []
        mock_build.return_value = mock_collection

        with patch(
            "sys.argv",
            [
                "markdown-vault-mcp",
                "search",
                "query",
                "-n",
                "5",
                "-m",
                "semantic",
                "--folder",
                "Journal",
            ],
        ):
            main()

        mock_collection.search.assert_called_once_with(
            "query", limit=5, mode="semantic", folder="Journal"
        )


class TestCmdReindex:
    """Test the reindex subcommand."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_reindex_prints_stats(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_result = MagicMock()
        mock_result.added = 3
        mock_result.modified = 1
        mock_result.deleted = 2
        mock_result.unchanged = 10

        mock_collection = MagicMock()
        mock_collection.reindex.return_value = mock_result
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "reindex"]):
            main()

        captured = capsys.readouterr()
        assert "3 added" in captured.out
        assert "1 modified" in captured.out
        assert "2 deleted" in captured.out
        assert "10 unchanged" in captured.out
