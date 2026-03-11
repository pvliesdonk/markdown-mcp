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
import base64
import json
import logging
import os
import re
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan
from mcp.types import Icon

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.config import _ENV_PREFIX, _parse_bool, load_config

# ---------------------------------------------------------------------------
# Tool icons (Lucide SVGs as data URIs)
# ---------------------------------------------------------------------------

_TOOL_ICONS: dict[str, list[Icon]] = {
    "search": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Im0yMSAyMWwtNC4zNC00LjM0Ii8+PGNpcmNsZSBjeD0iMTEiIGN5PSIxMSIgcj0iOCIvPjwvZz48L3N2Zz4=",
            mimeType="image/svg+xml",
        )
    ],
    "read": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Ik02IDIyYTIgMiAwIDAgMS0yLTJWNGEyIDIgMCAwIDEgMi0yaDhhMi40IDIuNCAwIDAgMSAxLjcwNC43MDZsMy41ODggMy41ODhBMi40IDIuNCAwIDAgMSAyMCA4djEyYTIgMiAwIDAgMS0yIDJ6Ii8+PHBhdGggZD0iTTE0IDJ2NWExIDEgMCAwIDAgMSAxaDVNMTAgOUg4bTggNEg4bTggNEg4Ii8+PC9nPjwvc3ZnPg==",
            mimeType="image/svg+xml",
        )
    ],
    "list_documents": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiIGQ9Ik0zIDVoLjAxTTMgMTJoLjAxTTMgMTloLjAxTTggNWgxM004IDEyaDEzTTggMTloMTMiLz48L3N2Zz4=",
            mimeType="image/svg+xml",
        )
    ],
    "list_folders": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiIGQ9Ik0yMCAyMGEyIDIgMCAwIDAgMi0yVjhhMiAyIDAgMCAwLTItMmgtNy45YTIgMiAwIDAgMS0xLjY5LS45TDkuNiAzLjlBMiAyIDAgMCAwIDcuOTMgM0g0YTIgMiAwIDAgMC0yIDJ2MTNhMiAyIDAgMCAwIDIgMloiLz48L3N2Zz4=",
            mimeType="image/svg+xml",
        )
    ],
    "list_tags": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Ik0xMi41ODYgMi41ODZBMiAyIDAgMCAwIDExLjE3MiAySDRhMiAyIDAgMCAwLTIgMnY3LjE3MmEyIDIgMCAwIDAgLjU4NiAxLjQxNGw4LjcwNCA4LjcwNGEyLjQyNiAyLjQyNiAwIDAgMCAzLjQyIDBsNi41OC02LjU4YTIuNDI2IDIuNDI2IDAgMCAwIDAtMy40MnoiLz48Y2lyY2xlIGN4PSI3LjUiIGN5PSI3LjUiIHI9Ii41IiBmaWxsPSJjdXJyZW50Q29sb3IiLz48L2c+PC9zdmc+",
            mimeType="image/svg+xml",
        )
    ],
    "stats": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxjaXJjbGUgY3g9IjEyIiBjeT0iMTIiIHI9IjEwIi8+PHBhdGggZD0iTTEyIDE2di00bTAtNGguMDEiLz48L2c+PC9zdmc+",
            mimeType="image/svg+xml",
        )
    ],
    "embeddings_status": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Ik0xMiAxOFY1bTMgOGE0LjE3IDQuMTcgMCAwIDEtMy00YTQuMTcgNC4xNyAwIDAgMS0zIDRtOC41OTgtNi41QTMgMyAwIDEgMCAxMiA1YTMgMyAwIDEgMC01LjU5OCAxLjUiLz48cGF0aCBkPSJNMTcuOTk3IDUuMTI1YTQgNCAwIDAgMSAyLjUyNiA1Ljc3Ii8+PHBhdGggZD0iTTE4IDE4YTQgNCAwIDAgMCAyLTcuNDY0Ii8+PHBhdGggZD0iTTE5Ljk2NyAxNy40ODNBNCA0IDAgMSAxIDEyIDE4YTQgNCAwIDEgMS03Ljk2Ny0uNTE3Ii8+PHBhdGggZD0iTTYgMThhNCA0IDAgMCAxLTItNy40NjQiLz48cGF0aCBkPSJNNi4wMDMgNS4xMjVhNCA0IDAgMCAwLTIuNTI2IDUuNzciLz48L2c+PC9zdmc+",
            mimeType="image/svg+xml",
        )
    ],
    "reindex": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Ik0zIDEyYTkgOSAwIDAgMSA5LTlhOS43NSA5Ljc1IDAgMCAxIDYuNzQgMi43NEwyMSA4Ii8+PHBhdGggZD0iTTIxIDN2NWgtNW01IDRhOSA5IDAgMCAxLTkgOWE5Ljc1IDkuNzUgMCAwIDEtNi43NC0yLjc0TDMgMTYiLz48cGF0aCBkPSJNOCAxNkgzdjUiLz48L2c+PC9zdmc+",
            mimeType="image/svg+xml",
        )
    ],
    "build_embeddings": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Im0xMC44NTIgMTQuNzcybC0uMzgzLjkyM20uMzgzLTYuNDY3bC0uMzgzLS45MjNtMi42NzkgNi40NjdsLjM4Mi45MjRtLjAwMS03LjM5MWwtLjM4My45MjNtMS42MjQgMS42MjRsLjkyMy0uMzgzbS0uOTIzIDIuNjc5bC45MjMuMzgzTTE3LjU5OCA2LjVBMyAzIDAgMSAwIDEyIDVhMyAzIDAgMCAwLTUuNjMtMS40NDZhMyAzIDAgMCAwLS4zNjggMS41NzFhNCA0IDAgMCAwLTIuNTI1IDUuNzcxIi8+PHBhdGggZD0iTTE3Ljk5OCA1LjEyNWE0IDQgMCAwIDEgMi41MjUgNS43NzEiLz48cGF0aCBkPSJNMTkuNTA1IDEwLjI5NGE0IDQgMCAwIDEtMS41IDcuNzA2Ii8+PHBhdGggZD0iTTQuMDMyIDE3LjQ4M0E0IDQgMCAwIDAgMTEuNDY0IDIwYy4xOC0uMzExLjg5Mi0uMzExIDEuMDcyIDBhNCA0IDAgMCAwIDcuNDMyLTIuNTE2Ii8+PHBhdGggZD0iTTQuNSAxMC4yOTFBNCA0IDAgMCAwIDYgMThtLjAwMi0xMi44NzVhMyAzIDAgMCAwIC40IDEuMzc1bTIuODI2IDQuMzUybC0uOTIzLS4zODNtLjkyMyAyLjY3OWwtLjkyMy4zODMiLz48Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSIzIi8+PC9nPjwvc3ZnPg==",
            mimeType="image/svg+xml",
        )
    ],
    "write": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48ZyBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Ik02IDIyYTIgMiAwIDAgMS0yLTJWNGEyIDIgMCAwIDEgMi0yaDhhMi40IDIuNCAwIDAgMSAxLjcwNC43MDZsMy41ODggMy41ODhBMi40IDIuNCAwIDAgMSAyMCA4djEyYTIgMiAwIDAgMS0yIDJ6Ii8+PHBhdGggZD0iTTE0IDJ2NWExIDEgMCAwIDAgMSAxaDVNOSAxNWg2bS0zIDN2LTYiLz48L2c+PC9zdmc+",
            mimeType="image/svg+xml",
        )
    ],
    "edit": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiIGQ9Ik0yMS4xNzQgNi44MTJhMSAxIDAgMCAwLTMuOTg2LTMuOTg3TDMuODQyIDE2LjE3NGEyIDIgMCAwIDAtLjUuODNsLTEuMzIxIDQuMzUyYS41LjUgMCAwIDAgLjYyMy42MjJsNC4zNTMtMS4zMmEyIDIgMCAwIDAgLjgzLS40OTd6TTE1IDVsNCA0Ii8+PC9zdmc+",
            mimeType="image/svg+xml",
        )
    ],
    "delete": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiIGQ9Ik0xMCAxMXY2bTQtNnY2bTUtMTF2MTRhMiAyIDAgMCAxLTIgMkg3YTIgMiAwIDAgMS0yLTJWNk0zIDZoMThNOCA2VjRhMiAyIDAgMCAxIDItMmg0YTIgMiAwIDAgMSAyIDJ2MiIvPjwvc3ZnPg==",
            mimeType="image/svg+xml",
        )
    ],
    "rename": [
        Icon(
            src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2Utd2lkdGg9IjIiIGQ9Ik0xMiAydjIwbTMtM2wtMyAzbC0zLTNNMTkgOWwzIDNsLTMgM00yIDEyaDIwTTUgOWwtMyAzbDMgM005IDVsMy0zbDMgMyIvPjwvc3ZnPg==",
            mimeType="image/svg+xml",
        )
    ],
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@lifespan
async def _collection_lifespan(
    server: FastMCP,  # noqa: ARG001
) -> AsyncIterator[dict[str, Any]]:
    """Build the Collection at server startup, tear down on shutdown."""
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

    # Build index eagerly so first tool call is fast.
    stats = await asyncio.to_thread(collection.build_index)
    logger.info(
        "Index built: %d documents, %d chunks",
        stats.documents_indexed,
        stats.chunks_indexed,
    )

    try:
        yield {"collection": collection}
    finally:
        collection.close()
        logger.info("Collection shut down")


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_collection(ctx: Context = CurrentContext()) -> Collection:
    """Resolve the Collection from lifespan context.

    Used as a ``Depends()`` default in tool/resource/prompt signatures.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    collection: Collection | None = ctx.lifespan_context.get("collection")
    if collection is None:
        msg = "Collection not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return collection


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


def _build_oidc_auth() -> Any:
    """Build an OIDCProxy auth provider from environment variables, or return None.

    All four of ``BASE_URL``, ``OIDC_CONFIG_URL``, ``OIDC_CLIENT_ID``, and
    ``OIDC_CLIENT_SECRET`` must be set to enable authentication.  If any is
    absent the server starts unauthenticated.

    Returns:
        A configured :class:`~fastmcp.server.auth.oidc_proxy.OIDCProxy` instance,
        or ``None`` when authentication is disabled.
    """
    base_url = os.environ.get(f"{_ENV_PREFIX}_BASE_URL", "").strip()
    config_url = os.environ.get(f"{_ENV_PREFIX}_OIDC_CONFIG_URL", "").strip()
    client_id = os.environ.get(f"{_ENV_PREFIX}_OIDC_CLIENT_ID", "").strip()
    client_secret = os.environ.get(f"{_ENV_PREFIX}_OIDC_CLIENT_SECRET", "").strip()

    if not all([base_url, config_url, client_id, client_secret]):
        return None

    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    jwt_signing_key = (
        os.environ.get(f"{_ENV_PREFIX}_OIDC_JWT_SIGNING_KEY", "").strip() or None
    )
    audience = os.environ.get(f"{_ENV_PREFIX}_OIDC_AUDIENCE", "").strip() or None
    raw_scopes = os.environ.get(f"{_ENV_PREFIX}_OIDC_REQUIRED_SCOPES", "openid").strip()
    required_scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()] or [
        "openid"
    ]

    if jwt_signing_key is None and sys.platform.startswith("linux"):
        logger.warning(
            "OIDC: MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY is not set — "
            "the JWT signing key is ephemeral on Linux; all clients must "
            "re-authenticate after every server restart"
        )

    return OIDCProxy(
        config_url=config_url,
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        audience=audience,
        required_scopes=required_scopes,
        jwt_signing_key=jwt_signing_key,
    )


def create_server() -> FastMCP:
    """Create and configure the FastMCP server.

    Reads configuration from environment variables via :func:`load_config`.
    Write tools are tagged with ``{"write"}`` and hidden via
    ``mcp.disable(tags={"write"})`` when ``READ_ONLY=true``.

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

    auth = _build_oidc_auth()
    if auth is None:
        logger.info(
            "OIDC auth not configured — server accepts unauthenticated connections"
        )
    else:
        logger.info("OIDC auth enabled")

    mcp = FastMCP(
        server_name,
        instructions=instructions,
        lifespan=_collection_lifespan,
        auth=auth,
    )

    # --- Read-only tools (always visible) ---

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
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
        collection: Collection = Depends(get_collection),
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
                Multiple filters are ANDed. For list fields (e.g. tags),
                this checks membership — {"tags": "pacing"} matches any
                document where "pacing" appears in the tags list.

        Returns:
            List of result dicts ranked by relevance (higher score is better).
            Each contains: path, title, folder, content (matched chunk),
            score, frontmatter.

        Raises:
            ValueError: If mode is "semantic" or "hybrid" and no embedding
                provider is configured.
        """
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
        icons=_TOOL_ICONS["read"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def read(
        path: str,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Read the full content of a document or attachment by path.

        For .md documents: returns markdown body, frontmatter, title, folder.
        For attachments (pdf, png, etc.): returns base64-encoded binary content
        and MIME type. Use 'list_documents(include_attachments=True)' to
        discover attachment paths. Use 'stats' to see allowed extensions.

        Do not guess paths — look them up first via 'search' or 'list_documents'.

        Args:
            path: Relative path to the document or attachment
                (e.g. "Journal/note.md" or "assets/diagram.pdf").
                Case-sensitive.

        Returns:
            For .md: dict with path, title, folder, content (markdown body),
            frontmatter (dict), modified_at (Unix timestamp),
            etag (SHA-256 hex str or null).
            For attachments: dict with path, mime_type (str or null),
            size_bytes (int), content_base64 (str), modified_at (Unix timestamp),
            etag (SHA-256 hex str or null).
            The 'etag' value can be passed as 'if_match' to write, edit,
            delete, or rename to guard against concurrent modifications.

        Raises:
            ValueError: If no file exists at the given path, the extension is
                not in the attachment allowlist, or the file exceeds the size
                limit.
        """
        if not path.endswith(".md"):
            attachment = await asyncio.to_thread(collection.read_attachment, path)
            return asdict(attachment)
        note = await asyncio.to_thread(collection.read, path)
        if note is None:
            raise ValueError(f"Document not found: {path}")
        return asdict(note)

    @mcp.tool(
        icons=_TOOL_ICONS["list_documents"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_documents(
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """List documents (and optionally attachments) in the collection.

        Use this to enumerate documents when you need a complete listing, not
        ranked search results. For finding documents by content, use 'search'.
        Does NOT include body content — call 'read' for full text.

        Args:
            folder: Return only documents in this folder (e.g. "Journal").
            pattern: Unix glob matched against relative paths (e.g.
                "Journal/*.md", "**/*meeting*.md").
            include_attachments: When True, also returns non-.md files (PDFs,
                images, etc.) that match the configured allowlist. Each
                attachment entry includes kind="attachment" and mime_type.
                Default False (notes only).

        Returns:
            List of info dicts. Every entry has a 'kind' field.
            Notes: path, title, folder, frontmatter, modified_at, kind="note".
            Attachments (when include_attachments=True): path, folder,
            mime_type, size_bytes, modified_at, kind="attachment".
            Body content is not included in either case.
        """
        results = await asyncio.to_thread(
            collection.list,
            folder=folder,
            pattern=pattern,
            include_attachments=include_attachments,
        )
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["list_folders"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_folders(
        collection: Collection = Depends(get_collection),
    ) -> list[str]:
        """List all folder paths that contain documents.

        Call this to discover valid folder names before filtering 'search' or
        'list_documents' by folder. The root folder (top-level documents) is
        represented as an empty string "".

        Returns:
            Sorted list of folder paths, e.g. ["", "Journal", "Projects"].
            Pass any of these as the 'folder' argument to 'search' or
            'list_documents'.
        """
        return await asyncio.to_thread(collection.list_folders)

    @mcp.tool(
        icons=_TOOL_ICONS["list_tags"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_tags(
        field: str = "tags",
        collection: Collection = Depends(get_collection),
    ) -> list[str]:
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
        return await asyncio.to_thread(collection.list_tags, field)

    @mcp.tool(
        icons=_TOOL_ICONS["stats"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def stats(
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
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
        result = await asyncio.to_thread(collection.stats)
        return asdict(result)

    @mcp.tool(
        icons=_TOOL_ICONS["embeddings_status"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def embeddings_status(
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Check the embedding provider configuration and vector index status.

        Use this to diagnose why semantic search is unavailable. Compare
        chunk_count here against chunk_count from 'stats': if stats has more
        chunks, call 'build_embeddings' to initialise the vector index for
        the first time (or 'reindex' to incrementally re-embed changed docs
        when semantic search is already active).

        Returns:
            Dict with available (bool), provider (str — provider class name,
            e.g. "OllamaProvider"), chunk_count (int — embedded chunks in the
            vector index), and path (str — vector index file path).
        """
        return await asyncio.to_thread(collection.embeddings_status)

    # --- Index management tools ---

    @mcp.tool(
        icons=_TOOL_ICONS["reindex"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def reindex(
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
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
        result = await asyncio.to_thread(collection.reindex)
        return asdict(result)

    @mcp.tool(
        icons=_TOOL_ICONS["build_embeddings"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def build_embeddings(
        force: bool = False,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
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
        count = await asyncio.to_thread(collection.build_embeddings, force=force)
        return {"chunks_embedded": count}

    # --- Write tools (tag-based visibility) ---

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["write"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def write(
        path: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
        content_base64: str = "",
        if_match: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Create or overwrite a document or attachment.

        For .md documents: uses 'content' (markdown body) and optional
        'frontmatter'. WARNING: replaces the entire file — use 'edit'
        for targeted changes.

        For attachments (pdf, png, etc.): uses 'content_base64' (base64-
        encoded binary). 'content' and 'frontmatter' are ignored.
        Parent directories are created automatically for both.

        Args:
            path: Relative path (e.g. "Journal/note.md" or
                "assets/photo.png"). Extension determines handling.
            content: Full markdown body for .md files (excluding
                frontmatter). Ignored for attachments.
            frontmatter: Optional YAML frontmatter dict for .md files,
                e.g. {"title": "My Note", "tags": ["draft"]}.
                Ignored for attachments.
            content_base64: Base64-encoded binary content for attachment
                files. Required when path is not .md.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the write only proceeds if the file has not
                been modified since that read (optimistic concurrency).
                Omit to write unconditionally.

        Returns:
            Dict with path (str) and created (bool — true if new file,
            false if overwrite).

        Raises:
            ValueError: If content_base64 is missing/invalid for
                attachments, or the content exceeds the size limit.
            McpError: If if_match is provided and the file has been
                modified (ConcurrentModificationError).
        """
        if not path.endswith(".md"):
            if not content_base64:
                raise ValueError(
                    f"content_base64 is required for non-.md attachments: {path}"
                )
            try:
                raw_bytes = base64.b64decode(content_base64)
            except Exception as exc:
                raise ValueError(f"Invalid base64 in content_base64: {exc}") from exc
            result = await asyncio.to_thread(
                collection.write_attachment, path, raw_bytes, if_match=if_match
            )
            return asdict(result)
        result = await asyncio.to_thread(
            collection.write, path, content, frontmatter=frontmatter, if_match=if_match
        )
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["edit"],
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
        if_match: str | None = None,
        collection: Collection = Depends(get_collection),
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
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the edit only proceeds if the file has not
                been modified since that read (optimistic concurrency).
                Omit to edit unconditionally.

        Returns:
            Dict with path (str) and replacements (int, always 1).
        """
        result = await asyncio.to_thread(
            collection.edit, path, old_text, new_text, if_match=if_match
        )
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["delete"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    )
    async def delete(
        path: str,
        if_match: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Permanently delete a document or attachment.

        For .md documents: also removes from all search indices.
        For attachments: only the file is deleted (no index to update).
        IRREVERSIBLE unless git history exists. Confirm the path with
        the user before calling.

        Args:
            path: Relative path to the document or attachment to delete.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the deletion only proceeds if the file has
                not been modified since that read (optimistic concurrency).
                Omit to delete unconditionally.

        Returns:
            Dict with path (str) of the deleted file.
        """
        result = await asyncio.to_thread(collection.delete, path, if_match=if_match)
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["rename"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def rename(
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Rename a document or attachment, or move it to a different folder.

        For .md documents: the file and its search index entries are updated.
        For attachments: only the file is moved (no index update needed).
        Parent directories for new_path are created automatically.

        Args:
            old_path: Current relative path (e.g. "drafts/idea.md"
                or "assets/old.png").
            new_path: Target relative path (e.g. "projects/idea.md"
                or "assets/new.png"). Fails if new_path already exists.
            if_match: Optional etag obtained from a previous 'read' call
                for old_path. When provided, the rename only proceeds if
                the file has not been modified since that read (optimistic
                concurrency). Omit to rename unconditionally.

        Returns:
            Dict with old_path (str) and new_path (str).
        """
        result = await asyncio.to_thread(
            collection.rename, old_path, new_path, if_match=if_match
        )
        return asdict(result)

    # --- Resources ---

    @mcp.resource("config://vault", mime_type="application/json")
    async def vault_config(
        collection: Collection = Depends(get_collection),
    ) -> str:
        """Vault configuration and runtime state."""
        config = load_config()
        stats = await asyncio.to_thread(collection.stats)
        return json.dumps(
            {
                "source_dir": str(config.source_dir),
                "read_only": config.read_only,
                "indexed_fields": config.indexed_frontmatter_fields or [],
                "required_fields": config.required_frontmatter or [],
                "exclude_patterns": config.exclude_patterns or [],
                "semantic_search_available": stats.semantic_search_available,
                "attachment_extensions": stats.attachment_extensions,
            }
        )

    @mcp.resource("stats://vault", mime_type="application/json")
    async def vault_stats(
        collection: Collection = Depends(get_collection),
    ) -> str:
        """Collection statistics — document count, chunk count, capabilities."""
        result = await asyncio.to_thread(collection.stats)
        return json.dumps(asdict(result))

    @mcp.resource("tags://vault", mime_type="application/json")
    async def vault_tags(
        collection: Collection = Depends(get_collection),
    ) -> str:
        """All tags grouped by indexed field."""
        stats = await asyncio.to_thread(collection.stats)
        grouped: dict[str, list[str]] = {}
        for field in stats.indexed_frontmatter_fields:
            values = await asyncio.to_thread(collection.list_tags, field)
            grouped[field] = values
        return json.dumps(grouped)

    @mcp.resource("tags://vault/{field}", mime_type="application/json")
    async def vault_tags_by_field(
        field: str,
        collection: Collection = Depends(get_collection),
    ) -> str:
        """Tags for a specific indexed field."""
        values = await asyncio.to_thread(collection.list_tags, field)
        return json.dumps(values)

    @mcp.resource("folders://vault", mime_type="application/json")
    async def vault_folders(
        collection: Collection = Depends(get_collection),
    ) -> str:
        """All folder paths in the vault."""
        folders = await asyncio.to_thread(collection.list_folders)
        return json.dumps(folders)

    @mcp.resource("toc://vault/{path}", mime_type="application/json")
    async def vault_toc(
        path: str,
        collection: Collection = Depends(get_collection),
    ) -> str:
        """Table of contents for a document — headings with levels."""
        toc = await asyncio.to_thread(collection.get_toc, path)
        return json.dumps(toc)

    # --- Prompts ---

    @mcp.prompt
    def summarize(path: str) -> str:
        """Summarize a document."""
        return (
            f"Call the `read` tool with path='{path}'. "
            "The result contains a `content` field (the markdown body) and a "
            "`frontmatter` field (metadata). "
            "Write a concise summary covering the document's main topics and "
            "key points. "
            "If `read` returns an error, report it and stop."
        )

    @mcp.prompt(tags={"write"})
    def research(topic: str) -> str:
        """Research a topic and consolidate findings as a new note."""
        slug = re.sub(r"[^\w\-]", "-", topic.lower()).strip("-")
        return (
            f"You are building a research note about: {topic!r}\n\n"
            "1. Call `search` with that query. Use mode='hybrid' if available "
            "(check `stats` first), otherwise mode='keyword'. Examine the top "
            "results; call `read` on the 3-5 highest-scoring paths.\n"
            "2. Write a structured markdown summary of what you found. Link "
            "each source as [document title](its/relative/path.md).\n"
            f"3. Choose a path like Research/{slug}.md. "
            "Call `write` with that path, your content, and "
            "frontmatter={'title': ..., 'tags': ['research']}.\n"
            "If no results are found, tell the user and stop — do not write "
            "an empty note."
        )

    @mcp.prompt(tags={"write"})
    def discuss(path: str) -> str:
        """Analyze a document and suggest improvements."""
        return (
            f"Step 1: Call `read` with path='{path}'. Review the document.\n"
            "Step 2: Identify specific improvements: factual corrections, "
            "clarity, structure, completeness.\n"
            "Step 3: Present your proposed changes to the user before editing. "
            "Then apply each change using `edit`. "
            "`edit` requires an exact `old_text` substring from the document "
            "returned in Step 1 — do not paraphrase. Each `edit` call changes "
            "one location; use multiple calls for multiple changes.\n"
            "Do not use `write` — it overwrites the entire file including "
            "frontmatter.\n"
            "If `read` fails, report the error and stop."
        )

    @mcp.prompt
    def related(path: str) -> str:
        """Find related notes and suggest cross-references."""
        return (
            f"Step 1: Call `read` with path='{path}'. Extract the main topics "
            "and key terms.\n"
            "Step 2: Call `search` using those terms. Use mode='semantic' if "
            "available, otherwise mode='keyword'.\n"
            "Step 3: Present a list of the most relevant related documents. "
            "For each, include: the document title, its path, and one sentence "
            "explaining the connection.\n"
            "Format suggested cross-references as: [title](relative/path.md)\n"
            "Do not edit any documents — this prompt is read-only."
        )

    @mcp.prompt
    def compare(path1: str, path2: str) -> str:
        """Compare two documents."""
        return (
            f"Call `read` for both '{path1}' and '{path2}'. "
            "Use the `content` field from each result for comparison. "
            "Present your comparison covering:\n"
            "- What both documents agree on\n"
            "- Where they differ or contradict\n"
            "- Information present in one but absent from the other\n"
            "If either `read` call fails, report which path was not found "
            "and stop."
        )

    # --- Visibility: hide write-tagged components in read-only mode ---

    if is_read_only:
        mcp.disable(tags={"write"})

    return mcp
