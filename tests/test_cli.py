"""Tests for cli.py — argument parsing and subcommand dispatch."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp.cli import _build_parser, _cmd_serve, main

if TYPE_CHECKING:
    from pathlib import Path


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

    def test_serve_http_transport(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve", "--transport", "http"])
        assert args.transport == "http"
        assert args.host == "0.0.0.0"
        assert args.port == 8000
        assert args.path is None

    def test_serve_http_custom_host_port(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["serve", "--transport", "http", "--host", "127.0.0.1", "--port", "9000"]
        )
        assert args.transport == "http"
        assert args.host == "127.0.0.1"
        assert args.port == 9000

    def test_serve_http_custom_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["serve", "--transport", "http", "--path", "/vault/mcp"]
        )
        assert args.transport == "http"
        assert args.path == "/vault/mcp"

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


class TestCmdServe:
    """Test the serve subcommand dispatch."""

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_calls_run_with_host_port(self, mock_create: MagicMock) -> None:
        """_cmd_serve passes host/port/path to server.run for http transport."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        args = _build_parser().parse_args(
            ["serve", "--transport", "http", "--host", "127.0.0.1", "--port", "9000"]
        )
        _cmd_serve(args)
        mock_server.run.assert_called_once_with(
            transport="http", host="127.0.0.1", port=9000, path="/mcp"
        )

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_custom_path(self, mock_create: MagicMock) -> None:
        """_cmd_serve passes custom --path to server.run for http transport."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        args = _build_parser().parse_args(
            ["serve", "--transport", "http", "--path", "/vault/mcp"]
        )
        _cmd_serve(args)
        mock_server.run.assert_called_once_with(
            transport="http", host="0.0.0.0", port=8000, path="/vault/mcp"
        )

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_custom_path_normalised(self, mock_create: MagicMock) -> None:
        """_cmd_serve normalises --path by adding leading slash and trimming tail."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        args = _build_parser().parse_args(
            ["serve", "--transport", "http", "--path", "vault/mcp/"]
        )
        _cmd_serve(args)
        mock_server.run.assert_called_once_with(
            transport="http", host="0.0.0.0", port=8000, path="/vault/mcp"
        )

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_path_env_fallback(self, mock_create: MagicMock) -> None:
        """_cmd_serve uses MARKDOWN_VAULT_MCP_HTTP_PATH when --path is omitted."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        with patch.dict("os.environ", {"MARKDOWN_VAULT_MCP_HTTP_PATH": "/vault/mcp"}):
            args = _build_parser().parse_args(["serve", "--transport", "http"])
            _cmd_serve(args)
        mock_server.run.assert_called_once_with(
            transport="http", host="0.0.0.0", port=8000, path="/vault/mcp"
        )

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_path_cli_overrides_env(self, mock_create: MagicMock) -> None:
        """_cmd_serve --path takes precedence over MARKDOWN_VAULT_MCP_HTTP_PATH."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        with patch.dict("os.environ", {"MARKDOWN_VAULT_MCP_HTTP_PATH": "/from-env"}):
            args = _build_parser().parse_args(
                ["serve", "--transport", "http", "--path", "/from-cli"]
            )
            _cmd_serve(args)
        mock_server.run.assert_called_once_with(
            transport="http", host="0.0.0.0", port=8000, path="/from-cli"
        )

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_stdio_does_not_pass_host_port(self, mock_create: MagicMock) -> None:
        """_cmd_serve does not pass host/port for stdio transport."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        args = _build_parser().parse_args(["serve"])
        _cmd_serve(args)
        mock_server.run.assert_called_once_with(transport="stdio")


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


class TestCmdServeEdgeCases:
    """Edge-case branches in _cmd_serve."""

    def test_import_error_exits_with_1(self) -> None:
        """_cmd_serve calls sys.exit(1) when FastMCP import fails."""
        import sys

        args = _build_parser().parse_args(["serve"])

        with (
            patch.dict(sys.modules, {"markdown_vault_mcp.mcp_server": None}),
            pytest.raises(SystemExit) as exc_info,
        ):
            _cmd_serve(args)

        assert exc_info.value.code == 1

    def test_non_http_transport_with_custom_host_port_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """--host/--port with non-http transport logs a warning."""
        import logging

        mock_server = MagicMock()

        args = _build_parser().parse_args(
            ["serve", "--transport", "stdio", "--host", "127.0.0.1", "--port", "9999"]
        )

        with (
            patch(
                "markdown_vault_mcp.mcp_server.create_server", return_value=mock_server
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.cli"),
        ):
            _cmd_serve(args)

        assert any(
            "--host" in r.message or "--port" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
        mock_server.run.assert_called_once_with(transport="stdio")


class TestBuildCollectionEmbeddingFailure:
    """Graceful degradation when the embedding provider fails to load."""

    def test_embedding_provider_failure_returns_collection(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Collection is still returned even when get_embedding_provider raises."""
        import logging

        from markdown_vault_mcp.cli import _build_collection

        vault = tmp_path / "vault"
        vault.mkdir()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        # Set an embeddings_path so the try-block is entered.
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(tmp_path / "vecs.npy")
        )

        args = _build_parser().parse_args(["index"])

        with (
            patch(
                "markdown_vault_mcp.providers.get_embedding_provider",
                side_effect=RuntimeError("no sentence-transformers"),
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.cli"),
        ):
            collection = _build_collection(args)

        # Collection is returned despite the provider failure.
        from markdown_vault_mcp.collection import Collection

        assert isinstance(collection, Collection)
        assert any("semantic search disabled" in r.message for r in caplog.records)


class TestCmdSearchJsonOutput:
    """Verify --json flag in _cmd_search produces valid parseable output."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_json_flag_produces_valid_json(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_cmd_search with --json outputs a valid JSON array."""
        from markdown_vault_mcp.types import SearchResult

        results = [
            SearchResult(
                path="notes/alpha.md",
                title="Alpha",
                folder="notes",
                heading=None,
                content="some text",
                score=0.75,
                search_type="keyword",
                frontmatter={},
            ),
            SearchResult(
                path="notes/beta.md",
                title="Beta",
                folder="notes",
                heading="Section",
                content="more text",
                score=0.55,
                search_type="keyword",
                frontmatter={"tag": "x"},
            ),
        ]
        mock_collection = MagicMock()
        mock_collection.search.return_value = results
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "alpha", "--json"]):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["path"] == "notes/alpha.md"
        assert data[0]["score"] == 0.75
        assert data[1]["path"] == "notes/beta.md"

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_json_flag_empty_results(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_cmd_search with --json and no results outputs an empty JSON array."""
        mock_collection = MagicMock()
        mock_collection.search.return_value = []
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "nothing", "--json"]):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == []


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
