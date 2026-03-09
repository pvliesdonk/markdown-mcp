"""Generic FastMCP server for markdown collections.

Exposes :class:`~markdown_vault_mcp.collection.Collection` methods as MCP tools
with proper ``ToolAnnotations``.  Uses a lifespan hook to build the
``Collection`` once at startup and tear it down on shutdown.

The server is configured entirely via environment variables (see
:mod:`markdown_vault_mcp.config`).  Call :func:`create_server` to build a
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

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.config import _ENV_PREFIX, _parse_bool, load_config

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

    # Resolve embedding provider if embeddings_path is configured.
    embedding_provider = None
    if config.embeddings_path is not None:
        try:
            from markdown_vault_mcp.providers import get_embedding_provider

            embedding_provider = get_embedding_provider()
            logger.info("Embedding provider: %s", type(embedding_provider).__name__)
        except Exception:
            logger.warning(
                "Could not load embedding provider; semantic search disabled",
                exc_info=True,
            )

    kwargs = config.to_collection_kwargs()
    if embedding_provider is not None:
        kwargs["embedding_provider"] = embedding_provider
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


def _build_default_instructions(*, read_only: bool) -> str:
    """Build the default instructions string based on read-only state.

    Args:
        read_only: Whether write tools are disabled on this instance.

    Returns:
        Instructions string suitable for the ``instructions`` parameter
        of :class:`~fastmcp.FastMCP`.
    """
    write_line = (
        "This instance is READ-ONLY — write tools are not available."
        if read_only
        else (
            "This instance is READ-WRITE — use 'write' to create, 'edit' for "
            "targeted changes (read first), 'rename' to move, 'delete' to remove."
        )
    )
    return (
        "A searchable markdown document collection. "
        "Paths are always relative (e.g. 'Journal/note.md'). "
        f"{write_line} "
        "Use 'search' (mode='hybrid' preferred when available) to find documents, "
        "'read' for full content, 'list_documents' to enumerate, 'stats' to check "
        "capabilities. "
        "Operators: set MARKDOWN_VAULT_MCP_INSTRUCTIONS to describe this "
        "collection's domain and frontmatter vocabulary."
    )


def create_server() -> FastMCP:
    """Create and configure the FastMCP server.

    Reads configuration from environment variables via :func:`load_config`.
    Tools are registered based on the ``MARKDOWN_VAULT_MCP_READ_ONLY`` setting:
    write tools are only registered when ``MARKDOWN_VAULT_MCP_READ_ONLY=false``.

    Server identity is configurable via:

    - ``MARKDOWN_VAULT_MCP_SERVER_NAME``: MCP server name shown to clients
      (default ``"markdown-vault-mcp"``).
    - ``MARKDOWN_VAULT_MCP_INSTRUCTIONS``: system-level instructions injected
      into LLM context (default: dynamic description reflecting read-only state).

    Returns:
        A fully configured :class:`~fastmcp.FastMCP` instance ready to run.
    """
    raw_read_only = os.environ.get(f"{_ENV_PREFIX}_READ_ONLY")
    is_read_only = _parse_bool(raw_read_only) if raw_read_only is not None else True

    server_name = os.environ.get(f"{_ENV_PREFIX}_SERVER_NAME", "markdown-vault-mcp")
    default_instructions = _build_default_instructions(read_only=is_read_only)
    instructions = os.environ.get(f"{_ENV_PREFIX}_INSTRUCTIONS", default_instructions)

    mcp = FastMCP(
        server_name,
        instructions=instructions,
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
        """Find documents matching a query using full-text or semantic search.

        Prefer mode="hybrid" when semantic search is available (check 'stats'
        for semantic_search_available). Use mode="keyword" for exact term
        matches; mode="semantic" for meaning-based similarity.

        Args:
            query: Natural language or keyword query string.
            limit: Maximum results to return (default 10).
            mode: "keyword" uses FTS5/BM25 for exact terms. "semantic" uses
                vector similarity (requires embeddings). "hybrid" fuses both
                via reciprocal rank fusion — best quality when available.
            folder: Restrict to documents under this folder path (e.g.
                "Journal"). Must match a value from 'list_folders'.
            filters: Filter by indexed frontmatter field values, e.g.
                {"cluster": "craft", "tags": "pacing"}. Only fields listed
                in indexed_frontmatter_fields (see 'stats') can be filtered.
                Multiple filters are ANDed.

        Returns:
            List of result dicts ranked by relevance (higher score is better).
            Each contains: path, title, folder, content (matched chunk),
            score, frontmatter.

        Raises:
            ValueError: If mode is "semantic" or "hybrid" and no embedding
                provider is configured.
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
        """Read the full content and frontmatter of a single document by path.

        Use this after 'search' or 'list_documents' to retrieve full text.
        Do not guess paths — look them up first.

        Args:
            path: Relative path to the document (e.g. "Journal/note.md").
                Case-sensitive. Must match a path from 'search' or
                'list_documents'.

        Returns:
            Dict with path, title, folder, content (full markdown body),
            frontmatter (dict of YAML fields), modified_at (ISO 8601).

        Raises:
            ValueError: If no document exists at the given path. Use
                'search' or 'list_documents' to find the correct path.
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
        """List all documents in the collection, optionally filtered by folder or glob.

        Use this to enumerate documents when you need a complete listing, not
        ranked search results. For finding documents by content, use 'search'.
        Does NOT include body content — call 'read' for full text.

        Args:
            folder: Return only documents in this folder (e.g. "Journal").
            pattern: Unix glob matched against relative paths (e.g.
                "Journal/*.md", "**/*meeting*.md").

        Returns:
            List of document info dicts with path, title, folder, frontmatter,
            modified_at. Body content is not included.
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
        """List all folder paths that contain documents.

        Call this to discover valid folder names before filtering 'search' or
        'list_documents' by folder. The root folder (top-level documents) is
        represented as an empty string "".

        Returns:
            Sorted list of folder paths, e.g. ["", "Journal", "Projects"].
            Pass any of these as the 'folder' argument to 'search' or
            'list_documents'.
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
        """List all distinct values for a frontmatter field across the collection.

        Use this to discover valid filter values before calling 'search' with
        the 'filters' argument. Only fields listed in indexed_frontmatter_fields
        (see 'stats') are indexed — querying other fields returns an empty list.

        Args:
            field: Frontmatter field name to enumerate (default "tags"). Must
                match a field in indexed_frontmatter_fields (check 'stats').

        Returns:
            Sorted list of distinct string values, e.g.
            ["craft", "pacing", "worldbuilding"]. Use these as values in the
            'filters' dict when calling 'search'.
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
        """Get an overview of the collection's size, capabilities, and configuration.

        Call this at the start of a session to understand what the collection
        contains and what search modes are available. The
        'semantic_search_available' field tells you whether mode="semantic" or
        mode="hybrid" can be used in 'search'.

        Returns:
            Dict with document_count, chunk_count, folder_count,
            semantic_search_available (bool), indexed_frontmatter_fields
            (list of field names usable as 'filters' in 'search' and as
            'field' in 'list_tags').
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
        """Check the embedding provider configuration and vector index freshness.

        Use this to diagnose why semantic search is unavailable or returning
        poor results. If embeddings are stale (new documents indexed since last
        embed run), call 'build_embeddings' to update the vector index.

        Returns:
            Dict with provider info (name, model), document/chunk counts,
            and a staleness indicator (whether unembedded chunks exist).
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
        """Incrementally update the full-text search index to reflect file changes.

        Call this when documents have been added, edited, or deleted on disk
        outside this server. Only processes changed files — unchanged documents
        are skipped.

        Note: if semantic search is already active (vector index loaded), this
        also re-embeds changed documents automatically. Call
        'build_embeddings' only to initialise semantic search for the
        first time, or use force=True to rebuild all embeddings.

        Returns:
            Dict with counts: added, modified, deleted, unchanged.
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
        """Build vector embeddings to enable semantic and hybrid search.

        This can be slow for large collections — it calls the embedding
        provider for every unembedded text chunk. Call once to enable semantic
        search for the first time (when the vector index does not yet exist).
        After that, 'reindex' handles incremental re-embedding automatically.
        Check 'embeddings_status' to see if this is needed.

        Args:
            force: When True, discards existing embeddings and rebuilds from
                scratch. Use only if the embedding model has changed.
                False (default) only embeds chunks not yet embedded.

        Returns:
            Dict with chunks_embedded: number of chunks newly embedded.
        """
        collection = _get_collection()
        count = await asyncio.to_thread(collection.build_embeddings, force=force)
        return {"chunks_embedded": count}

    # --- Write tools (conditionally registered) ---

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
            """Create a new document or completely overwrite an existing one.

            WARNING: If the path already exists, its entire content is replaced.
            To make targeted changes to an existing document, use 'edit' instead.
            Call 'read' first if you are unsure whether the document exists.

            Args:
                path: Relative path (e.g. "Journal/note.md"). Parent
                    directories are created automatically.
                content: Full markdown body (excluding frontmatter). Do not
                    include YAML delimiters — pass frontmatter separately.
                frontmatter: Optional dict of YAML frontmatter fields,
                    e.g. {"title": "My Note", "tags": ["draft", "idea"]}.

            Returns:
                Dict with path (str) and created (bool — true if new file,
                false if overwrite).
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
            """Make a targeted text replacement in an existing document.

            Always call 'read' first to get the exact current text, then pass
            a portion of it as old_text. The match is exact and must appear
            only once — if not found the call fails (text changed or wrong);
            if found multiple times the call fails (use a longer, unique
            excerpt). Frontmatter can be edited: old_text may span the YAML
            block.

            Args:
                path: Relative path to the document.
                old_text: Exact text to replace. Must appear exactly once in
                    the document (including frontmatter). Get this via 'read'.
                new_text: Replacement text. May be longer or shorter.

            Returns:
                Dict with path (str) and replacements (int, always 1).
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
            """Permanently delete a document and remove it from all search indices.

            IRREVERSIBLE unless git history exists. Confirm the path with the
            user before calling. Use 'list_documents' or 'search' to verify
            the path.

            Args:
                path: Relative path to the document to delete.

            Returns:
                Dict with path (str) of the deleted document.
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
            """Rename a document or move it to a different folder.

            Both the file and its search index entries are updated atomically.
            No need to call 'reindex' after renaming. Parent directories for
            new_path are created automatically.

            Args:
                old_path: Current relative path (e.g. "drafts/idea.md").
                new_path: Target relative path (e.g. "projects/idea.md").
                    Can cross folders. Fails if new_path already exists.

            Returns:
                Dict with old_path (str) and new_path (str).
            """
            collection = _get_collection()
            result = await asyncio.to_thread(collection.rename, old_path, new_path)
            return asdict(result)

    return mcp
