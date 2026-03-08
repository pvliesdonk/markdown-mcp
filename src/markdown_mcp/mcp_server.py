"""Generic FastMCP server for markdown collections.

Exposes :class:`~markdown_mcp.collection.Collection` methods as MCP tools
with proper ``ToolAnnotations``.  Uses a lifespan hook to build the
``Collection`` once at startup and tear it down on shutdown.

The server is configured entirely via environment variables (see
:mod:`markdown_mcp.config`).  Call :func:`create_server` to build a
configured :class:`~fastmcp.FastMCP` instance.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import FastMCP

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from markdown_mcp.collection import Collection
from markdown_mcp.config import load_config

logger = logging.getLogger(__name__)

# Module-level state set during lifespan.
_collection: Collection | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _collection_lifespan(
    server: FastMCP,  # noqa: ARG001
) -> AsyncIterator[dict[str, Any]]:
    """Build the Collection at server startup, tear down on shutdown."""
    global _collection

    config = load_config()
    logger.info("Initialising collection from %s", config.source_dir)

    kwargs = config.to_collection_kwargs()

    # Resolve embedding provider if embeddings_path is configured.
    if config.embeddings_path is not None:
        try:
            from markdown_mcp.providers import get_embedding_provider

            provider = get_embedding_provider()
            kwargs["embedding_provider"] = provider
            logger.info("Embedding provider: %s", type(provider).__name__)
        except Exception:
            logger.warning(
                "Could not load embedding provider; semantic search disabled",
                exc_info=True,
            )

    collection = Collection(**kwargs)
    _collection = collection

    # Build index eagerly so first tool call is fast.
    stats = await asyncio.to_thread(collection.build_index)
    logger.info(
        "Index built: %d documents, %d chunks",
        stats.documents_indexed,
        stats.chunks_indexed,
    )

    try:
        yield {}
    finally:
        collection.close()
        _collection = None
        logger.info("Collection shut down")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_collection() -> Collection:
    """Return the module-level Collection, raising if not initialised."""
    if _collection is None:
        msg = "Collection not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return _collection


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server() -> FastMCP:
    """Create and configure the FastMCP server.

    Reads configuration from environment variables via :func:`load_config`.
    Tools are registered based on the ``MARKDOWN_MCP_READ_ONLY`` setting:
    write tools are only registered when ``MARKDOWN_MCP_READ_ONLY=false``.

    Returns:
        A fully configured :class:`~fastmcp.FastMCP` instance ready to run.
    """
    mcp = FastMCP(
        "markdown-mcp",
        instructions=(
            "A markdown collection server with full-text and semantic search. "
            "Use 'search' to find documents, 'read' to get full content, "
            "'list_documents' to browse, and metadata tools for collection info."
        ),
        lifespan=_collection_lifespan,
    )

    # --- Read-only tools (always registered) ---

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def search(
        query: str,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        folder: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search the collection by query.

        Args:
            query: Search query string.
            limit: Maximum number of results (default 10).
            mode: Search mode — "keyword" (FTS5/BM25), "semantic" (vector),
                or "hybrid" (reciprocal rank fusion of both).
            folder: Restrict results to this folder.
            filters: Frontmatter tag filters as key-value pairs (ANDed).

        Returns:
            List of search result dicts with path, title, content, score, etc.
        """
        collection = _get_collection()
        results = await asyncio.to_thread(
            collection.search,
            query,
            limit=limit,
            mode=mode,
            folder=folder,
            filters=filters,
        )
        return [asdict(r) for r in results]

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def read(path: str) -> dict[str, Any]:
        """Read a document's full content.

        Args:
            path: Relative path to the document (e.g. "Journal/note.md").

        Returns:
            Dict with path, title, folder, content, frontmatter, modified_at.

        Raises:
            ValueError: If the document is not found.
        """
        collection = _get_collection()
        result = await asyncio.to_thread(collection.read, path)
        if result is None:
            raise ValueError(f"Document not found: {path}")
        return asdict(result)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_documents(
        folder: str | None = None,
        pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """List documents in the collection, optionally filtered.

        Args:
            folder: Restrict to documents in this folder.
            pattern: Unix glob pattern matched against relative paths
                (e.g. "Journal/*.md").

        Returns:
            List of document info dicts with path, title, folder, frontmatter,
            modified_at.
        """
        collection = _get_collection()
        results = await asyncio.to_thread(
            collection.list, folder=folder, pattern=pattern
        )
        return [asdict(r) for r in results]

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_folders() -> list[str]:
        """List all folders in the collection.

        Returns:
            Sorted list of folder paths. Root is represented as empty string.
        """
        collection = _get_collection()
        return await asyncio.to_thread(collection.list_folders)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_tags(field: str = "tags") -> list[str]:
        """List distinct tag values for a frontmatter field.

        Args:
            field: Frontmatter field name (default "tags"). Must be in
                indexed_frontmatter_fields or returns empty list.

        Returns:
            Sorted list of distinct values for the field.
        """
        collection = _get_collection()
        return await asyncio.to_thread(collection.list_tags, field)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def stats() -> dict[str, Any]:
        """Get collection statistics.

        Returns:
            Dict with document_count, chunk_count, folder_count,
            semantic_search_available, indexed_frontmatter_fields.
        """
        collection = _get_collection()
        result = await asyncio.to_thread(collection.stats)
        return asdict(result)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def embeddings_status() -> dict[str, Any]:
        """Check embedding provider and index status.

        Returns:
            Dict with provider info, document/chunk counts, and staleness.
        """
        collection = _get_collection()
        return await asyncio.to_thread(collection.embeddings_status)

    # --- Index management tools ---

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def reindex() -> dict[str, Any]:
        """Incrementally reindex the collection.

        Detects added, modified, and deleted files since the last index
        and applies only the changes.

        Returns:
            Dict with added, modified, deleted, unchanged counts.
        """
        collection = _get_collection()
        result = await asyncio.to_thread(collection.reindex)
        return asdict(result)

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def build_embeddings(force: bool = False) -> dict[str, Any]:
        """Build or rebuild vector embeddings for semantic search.

        Args:
            force: When True, rebuild all embeddings from scratch.

        Returns:
            Dict with the number of chunks embedded.
        """
        collection = _get_collection()
        count = await asyncio.to_thread(collection.build_embeddings, force=force)
        return {"chunks_embedded": count}

    # --- Write tools (conditionally registered) ---
    # The underlying Collection methods are Phase 3 stubs that raise
    # NotImplementedError.  The conditional registration mechanism is in
    # place now so clients see the correct tool surface based on read_only.

    raw_read_only = os.environ.get("MARKDOWN_MCP_READ_ONLY", "true").strip().lower()
    is_read_only = raw_read_only in ("true", "1", "yes")

    if not is_read_only:

        @mcp.tool(
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        )
        async def write(
            path: str,
            content: str,
            frontmatter: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Create or overwrite a document.

            Args:
                path: Relative path for the document (e.g. "Journal/note.md").
                content: Full markdown content to write.
                frontmatter: Optional frontmatter dict to prepend as YAML.

            Returns:
                Dict with path and created (bool).
            """
            collection = _get_collection()
            result = await asyncio.to_thread(
                collection.write, path, content, frontmatter=frontmatter
            )
            return asdict(result)

        @mcp.tool(
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        )
        async def edit(
            path: str,
            old_text: str,
            new_text: str,
        ) -> dict[str, Any]:
            """Patch a section of a document (read-before-edit).

            Replaces exactly one occurrence of old_text with new_text.

            Args:
                path: Relative path to the document.
                old_text: Text to find (must appear exactly once).
                new_text: Replacement text.

            Returns:
                Dict with path and replacements count.
            """
            collection = _get_collection()
            result = await asyncio.to_thread(collection.edit, path, old_text, new_text)
            return asdict(result)

        @mcp.tool(
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": True,
            },
        )
        async def delete(path: str) -> dict[str, Any]:
            """Delete a document.

            Args:
                path: Relative path to the document.

            Returns:
                Dict with the deleted path.
            """
            collection = _get_collection()
            result = await asyncio.to_thread(collection.delete, path)
            return asdict(result)

        @mcp.tool(
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        )
        async def rename(
            old_path: str,
            new_path: str,
        ) -> dict[str, Any]:
            """Rename or move a document.

            Args:
                old_path: Current relative path.
                new_path: New relative path.

            Returns:
                Dict with old_path and new_path.
            """
            collection = _get_collection()
            result = await asyncio.to_thread(collection.rename, old_path, new_path)
            return asdict(result)

    return mcp
