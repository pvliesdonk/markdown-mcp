"""File discovery, frontmatter parsing, and chunking for markdown-vault-mcp."""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import frontmatter

from markdown_vault_mcp.types import Chunk, ParsedNote

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# Threshold below which a document is not split (single chunk).
_SHORT_DOC_LINES = 30


@runtime_checkable
class ChunkStrategy(Protocol):
    """Protocol for document chunking strategies."""

    def chunk(self, content: str, metadata: dict[str, Any]) -> list[Chunk]:
        """Chunk the markdown body into sections.

        Args:
            content: Markdown body after frontmatter has been stripped.
            metadata: Parsed frontmatter dict (for context, not modification).

        Returns:
            List of Chunk objects.
        """
        ...


class WholeDocumentChunker:
    """Returns the entire document as a single chunk.

    Suitable for short documents or when per-section search is not needed.
    """

    def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
        """Return the document as one chunk.

        Args:
            content: Markdown body after frontmatter has been stripped.
            _metadata: Parsed frontmatter dict (unused by this strategy).

        Returns:
            A list containing exactly one Chunk covering the full document.
        """
        return [
            Chunk(
                heading=None,
                heading_level=0,
                content=content,
                start_line=0,
            )
        ]


class HeadingChunker:
    """Split document on H1/H2 boundaries.

    Short documents (fewer than ``short_doc_lines`` lines) are returned as a
    single chunk without splitting. Each chunk receives the heading text and
    level of the section it starts with; a preamble before the first heading
    gets ``heading=None`` and ``heading_level=0``.

    This is the default chunking strategy.
    """

    def __init__(self, short_doc_lines: int = _SHORT_DOC_LINES) -> None:
        """Initialise the chunker.

        Args:
            short_doc_lines: Line count at or below which the document is
                returned as a single chunk rather than split on headings.
        """
        self.short_doc_lines = short_doc_lines

    def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
        """Split content on H1/H2 boundaries.

        Args:
            content: Markdown body after frontmatter has been stripped.
            _metadata: Parsed frontmatter dict (for context, not modification).

        Returns:
            List of Chunk objects, one per section. Short documents return a
            single Chunk.
        """
        lines = content.splitlines(keepends=True)

        # Short documents: no split.
        if len(lines) <= self.short_doc_lines:
            return [
                Chunk(
                    heading=None,
                    heading_level=0,
                    content=content,
                    start_line=0,
                )
            ]

        # Walk lines and record where H1/H2 headings appear.
        split_points: list[tuple[int, int, str]] = []  # (line_index, level, text)
        for idx, line in enumerate(lines):
            m = re.match(r"^(#{1,2})\s+(.+)$", line.rstrip())
            if m:
                level = len(m.group(1))
                text = m.group(2).strip()
                split_points.append((idx, level, text))

        # No headings found: single chunk.
        if not split_points:
            return [
                Chunk(
                    heading=None,
                    heading_level=0,
                    content=content,
                    start_line=0,
                )
            ]

        chunks: list[Chunk] = []

        # Preamble: content before the first heading.
        first_heading_line = split_points[0][0]
        if first_heading_line > 0:
            preamble = "".join(lines[:first_heading_line])
            if preamble.strip():
                chunks.append(
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content=preamble,
                        start_line=0,
                    )
                )

        # Sections between headings.
        for i, (line_idx, level, heading_text) in enumerate(split_points):
            # Content runs from the line after the heading to the next split.
            content_start = line_idx + 1
            if i + 1 < len(split_points):
                content_end = split_points[i + 1][0]
            else:
                content_end = len(lines)

            section_content = "".join(lines[content_start:content_end])
            # Skip heading-only sections that have no meaningful body content.
            if not section_content.strip():
                continue
            chunks.append(
                Chunk(
                    heading=heading_text,
                    heading_level=level,
                    content=section_content,
                    start_line=line_idx,
                )
            )

        return chunks


def _resolve_title(metadata: dict[str, Any], content: str, path: Path) -> str:
    """Resolve the document title using the priority order from the design spec.

    Priority: frontmatter ``title`` field → first H1 heading → filename
    without extension.

    Args:
        metadata: Parsed frontmatter dict.
        content: Markdown body (frontmatter stripped).
        path: Absolute path to the file (used for filename fallback).

    Returns:
        Resolved title string.
    """
    # 1. Frontmatter title field.
    if "title" in metadata and isinstance(metadata["title"], str):
        title = metadata["title"].strip()
        if title:
            return title

    # 2. First H1 heading in content.
    for line in content.splitlines():
        m = re.match(r"^#\s+(.+)$", line.rstrip())
        if m:
            return m.group(1).strip()

    # 3. Filename without extension.
    return path.stem


