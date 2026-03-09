"""Unit tests for FTSIndex (fts_index.py)."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.types import Chunk, FTSResult, ParsedNote

# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def make_note(
    path: str = "test.md",
    title: str = "Test",
    frontmatter: dict | None = None,
    chunks: list[Chunk] | None = None,
    content_hash: str = "abc123",
    modified_at: float = 1000.0,
) -> ParsedNote:
    """Create a ParsedNote for testing.

    Args:
        path: Relative document path including ``.md`` extension.
        title: Document title.
        frontmatter: Frontmatter metadata dict. Defaults to ``{}``.
        chunks: List of chunks. Defaults to a single generic chunk.
        content_hash: Hash string stored in the note.
        modified_at: Modification timestamp.

    Returns:
        A fully-populated :class:`ParsedNote` suitable for indexing.
    """
    if chunks is None:
        chunks = [
            Chunk(heading="Test", heading_level=1, content="Test content", start_line=0)
        ]
    return ParsedNote(
        path=path,
        frontmatter=frontmatter or {},
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
    )


# ---------------------------------------------------------------------------
# Helpers for building a tagged index in multiple tests
# ---------------------------------------------------------------------------

_INDEXED_FIELDS = ["cluster", "topics", "genre"]


def _tagged_index() -> FTSIndex:
    """Return a fresh in-memory index with the standard indexed fields."""
    return FTSIndex(":memory:", indexed_frontmatter_fields=_INDEXED_FIELDS)


# ===========================================================================
# Tests
# ===========================================================================


class TestBuildFromNotes:
    def test_build_from_notes_returns_total_chunk_count(self) -> None:
        """build_from_notes returns the total number of chunks indexed."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                "a.md",
                chunks=[
                    Chunk(heading="H1", heading_level=1, content="alpha", start_line=0),
                    Chunk(heading="H2", heading_level=2, content="beta", start_line=5),
                ],
            ),
            make_note(
                "b.md",
                chunks=[
                    Chunk(heading="B1", heading_level=1, content="gamma", start_line=0),
                ],
            ),
            make_note(
                "c.md",
                chunks=[
                    Chunk(heading="C1", heading_level=1, content="delta", start_line=0),
                    Chunk(
                        heading="C2", heading_level=2, content="epsilon", start_line=3
                    ),
                    Chunk(heading="C3", heading_level=2, content="zeta", start_line=6),
                ],
            ),
        ]
        total = idx.build_from_notes(notes)
        assert total == 6


