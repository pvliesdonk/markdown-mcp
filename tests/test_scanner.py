"""Tests for markdown_vault_mcp.scanner module."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
    parse_note,
    scan_directory,
)
from markdown_vault_mcp.types import Chunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_folder(path: str) -> str:
    """Derive the folder string from a relative document path.

    Mirrors the logic in fts_index._derive_folder: parent directory of the
    path, with "." replaced by "" for root-level documents.
    """
    parent = Path(path).parent.as_posix()
    return "" if parent == "." else parent


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_scan_directory_discovers_all_md_files(fixtures_path: Path) -> None:
    """scan_directory yields all .md files except invalid_utf8 and malformed_yaml.

    invalid_utf8.md is skipped due to UnicodeDecodeError.
    malformed_yaml.md is skipped due to YAML parse error.
    Both are handled gracefully without aborting the scan.
    """
    notes = list(
        scan_directory(
            fixtures_path,
        )
    )
    paths = {n.path for n in notes}

    expected = {
        "simple.md",
        "full_frontmatter.md",
        "minimal_frontmatter.md",
        "no_frontmatter.md",
        "deep_headings.md",
        "unicode.md",
        "empty.md",
        "subfolder/nested.md",
        "subfolder/deep/doc.md",
    }
    assert paths == expected


# ---------------------------------------------------------------------------
# Frontmatter and title resolution
# ---------------------------------------------------------------------------


def test_parse_note_with_frontmatter(fixtures_path: Path) -> None:
    """parse_note reads frontmatter fields and derives title from them."""
    note = parse_note(fixtures_path / "full_frontmatter.md", fixtures_path)

    assert note.title == "Full Frontmatter Note"
    assert note.frontmatter["cluster"] == "fiction"
    assert note.frontmatter["author"] == "Test Author"
    assert "horror" in note.frontmatter["topics"]
    assert "gothic" in note.frontmatter["topics"]
    assert "dark" in note.frontmatter["tags"]
    # Must produce at least one chunk.
    assert len(note.chunks) >= 1


def test_parse_note_without_frontmatter(fixtures_path: Path) -> None:
    """parse_note returns an empty frontmatter dict when no YAML block exists."""
    note = parse_note(fixtures_path / "no_frontmatter.md", fixtures_path)

    assert note.frontmatter == {}


def test_parse_note_minimal_frontmatter(fixtures_path: Path) -> None:
    """parse_note correctly parses a frontmatter block with only a title field."""
    note = parse_note(fixtures_path / "minimal_frontmatter.md", fixtures_path)

    assert note.title == "Minimal Note"
    assert list(note.frontmatter.keys()) == ["title"]


def test_title_from_h1(fixtures_path: Path) -> None:
    """When there is no frontmatter title, the first H1 heading becomes the title."""
    note = parse_note(fixtures_path / "simple.md", fixtures_path)

    # simple.md has no frontmatter; its first line is "# Simple Document".
    assert note.title == "Simple Document"
    assert note.frontmatter == {}


def test_title_from_filename(fixtures_path: Path) -> None:
    """When there is no frontmatter title and no H1, the filename stem is the title."""
    note = parse_note(fixtures_path / "no_frontmatter.md", fixtures_path)

    # no_frontmatter.md has no frontmatter and no H1 heading.
    assert note.title == "no_frontmatter"


# ---------------------------------------------------------------------------
# HeadingChunker
# ---------------------------------------------------------------------------


def test_heading_chunker_splits_on_h1_h2(fixtures_path: Path) -> None:
    """HeadingChunker splits simple.md into two chunks at H1 and H2 boundaries.

    short_doc_lines=0 disables the short-document bypass so the 7-line fixture
    is actually split.
    """
    chunker = HeadingChunker(short_doc_lines=0)
    note = parse_note(fixtures_path / "simple.md", fixtures_path, chunker)

    assert len(note.chunks) == 2

    h1_chunk = note.chunks[0]
    assert h1_chunk.heading == "Simple Document"
    assert h1_chunk.heading_level == 1

    h2_chunk = note.chunks[1]
    assert h2_chunk.heading == "Section Two"
    assert h2_chunk.heading_level == 2


def test_heading_chunker_deep_headings(fixtures_path: Path) -> None:
    """HeadingChunker only splits on H1/H2; H3 and H4 do not create new chunks.

    deep_headings.md has H1, H2, H3, H4 headings.  Only the H1 and H2 form
    chunk boundaries, so the result must have exactly 2 chunks.
    """
    chunker = HeadingChunker(short_doc_lines=0)
    note = parse_note(fixtures_path / "deep_headings.md", fixtures_path, chunker)

    assert len(note.chunks) == 2

    levels = [c.heading_level for c in note.chunks]
    assert levels == [1, 2]

    # H3/H4 content must appear inside the H2 chunk, not as a separate chunk.
    h2_chunk = note.chunks[1]
    assert "### Heading 3" in h2_chunk.content
    assert "#### Heading 4" in h2_chunk.content


def test_heading_chunker_short_doc_bypass(fixtures_path: Path) -> None:
    """HeadingChunker returns a single chunk for documents under short_doc_lines."""
    # simple.md has 7 lines, well under the default 30-line threshold.
    chunker = HeadingChunker()  # default short_doc_lines=30
    note = parse_note(fixtures_path / "simple.md", fixtures_path, chunker)

    assert len(note.chunks) == 1
    assert note.chunks[0].heading is None
    assert note.chunks[0].heading_level == 0


# ---------------------------------------------------------------------------
# WholeDocumentChunker
# ---------------------------------------------------------------------------


def test_whole_document_chunker(fixtures_path: Path) -> None:
    """WholeDocumentChunker always returns exactly one chunk for any document."""
    chunker = WholeDocumentChunker()
    note = parse_note(fixtures_path / "full_frontmatter.md", fixtures_path, chunker)

    assert len(note.chunks) == 1
    chunk = note.chunks[0]
    assert chunk.heading is None
    assert chunk.heading_level == 0
    # The single chunk must include the body content.
    assert "Full Frontmatter Note" in chunk.content


# ---------------------------------------------------------------------------
# Required-frontmatter filtering
# ---------------------------------------------------------------------------


def test_required_frontmatter_filters(fixtures_path: Path) -> None:
    """scan_directory excludes documents missing required frontmatter fields.

    Requiring both 'title' and 'cluster' should match only full_frontmatter.md,
    since it is the only fixture that contains a 'cluster' field.
    """
    notes = list(
        scan_directory(
            fixtures_path,
            required_frontmatter=["title", "cluster"],
        )
    )

    assert len(notes) == 1
    assert notes[0].path == "full_frontmatter.md"


# ---------------------------------------------------------------------------
# UTF-8 fault tolerance
# ---------------------------------------------------------------------------


def test_utf8_fault_tolerance(fixtures_path: Path) -> None:
    """scan_directory skips files that cannot be decoded as UTF-8.

    invalid_utf8.md contains raw non-UTF-8 bytes.  The scan must complete
    without raising, and that file must be absent from the results.
    """
    notes = list(
        scan_directory(
            fixtures_path,
        )
    )
    paths = {n.path for n in notes}
    assert "invalid_utf8.md" not in paths
    assert "malformed_yaml.md" not in paths


def test_parse_note_invalid_utf8_raises(fixtures_path: Path) -> None:
    """parse_note propagates UnicodeDecodeError for non-UTF-8 files."""
    with pytest.raises(UnicodeDecodeError):
        parse_note(fixtures_path / "invalid_utf8.md", fixtures_path)


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


def test_exclude_patterns(fixtures_path: Path) -> None:
    """scan_directory excludes files whose relative path matches exclude_patterns.

    Using patterns 'subfolder/*' and 'subfolder/**/*' excludes both direct
    children and deeper descendants of subfolder/.
    """
    notes = list(
        scan_directory(
            fixtures_path,
            exclude_patterns=[
                "subfolder/*",
                "subfolder/**/*",
            ],
        )
    )
    paths = {n.path for n in notes}

    assert "subfolder/nested.md" not in paths
    assert "subfolder/deep/doc.md" not in paths
    # Root-level documents are unaffected.
    assert "simple.md" in paths


# ---------------------------------------------------------------------------
# Folder derivation
# ---------------------------------------------------------------------------


def test_folder_derivation(fixtures_path: Path) -> None:
    """Folder is derived as the parent directory of the relative path.

    Root-level documents have folder '', nested documents have their parent
    directory as folder (e.g. 'subfolder').
    """
    note_root = parse_note(fixtures_path / "simple.md", fixtures_path)
    note_sub = parse_note(fixtures_path / "subfolder/nested.md", fixtures_path)

    assert _derive_folder(note_root.path) == ""
    assert _derive_folder(note_sub.path) == "subfolder"


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


def test_content_hash_is_sha256(fixtures_path: Path) -> None:
    """content_hash is the SHA-256 hex digest of the raw file bytes (64 chars)."""
    target = fixtures_path / "simple.md"
    note = parse_note(target, fixtures_path)

    expected = hashlib.sha256(target.read_bytes()).hexdigest()

    assert len(note.content_hash) == 64
    assert note.content_hash == expected


# ---------------------------------------------------------------------------
# Unicode content
# ---------------------------------------------------------------------------


def test_unicode_content(fixtures_path: Path) -> None:
    """parse_note preserves Unicode content (Japanese, accented, emoji)."""
    note = parse_note(fixtures_path / "unicode.md", fixtures_path)

    # Title comes from frontmatter.
    assert note.title == "ユニコード文書"

    full_content = " ".join(c.content for c in note.chunks)
    assert "日本語テスト" in full_content
    assert "Ñoño" in full_content
    assert "🎉" in full_content


# ---------------------------------------------------------------------------
# Chunk start lines
# ---------------------------------------------------------------------------


def test_chunk_start_lines(fixtures_path: Path) -> None:
    """Each chunk's start_line is a non-negative integer.

    When HeadingChunker splits a document the second chunk must start after
    the first heading line (start_line > 0).
    """
    chunker = HeadingChunker(short_doc_lines=0)
    note = parse_note(fixtures_path / "simple.md", fixtures_path, chunker)

    for chunk in note.chunks:
        assert isinstance(chunk.start_line, int)
        assert chunk.start_line >= 0

    # The H1 chunk starts at line 0 (the heading itself).
    assert note.chunks[0].start_line == 0
    # The H2 chunk must start at a later line.
    assert note.chunks[1].start_line > 0


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_chunk_strategy_protocol() -> None:
    """HeadingChunker and WholeDocumentChunker satisfy the ChunkStrategy Protocol."""
    heading_chunker = HeadingChunker()
    whole_chunker = WholeDocumentChunker()

    assert isinstance(heading_chunker, ChunkStrategy)
    assert isinstance(whole_chunker, ChunkStrategy)


def test_chunk_strategy_protocol_custom() -> None:
    """A custom class with a matching chunk() signature satisfies ChunkStrategy."""

    class MyChunker:
        def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
            return [Chunk(heading=None, heading_level=0, content=content, start_line=0)]

    assert isinstance(MyChunker(), ChunkStrategy)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_file(fixtures_path: Path) -> None:
    """parse_note handles a 0-byte file without raising."""
    note = parse_note(fixtures_path / "empty.md", fixtures_path)

    # Title falls back to filename stem.
    assert note.title == "empty"
    assert note.frontmatter == {}
    assert len(note.chunks) == 1


def test_malformed_yaml_raises(fixtures_path: Path) -> None:
    """parse_note propagates ParserError for malformed YAML frontmatter."""
    import yaml

    with pytest.raises(yaml.YAMLError):
        parse_note(fixtures_path / "malformed_yaml.md", fixtures_path)


def test_relative_path_uses_forward_slashes(fixtures_path: Path) -> None:
    """ParsedNote.path always uses forward slashes regardless of OS."""
    note = parse_note(fixtures_path / "subfolder" / "deep" / "doc.md", fixtures_path)

    assert note.path == "subfolder/deep/doc.md"
    assert "\\" not in note.path


def test_parse_note_path_relative_to_source_dir(fixtures_path: Path) -> None:
    """ParsedNote.path is relative to source_dir, not absolute."""
    note = parse_note(fixtures_path / "subfolder/nested.md", fixtures_path)

    assert not Path(note.path).is_absolute()
    assert note.path == "subfolder/nested.md"


# ---------------------------------------------------------------------------
# HeadingChunker preamble (content before first heading)
# ---------------------------------------------------------------------------


class TestHeadingChunkerPreamble:
    def test_preamble_before_first_heading_is_its_own_chunk(
        self, tmp_path: Path
    ) -> None:
        """Text before the first H1/H2 becomes a preamble chunk with heading=None."""
        # Build a document long enough to avoid the short-doc bypass (>30 lines).
        intro_lines = ["Introductory text — no heading yet.\n"] * 5
        padding = [f"More intro line {i}.\n" for i in range(30)]
        heading_section = [
            "# First Section\n",
            "Section body content.\n",
            "More section content.\n",
        ]
        content = "".join(intro_lines + padding + heading_section)

        doc = tmp_path / "with_preamble.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        # There must be at least two chunks: a preamble and the first section.
        assert len(note.chunks) >= 2

        preamble = note.chunks[0]
        assert preamble.heading is None
        assert preamble.heading_level == 0
        assert "Introductory text" in preamble.content

        section = note.chunks[1]
        assert section.heading == "First Section"
        assert section.heading_level == 1

    def test_no_preamble_when_heading_is_first_line(self, tmp_path: Path) -> None:
        """When the document starts immediately with a heading, no preamble chunk."""
        # 35+ lines starting directly with a heading.
        lines = ["# Opening Heading\n"]
        lines += [f"body line {i}\n" for i in range(35)]
        content = "".join(lines)

        doc = tmp_path / "heading_first.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        # All chunks must have a non-None heading (no preamble).
        assert all(c.heading is not None for c in note.chunks)


# ---------------------------------------------------------------------------
# HeadingChunker empty section body skipped
# ---------------------------------------------------------------------------


class TestHeadingChunkerEmptySection:
    def test_empty_body_section_not_in_chunks(self, tmp_path: Path) -> None:
        """A heading immediately followed by the next heading (no body) is skipped."""
        # Three headings; the first has no body content.
        lines = ["# Ghost Heading\n"]  # line 0 — empty body
        lines += ["## Real Section\n"]  # line 1
        lines += [f"Real content line {i}.\n" for i in range(35)]

        content = "".join(lines)
        doc = tmp_path / "empty_section.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        headings = [c.heading for c in note.chunks]
        # "Ghost Heading" had no body — it must not appear as a chunk.
        assert "Ghost Heading" not in headings
        # "Real Section" has body content — it must appear.
        assert "Real Section" in headings

    def test_multiple_consecutive_empty_sections_skipped(self, tmp_path: Path) -> None:
        """Multiple consecutive heading-only sections are all skipped."""
        lines = [
            "# Empty A\n",
            "# Empty B\n",
            "# Empty C\n",
            "## Content Section\n",
        ]
        lines += [f"content {i}\n" for i in range(35)]

        content = "".join(lines)
        doc = tmp_path / "multi_empty.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        headings = [c.heading for c in note.chunks]
        assert "Empty A" not in headings
        assert "Empty B" not in headings
        assert "Empty C" not in headings
        assert "Content Section" in headings