def parse_note(
    path: Path,
    source_dir: Path,
    chunk_strategy: ChunkStrategy | None = None,
) -> ParsedNote:
    """Parse a single markdown file into a ParsedNote.

    Reads raw bytes for hash computation, decodes as UTF-8, parses frontmatter
    with ``python-frontmatter``, resolves title, and chunks content.

    Args:
        path: Absolute path to the markdown file.
        source_dir: Root directory of the collection; used to derive the
            document's relative identity path.
        chunk_strategy: Chunking strategy to apply. Defaults to
            :class:`HeadingChunker`.

    Returns:
        A :class:`~markdown_vault_mcp.types.ParsedNote` instance.

    Raises:
        UnicodeDecodeError: If the file cannot be decoded as UTF-8. Callers
            such as :func:`scan_directory` catch this and skip the file.
    """
    if chunk_strategy is None:
        chunk_strategy = HeadingChunker()

    raw_bytes = path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    modified_at = path.stat().st_mtime

    # May raise UnicodeDecodeError — propagated to caller.
    text = raw_bytes.decode("utf-8")

    # python-frontmatter strips the YAML block and returns the body separately.
    post = frontmatter.loads(text)
    metadata: dict[str, Any] = dict(post.metadata)
    body: str = post.content

    title = _resolve_title(metadata, body, path)

    # Relative path from source_dir, always using forward slashes.
    rel_path = path.relative_to(source_dir)
    rel_str = rel_path.as_posix()

    chunks = chunk_strategy.chunk(body, metadata)

    return ParsedNote(
        path=rel_str,
        frontmatter=metadata,
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
    )


def scan_directory(
    source_dir: Path,
    *,
    glob_pattern: str = "**/*.md",
    exclude_patterns: list[str] | None = None,
    required_frontmatter: list[str] | None = None,
    chunk_strategy: ChunkStrategy | None = None,
) -> Iterator[ParsedNote]:
    """Discover and parse all markdown files under ``source_dir``.

    Yields :class:`~markdown_vault_mcp.types.ParsedNote` objects. Fault-tolerant: a
    single bad file (UTF-8 decode error, I/O error) is skipped with a
    ``WARNING`` log entry; the scan continues.

    Args:
        source_dir: Root directory to scan.
        glob_pattern: Glob pattern relative to ``source_dir`` that selects
            files to scan. Defaults to ``"**/*.md"``.
        exclude_patterns: List of glob patterns matched against each file's
            relative POSIX path using :func:`fnmatch.fnmatch`. Files whose
            path matches any pattern are excluded. Supports ``**`` on all
            Python versions (unlike :meth:`pathlib.Path.match` in < 3.12).
            Example: ``[".obsidian/**", "_templates/**"]``.
        required_frontmatter: If provided, documents missing any of the listed
            frontmatter fields are excluded from the results. The number of
            skipped documents is logged at ``INFO`` level after the scan.
        chunk_strategy: Chunking strategy to pass to :func:`parse_note`.
            Defaults to :class:`HeadingChunker`.

    Yields:
        Parsed notes in filesystem traversal order.
    """
    exclude_patterns = exclude_patterns or []
    skipped_required: int = 0

    for abs_path in sorted(source_dir.glob(glob_pattern)):
        if not abs_path.is_file():
            continue

        # Compute relative path for exclude matching.
        try:
            rel = abs_path.relative_to(source_dir)
        except ValueError:
            # Shouldn't happen, but be safe.
            logger.warning("File outside source_dir, skipping: %s", abs_path)
            continue

        # Check exclude patterns against the relative POSIX path string.
        # fnmatch is used instead of Path.match() because Path.match() does
        # not support ** patterns in Python < 3.12.
        rel_posix = rel.as_posix()
        if any(fnmatch.fnmatch(rel_posix, pat) for pat in exclude_patterns):
            logger.debug("Excluding %s (matched exclude pattern)", rel)
            continue

        # Parse the file; skip on decode / I/O / YAML errors.
        try:
            note = parse_note(abs_path, source_dir, chunk_strategy)
        except UnicodeDecodeError:
            logger.warning(
                "Skipping %s: cannot decode as UTF-8", abs_path, exc_info=False
            )
            continue
        except OSError as exc:
            logger.warning("Skipping %s: I/O error (%s)", abs_path, exc)
            continue
        except Exception as exc:
            logger.warning(
                "Skipping %s: parse error (%s)", abs_path, exc, exc_info=True
            )
            continue

        # Apply required_frontmatter filter.
        if required_frontmatter:
            missing = [
                field for field in required_frontmatter if field not in note.frontmatter
            ]
            if missing:
                logger.debug(
                    "Skipping %s: missing required frontmatter fields: %s",
                    rel,
                    missing,
                )
                skipped_required += 1
                continue

        yield note

    if skipped_required:
        logger.info(
            "%d document(s) skipped due to missing required frontmatter fields.",
            skipped_required,
        )
