"""Data types for markdown-vault-mcp."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class Chunk:
    """A chunk of a document, typically a section under a heading."""

    heading: str | None
    heading_level: int
    content: str
    start_line: int


@dataclass
class ParsedNote:
    """A parsed markdown document."""

    path: str
    frontmatter: dict[str, Any]
    title: str
    chunks: list[Chunk]
    content_hash: str
    modified_at: float


@dataclass
class SearchResult:
    """A search result from the Collection API."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    search_type: Literal["keyword", "semantic"]
    frontmatter: dict[str, Any]


@dataclass
class FTSResult:
    """A raw search result from the FTS5 index layer."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float


@dataclass
class NoteContent:
    """Full content of a document, returned by read()."""

    path: str
    title: str
    folder: str
    content: str
    frontmatter: dict[str, Any]
    modified_at: float


@dataclass
class NoteInfo:
    """Summary info for a document, returned by list()."""

    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    kind: str = "note"


@dataclass
class WriteResult:
    """Result of a write operation."""

    path: str
    created: bool


@dataclass
class EditResult:
    """Result of an edit operation."""

    path: str
    replacements: int


@dataclass
class DeleteResult:
    """Result of a delete operation."""

    path: str


@dataclass
class RenameResult:
    """Result of a rename operation."""

    old_path: str
    new_path: str


@dataclass
class IndexStats:
    """Statistics from build_index()."""

    documents_indexed: int
    chunks_indexed: int
    skipped: int


@dataclass
class ReindexResult:
    """Result of an incremental reindex."""

    added: int
    modified: int
    deleted: int
    unchanged: int


@dataclass
class AttachmentContent:
    """Full content of an attachment, returned by read() for non-.md files."""

    path: str
    mime_type: str | None
    size_bytes: int
    content_base64: str
    modified_at: float


@dataclass
class AttachmentInfo:
    """Summary info for an attachment, returned by list(include_attachments=True)."""

    path: str
    folder: str
    mime_type: str | None
    size_bytes: int
    modified_at: float
    kind: str = "attachment"


@dataclass
class CollectionStats:
    """Collection-wide statistics."""

    document_count: int
    chunk_count: int
    folder_count: int
    semantic_search_available: bool
    indexed_frontmatter_fields: list[str] = field(default_factory=list)
    attachment_extensions: list[str] = field(default_factory=list)


@dataclass
class ChangeSet:
    """Documents that changed since last index."""

    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: int


WriteCallback = Callable[
    [Path, str, Literal["write", "edit", "delete", "rename"]], None
]
