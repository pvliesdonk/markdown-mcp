"""Generic markdown collection with FTS5 + semantic search."""

from markdown_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    MarkdownMCPError,
    ReadOnlyError,
)
from markdown_mcp.types import (
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
]
