"""Generic markdown collection with FTS5 + semantic search."""

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.config import CollectionConfig, load_config
from markdown_vault_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    MarkdownMCPError,
    ReadOnlyError,
)
from markdown_vault_mcp.git import git_write_strategy
from markdown_vault_mcp.types import (
    ChangeSet,
    Chunk,
    CollectionStats,
    DeleteResult,
    EditResult,
    FTSResult,
    IndexStats,
    NoteContent,
    NoteInfo,
    ParsedNote,
    ReindexResult,
    RenameResult,
    SearchResult,
    WriteCallback,
    WriteResult,
)

__all__ = [
    "ChangeSet",
    "Chunk",
    "Collection",
    "CollectionConfig",
    "CollectionStats",
    "DeleteResult",
    "DocumentExistsError",
    "DocumentNotFoundError",
    "EditConflictError",
    "EditResult",
    "FTSResult",
    "IndexStats",
    "MarkdownMCPError",
    "NoteContent",
    "NoteInfo",
    "ParsedNote",
    "ReadOnlyError",
    "ReindexResult",
    "RenameResult",
    "SearchResult",
    "WriteCallback",
    "WriteResult",
    "git_write_strategy",
    "load_config",
]