class TestSearch:
    def test_search_returns_fts_results(self) -> None:
        """search() returns FTSResult objects for matching terms."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "dragons.md",
                title="Dragons",
                chunks=[
                    Chunk(
                        heading="Overview",
                        heading_level=1,
                        content="Dragons breathe fire and hoard treasure.",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("dragons")
        assert len(results) >= 1
        assert all(isinstance(r, FTSResult) for r in results)
        paths = {r.path for r in results}
        assert "dragons.md" in paths

    def test_search_bm25_ranking_orders_by_relevance(self) -> None:
        """More-relevant documents score higher than less-relevant ones."""
        idx = FTSIndex(":memory:")
        # "python" appears many times in high.md, once in low.md
        idx.upsert_note(
            make_note(
                "high.md",
                title="High relevance",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="python python python python python programming",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "low.md",
                title="Low relevance",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="python is mentioned once here among other words",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("python", limit=10)
        assert len(results) == 2
        high_result = next(r for r in results if r.path == "high.md")
        low_result = next(r for r in results if r.path == "low.md")
        assert high_result.score > low_result.score

    def test_search_with_folder_filter(self) -> None:
        """folder= filter returns only documents under that folder."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "Journal/2024-01.md",
                title="January",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="today I went for a walk",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "Projects/alpha.md",
                title="Alpha",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="today the project started",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("today", folder="Journal")
        assert len(results) == 1
        assert results[0].path == "Journal/2024-01.md"
        assert results[0].folder == "Journal"

    def test_search_with_tag_filters(self) -> None:
        """filters= restricts results to documents matching the tag pair."""
        idx = _tagged_index()
        idx.upsert_note(
            make_note(
                "fiction/story.md",
                title="Story",
                frontmatter={"cluster": "fiction"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="once upon a time",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "nonfiction/essay.md",
                title="Essay",
                frontmatter={"cluster": "nonfiction"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="once upon a time there were facts",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("once", filters={"cluster": "fiction"})
        assert len(results) == 1
        assert results[0].path == "fiction/story.md"

    def test_search_multiple_filters_anded(self) -> None:
        """Multiple filter entries are ANDed — only docs matching ALL pass."""
        idx = _tagged_index()
        # Matches cluster=fiction but not genre=horror
        idx.upsert_note(
            make_note(
                "a.md",
                frontmatter={"cluster": "fiction", "genre": "romance"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="love story",
                        start_line=0,
                    )
                ],
            )
        )
        # Matches both cluster=fiction AND genre=horror
        idx.upsert_note(
            make_note(
                "b.md",
                frontmatter={"cluster": "fiction", "genre": "horror"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="scary story",
                        start_line=0,
                    )
                ],
            )
        )
        # Matches genre=horror but not cluster=fiction
        idx.upsert_note(
            make_note(
                "c.md",
                frontmatter={"cluster": "nonfiction", "genre": "horror"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="true horror story",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("story", filters={"cluster": "fiction", "genre": "horror"})
        assert len(results) == 1
        assert results[0].path == "b.md"


class TestUpsert:
    def test_upsert_note_replaces_existing(self) -> None:
        """upsert_note removes old content and makes new content searchable."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "replace.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="the old unique word xylophone",
                        start_line=0,
                    )
                ],
                content_hash="old",
            )
        )
        # Sanity: old content is searchable before upsert.
        assert len(idx.search("xylophone")) == 1

        idx.upsert_note(
            make_note(
                "replace.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="entirely new content kazoo",
                        start_line=0,
                    )
                ],
                content_hash="new",
            )
        )
        assert idx.search("xylophone") == []
        results = idx.search("kazoo")
        assert len(results) == 1
        assert results[0].path == "replace.md"


class TestFrontmatterSerialization:
    def test_date_in_frontmatter_is_serialized(self) -> None:
        """Frontmatter containing datetime.date values should be indexed."""
        import datetime

        idx = FTSIndex(":memory:")
        note = make_note(
            "dated.md",
            frontmatter={"created": datetime.date(2024, 1, 15), "title": "Dated"},
        )
        idx.upsert_note(note)
        results = idx.search("Test content")
        assert len(results) == 1
        assert results[0].path == "dated.md"

    def test_datetime_in_frontmatter_is_serialized(self) -> None:
        """Frontmatter containing datetime.datetime values should be indexed."""
        import datetime

        idx = FTSIndex(":memory:")
        note = make_note(
            "timestamped.md",
            frontmatter={
                "updated": datetime.datetime(2024, 6, 15, 12, 30, 0),
            },
        )
        idx.upsert_note(note)
        results = idx.search("Test content")
        assert len(results) == 1


class TestDelete:
    def test_delete_by_path_removes_search_results(self) -> None:
        """delete_by_path makes the note unsearchable."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "gone.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="the unique word fjord",
                        start_line=0,
                    )
                ],
            )
        )
        assert len(idx.search("fjord")) == 1

        deleted = idx.delete_by_path("gone.md")
        assert deleted == 1
        assert idx.search("fjord") == []

    def test_delete_cascades_to_sections_and_tags(self) -> None:
        """Deleting a document removes its sections and tags from the DB."""
        idx = _tagged_index()
        idx.upsert_note(
            make_note(
                "cascade.md",
                frontmatter={"cluster": "fiction"},
                chunks=[
                    Chunk(
                        heading="Ch1", heading_level=1, content="content", start_line=0
                    ),
                    Chunk(heading="Ch2", heading_level=2, content="more", start_line=5),
                ],
            )
        )

        # Verify sections and tags exist before deletion.
        conn = idx._conn
        sec_count = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE document_id IN "
            "(SELECT id FROM documents WHERE path = ?)",
            ("cascade.md",),
        ).fetchone()[0]
        assert sec_count == 2

        tag_count = conn.execute(
            "SELECT COUNT(*) FROM document_tags WHERE document_id IN "
            "(SELECT id FROM documents WHERE path = ?)",
            ("cascade.md",),
        ).fetchone()[0]
        assert tag_count == 1

        idx.delete_by_path("cascade.md")

        # Documents row is gone — CASCADE should have cleared child rows.
        doc_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE path = ?", ("cascade.md",)
        ).fetchone()[0]
        assert doc_count == 0

        # Sections and tags for the deleted document must also be gone.
        orphan_secs = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE document_id NOT IN "
            "(SELECT id FROM documents)"
        ).fetchone()[0]
        assert orphan_secs == 0

        orphan_tags = conn.execute(
            "SELECT COUNT(*) FROM document_tags WHERE document_id NOT IN "
            "(SELECT id FROM documents)"
        ).fetchone()[0]
        assert orphan_tags == 0


