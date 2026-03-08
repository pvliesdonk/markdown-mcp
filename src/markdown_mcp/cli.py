"""Command-line interface for markdown-mcp.

Provides ``serve``, ``index``, ``search``, and ``reindex`` subcommands.
The entry point is :func:`main`, registered as ``markdown-mcp`` in
``pyproject.toml``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from markdown_mcp.collection import Collection
from markdown_mcp.config import load_config

logger = logging.getLogger(__name__)


def _build_collection(args: argparse.Namespace) -> Collection:
    """Build a Collection from environment variables and CLI overrides.

    CLI arguments ``--source-dir`` and ``--index-path`` override the
    corresponding environment variables when provided.

    Args:
        args: Parsed CLI arguments (may contain ``source_dir`` and
            ``index_path`` attributes).

    Returns:
        A fully initialised :class:`Collection` (index not yet built).
    """
    # CLI --source-dir overrides env var.
    source_dir_override = getattr(args, "source_dir", None)
    if source_dir_override:
        os.environ["MARKDOWN_MCP_SOURCE_DIR"] = source_dir_override

    config = load_config()

    # CLI --index-path overrides env var.
    index_path_override = getattr(args, "index_path", None)
    index_path = Path(index_path_override) if index_path_override else config.index_path

    embedding_provider = None
    if config.embeddings_path is not None:
        try:
            from markdown_mcp.providers import get_embedding_provider

            embedding_provider = get_embedding_provider()
        except Exception:
            logger.warning(
                "Could not load embedding provider; semantic search disabled",
                exc_info=True,
            )

    return Collection(
        source_dir=config.source_dir,
        read_only=config.read_only,
        index_path=index_path,
        embeddings_path=config.embeddings_path,
        embedding_provider=embedding_provider,
        state_path=config.state_path,
        indexed_frontmatter_fields=config.indexed_frontmatter_fields,
        required_frontmatter=config.required_frontmatter,
    )


def _cmd_serve(args: argparse.Namespace) -> None:
    """Run the MCP server."""
    try:
        from markdown_mcp.mcp_server import create_server
    except ImportError:
        logger.error(
            "FastMCP is not installed. Install with: pip install markdown-mcp[mcp]"
        )
        sys.exit(1)

    server = create_server()
    server.run(transport=args.transport)


def _cmd_index(args: argparse.Namespace) -> None:
    """Build the full-text search index."""
    collection = _build_collection(args)
    stats = collection.build_index(force=args.force)
    print(f"Indexed {stats.documents_indexed} documents, {stats.chunks_indexed} chunks")


def _cmd_search(args: argparse.Namespace) -> None:
    """Search the collection."""
    collection = _build_collection(args)

    results = collection.search(
        args.query,
        limit=args.limit,
        mode=args.mode,
        folder=args.folder,
    )

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            score = f" ({r.score:.4f})"
            print(f"  {r.path}{score}")
            if r.title:
                print(f"    {r.title}")


def _cmd_reindex(args: argparse.Namespace) -> None:
    """Incrementally reindex the collection."""
    collection = _build_collection(args)
    result = collection.reindex()
    print(
        f"Reindex: {result.added} added, {result.modified} modified, "
        f"{result.deleted} deleted, {result.unchanged} unchanged"
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="markdown-mcp",
        description="Generic markdown collection MCP server",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    serve_parser = sub.add_parser("serve", help="run the MCP server")
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )

    # index
    index_parser = sub.add_parser("index", help="build the full-text search index")
    index_parser.add_argument(
        "--source-dir",
        help="path to markdown collection (overrides MARKDOWN_MCP_SOURCE_DIR)",
    )
    index_parser.add_argument(
        "--index-path",
        help="path to SQLite index file (overrides MARKDOWN_MCP_INDEX_PATH)",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="drop and rebuild the index from scratch",
    )

    # search
    search_parser = sub.add_parser("search", help="search the collection")
    search_parser.add_argument("query", help="search query")
    search_parser.add_argument(
        "--source-dir",
        help="path to markdown collection (overrides MARKDOWN_MCP_SOURCE_DIR)",
    )
    search_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="max results (default: 10)",
    )
    search_parser.add_argument(
        "-m",
        "--mode",
        choices=["keyword", "semantic", "hybrid"],
        default="keyword",
        help="search mode (default: keyword)",
    )
    search_parser.add_argument(
        "--folder",
        help="restrict to folder",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="output results as JSON",
    )

    # reindex
    reindex_parser = sub.add_parser(
        "reindex", help="incrementally reindex the collection"
    )
    reindex_parser.add_argument(
        "--source-dir",
        help="path to markdown collection (overrides MARKDOWN_MCP_SOURCE_DIR)",
    )
    reindex_parser.add_argument(
        "--index-path",
        help="path to SQLite index file (overrides MARKDOWN_MCP_INDEX_PATH)",
    )

    return parser


_COMMANDS = {
    "serve": _cmd_serve,
    "index": _cmd_index,
    "search": _cmd_search,
    "reindex": _cmd_reindex,
}


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    handler = _COMMANDS[args.command]
    handler(args)


if __name__ == "__main__":
    main()
