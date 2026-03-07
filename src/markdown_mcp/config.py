"""Configuration loading from environment variables for markdown-mcp.

Reads env vars and returns a :class:`CollectionConfig` suitable for
constructing a :class:`~markdown_mcp.collection.Collection`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_bool(value: str) -> bool:
    """Parse a boolean from an environment variable string.

    Treats ``"true"``, ``"1"``, and ``"yes"`` (case-insensitive) as ``True``.
    Everything else is ``False``.

    Args:
        value: Raw environment variable string.

    Returns:
        ``True`` for truthy strings, ``False`` otherwise.
    """
    return value.strip().lower() in ("true", "1", "yes")


def _parse_list(value: str) -> list[str]:
    """Parse a comma-separated environment variable into a list of strings.

    Splits on commas, strips whitespace from each element, and filters out
    empty strings.

    Args:
        value: Raw environment variable string (e.g. ``"a, b, c"``).

    Returns:
        List of non-empty stripped strings.  Returns ``[]`` when *value* is
        blank.
    """
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class CollectionConfig:
    """Configuration for a :class:`~markdown_mcp.collection.Collection`.

    Attributes:
        source_dir: Root directory of the markdown collection.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_mcp.exceptions.ReadOnlyError`.
        index_path: Path to the persistent SQLite index file.  ``None``
            (default) uses an in-memory database.
        embeddings_path: Base path for vector index sidecar files.  ``None``
            (default) means semantic search is disabled.
        state_path: Path to the hash-state JSON file used by
            :class:`~markdown_mcp.tracker.ChangeTracker`.  ``None`` defaults
            to ``{source_dir}/.markdown_mcp/state.json``.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table for structured filtering.
            ``None`` means no fields are indexed.
        required_frontmatter: If set, documents missing any listed field are
            excluded from the index entirely.  ``None`` means all documents
            are indexed regardless of frontmatter.
        exclude_patterns: Glob patterns matched against relative document
            paths to exclude from scanning (e.g. ``[".obsidian/**"]``).
            ``None`` means no files are excluded.

    Example::

        config = load_config()
        collection = Collection(**config.to_collection_kwargs())
    """

    source_dir: Path
    read_only: bool = True
    index_path: Path | None = None
    embeddings_path: Path | None = None
    state_path: Path | None = None
    indexed_frontmatter_fields: list[str] | None = None
    required_frontmatter: list[str] | None = None
    exclude_patterns: list[str] | None = field(default=None)

    def to_collection_kwargs(self) -> dict:
        """Return keyword arguments suitable for ``Collection(**kwargs)``.

        Excludes :attr:`exclude_patterns` because :class:`Collection` does not
        accept that parameter directly — pass it to
        :func:`~markdown_mcp.scanner.scan_directory` instead.

        Returns:
            Dict of keyword arguments accepted by
            :class:`~markdown_mcp.collection.Collection.__init__`.

        Example::

            config = load_config()
            collection = Collection(**config.to_collection_kwargs())
        """
        kwargs: dict = {
            "source_dir": self.source_dir,
            "read_only": self.read_only,
            "index_path": self.index_path,
            "embeddings_path": self.embeddings_path,
            "state_path": self.state_path,
            "indexed_frontmatter_fields": self.indexed_frontmatter_fields,
            "required_frontmatter": self.required_frontmatter,
        }
        return kwargs


def load_config() -> CollectionConfig:
    """Load configuration from environment variables.

    Reads the following environment variables:

    - ``MARKDOWN_MCP_SOURCE_DIR`` (required): path to markdown files.
    - ``MARKDOWN_MCP_READ_ONLY``: disable write tools; default ``true``.
    - ``MARKDOWN_MCP_INDEX_PATH``: SQLite index path; default in-memory.
    - ``MARKDOWN_MCP_EMBEDDINGS_PATH``: embeddings directory; default disabled.
    - ``MARKDOWN_MCP_STATE_PATH``: state file path; default
      ``{source_dir}/.markdown_mcp/state.json``.
    - ``MARKDOWN_MCP_INDEXED_FIELDS``: comma-separated frontmatter fields to
      index; default none.
    - ``MARKDOWN_MCP_REQUIRED_FIELDS``: comma-separated required frontmatter
      fields; default none.
    - ``MARKDOWN_MCP_EXCLUDE``: comma-separated glob patterns to exclude;
      default none.

    The ``EMBEDDING_PROVIDER`` variable is intentionally **not** resolved here;
    call :func:`~markdown_mcp.providers.get_embedding_provider` separately in
    the server layer.

    Returns:
        A fully populated :class:`CollectionConfig` instance.

    Raises:
        ValueError: If ``MARKDOWN_MCP_SOURCE_DIR`` is not set.

    Example::

        import os
        os.environ["MARKDOWN_MCP_SOURCE_DIR"] = "/home/user/vault"
        config = load_config()
        collection = Collection(**config.to_collection_kwargs())
    """
    raw_source_dir = os.environ.get("MARKDOWN_MCP_SOURCE_DIR", "").strip()
    if not raw_source_dir:
        raise ValueError(
            "MARKDOWN_MCP_SOURCE_DIR is required but not set. "
            "Set it to the path of your markdown collection."
        )
    source_dir = Path(raw_source_dir)
    logger.debug("load_config: source_dir=%s", source_dir)

    raw_read_only = os.environ.get("MARKDOWN_MCP_READ_ONLY", "true")
    read_only = _parse_bool(raw_read_only)
    logger.debug("load_config: read_only=%s (raw=%r)", read_only, raw_read_only)

    raw_index_path = os.environ.get("MARKDOWN_MCP_INDEX_PATH", "").strip()
    index_path: Path | None = Path(raw_index_path) if raw_index_path else None
    logger.debug("load_config: index_path=%s", index_path)

    raw_embeddings_path = os.environ.get("MARKDOWN_MCP_EMBEDDINGS_PATH", "").strip()
    embeddings_path: Path | None = (
        Path(raw_embeddings_path) if raw_embeddings_path else None
    )
    logger.debug("load_config: embeddings_path=%s", embeddings_path)

    raw_state_path = os.environ.get("MARKDOWN_MCP_STATE_PATH", "").strip()
    state_path: Path | None = Path(raw_state_path) if raw_state_path else None
    logger.debug("load_config: state_path=%s", state_path)

    raw_indexed_fields = os.environ.get("MARKDOWN_MCP_INDEXED_FIELDS", "").strip()
    indexed_frontmatter_fields: list[str] | None = (
        _parse_list(raw_indexed_fields) or None if raw_indexed_fields else None
    )
    logger.debug(
        "load_config: indexed_frontmatter_fields=%s", indexed_frontmatter_fields
    )

    raw_required_fields = os.environ.get("MARKDOWN_MCP_REQUIRED_FIELDS", "").strip()
    required_frontmatter: list[str] | None = (
        _parse_list(raw_required_fields) or None if raw_required_fields else None
    )
    logger.debug("load_config: required_frontmatter=%s", required_frontmatter)

    raw_exclude = os.environ.get("MARKDOWN_MCP_EXCLUDE", "").strip()
    exclude_patterns: list[str] | None = (
        _parse_list(raw_exclude) or None if raw_exclude else None
    )
    logger.debug("load_config: exclude_patterns=%s", exclude_patterns)

    return CollectionConfig(
        source_dir=source_dir,
        read_only=read_only,
        index_path=index_path,
        embeddings_path=embeddings_path,
        state_path=state_path,
        indexed_frontmatter_fields=indexed_frontmatter_fields,
        required_frontmatter=required_frontmatter,
        exclude_patterns=exclude_patterns,
    )