class TestListFolders:
    def test_list_folders_returns_sorted_distinct_values(self) -> None:
        """list_folders() returns all distinct folder values in sorted order."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note("Journal/jan.md"))
        idx.upsert_note(make_note("Journal/feb.md"))
        idx.upsert_note(make_note("Projects/alpha.md"))
        idx.upsert_note(make_note("root.md"))

        folders = idx.list_folders()
        assert folders == sorted(set(folders))
        assert "Journal" in folders
        assert "Projects" in folders
        assert "" in folders  # root document
        # No duplicates.
        assert len(folders) == len(set(folders))


class TestListFieldValues:
    def test_list_field_values_returns_distinct_values(self) -> None:
        """list_field_values() returns distinct tag values for a field."""
        idx = _tagged_index()
        idx.upsert_note(make_note("a.md", frontmatter={"cluster": "fiction"}))
        idx.upsert_note(make_note("b.md", frontmatter={"cluster": "nonfiction"}))
        idx.upsert_note(make_note("c.md", frontmatter={"cluster": "fiction"}))

        values = idx.list_field_values("cluster")
        assert sorted(values) == ["fiction", "nonfiction"]
        # No duplicates.
        assert len(values) == len(set(values))


class TestTagIndexing:
    def test_tag_indexing_scalar_creates_one_row(self) -> None:
        """A scalar frontmatter value produces exactly one document_tags row."""
        idx = _tagged_index()
        idx.upsert_note(make_note("scalar.md", frontmatter={"cluster": "fiction"}))
        rows = idx._conn.execute(
            "SELECT tag_value FROM document_tags WHERE tag_key = 'cluster'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "fiction"

    def test_tag_indexing_list_deduplicates(self) -> None:
        """A list frontmatter value creates one row per distinct item."""
        idx = _tagged_index()
        idx.upsert_note(make_note("list.md", frontmatter={"topics": ["a", "b", "a"]}))
        rows = idx._conn.execute(
            "SELECT tag_value FROM document_tags WHERE tag_key = 'topics' "
            "ORDER BY tag_value"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "a"
        assert rows[1][0] == "b"

    def test_tag_indexing_complex_value_skipped(self) -> None:
        """A nested dict frontmatter value is NOT promoted to document_tags."""
        idx = _tagged_index()
        idx.upsert_note(
            make_note("complex.md", frontmatter={"cluster": {"key": "val"}})
        )
        rows = idx._conn.execute(
            "SELECT COUNT(*) FROM document_tags WHERE tag_key = 'cluster'"
        ).fetchone()
        assert rows[0] == 0


class TestGetNote:
    def test_get_note_returns_correct_dict(self) -> None:
        """get_note() returns a dict with the expected keys and values."""
        idx = FTSIndex(":memory:")
        note = make_note(
            "Journal/entry.md",
            title="My Entry",
            frontmatter={"date": "2024-01-01"},
            content_hash="deadbeef",
            modified_at=9999.0,
        )
        idx.upsert_note(note)

        result = idx.get_note("Journal/entry.md")
        assert result is not None
        assert result["path"] == "Journal/entry.md"
        assert result["title"] == "My Entry"
        assert result["folder"] == "Journal"
        assert result["content_hash"] == "deadbeef"
        assert result["modified_at"] == pytest.approx(9999.0)

    def test_get_note_not_found_returns_none(self) -> None:
        """get_note() returns None for a path that was never indexed."""
        idx = FTSIndex(":memory:")
        assert idx.get_note("nonexistent.md") is None


class TestInMemoryMode:
    def test_in_memory_mode_works(self) -> None:
        """FTSIndex with ':memory:' is functional end-to-end."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "mem.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="in-memory test passage",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("memory")
        assert len(results) >= 1
        assert results[0].path == "mem.md"
